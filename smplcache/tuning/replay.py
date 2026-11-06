# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""Benchmark replay for self-tuning experiments.

The workload is a recorded "day": a write-heavy stream (3 writes per read) with
Zipfian skew over rows, tables and columns, a phase change halfway through, and
a SQLite database that plays the system of record. Every value SmplCache serves
from cache is compared with a fresh SQL query against that database; the
mismatch count is the oracle metric and must stay at 0.

`CacheSim` replays the day against a configuration:

- which shapes are materialized (`materialized`);
- how shapes are grouped into store partitions (`partition_of`) when the store
  can only drop whole partitions (`store_granularity="partition"`), which is how
  tag-based invalidation works in many caches;
- an optional shape cap with an admission policy (E3).

Certification is always per shape and always by `smplcache.process_event`.
A configuration can only make SmplCache drop *more* (collateral flushes) or
cache *less*; it can never keep a value the certifier invalidated. That is the
invariant the oracle check enforces on every run.

Baselines: `plain` (every shape materialized, per-shape store) and `ttl`
(cache-aside with a fixed time to live, no certification: it serves stale values
and the oracle counts them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import random
import sqlite3
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from smplcache.smplcache import (
    AggregateCache,
    DecisionKind,
    Predicate,
    QueryShape,
    WriteEvent,
    _values_equal,
    process_event,
)

TABLES: dict[str, tuple[str, ...]] = {
    "orders": ("id", "customer_id", "status", "amount", "region", "notes"),
    "customers": ("id", "region", "tier", "credit", "notes"),
    "inventory": ("id", "sku", "warehouse", "quantity", "notes"),
}
REGIONS = ("north", "south", "east", "west")
STATUSES = ("paid", "pending", "cancelled")
TIERS = ("gold", "silver", "bronze")
WAREHOUSES = ("w1", "w2", "w3")


def benchmark_shapes() -> list[QueryShape]:
    """Twelve cached queries; one (MAX) is outside the admitted fragment on purpose."""
    return [
        QueryShape("orders", "sum", "amount", ("region",), name="sum_amount_by_region"),
        QueryShape("orders", "count_star", None, (), Predicate((("status", "=", "paid"),)), name="count_paid_orders"),
        QueryShape("orders", "avg", "amount", (), Predicate((("status", "=", "paid"),)), name="avg_amount_paid"),
        QueryShape("orders", "sum", "amount", ("customer_id",), name="sum_amount_by_customer"),
        QueryShape("orders", "count_star", None, ("status",), name="count_orders_by_status"),
        QueryShape("orders", "count_star", None, (), Predicate((("region", "=", "west"),)), name="count_orders_west"),
        QueryShape("orders", None, None, (), name="max_amount_by_region", aggregate_cols=("max(amount)",)),
        QueryShape("customers", "count_star", None, ("tier",), name="count_customers_by_tier"),
        QueryShape("customers", "sum", "credit", ("region",), name="sum_credit_by_region"),
        QueryShape("customers", "avg", "credit", (), Predicate((("tier", "=", "gold"),)), name="avg_credit_gold"),
        QueryShape("inventory", "sum", "quantity", ("warehouse",), name="sum_qty_by_warehouse"),
        QueryShape("inventory", "count_star", None, (), Predicate((("quantity", "<", 10),)), name="count_low_stock"),
    ]


@dataclass(frozen=True)
class ReadOp:
    shape: str
    seq: int


@dataclass
class Day:
    """A recorded workload: seed rows, then an interleaved stream of writes and reads."""

    seed_rows: dict[str, list[dict[str, Any]]]
    stream: list[WriteEvent | ReadOp]
    seed: int
    phase_change_at: int

    @property
    def writes(self) -> int:
        return sum(1 for op in self.stream if isinstance(op, WriteEvent))

    @property
    def reads(self) -> int:
        return sum(1 for op in self.stream if isinstance(op, ReadOp))

    def slice(self, start: int, stop: int) -> "Day":
        """A window of the day whose seed rows are the database state at ``start``.

        The oracle caught the first version of this method, which reused the
        day's seed rows for every window: cached values then disagreed with the
        database on every window that did not start at 0.
        """
        db = SqlDatabase(self.seed_rows)
        for op in self.stream[:start]:
            if isinstance(op, WriteEvent):
                db.apply(op)
        seed = {t: db.rows(t) for t in TABLES}
        return Day(seed, self.stream[start:stop], self.seed, max(self.phase_change_at - start, 0))


def _zipf_weights(n: int, s: float) -> list[float]:
    return [1.0 / ((k + 1) ** s) for k in range(n)]


def generate_day(
    seed: int = 1,
    writes: int = 6000,
    reads_per_write: float = 1 / 3,
    rows_per_table: int = 400,
    zipf_s: float = 1.1,
    replica_identity_default: Iterable[str] = ("customers",),
) -> Day:
    """Write-heavy day with Zipfian skew and a phase change at the midpoint.

    Tables listed in ``replica_identity_default`` emit UPDATE events without a
    before-image for unchanged columns (as Debezium does under REPLICA IDENTITY
    DEFAULT), so SmplCache must invalidate them for lack of evidence (Theorem 7).
    """
    rng = random.Random(seed)
    shapes = benchmark_shapes()
    shape_names = [s.name for s in shapes]
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    next_id = {t: 1 for t in TABLES}

    def new_row(table: str) -> dict[str, Any]:
        rid = next_id[table]
        next_id[table] += 1
        if table == "orders":
            return {"id": rid, "customer_id": rng.randint(1, rows_per_table // 4), "status": rng.choice(STATUSES),
                    "amount": rng.randint(1, 500), "region": rng.choice(REGIONS), "notes": "n"}
        if table == "customers":
            return {"id": rid, "region": rng.choice(REGIONS), "tier": rng.choice(TIERS), "credit": rng.randint(0, 1000), "notes": "n"}
        return {"id": rid, "sku": f"sku{rid}", "warehouse": rng.choice(WAREHOUSES), "quantity": rng.randint(0, 50), "notes": "n"}

    for table in TABLES:
        for _ in range(rows_per_table):
            rows[table].append(new_row(table))
    seed_rows = {t: [dict(r) for r in rs] for t, rs in rows.items()}
    live = {t: {r["id"]: dict(r) for r in rs} for t, rs in rows.items()}
    no_before_image = set(replica_identity_default)

    # phase 1: orders dominate, amount/status churn; phase 2: inventory and region churn
    table_mix = {1: (("orders", 0.7), ("customers", 0.15), ("inventory", 0.15)), 2: (("orders", 0.35), ("customers", 0.15), ("inventory", 0.5))}
    col_mix = {
        1: {"orders": (("amount", 0.45), ("status", 0.25), ("notes", 0.2), ("region", 0.1)),
            "customers": (("credit", 0.5), ("tier", 0.2), ("notes", 0.3)),
            "inventory": (("quantity", 0.6), ("notes", 0.4))},
        2: {"orders": (("region", 0.4), ("notes", 0.3), ("amount", 0.2), ("status", 0.1)),
            "customers": (("region", 0.4), ("credit", 0.3), ("notes", 0.3)),
            "inventory": (("quantity", 0.8), ("warehouse", 0.2))},
    }
    read_mix = {1: _zipf_weights(len(shape_names), zipf_s), 2: list(reversed(_zipf_weights(len(shape_names), zipf_s)))}

    def pick(pairs: Sequence[tuple[str, float]]) -> str:
        return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]

    def mutate(table: str, row: dict[str, Any], col: str) -> Any:
        if col == "amount":
            return max(1, row["amount"] + rng.randint(-100, 100))
        if col == "status":
            return rng.choice([s for s in STATUSES if s != row["status"]])
        if col == "region":
            return rng.choice([r for r in REGIONS if r != row["region"]])
        if col == "notes":
            return row["notes"] + "x" if len(row["notes"]) < 8 else "n"
        if col == "credit":
            return max(0, row["credit"] + rng.randint(-200, 200))
        if col == "tier":
            return rng.choice([t for t in TIERS if t != row["tier"]])
        if col == "quantity":
            return max(0, row["quantity"] + rng.randint(-15, 15))
        if col == "warehouse":
            return rng.choice([w for w in WAREHOUSES if w != row["warehouse"]])
        raise KeyError(col)

    stream: list[WriteEvent | ReadOp] = []
    phase_change_at = writes // 2
    seq = 0
    read_budget = 0.0
    hot_ids = {t: ZipfIds(rows_per_table, zipf_s, rng) for t in TABLES}
    for w in range(writes):
        phase = 1 if w < phase_change_at else 2
        table = pick(table_mix[phase])
        op_roll = rng.random()
        seq += 1
        clock = f"lsn-{seq}"
        if op_roll < 0.2 or not live[table]:
            row = new_row(table)
            live[table][row["id"]] = row
            stream.append(WriteEvent(table, set(), {}, dict(row), f"evt-{seq}", "INSERT", boundary_clock=clock))
        elif op_roll < 0.3:
            rid = hot_ids[table].pick(sorted(live[table]))
            row = live[table].pop(rid)
            stream.append(WriteEvent(table, set(), dict(row), {}, f"evt-{seq}", "DELETE", boundary_clock=clock))
        else:
            rid = hot_ids[table].pick(sorted(live[table]))
            row = live[table][rid]
            col = pick(col_mix[phase][table])
            new = dict(row)
            new[col] = mutate(table, row, col)
            old_full = dict(row)
            live[table][rid] = new
            if table in no_before_image:
                unavailable = {c for c in TABLES[table] if c != "id"}
                stream.append(WriteEvent(table, set(), {"id": rid}, dict(new), f"evt-{seq}", "UPDATE",
                                         unavailable_cols=unavailable, boundary_clock=clock))
            else:
                stream.append(WriteEvent(table, set(), old_full, dict(new), f"evt-{seq}", "UPDATE", boundary_clock=clock))
        read_budget += reads_per_write
        while read_budget >= 1.0:
            read_budget -= 1.0
            seq += 1
            stream.append(ReadOp(rng.choices(shape_names, weights=read_mix[phase])[0], seq))
    return Day(seed_rows, stream, seed, phase_change_at)


class ZipfIds:
    """Zipf over the rank of a row id in the sorted live id list (hot rows stay hot)."""

    def __init__(self, n: int, s: float, rng: random.Random) -> None:
        self.weights = _zipf_weights(n, s)
        self.rng = rng

    def pick(self, ids: Sequence[int]) -> int:
        w = self.weights[: len(ids)] if len(ids) <= len(self.weights) else self.weights + [self.weights[-1]] * (len(ids) - len(self.weights))
        return self.rng.choices(ids, weights=w)[0]


# --- SQLite system of record and oracle ---------------------------------------------------


class SqlDatabase:
    """The database the cache fronts. Also the oracle: every served value is checked here."""

    def __init__(self, seed_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self.con = sqlite3.connect(":memory:")
        self.queries = 0
        for table, cols in TABLES.items():
            self.con.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
            self.con.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [tuple(r.get(c) for c in cols) for r in seed_rows.get(table, ())],
            )

    def apply(self, event: WriteEvent) -> None:
        cols = TABLES[event.relation]
        if event.operation == "INSERT":
            self.con.execute(
                f"INSERT INTO {event.relation} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                tuple(event.new.get(c) for c in cols),
            )
        elif event.operation == "DELETE":
            self.con.execute(f"DELETE FROM {event.relation} WHERE id = ?", (event.old["id"],))
        else:
            sets = ", ".join(f"{c} = ?" for c in cols if c != "id")
            self.con.execute(
                f"UPDATE {event.relation} SET {sets} WHERE id = ?",
                tuple(event.new.get(c) for c in cols if c != "id") + (event.new["id"],),
            )

    def rows(self, table: str) -> list[dict[str, Any]]:
        cols = TABLES[table]
        return [dict(zip(cols, r)) for r in self.con.execute(f"SELECT {', '.join(cols)} FROM {table}")]

    def query(self, shape: QueryShape) -> dict[Any, Decimal | None]:
        """Run the shape's SQL and return {group_key: value} with exact Decimal arithmetic."""
        self.queries += 1
        rows = self.rows(shape.relation)
        return AggregateCache.from_rows(shape, rows).values if shape.agg_fn is not None else _extremum(shape, rows)

    def sql_check(self, shape: QueryShape) -> dict[Any, Decimal | None]:
        """Independent SQL evaluation (not AggregateCache) used only by the oracle."""
        sql = shape_sql(shape)
        self.queries += 1
        out: dict[Any, Decimal | None] = {}
        ngroups = len(shape.group_cols)
        for row in self.con.execute(sql).fetchall():
            key: Any = () if ngroups == 0 else (row[0] if ngroups == 1 else tuple(row[:ngroups]))
            val = row[-1]
            out[key] = None if val is None else Decimal(str(val))
        if ngroups == 0 and not out:
            out[()] = Decimal(0) if shape.agg_fn in ("count", "count_star") else None
        return out


def _extremum(shape: QueryShape, rows: Sequence[Mapping[str, Any]]) -> dict[Any, Decimal | None]:
    """Reference values for the unsupported MAX shape (it is always read from the database)."""
    out: dict[Any, Decimal | None] = {}
    for r in rows:
        key = r.get("region")
        cur = out.get(key)
        val = Decimal(r["amount"])
        out[key] = val if cur is None or val > cur else cur
    return out


def shape_sql(shape: QueryShape) -> str:
    if shape.agg_fn is None:
        return "SELECT region, MAX(amount) FROM orders GROUP BY region"
    parts = list(shape.group_cols)
    agg = {"sum": f"SUM({shape.agg_col})", "count": f"COUNT({shape.agg_col})", "count_star": "COUNT(*)", "avg": f"AVG({shape.agg_col})"}[shape.agg_fn]
    if shape.agg_fn == "avg":
        agg = f"SUM({shape.agg_col}) * 1.0 / COUNT({shape.agg_col})"
    parts.append(agg)
    sql = f"SELECT {', '.join(parts)} FROM {shape.relation}"
    if shape.predicate.terms:
        sql += " WHERE " + " AND ".join(
            f"{c} {'=' if op in ('=', '==') else op} {repr(lit) if isinstance(lit, str) else lit}" for c, op, lit in shape.predicate.terms
        )
    if shape.group_cols:
        sql += " GROUP BY " + ", ".join(shape.group_cols)
    return sql


def values_match(cached: Mapping[Any, Any], oracle: Mapping[Any, Any], agg_fn: str | None) -> bool:
    if set(cached) != set(oracle):
        return False
    for k, v in cached.items():
        o = oracle[k]
        if v is None or o is None:
            if (v is None) != (o is None):
                return False
            continue
        if agg_fn == "avg":
            if abs(Decimal(v) - Decimal(o)) > Decimal("0.000001") * max(1, abs(Decimal(o))):
                return False
        elif not _values_equal(v, o):
            return False
    return True


# --- the cache simulation -----------------------------------------------------------------


@dataclass
class ShapeStats:
    hits: int = 0
    db_reads: int = 0
    rereads: int = 0
    invalidations: int = 0
    collateral: int = 0
    repairs: int = 0
    preserves: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    epochs_without_hit: int = 0
    epochs_with_hit: int = 0


@dataclass
class ReplayResult:
    label: str
    reads: int = 0
    writes: int = 0
    hits: int = 0
    db_reads: int = 0
    rereads: int = 0
    invalidations: int = 0
    collateral_flushes: int = 0
    oracle_checks: int = 0
    oracle_mismatches: int = 0
    decision_latencies_ns: list[int] = field(default_factory=list)
    peak_cached_groups: int = 0
    group_seconds: int = 0
    per_shape: dict[str, ShapeStats] = field(default_factory=dict)
    co_invalidation: list[tuple[int, frozenset[str]]] = field(default_factory=list)
    """Per write: (clock, shapes the write touched, i.e. repaired or invalidated)."""
    invalidated_sets: list[tuple[int, frozenset[str]]] = field(default_factory=list)
    """Per write: (clock, shapes the certifier invalidated), the flush-relevant subset."""
    certificates: list[dict[str, Any]] = field(default_factory=list)
    event_log: list[tuple[int, str, str]] = field(default_factory=list)
    """Receipts for off-policy estimation: (clock, "read" | "invalidate", shape)."""

    @property
    def hit_rate(self) -> float:
        return self.hits / self.reads if self.reads else 0.0

    @property
    def mean_cached_groups(self) -> float:
        return self.group_seconds / max(1, self.reads + self.writes)

    @property
    def p99_decision_latency_us(self) -> float:
        if not self.decision_latencies_ns:
            return 0.0
        xs = sorted(self.decision_latencies_ns)
        return xs[min(len(xs) - 1, int(0.99 * len(xs)))] / 1000.0

    def row(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "reads": self.reads,
            "writes": self.writes,
            "db_reads": self.db_reads,
            "rereads": self.rereads,
            "hit_rate": round(self.hit_rate, 4),
            "p99_decision_latency_us": round(self.p99_decision_latency_us, 1),
            "oracle_mismatches": self.oracle_mismatches,
            "oracle_checks": self.oracle_checks,
            "collateral_flushes": self.collateral_flushes,
            "peak_cached_groups": self.peak_cached_groups,
            "mean_cached_groups": round(self.mean_cached_groups, 1),
        }


AdmissionPolicy = Callable[["CacheSim", str], str | None]
"""Given the sim and a shape that wants to be admitted under a full cap, return the shape to evict or None to refuse."""


@dataclass
class CacheConfig:
    materialized: frozenset[str]
    partition_of: Mapping[str, str] | None = None
    store_granularity: str = "shape"  # "shape" or "partition"
    shape_cap: int | None = None
    admission: AdmissionPolicy | None = None
    ttl_events: int | None = None  # TTL baseline only: no certification
    label: str = "plain"
    on_decision: Callable[[str, WriteEvent, DecisionKind, str], None] | None = None


class CacheSim:
    """Replay a `Day` against one `CacheConfig`; every served value is oracle-checked."""

    def __init__(self, day: Day, config: CacheConfig, shapes: Sequence[QueryShape] | None = None, oracle_every: int = 1, fast: bool = False) -> None:
        """``fast`` skips oracle checks and the SQL for uncached reads (counts them only).
        Evaluators use it for rollouts; every reported run uses ``fast=False``."""
        self.day = day
        self.config = config
        self.shapes = {s.name: s for s in (shapes or benchmark_shapes())}
        self.db = SqlDatabase(day.seed_rows)
        self.cached: dict[str, AggregateCache | None] = {}
        self.cached_values: dict[str, dict[Any, Any]] = {}
        self.valid: dict[str, bool] = {}
        self.expires_at: dict[str, int] = {}
        self.last_read: dict[str, int] = {}
        self.read_count: dict[str, int] = {}
        self.result = ReplayResult(config.label, per_shape={n: ShapeStats() for n in self.shapes})
        self.oracle_every = oracle_every
        self.fast = fast
        self.clock = 0

    # -- helpers
    def partition(self, name: str) -> str:
        if self.config.partition_of is None:
            return name
        return self.config.partition_of.get(name, name)

    def resident(self) -> list[str]:
        return [n for n, c in self.cached.items() if c is not None]

    def cached_groups(self) -> int:
        return sum(len(c.values) for c in self.cached.values() if c is not None)

    def _materialize(self, name: str) -> None:
        shape = self.shapes[name]
        stats = self.result.per_shape[name]
        self.result.db_reads += 1
        stats.db_reads += 1
        if shape.agg_fn is None:
            self.cached[name] = None  # outside the admitted fragment: never cached
            return
        cache = AggregateCache.from_rows(shape, self.db.rows(shape.relation))
        self.db.queries += 1
        self.cached[name] = cache
        self.valid[name] = True
        self.expires_at[name] = self.clock + (self.config.ttl_events or 0)
        stats.epochs_without_hit += 1  # moved to epochs_with_hit on this epoch's first hit

    def _drop(self, name: str, reason: str, collateral: bool = False) -> None:
        if self.cached.get(name) is None:
            return
        stats = self.result.per_shape[name]
        self.valid[name] = False
        stats.invalidations += 1
        self.result.invalidations += 1
        if collateral:
            stats.collateral += 1
            self.result.collateral_flushes += 1
        stats.reasons[reason] = stats.reasons.get(reason, 0) + 1

    def evict(self, name: str) -> None:
        self.cached[name] = None
        self.valid[name] = False

    # -- the two operations
    def on_write(self, event: WriteEvent) -> None:
        self.result.writes += 1
        self.db.apply(event)
        if self.config.ttl_events is not None:
            return  # TTL cache-aside never looks at writes
        touched: set[str] = set()
        invalidated: set[str] = set()
        flushed_partitions: set[str] = set()
        for name, cache in self.cached.items():
            if cache is None or not self.valid.get(name):
                continue
            shape = self.shapes[name]
            if shape.relation != event.relation:
                continue
            t0 = time.perf_counter_ns()
            decision = process_event(shape, cache, event)
            self.result.decision_latencies_ns.append(time.perf_counter_ns() - t0)
            stats = self.result.per_shape[name]
            if self.config.on_decision is not None:
                self.config.on_decision(name, event, decision.kind, decision.reason)
            if decision.kind == DecisionKind.PRESERVE:
                stats.preserves += 1
            elif decision.kind == DecisionKind.REPAIR:
                stats.repairs += 1
                touched.add(name)
            else:
                touched.add(name)
                invalidated.add(name)
                self.result.certificates.append(
                    {"shape": name, "event_id": event.event_id, "relation": event.relation, "decision": decision.kind.value,
                     "reason": decision.reason, "required": list(decision.certificate.required_evidence),
                     "available": list(decision.certificate.available_evidence), "unavailable": sorted(event.unavailable_cols)}
                )
                self._drop(name, decision.reason)
                self.result.event_log.append((self.clock, "invalidate", name))
                if self.config.store_granularity == "partition":
                    flushed_partitions.add(self.partition(name))
        for part in flushed_partitions:
            for other in self.resident():
                if self.valid.get(other) and self.partition(other) == part:
                    self._drop(other, "collateral_partition_flush", collateral=True)
        if touched:
            self.result.co_invalidation.append((self.clock, frozenset(touched)))
        if invalidated:
            self.result.invalidated_sets.append((self.clock, frozenset(invalidated)))

    def on_read(self, op: ReadOp) -> None:
        name = op.shape
        shape = self.shapes[name]
        stats = self.result.per_shape[name]
        self.result.reads += 1
        self.result.event_log.append((self.clock, "read", name))
        self.last_read[name] = self.clock
        self.read_count[name] = self.read_count.get(name, 0) + 1
        if name not in self.config.materialized or shape.agg_fn is None:
            self.result.db_reads += 1
            stats.db_reads += 1
            if not self.fast:
                self.db.query(shape)
            return
        cache = self.cached.get(name)
        if cache is None:
            if self.config.shape_cap is not None and len(self.resident()) >= self.config.shape_cap:
                victim = self.config.admission(self, name) if self.config.admission else None
                if victim is None:
                    self.result.db_reads += 1
                    stats.db_reads += 1
                    if not self.fast:
                        self.db.query(shape)
                    return
                self.evict(victim)
            self._materialize(name)
            return
        if self.config.ttl_events is not None:
            if self.clock >= self.expires_at.get(name, 0):
                self._materialize(name)
                stats.rereads += 1
                self.result.rereads += 1
                return
        elif not self.valid.get(name):
            stats.rereads += 1
            self.result.rereads += 1
            self._materialize(name)
            return
        # served from cache: oracle check
        self.result.hits += 1
        stats.hits += 1
        if stats.epochs_without_hit > 0:
            stats.epochs_without_hit -= 1
            stats.epochs_with_hit += 1
        if not self.fast and self.result.hits % self.oracle_every == 0:
            self.result.oracle_checks += 1
            if not values_match(cache.values, self.db.sql_check(shape), shape.agg_fn):
                self.result.oracle_mismatches += 1

    def run(self) -> ReplayResult:
        for op in self.day.stream:
            self.clock += 1
            if isinstance(op, WriteEvent):
                self.on_write(op)
            else:
                self.on_read(op)
            groups = self.cached_groups()
            self.result.group_seconds += groups
            self.result.peak_cached_groups = max(self.result.peak_cached_groups, groups)
        return self.result


def run_plain(day: Day, shapes: Sequence[QueryShape] | None = None, **kw: Any) -> ReplayResult:
    names = frozenset(s.name for s in (shapes or benchmark_shapes()))
    return CacheSim(day, CacheConfig(materialized=names, label="plain"), shapes, **kw).run()


def run_ttl(day: Day, ttl_events: int = 200, shapes: Sequence[QueryShape] | None = None, **kw: Any) -> ReplayResult:
    names = frozenset(s.name for s in (shapes or benchmark_shapes()))
    return CacheSim(day, CacheConfig(materialized=names, ttl_events=ttl_events, label=f"ttl-{ttl_events}"), shapes, **kw).run()


def run_uncached(day: Day, shapes: Sequence[QueryShape] | None = None, **kw: Any) -> ReplayResult:
    return CacheSim(day, CacheConfig(materialized=frozenset(), label="uncached"), shapes, **kw).run()


def table_markdown(results: Iterable[ReplayResult]) -> str:
    rows = [r.row() for r in results]
    cols = ["label", "db_reads", "rereads", "hit_rate", "p99_decision_latency_us", "oracle_mismatches", "collateral_flushes", "peak_cached_groups"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)
