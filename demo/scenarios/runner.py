# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Deterministic scenario generator, real SmplCache execution harness, table-invalidation
baseline, and independent native SQLite oracle for SmplCache Live.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
import platform
import random
import sqlite3
import subprocess
import time
from typing import Any, Callable, Literal, Mapping, Sequence

import smplcache
from smplcache import (
    AggregateCache,
    Decision,
    DecisionKind,
    JoinAuxState,
    MultiTableJoinShape,
    QueryShape,
    WalStreamProcessor,
    WriteEvent,
    parse_join_sql,
    parse_sql,
    process_event,
    process_join_event,
    rebuild_join_cache,
)
from smplcache.debezium import DEBEZIUM_UNAVAILABLE_VALUE

API_VERSION = "1.0.0"
SCENARIO_VERSION = "1.0.0"

ALLOWED_DATASET_SIZES: tuple[int, ...] = (100, 500, 1000, 2500)
ALLOWED_AUX_CAPACITIES: tuple[int | None, ...] = (None, 60, 120, 250, 500, 1500, 3000)

REGIONS_TABLE_SEED: tuple[dict[str, Any], ...] = (
    {"id": 1, "code": "us-east", "name": "North America"},
    {"id": 2, "code": "eu-west", "name": "Europe"},
    {"id": 3, "code": "ap-south", "name": "Asia Pacific"},
    {"id": 4, "code": "latam", "name": "Latin America"},
)

PLAIN_LANGUAGE_REASONS: dict[str, str] = {
    "disjoint_columns": (
        "Preserve — the changed column (delivery note) is outside the query's "
        "dependency boundary, so the cached total remains valid without querying SQLite."
    ),
    "noop_update": (
        "Preserve — neither before nor after row state alters the filtered aggregate "
        "contribution."
    ),
    "unrelated_relation": (
        "Preserve — write targeted a different table or tenant schema outside this "
        "query's bound relation."
    ),
    "transaction_rollback": (
        "Preserve — transaction rolled back; buffered writes were discarded without "
        "touching committed state."
    ),
    "value_change": (
        "Repair — complete before/after row evidence permits an exact in-place delta "
        "update to the group sum."
    ),
    "predicate_entry": (
        "Repair — order status transitioned into the WHERE status = 'paid' filter; "
        "added row contribution in place."
    ),
    "predicate_exit": (
        "Repair — order status exited the WHERE status = 'paid' filter; subtracted "
        "prior row contribution in place."
    ),
    "group_move": (
        "Repair — row moved between GROUP BY buckets; subtracted from old group and "
        "added to new group in place."
    ),
    "insert": (
        "Repair — inserted row carries complete required columns; updated aggregate "
        "state in place."
    ),
    "delete": (
        "Repair — deleted row carries complete before-image evidence; decremented "
        "group sum and row count (removing empty groups when count reaches zero)."
    ),
    "join_fact_update": (
        "Repair — fact-table change resolved against resident foreign-key dimension "
        "rows in JoinAuxState; updated joined regional totals in place."
    ),
    "dimension_update": (
        "Repair — customer moved between regions; JoinAuxState resolved all child "
        "orders in the foreign-key fanout and transferred their revenue between regions."
    ),
    "fk_reparent": (
        "Repair — order foreign key reparented to a different customer; JoinAuxState "
        "updated multiplicity indexes and moved revenue between regions in place."
    ),
    "missing_evidence_for_repair": (
        "Invalidate / Recompute — CDC event omitted required before-image evidence "
        "(amount_cents). SmplCache refused to guess, withheld the stale cache, and "
        "rebuilt from the committed SQLite snapshot."
    ),
    "evicted_join_auxiliary_key": (
        "Invalidate / Recompute — event referenced a key evicted under the configured "
        "resident auxiliary row limit. SmplCache refused unverified join repair and "
        "rebuilt from the 3 source tables."
    ),
    "cache_requires_rebuild": (
        "Invalidate / Recompute — cache was latched invalid from an earlier unverified "
        "boundary; repair remained blocked until snapshot rebuild completed."
    ),
}


def _detect_git_commit() -> str:
    env_commit = os.environ.get("SMPLCACHE_COMMIT", "").strip()
    if env_commit:
        return env_commit
    repo_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return "bbc5d703a297"


@dataclass(frozen=True)
class ScenarioConfig:
    scenario_id: Literal[
        "live_revenue", "customers_and_regions", "missing_evidence"
    ] = "live_revenue"
    seed: int = 42
    dataset_size: int = 1000
    aux_max_total_rows: int | None = None
    max_steps: int | None = None
    evidence_mode: Literal["standard", "remove_required_evidence"] = "standard"
    defer_rebuild: bool = False
    _fault_inject_corrupt_step: int | None = None


SCENARIO_CATALOG: dict[str, dict[str, Any]] = {
    "live_revenue": {
        "scenario_id": "live_revenue",
        "title": "Live revenue (60-second walkthrough)",
        "short_label": "Live revenue",
        "category": "single_table",
        "sql": (
            "SELECT region, SUM(amount_cents) "
            "FROM orders "
            "WHERE status = 'paid' "
            "GROUP BY region"
        ),
        "description": (
            "Single-table regional revenue dashboard in integer cents. Demonstrates "
            "irrelevant delivery-note writes (Preserve), paid order amount edits (Repair), "
            "predicate entry/exit, group moves, last-member group deletion, and "
            "missing-evidence refusal + snapshot recovery."
        ),
        "engine_components": [
            "parse_sql",
            "QueryShape",
            "AggregateCache.from_rows",
            "certify_event",
            "process_event",
        ],
        "participating_tables": ["orders"],
        "default_seed": 42,
        "default_dataset_size": 1000,
        "default_aux_max_total_rows": None,
        "supports_aux_capacity": False,
        "step_count": 7,
    },
    "customers_and_regions": {
        "scenario_id": "customers_and_regions",
        "title": "Customers and regions (Foreign-key join & bounded capacity)",
        "short_label": "Customers & regions",
        "category": "multi_table_join",
        "sql": (
            "SELECT r.name, SUM(o.amount_cents) "
            "FROM orders o "
            "JOIN customers c ON o.customer_id = c.id "
            "JOIN regions r ON c.region_id = r.id "
            "WHERE o.status = 'paid' "
            "GROUP BY r.name"
        ),
        "description": (
            "Three-table foreign-key join (orders -> customers -> regions). Demonstrates "
            "dimension-table region moves with 1-to-k order fanout repair, multi-table "
            "COMMIT/ROLLBACK transactions, and capacity-triggered eviction fallback "
            "when resident auxiliary row limits are lowered."
        ),
        "engine_components": [
            "parse_join_sql",
            "MultiTableJoinShape",
            "JoinAuxState.from_tables",
            "process_join_event",
            "rebuild_join_cache",
            "WalStreamProcessor",
        ],
        "participating_tables": ["orders", "customers", "regions"],
        "default_seed": 42,
        "default_dataset_size": 1000,
        "default_aux_max_total_rows": None,
        "adverse_aux_max_total_rows": 60,
        "supports_aux_capacity": True,
        "capacity_disclosure": (
            "Configured limit counts resident auxiliary rows in JoinAuxState. "
            "Eviction tombstones, foreign-key indexes, Count-Min Sketch counters, "
            "aggregate group state, and transient work also consume process memory; "
            "this row cap is not a total RAM limit."
        ),
        "step_count": 7,
    },
    "missing_evidence": {
        "scenario_id": "missing_evidence",
        "title": "Missing evidence (WAL refusal, latching & boundary rebuild)",
        "short_label": "Missing evidence",
        "category": "wal_recovery",
        "sql": (
            "SELECT region, SUM(amount_cents) "
            "FROM main.orders "
            "WHERE status = 'paid' "
            "GROUP BY region"
        ),
        "description": (
            "Decoded Debezium WAL stream demonstrating refusal to repair when before-image "
            "or TOASTed columns are missing, invalidation latching across subsequent events "
            "until rebuilt from the exact committed SQLite boundary, cross-schema write "
            "isolation, and clean post-rebuild delta repair."
        ),
        "engine_components": [
            "WalStreamProcessor",
            "parse_wal_record",
            "AggregateCache.from_rows",
            "certify_event",
        ],
        "participating_tables": ["main.orders"],
        "default_seed": 42,
        "default_dataset_size": 1000,
        "default_aux_max_total_rows": None,
        "supports_aux_capacity": False,
        "step_count": 6,
    },
}


def get_rejected_sql_examples() -> list[dict[str, Any]]:
    """
    Evaluate unsupported SQL queries through the real `parse_sql` and `parse_join_sql`
    AST allowlist extractors so visitors can inspect actual parser rejection reasons.
    """
    samples = [
        {
            "id": "window_rank",
            "label": "Window function (OVER / PARTITION BY)",
            "parser": "parse_sql",
            "sql": (
                "SELECT region, SUM(amount_cents) OVER (PARTITION BY region) "
                "FROM orders WHERE status = 'paid'"
            ),
            "explanation": (
                "Window frames require full partition ordering/peer state and are "
                "outside the admitted single-block SPJA fragment."
            ),
        },
        {
            "id": "min_max_extremum",
            "label": "Non-invertible extremum (MAX / MIN)",
            "parser": "parse_sql",
            "sql": "SELECT region, MAX(amount_cents) FROM orders GROUP BY region",
            "explanation": (
                "Deleting or lowering the current maximum cannot be repaired from a "
                "single scalar total without maintaining an ordered all-values index."
            ),
        },
        {
            "id": "having_clause",
            "label": "Post-aggregation HAVING filter",
            "parser": "parse_sql",
            "sql": (
                "SELECT region, SUM(amount_cents) FROM orders "
                "GROUP BY region HAVING SUM(amount_cents) > 50000"
            ),
            "explanation": (
                "HAVING predicates over aggregate outputs are rejected by the select-clause "
                "AST allowlist."
            ),
        },
        {
            "id": "or_disjunction",
            "label": "Disjunctive WHERE predicate (OR)",
            "parser": "parse_sql",
            "sql": (
                "SELECT region, SUM(amount_cents) FROM orders "
                "WHERE status = 'paid' OR status = 'settled' GROUP BY region"
            ),
            "explanation": (
                "The v1 predicate allowlist admits conjunctions (AND) of column-literal "
                "comparisons and rejects OR trees."
            ),
        },
        {
            "id": "unqualified_join",
            "label": "Ambiguous unqualified column in JOIN",
            "parser": "parse_join_sql",
            "sql": (
                "SELECT name, SUM(amount_cents) FROM orders o "
                "JOIN customers c ON o.customer_id = c.id GROUP BY name"
            ),
            "explanation": (
                "Multi-table joins require explicit table or alias qualification on "
                "every column to prevent schema/binding ambiguity."
            ),
        },
        {
            "id": "cross_schema_mismatch",
            "label": "Cross-schema column binding mismatch",
            "parser": "parse_join_sql",
            "sql": (
                "SELECT SUM(tenant_b.orders.amount_cents) "
                "FROM tenant_a.orders JOIN tenant_a.customers "
                "ON orders.customer_id = customers.id"
            ),
            "explanation": (
                "Column qualifier schema (tenant_b) conflicts with the bound FROM "
                "table schema (tenant_a); rejected fail-closed."
            ),
        },
    ]

    results: list[dict[str, Any]] = []
    for item in samples:
        if item["parser"] == "parse_join_sql":
            res = parse_join_sql(item["sql"])
            results.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "parser": item["parser"],
                    "sql": item["sql"],
                    "is_supported": res.is_supported,
                    "rejection_reason": res.reason,
                    "explanation": item["explanation"],
                }
            )
        else:
            res_s = parse_sql(item["sql"])
            results.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "parser": item["parser"],
                    "sql": item["sql"],
                    "is_supported": res_s.is_supported,
                    "rejection_reason": res_s.reason,
                    "explanation": item["explanation"],
                }
            )
    return results


def get_version_metadata() -> dict[str, Any]:
    commit = _detect_git_commit()
    return {
        "api_version": API_VERSION,
        "scenario_version": SCENARIO_VERSION,
        "app_commit": commit,
        "engine_commit": commit,
        "engine_version": getattr(smplcache, "__version__", "0.1.0"),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
    }


def get_scenarios_metadata() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "scenario_version": SCENARIO_VERSION,
        "default_scenario_id": "live_revenue",
        "allowed_dataset_sizes": list(ALLOWED_DATASET_SIZES),
        "allowed_aux_capacities": [None, 60, 120, 250, 500, 1500, 3000],
        "seed_bounds": {"min": 1, "max": 999999, "default": 42},
        "max_steps_bounds": {"min": 1, "max": 10},
        "scenarios": list(SCENARIO_CATALOG.values()),
        "rejected_sql_examples": get_rejected_sql_examples(),
    }


def _format_cents_usd(cents: int | None) -> str:
    if cents is None:
        return "$0.00"
    sign = "-" if cents < 0 else ""
    abs_cents = abs(int(cents))
    dollars, rem = divmod(abs_cents, 100)
    return f"{sign}${dollars:,}.{rem:02d}"


def _serialize_group_map(raw_values: Mapping[Any, Any]) -> dict[str, int]:
    """
    Convert AggregateCache.values or SQLite grouped query rows into a canonical
    sorted `{group_label: int_cents}` dictionary with exact 64-bit integer cents.
    """
    out: dict[str, int] = {}
    for k, v in raw_values.items():
        if v is None:
            continue
        label = "Total" if k == () else str(k)
        if isinstance(v, Decimal):
            out[label] = int(v)
        else:
            out[label] = int(v)
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def _generate_seeded_tables(
    seed: int, dataset_size: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Deterministically generate `regions`, `customers`, and `orders` rows for a
    given `(seed, dataset_size)` pair.

    Orders #101..#107 and Customers #1..#4 are anchored with deterministic
    human-readable records so the 60-second walkthrough steps always tell a clear,
    consistent story while the remaining `dataset_size - 7` orders scale the
    background workload deterministically.
    """
    rng = random.Random(seed)
    regions = [dict(r) for r in REGIONS_TABLE_SEED]

    num_customers = max(24, min(400, dataset_size // 4))
    customers: list[dict[str, Any]] = [
        {"id": 1, "name": "Acme Industrial", "region_id": 1, "tier": "enterprise"},
        {"id": 2, "name": "Nordic Freight", "region_id": 2, "tier": "enterprise"},
        {"id": 3, "name": "Kansai Robotics", "region_id": 3, "tier": "growth"},
        {"id": 4, "name": "Andes Logistics", "region_id": 4, "tier": "growth"},
    ]
    tiers = ("enterprise", "growth", "standard")
    for cid in range(5, num_customers + 1):
        customers.append(
            {
                "id": cid,
                "name": f"Customer #{cid:03d}",
                "region_id": rng.choice((1, 1, 2, 2, 3, 4)),
                "tier": rng.choice(tiers),
            }
        )

    cust_region_code = {1: "us-east", 2: "eu-west", 3: "ap-south", 4: "latam"}
    customer_by_id = {c["id"]: c for c in customers}

    # Anchored orders #101..#107 used in the scripted walkthrough steps
    anchored_orders: list[dict[str, Any]] = [
        {
            "id": 101,
            "customer_id": 1,
            "region": "North America",
            "status": "paid",
            "amount_cents": 10000,  # $100.00 -> edited to $125.00 in Step 2
            "notes": "Leave at front desk",
        },
        {
            "id": 102,
            "customer_id": 2,
            "region": "Europe",
            "status": "pending",  # transitions pending -> paid ($80.00) in Step 3
            "amount_cents": 8000,
            "notes": "Awaiting wire confirmation",
        },
        {
            "id": 103,
            "customer_id": 1,
            "region": "North America",  # moves North America -> Europe ($150.00)
            "status": "paid",
            "amount_cents": 15000,
            "notes": "Priority pallet delivery",
        },
        {
            "id": 104,
            "customer_id": 3,
            "region": "Asia Pacific",
            "status": "paid",  # transitions paid -> refunded ($60.00)
            "amount_cents": 6000,
            "notes": "Standard sea container",
        },
        {
            "id": 105,
            "customer_id": 4,
            "region": "Pilot Sandbox",  # sole order in Pilot Sandbox group; deleted in Step 5
            "status": "paid",
            "amount_cents": 4500,
            "notes": "Single-order pilot cohort",
        },
        {
            "id": 106,
            "customer_id": 1,
            "region": "North America",
            "status": "paid",
            "amount_cents": 20000,  # $200.00 -> $240.00 (missing evidence step)
            "notes": "Annual renewal invoice",
        },
        {
            "id": 107,
            "customer_id": 2,
            "region": "Europe",
            "status": "paid",
            "amount_cents": 11000,  # $110.00 -> $140.00 (post-rebuild repair step)
            "notes": "Quarterly expansion order",
        },
    ]

    region_id_to_name = {r["id"]: r["name"] for r in regions}
    orders: list[dict[str, Any]] = list(anchored_orders)
    remaining = max(0, dataset_size - len(anchored_orders))
    statuses = ("paid", "paid", "paid", "pending", "refunded")
    note_templates = (
        "Standard dock delivery",
        "Signature required",
        "Call receiving manager",
        "Warehouse gate B",
        "Fragile instrumentation",
    )

    for idx in range(remaining):
        oid = 1000 + idx
        # Give Customer #1 a known multi-order fanout (4 paid orders total)
        if idx < 2:
            cid = 1
            status = "paid"
            amt_cents = 5000 + (idx * 2500)
        else:
            cid = rng.randint(2, num_customers)
            status = rng.choice(statuses)
            amt_cents = rng.randint(15, 250) * 100
        cust = customer_by_id[cid]
        r_name = region_id_to_name[cust["region_id"]]
        orders.append(
            {
                "id": oid,
                "customer_id": cid,
                "region": r_name,
                "status": status,
                "amount_cents": amt_cents,
                "notes": rng.choice(note_templates),
                "_region_code": cust_region_code[cust["region_id"]],
            }
        )

    # Strip internal helper key so table schema is exact
    for o in orders:
        o.pop("_region_code", None)

    return regions, customers, orders


class _IndependentSqliteVerifier:
    """
    Independent SQLite database owning the committed relational state (`regions`,
    `customers`, `orders`, plus attached `tenant_archive.orders`) and an
    instrumented SQL execution log.

    Never calls SmplCache helpers to compute query answers.
    """

    def __init__(
        self,
        regions: Sequence[Mapping[str, Any]],
        customers: Sequence[Mapping[str, Any]],
        orders: Sequence[Mapping[str, Any]],
    ) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.query_log: list[dict[str, Any]] = []
        self._init_schema_and_seed(regions, customers, orders)

    def close(self) -> None:
        self.conn.close()

    def _init_schema_and_seed(
        self,
        regions: Sequence[Mapping[str, Any]],
        customers: Sequence[Mapping[str, Any]],
        orders: Sequence[Mapping[str, Any]],
    ) -> None:
        cur = self.conn.cursor()
        cur.execute("ATTACH DATABASE ':memory:' AS tenant_archive")
        cur.execute("""
            CREATE TABLE regions (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT NOT NULL
            )
            """)
        cur.execute("""
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                region_id INTEGER NOT NULL,
                tier TEXT NOT NULL
            )
            """)
        cur.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                region TEXT NOT NULL,
                status TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                notes TEXT NOT NULL
            )
            """)
        cur.execute("""
            CREATE TABLE tenant_archive.orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                region TEXT NOT NULL,
                status TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                notes TEXT NOT NULL
            )
            """)
        cur.executemany(
            "INSERT INTO regions (id, code, name) VALUES (:id, :code, :name)",
            regions,
        )
        cur.executemany(
            "INSERT INTO customers (id, name, region_id, tier) "
            "VALUES (:id, :name, :region_id, :tier)",
            customers,
        )
        cur.executemany(
            "INSERT INTO orders (id, customer_id, region, status, amount_cents, notes) "
            "VALUES (:id, :customer_id, :region, :status, :amount_cents, :notes)",
            orders,
        )
        self.conn.commit()

    def apply_sql_mutation(
        self,
        statements: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        commit: bool = True,
    ) -> None:
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            for stmt, params in statements:
                cur.execute(stmt, dict(params))
            if commit:
                self.conn.commit()
            else:
                self.conn.rollback()
        except Exception:
            self.conn.rollback()
            raise

    def execute_grouped_query(
        self,
        sql: str,
        *,
        category: Literal[
            "initialization", "baseline", "smplcache_rebuild", "oracle_verification"
        ],
        boundary_index: int,
    ) -> dict[str, int]:
        self.query_log.append(
            {
                "boundary_index": boundary_index,
                "category": category,
                "sql": sql,
            }
        )
        cur = self.conn.cursor()
        rows = cur.execute(sql).fetchall()
        out: dict[str, int] = {}
        for row in rows:
            if len(row) == 1:
                if row[0] is not None:
                    out["Total"] = int(row[0])
            else:
                grp = "NULL" if row[0] is None else str(row[0])
                if row[1] is not None:
                    out[grp] = int(row[1])
        return dict(sorted(out.items(), key=lambda kv: kv[0]))

    def fetch_table_snapshot(
        self,
        table_name: str,
        *,
        category: Literal["initialization", "smplcache_rebuild"],
        boundary_index: int,
    ) -> list[dict[str, Any]]:
        if table_name not in ("orders", "customers", "regions"):
            raise ValueError(f"Unsupported snapshot table: {table_name}")
        sql = f"SELECT * FROM {table_name} ORDER BY id ASC"
        self.query_log.append(
            {
                "boundary_index": boundary_index,
                "category": category,
                "sql": sql,
            }
        )
        cur = self.conn.cursor()
        rows = cur.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def fetch_sample_orders(self, limit: int = 7) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        rows = cur.execute(
            """
            SELECT o.id, o.customer_id, c.name AS customer_name, o.region,
                   r.name AS joined_region_name, o.status, o.amount_cents, o.notes
            FROM orders o
            LEFT JOIN customers c ON o.customer_id = c.id
            LEFT JOIN regions r ON c.region_id = r.id
            WHERE o.id BETWEEN 101 AND 107
            ORDER BY o.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _serialize_certificate(decision: Decision, boundary_index: int) -> dict[str, Any]:
    cert = decision.certificate
    return {
        "shape": cert.shape,
        "event_id": cert.event_id,
        "relation": cert.relation,
        "decision_kind": decision.kind.value,
        "reason_code": decision.reason,
        "required_evidence": list(cert.required_evidence),
        "available_evidence": list(cert.available_evidence),
        "repair_program": cert.repair_program,
        "boundary_clock": f"b{boundary_index:02d}",
    }


def _build_live_revenue_steps(
    evidence_mode: Literal["standard", "remove_required_evidence"],
) -> list[dict[str, Any]]:
    """
    Build the 7 committed boundary steps for the `live_revenue` walkthrough.
    """
    return [
        {
            "step_number": 1,
            "boundary_id": "step_1_preserve_delivery_note",
            "title": "Change an order's delivery note",
            "action_label": "Update delivery note on Order #101",
            "narration": (
                "Order #101's delivery note changes from 'Leave at front desk' to "
                "'Ring bell on arrival'. Because notes is outside the query's "
                "dependency fingerprint {region, status, amount_cents}, SmplCache "
                "preserves the cached answer with 0 database queries while the "
                "table-invalidation baseline reruns the SQL query."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET notes = :notes WHERE id = :id",
                    {"id": 101, "notes": "Ring bell on arrival"},
                )
            ],
            "event": WriteEvent(
                relation="orders",
                event_id="evt_101_note",
                explicit_operation="UPDATE",
                old={
                    "id": 101,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    "amount_cents": 10000,
                    "notes": "Leave at front desk",
                },
                new={
                    "id": 101,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    "amount_cents": 10000,
                    "notes": "Ring bell on arrival",
                },
            ),
            "highlighted_order_id": 101,
            "delta_summary": "$0.00 (no revenue change)",
        },
        {
            "step_number": 2,
            "boundary_id": "step_2_repair_order_amount",
            "title": "Change a paid order from $100.00 to $125.00",
            "action_label": "Increase Order #101 amount ($100 -> $125)",
            "narration": (
                "Paid Order #101 increases from $100.00 (10,000 cents) to $125.00 "
                "(12,500 cents). Both old and new row images include region, status, "
                "and amount_cents, so SmplCache applies a +$25.00 delta repair in "
                "place without querying SQLite."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 101, "amount_cents": 12500},
                )
            ],
            "event": WriteEvent(
                relation="orders",
                event_id="evt_101_amount",
                explicit_operation="UPDATE",
                old={
                    "id": 101,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    "amount_cents": 10000,
                    "notes": "Ring bell on arrival",
                },
                new={
                    "id": 101,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    "amount_cents": 12500,
                    "notes": "Ring bell on arrival",
                },
            ),
            "highlighted_order_id": 101,
            "delta_summary": "+$25.00 in North America",
        },
        {
            "step_number": 3,
            "boundary_id": "step_3_repair_predicate_entry",
            "title": "Settle a pending order (Predicate entry)",
            "action_label": "Mark Order #102 as paid (+$80.00)",
            "narration": (
                "Order #102 ($80.00 in Europe) changes status from 'pending' to "
                "'paid', entering the WHERE status = 'paid' filter. SmplCache "
                "certifies predicate_entry and adds $80.00 to Europe in place."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET status = :status WHERE id = :id",
                    {"id": 102, "status": "paid"},
                )
            ],
            "event": WriteEvent(
                relation="orders",
                event_id="evt_102_paid",
                explicit_operation="UPDATE",
                old={
                    "id": 102,
                    "customer_id": 2,
                    "region": "Europe",
                    "status": "pending",
                    "amount_cents": 8000,
                    "notes": "Awaiting wire confirmation",
                },
                new={
                    "id": 102,
                    "customer_id": 2,
                    "region": "Europe",
                    "status": "paid",
                    "amount_cents": 8000,
                    "notes": "Awaiting wire confirmation",
                },
            ),
            "highlighted_order_id": 102,
            "delta_summary": "+$80.00 in Europe",
        },
        {
            "step_number": 4,
            "boundary_id": "step_4_repair_group_move",
            "title": "Reassign an order's region (Group move)",
            "action_label": "Move Order #103 from North America to Europe ($150.00)",
            "narration": (
                "Paid Order #103 ($150.00) is reassigned from North America to "
                "Europe. SmplCache subtracts $150.00 from North America and adds "
                "$150.00 to Europe in a single group_move repair."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET region = :region WHERE id = :id",
                    {"id": 103, "region": "Europe"},
                )
            ],
            "event": WriteEvent(
                relation="orders",
                event_id="evt_103_group_move",
                explicit_operation="UPDATE",
                old={
                    "id": 103,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    "amount_cents": 15000,
                    "notes": "Priority pallet delivery",
                },
                new={
                    "id": 103,
                    "customer_id": 1,
                    "region": "Europe",
                    "status": "paid",
                    "amount_cents": 15000,
                    "notes": "Priority pallet delivery",
                },
            ),
            "highlighted_order_id": 103,
            "delta_summary": "-$150.00 North America / +$150.00 Europe",
        },
        {
            "step_number": 5,
            "boundary_id": "step_5_delete_last_group_member",
            "title": "Delete the sole order in a pilot region (Group removal)",
            "action_label": "Delete Order #105 in Pilot Sandbox (-$45.00)",
            "narration": (
                "Order #105 ($45.00) is the only paid order in the 'Pilot Sandbox' "
                "group. Because AggregateCache tracks per-group row counts alongside "
                "sums, deleting Order #105 cleanly removes the 'Pilot Sandbox' group "
                "instead of leaving a stale zero or fabricated count."
            ),
            "table_written": "orders",
            "operation": "DELETE",
            "sqlite_commit": True,
            "sqlite_statements": [
                ("DELETE FROM orders WHERE id = :id", {"id": 105}),
            ],
            "event": WriteEvent(
                relation="orders",
                event_id="evt_105_delete",
                explicit_operation="DELETE",
                old={
                    "id": 105,
                    "customer_id": 4,
                    "region": "Pilot Sandbox",
                    "status": "paid",
                    "amount_cents": 4500,
                    "notes": "Single-order pilot cohort",
                },
                new={},
            ),
            "highlighted_order_id": 105,
            "delta_summary": "-$45.00 (Pilot Sandbox group removed)",
        },
        {
            "step_number": 6,
            "boundary_id": "step_6_missing_evidence_refusal",
            "title": "Remove required evidence (Fail-closed refusal & rebuild)",
            "action_label": "Update Order #106 without old amount_cents",
            "narration": (
                "Order #106 increases from $200.00 to $240.00 in SQLite, but the CDC "
                "event omits old.amount_cents. SmplCache refuses to guess the delta, "
                "emits invalidate (missing_evidence_for_repair), withholds the stale "
                "cache, and rebuilds from the committed SQLite snapshot."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 106, "amount_cents": 24000},
                )
            ],
            "event": WriteEvent(
                relation="orders",
                event_id="evt_106_incomplete",
                explicit_operation="UPDATE",
                changed_cols={"amount_cents"},
                old={
                    "id": 106,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    # amount_cents intentionally omitted from before-image!
                },
                new={
                    "id": 106,
                    "customer_id": 1,
                    "region": "North America",
                    "status": "paid",
                    "amount_cents": 24000,
                    "notes": "Annual renewal invoice",
                },
            ),
            "highlighted_order_id": 106,
            "delta_summary": "+$40.00 (verified via snapshot rebuild)",
        },
        {
            "step_number": 7,
            "boundary_id": "step_7_post_rebuild_repair",
            "title": "Subsequent complete write after rebuild (Recovery verified)",
            "action_label": "Update Order #107 ($110 -> $140) after recovery",
            "narration": (
                "Following the snapshot rebuild at Step 6, Order #107 in Europe "
                "increases from $110.00 to $140.00 with complete before/after "
                "evidence. SmplCache resumes in-place delta repair (+$30.00) on "
                "the freshly rebuilt state."
            ),
            "table_written": "orders",
            "operation": (
                "UPDATE" if evidence_mode == "standard" else "UPDATE (missing old)"
            ),
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 107, "amount_cents": 14000},
                )
            ],
            "event": (
                WriteEvent(
                    relation="orders",
                    event_id="evt_107_post_rebuild",
                    explicit_operation="UPDATE",
                    old={
                        "id": 107,
                        "customer_id": 2,
                        "region": "Europe",
                        "status": "paid",
                        "amount_cents": 11000,
                        "notes": "Quarterly expansion order",
                    },
                    new={
                        "id": 107,
                        "customer_id": 2,
                        "region": "Europe",
                        "status": "paid",
                        "amount_cents": 14000,
                        "notes": "Quarterly expansion order",
                    },
                )
                if evidence_mode == "standard"
                else WriteEvent(
                    relation="orders",
                    event_id="evt_107_incomplete_mode",
                    explicit_operation="UPDATE",
                    changed_cols={"amount_cents"},
                    old={
                        "id": 107,
                        "customer_id": 2,
                        "region": "Europe",
                        "status": "paid",
                    },
                    new={
                        "id": 107,
                        "customer_id": 2,
                        "region": "Europe",
                        "status": "paid",
                        "amount_cents": 14000,
                    },
                )
            ),
            "highlighted_order_id": 107,
            "delta_summary": "+$30.00 in Europe",
        },
    ]


def _build_customers_and_regions_steps() -> list[dict[str, Any]]:
    """
    Build the 7 committed boundary steps for the `customers_and_regions` 3-table
    join scenario (`orders -> customers -> regions`).
    """
    return [
        {
            "step_number": 1,
            "boundary_id": "join_step_1_preserve_note",
            "title": "Change fact-table delivery note (Preserve across join)",
            "action_label": "Update Order #101 delivery note",
            "narration": (
                "Order #101's notes column changes. Even in a 3-table join "
                "(orders -> customers -> regions), notes is disjoint from the join "
                "shape's table fingerprint, so SmplCache certifies preserve."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET notes = :notes WHERE id = :id",
                    {"id": 101, "notes": "Deliver to loading bay 4"},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"table": "orders"},
                    "before": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 10000,
                        "notes": "Leave at front desk",
                    },
                    "after": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 10000,
                        "notes": "Deliver to loading bay 4",
                    },
                }
            ],
            "highlighted_order_id": 101,
            "delta_summary": "$0.00 (disjoint fact column)",
        },
        {
            "step_number": 2,
            "boundary_id": "join_step_2_fact_amount",
            "title": "Update paid order amount ($100.00 -> $125.00)",
            "action_label": "Increase Order #101 amount (+$25.00)",
            "narration": (
                "Order #101 increases by $25.00. JoinAuxState resolves Customer #1 "
                "-> Region #1 (North America) in memory and repairs North America's "
                "joined total without querying SQLite."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 101, "amount_cents": 12500},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"table": "orders"},
                    "before": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 10000,
                        "notes": "Deliver to loading bay 4",
                    },
                    "after": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 12500,
                        "notes": "Deliver to loading bay 4",
                    },
                }
            ],
            "highlighted_order_id": 101,
            "delta_summary": "+$25.00 in North America",
        },
        {
            "step_number": 3,
            "boundary_id": "join_step_3_move_customer_region",
            "title": "Move Customer #1 (Acme Industrial) from North America to Europe",
            "action_label": "Move Customer #1 region (1-to-k fanout repair)",
            "narration": (
                "Dimension table update: Customer #1 moves from Region 1 (North America) "
                "to Region 2 (Europe). JoinAuxState traverses the foreign-key index to "
                "locate all paid orders belonging to Customer #1 and transfers their "
                "combined revenue from North America to Europe in place."
            ),
            "table_written": "customers",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE customers SET region_id = :region_id WHERE id = :id",
                    {"id": 1, "region_id": 2},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"table": "customers"},
                    "before": {
                        "id": 1,
                        "name": "Acme Industrial",
                        "region_id": 1,
                        "tier": "enterprise",
                    },
                    "after": {
                        "id": 1,
                        "name": "Acme Industrial",
                        "region_id": 2,
                        "tier": "enterprise",
                    },
                }
            ],
            "highlighted_order_id": 101,
            "delta_summary": "Customer #1 fanout transferred to Europe",
        },
        {
            "step_number": 4,
            "boundary_id": "join_step_4_fk_reparent",
            "title": "Reparent Order #103 from Customer #1 to Customer #3 (Asia Pacific)",
            "action_label": "Reparent Order #103 foreign key to Customer #3",
            "narration": (
                "Order #103 ($150.00) changes customer_id from 1 (Europe) to 3 "
                "(Asia Pacific). JoinAuxState updates its foreign-key multiplicity "
                "index and moves $150.00 from Europe to Asia Pacific."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET customer_id = :customer_id WHERE id = :id",
                    {"id": 103, "customer_id": 3},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"table": "orders"},
                    "before": {
                        "id": 103,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 15000,
                        "notes": "Priority pallet delivery",
                    },
                    "after": {
                        "id": 103,
                        "customer_id": 3,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 15000,
                        "notes": "Priority pallet delivery",
                    },
                }
            ],
            "highlighted_order_id": 103,
            "delta_summary": "-$150.00 Europe / +$150.00 Asia Pacific",
        },
        {
            "step_number": 5,
            "boundary_id": "join_step_5_multi_table_tx_commit",
            "title": "Multi-table transaction COMMIT (New customer + paid order)",
            "action_label": "Commit atomic customer + order transaction (+$95.00)",
            "narration": (
                "A single BEGIN ... COMMIT transaction inserts Customer #9001 in "
                "Latin America and paid Order #9001 ($95.00). WalStreamProcessor "
                "buffers the transaction until COMMIT and repairs Latin America."
            ),
            "table_written": "customers,orders",
            "operation": "BEGIN..COMMIT",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "INSERT INTO customers (id, name, region_id, tier) "
                    "VALUES (:id, :name, :region_id, :tier)",
                    {
                        "id": 9001,
                        "name": "Sao Paulo Solar",
                        "region_id": 4,
                        "tier": "enterprise",
                    },
                ),
                (
                    "INSERT INTO orders (id, customer_id, region, status, amount_cents, notes) "
                    "VALUES (:id, :customer_id, :region, :status, :amount_cents, :notes)",
                    {
                        "id": 9001,
                        "customer_id": 9001,
                        "region": "Latin America",
                        "status": "paid",
                        "amount_cents": 9500,
                        "notes": "Atomic multi-table onboarding",
                    },
                ),
            ],
            "wal_records": [
                {"status": "BEGIN", "id": "tx_501"},
                {
                    "op": "c",
                    "source": {"table": "customers", "txId": "tx_501"},
                    "after": {
                        "id": 9001,
                        "name": "Sao Paulo Solar",
                        "region_id": 4,
                        "tier": "enterprise",
                    },
                },
                {
                    "op": "c",
                    "source": {"table": "orders", "txId": "tx_501"},
                    "after": {
                        "id": 9001,
                        "customer_id": 9001,
                        "region": "Latin America",
                        "status": "paid",
                        "amount_cents": 9500,
                        "notes": "Atomic multi-table onboarding",
                    },
                },
                {"status": "COMMIT", "id": "tx_501", "event_count": 2},
            ],
            "highlighted_order_id": 104,
            "delta_summary": "+$95.00 in Latin America (atomic commit)",
        },
        {
            "step_number": 6,
            "boundary_id": "join_step_6_tx_rollback",
            "title": "Rolled-back transaction (BEGIN ... ROLLBACK)",
            "action_label": "Abort multi-table write transaction",
            "narration": (
                "An aborted transaction attempts to delete Order #101 and update "
                "Customer #2, then issues ROLLBACK. WalStreamProcessor discards "
                "the buffered records and certifies preserve (transaction_rollback)."
            ),
            "table_written": "none (rolled back)",
            "operation": "ROLLBACK",
            "sqlite_commit": False,
            "sqlite_statements": [
                ("DELETE FROM orders WHERE id = :id", {"id": 101}),
            ],
            "wal_records": [
                {"status": "BEGIN", "id": "tx_502_abort"},
                {
                    "op": "d",
                    "source": {"table": "orders", "txId": "tx_502_abort"},
                    "before": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 12500,
                        "notes": "Deliver to loading bay 4",
                    },
                },
                {"status": "ROLLBACK", "id": "tx_502_abort"},
            ],
            "highlighted_order_id": 101,
            "delta_summary": "$0.00 (transaction rolled back)",
        },
        {
            "step_number": 7,
            "boundary_id": "join_step_7_missing_or_evicted",
            "title": "Incomplete join evidence (3-table snapshot rebuild cost)",
            "action_label": "Update Order #106 without old amount_cents",
            "narration": (
                "Order #106 increases from $200.00 to $240.00 without old.amount_cents. "
                "SmplCache invalidates and rebuildsJoinAuxState + AggregateCache from "
                "all 3 participating tables (orders, customers, regions = 3 source "
                "queries vs. 1 joined baseline query), disclosing exact multi-table "
                "rebuild overhead."
            ),
            "table_written": "orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 106, "amount_cents": 24000},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"table": "orders"},
                    "before": {
                        "id": 106,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                    },
                    "after": {
                        "id": 106,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 24000,
                        "notes": "Annual renewal invoice",
                    },
                }
            ],
            "highlighted_order_id": 106,
            "delta_summary": "+$40.00 in Europe (rebuilt across 3 tables)",
        },
    ]


def _build_missing_evidence_steps() -> list[dict[str, Any]]:
    """
    Build the 6 committed boundary steps for the `missing_evidence` WAL recovery
    scenario.
    """
    return [
        {
            "step_number": 1,
            "boundary_id": "wal_step_1_complete_repair",
            "title": "Complete Debezium update (Full before/after row image)",
            "action_label": "Apply complete WAL update on Order #101 (+$25.00)",
            "narration": (
                "With PostgreSQL REPLICA IDENTITY FULL, the Debezium update carries "
                "complete before and after images. SmplCache repairs North America "
                "by +$25.00 with 0 database queries."
            ),
            "table_written": "main.orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE main.orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 101, "amount_cents": 12500},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"schema": "main", "table": "orders", "lsn": "0/16B3748"},
                    "before": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 10000,
                        "notes": "Leave at front desk",
                    },
                    "after": {
                        "id": 101,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 12500,
                        "notes": "Leave at front desk",
                    },
                }
            ],
            "highlighted_order_id": 101,
            "delta_summary": "+$25.00 in North America",
        },
        {
            "step_number": 2,
            "boundary_id": "wal_step_2_missing_before_amount",
            "title": "Incomplete WAL before-image (Missing old.amount_cents)",
            "action_label": "Omit before.amount_cents on Order #106 (+$40.00)",
            "narration": (
                "Order #106 increases from $200.00 to $240.00, but the WAL record "
                "only includes primary key id=106 in `before`. SmplCache emits "
                "invalidate (missing_evidence_for_repair), withholds the stale cache, "
                "and triggers a snapshot rebuild at this committed boundary."
            ),
            "table_written": "main.orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE main.orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 106, "amount_cents": 24000},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"schema": "main", "table": "orders", "lsn": "0/16B3810"},
                    "before": {"id": 106},
                    "after": {
                        "id": 106,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 24000,
                        "notes": "Annual renewal invoice",
                    },
                }
            ],
            "highlighted_order_id": 106,
            "delta_summary": "+$40.00 (rebuilt from committed snapshot)",
        },
        {
            "step_number": 3,
            "boundary_id": "wal_step_3_toast_placeholder",
            "title": "Unavailable TOASTed column placeholder in WAL",
            "action_label": "Send __debezium_unavailable_value on region",
            "narration": (
                "Order #103's region column arrives as `__debezium_unavailable_value` "
                "(an unchanged TOASTed value placeholder). Because `region` is a "
                "required GROUP BY key, SmplCache refuses repair and rebuilds "
                "from the SQLite snapshot."
            ),
            "table_written": "main.orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE main.orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 103, "amount_cents": 16500},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"schema": "main", "table": "orders", "lsn": "0/16B3920"},
                    "before": {
                        "id": 103,
                        "customer_id": 1,
                        "region": DEBEZIUM_UNAVAILABLE_VALUE,
                        "status": "paid",
                        "amount_cents": 15000,
                        "notes": "Priority pallet delivery",
                    },
                    "after": {
                        "id": 103,
                        "customer_id": 1,
                        "region": DEBEZIUM_UNAVAILABLE_VALUE,
                        "status": "paid",
                        "amount_cents": 16500,
                        "notes": "Priority pallet delivery",
                    },
                }
            ],
            "highlighted_order_id": 103,
            "delta_summary": "+$15.00 (rebuilt after TOAST placeholder refusal)",
        },
        {
            "step_number": 4,
            "boundary_id": "wal_step_4_cross_schema_isolation",
            "title": "Write to same-named table in another schema (tenant_archive.orders)",
            "action_label": "Insert into tenant_archive.orders",
            "narration": (
                "An insert targets `tenant_archive.orders` ($500.00). Because "
                "SmplCache preserves schema qualifiers during normalization, the "
                "write is certified unrelated_relation (Preserve) and cannot corrupt "
                "`main.orders`."
            ),
            "table_written": "tenant_archive.orders",
            "operation": "INSERT",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "INSERT INTO tenant_archive.orders "
                    "(id, customer_id, region, status, amount_cents, notes) "
                    "VALUES (:id, :customer_id, :region, :status, :amount_cents, :notes)",
                    {
                        "id": 8001,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 50000,
                        "notes": "Archived historical order",
                    },
                )
            ],
            "wal_records": [
                {
                    "op": "c",
                    "source": {
                        "schema": "tenant_archive",
                        "table": "orders",
                        "lsn": "0/16B4010",
                    },
                    "after": {
                        "id": 8001,
                        "customer_id": 1,
                        "region": "North America",
                        "status": "paid",
                        "amount_cents": 50000,
                        "notes": "Archived historical order",
                    },
                }
            ],
            "highlighted_order_id": 101,
            "delta_summary": "$0.00 (cross-schema write isolated)",
        },
        {
            "step_number": 5,
            "boundary_id": "wal_step_5_tx_rollback",
            "title": "Rolled-back WAL transaction (BEGIN ... ROLLBACK)",
            "action_label": "Rollback uncommitted update on Order #104",
            "narration": (
                "A WAL transaction buffers an update to Order #104 and then emits "
                "ROLLBACK. WalStreamProcessor discards the uncommitted buffer and "
                "certifies preserve (transaction_rollback)."
            ),
            "table_written": "none (rolled back)",
            "operation": "ROLLBACK",
            "sqlite_commit": False,
            "sqlite_statements": [
                (
                    "UPDATE main.orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 104, "amount_cents": 99900},
                )
            ],
            "wal_records": [
                {"status": "BEGIN", "id": "tx_901"},
                {
                    "op": "u",
                    "source": {"schema": "main", "table": "orders", "txId": "tx_901"},
                    "before": {
                        "id": 104,
                        "customer_id": 3,
                        "region": "Asia Pacific",
                        "status": "paid",
                        "amount_cents": 6000,
                        "notes": "Standard sea container",
                    },
                    "after": {
                        "id": 104,
                        "customer_id": 3,
                        "region": "Asia Pacific",
                        "status": "paid",
                        "amount_cents": 99900,
                        "notes": "Standard sea container",
                    },
                },
                {"status": "ROLLBACK", "id": "tx_901"},
            ],
            "highlighted_order_id": 104,
            "delta_summary": "$0.00 (rolled-back WAL transaction)",
        },
        {
            "step_number": 6,
            "boundary_id": "wal_step_6_post_rebuild_repair",
            "title": "Complete WAL update after rebuild (Clean delta repair)",
            "action_label": "Update Order #107 ($110 -> $140) with full evidence",
            "narration": (
                "Order #107 in Europe increases from $110.00 to $140.00 with full "
                "before/after WAL images. Because the earlier invalidation was "
                "rebuilt at its committed boundary, SmplCache repairs Europe by "
                "+$30.00 in place."
            ),
            "table_written": "main.orders",
            "operation": "UPDATE",
            "sqlite_commit": True,
            "sqlite_statements": [
                (
                    "UPDATE main.orders SET amount_cents = :amount_cents WHERE id = :id",
                    {"id": 107, "amount_cents": 14000},
                )
            ],
            "wal_records": [
                {
                    "op": "u",
                    "source": {"schema": "main", "table": "orders", "lsn": "0/16B4200"},
                    "before": {
                        "id": 107,
                        "customer_id": 2,
                        "region": "Europe",
                        "status": "paid",
                        "amount_cents": 11000,
                        "notes": "Quarterly expansion order",
                    },
                    "after": {
                        "id": 107,
                        "customer_id": 2,
                        "region": "Europe",
                        "status": "paid",
                        "amount_cents": 14000,
                        "notes": "Quarterly expansion order",
                    },
                }
            ],
            "highlighted_order_id": 107,
            "delta_summary": "+$30.00 in Europe",
        },
    ]


def execute_scenario_run(config: ScenarioConfig) -> dict[str, Any]:
    """
    Execute a self-contained, deterministic scenario run against both the real
    `smplcache` engine and an independent native `sqlite3` database.
    """
    t0 = time.perf_counter()
    if config.scenario_id not in SCENARIO_CATALOG:
        raise ValueError(f"Unknown scenario_id: {config.scenario_id}")
    if config.dataset_size not in ALLOWED_DATASET_SIZES:
        raise ValueError(
            f"Unsupported dataset_size {config.dataset_size}; "
            f"allowed: {ALLOWED_DATASET_SIZES}"
        )
    if config.aux_max_total_rows not in ALLOWED_AUX_CAPACITIES:
        raise ValueError(
            f"Unsupported aux_max_total_rows {config.aux_max_total_rows}; "
            f"allowed: {ALLOWED_AUX_CAPACITIES}"
        )
    if not (1 <= int(config.seed) <= 999999):
        raise ValueError("seed must be between 1 and 999999")

    meta = SCENARIO_CATALOG[config.scenario_id]
    sql = str(meta["sql"])
    participating_tables = frozenset(meta["participating_tables"])
    is_join = config.scenario_id == "customers_and_regions"

    regions, customers, orders = _generate_seeded_tables(
        config.seed, config.dataset_size
    )
    verifier = _IndependentSqliteVerifier(regions, customers, orders)

    policy_time_ms = 0.0
    oracle_time_ms = 0.0

    try:
        # 1. Parse SQL shape using the real AST allowlist extractor
        if is_join:
            parsed_join = parse_join_sql(sql, name="revenue_by_region_join")
            if not parsed_join.is_supported or parsed_join.shape is None:
                raise RuntimeError(f"Join SQL failed admission: {parsed_join.reason}")
            join_shape: MultiTableJoinShape = parsed_join.shape
            eff_aux_cap = (
                config.aux_max_total_rows
                if config.aux_max_total_rows is not None
                else meta["default_aux_max_total_rows"]
            )
            init_tables = {
                "orders": verifier.fetch_table_snapshot(
                    "orders", category="initialization", boundary_index=0
                ),
                "customers": verifier.fetch_table_snapshot(
                    "customers", category="initialization", boundary_index=0
                ),
                "regions": verifier.fetch_table_snapshot(
                    "regions", category="initialization", boundary_index=0
                ),
            }
            smpl_cache = rebuild_join_cache(join_shape, init_tables)
            aux_state: JoinAuxState | None = JoinAuxState.from_tables(
                join_shape,
                init_tables,
                max_total_rows=eff_aux_cap,
                eviction_policy="lru",
            )
            wal_proc = WalStreamProcessor(join_shape, aux_state=aux_state)
            shape_summary: dict[str, Any] = {
                "shape_name": join_shape.name,
                "kind": "multi_table_join",
                "relations": sorted(join_shape.relations()),
                "agg_fn": join_shape.agg_fn,
                "agg_col": (
                    f"{join_shape.agg_col[0]}.{join_shape.agg_col[1]}"
                    if join_shape.agg_col
                    else None
                ),
                "group_cols": [f"{q}.{c}" for q, c in join_shape.group_cols],
                "predicate_terms": [
                    f"{q}.{c} {op} {lit!r}"
                    for q, c, op, lit in join_shape.predicate_terms
                ],
                "table_fingerprints": {
                    rel: sorted(join_shape.table_fingerprint(rel))
                    for rel in sorted(join_shape.relations())
                },
                "required_evidence_by_table": {
                    rel: sorted(join_shape.table_required_evidence(rel))
                    for rel in sorted(join_shape.relations())
                },
            }
        else:
            parsed_single = parse_sql(sql, name="revenue_by_region_paid")
            if not parsed_single.is_supported or parsed_single.shape is None:
                raise RuntimeError(
                    f"Single-table SQL failed admission: {parsed_single.reason}"
                )
            single_shape: QueryShape = parsed_single.shape
            eff_aux_cap = None
            init_orders = verifier.fetch_table_snapshot(
                "orders", category="initialization", boundary_index=0
            )
            smpl_cache = AggregateCache.from_rows(single_shape, init_orders)
            aux_state = None
            wal_proc = WalStreamProcessor(single_shape)
            shape_summary = {
                "shape_name": single_shape.name,
                "kind": "single_table_aggregate",
                "relations": [single_shape.relation],
                "agg_fn": single_shape.agg_fn,
                "agg_col": single_shape.agg_col,
                "group_cols": list(single_shape.group_cols),
                "predicate_terms": [
                    f"{c} {op} {lit!r}" for c, op, lit in single_shape.predicate.terms
                ],
                "fingerprint": sorted(single_shape.fingerprint()),
                "required_evidence": sorted(single_shape.required_evidence()),
            }

        # Baseline initialization query
        baseline_cache = verifier.execute_grouped_query(
            sql, category="initialization", boundary_index=0
        )

        # Independent oracle verification at Boundary 0 (initial state)
        t_or0 = time.perf_counter()
        oracle_initial = verifier.execute_grouped_query(
            sql, category="oracle_verification", boundary_index=0
        )
        oracle_time_ms += (time.perf_counter() - t_or0) * 1000.0

        smpl_initial = _serialize_group_map(smpl_cache.values)
        initial_matches = (smpl_initial == oracle_initial) and (
            baseline_cache == oracle_initial
        )
        init_queries_count = sum(
            1 for q in verifier.query_log if q["category"] == "initialization"
        )

        initial_sample_orders = verifier.fetch_sample_orders(limit=7)
        t_init_done = time.perf_counter()
        seed_and_init_ms = (t_init_done - t0) * 1000.0 - oracle_time_ms

        # Build step list for the chosen scenario
        if config.scenario_id == "live_revenue":
            raw_steps = _build_live_revenue_steps(config.evidence_mode)
        elif config.scenario_id == "customers_and_regions":
            raw_steps = _build_customers_and_regions_steps()
        else:
            raw_steps = _build_missing_evidence_steps()

        if config.max_steps is not None:
            raw_steps = raw_steps[: max(1, min(len(raw_steps), int(config.max_steps)))]

        # Cumulative counters (post-initialization)
        baseline_recomputations = 0
        baseline_source_queries = 0
        smplcache_rebuilds = 0
        smplcache_source_queries = 0
        preserve_count = 0
        repair_count = 0
        invalidate_count = 0
        unsupported_count = 0
        oracle_checks = 1  # Boundary 0 check included
        oracle_queries = 1
        mismatch_count = 0 if initial_matches else 1
        first_failing_boundary: int | None = None if initial_matches else 0

        boundaries: list[dict[str, Any]] = []
        latched_invalid = False

        for idx, step in enumerate(raw_steps, start=1):
            b_base_recomp = 0
            b_base_queries = 0
            b_smpl_rebuilds = 0
            b_smpl_queries = 0

            # 1. Apply committed mutation to SQLite source database
            verifier.apply_sql_mutation(
                step["sqlite_statements"],
                commit=bool(step.get("sqlite_commit", True)),
            )

            # 2. Baseline policy: invalidates if a participating table was written in a committed tx
            t_pol0 = time.perf_counter()
            written_tables = {
                t.strip()
                for t in str(step["table_written"]).split(",")
                if t.strip() and t.strip() != "none (rolled back)"
            }
            baseline_dirtied = bool(
                step.get("sqlite_commit", True)
                and (written_tables & participating_tables)
            )
            if baseline_dirtied:
                baseline_cache = verifier.execute_grouped_query(
                    sql, category="baseline", boundary_index=idx
                )
                b_base_recomp += 1
                b_base_queries += 1
                baseline_recomputations += 1
                baseline_source_queries += 1

            # 3. SmplCache execution
            affected_fanout_count: int | None = None

            def _rebuild_at_current_boundary() -> (
                AggregateCache | tuple[AggregateCache, JoinAuxState]
            ):
                nonlocal b_smpl_rebuilds, b_smpl_queries
                nonlocal smplcache_rebuilds, smplcache_source_queries
                nonlocal aux_state, latched_invalid
                b_smpl_rebuilds += 1
                smplcache_rebuilds += 1
                if is_join:
                    snap_tables = {
                        "orders": verifier.fetch_table_snapshot(
                            "orders",
                            category="smplcache_rebuild",
                            boundary_index=idx,
                        ),
                        "customers": verifier.fetch_table_snapshot(
                            "customers",
                            category="smplcache_rebuild",
                            boundary_index=idx,
                        ),
                        "regions": verifier.fetch_table_snapshot(
                            "regions",
                            category="smplcache_rebuild",
                            boundary_index=idx,
                        ),
                    }
                    b_smpl_queries += 3
                    smplcache_source_queries += 3
                    new_cache = rebuild_join_cache(join_shape, snap_tables)
                    new_aux = JoinAuxState.from_tables(
                        join_shape,
                        snap_tables,
                        max_total_rows=eff_aux_cap,
                        eviction_policy="lru",
                        prior_state=aux_state,
                    )
                    aux_state = new_aux
                    latched_invalid = False
                    return new_cache, new_aux
                else:
                    snap_orders = verifier.fetch_table_snapshot(
                        "orders",
                        category="smplcache_rebuild",
                        boundary_index=idx,
                    )
                    b_smpl_queries += 1
                    smplcache_source_queries += 1
                    latched_invalid = False
                    return AggregateCache.from_rows(single_shape, snap_orders)

            if config.scenario_id == "live_revenue":
                evt: WriteEvent = step["event"]
                if latched_invalid:
                    cert = smplcache.Certificate(
                        shape=single_shape.name,
                        event_id=evt.event_id,
                        relation=evt.relation,
                        decision_kind=DecisionKind.INVALIDATE,
                        reason_code="cache_requires_rebuild",
                    )
                    primary_decision = Decision.invalidate(
                        "cache_requires_rebuild", cert
                    )
                else:
                    primary_decision = process_event(single_shape, smpl_cache, evt)
                emitted_decisions = [primary_decision]
                if primary_decision.kind in (
                    DecisionKind.INVALIDATE,
                    DecisionKind.UNSUPPORTED,
                ):
                    latched_invalid = True
                    if not config.defer_rebuild:
                        rebuilt_res = _rebuild_at_current_boundary()
                        assert isinstance(rebuilt_res, AggregateCache)
                        smpl_cache = rebuilt_res
            else:
                # Measure fanout for Customer #1 dimension update before/during WAL ingest
                if (
                    is_join
                    and aux_state is not None
                    and step["boundary_id"] == "join_step_3_move_customer_region"
                ):
                    fk_bucket = aux_state.fk_index.get(("orders", "customer_id"), {})
                    child_pks = fk_bucket.get(1, set())
                    orders_map = aux_state.table_rows.get("orders", {})
                    affected_fanout_count = sum(
                        1
                        for pk in child_pks
                        if orders_map.get(pk, {}).get("status") == "paid"
                    )

                emitted_decisions = []
                for wal_rec in step["wal_records"]:
                    ds, smpl_cache = wal_proc.ingest(
                        wal_rec,
                        smpl_cache,
                        on_invalidate=(
                            None
                            if config.defer_rebuild
                            else _rebuild_at_current_boundary
                        ),
                    )
                    emitted_decisions.extend(ds)
                if wal_proc.aux_state is not None:
                    aux_state = wal_proc.aux_state
                latched_invalid = wal_proc.is_invalidated
                primary_decision = emitted_decisions[-1]

            # Tally decision counts across all emitted decisions at this boundary
            for d in emitted_decisions:
                if d.kind == DecisionKind.PRESERVE:
                    preserve_count += 1
                elif d.kind == DecisionKind.REPAIR:
                    repair_count += 1
                elif d.kind == DecisionKind.INVALIDATE:
                    invalidate_count += 1
                elif d.kind == DecisionKind.UNSUPPORTED:
                    unsupported_count += 1

            # Optional test-only fault injection to verify mismatch halting
            if (
                config._fault_inject_corrupt_step is not None
                and idx == config._fault_inject_corrupt_step
            ):
                smpl_cache.values["North America"] = Decimal(-999999)

            policy_time_ms += (time.perf_counter() - t_pol0) * 1000.0

            # 4. Independent SQLite Oracle Verification at this committed boundary
            t_or = time.perf_counter()
            oracle_result = verifier.execute_grouped_query(
                sql, category="oracle_verification", boundary_index=idx
            )
            oracle_time_ms += (time.perf_counter() - t_or) * 1000.0
            oracle_checks += 1
            oracle_queries += 1

            was_invalidated = any(
                d.kind in (DecisionKind.INVALIDATE, DecisionKind.UNSUPPORTED)
                for d in emitted_decisions
            )

            if latched_invalid and config.defer_rebuild:
                # Rebuild was intentionally deferred: stale value is withheld!
                smpl_post_result: dict[str, int] | None = None
                boundary_matches = True
                freshness_state = "invalidated_withheld"
                ui_badge = "Invalidate / Recompute"
            else:
                smpl_post_result = _serialize_group_map(smpl_cache.values)
                boundary_matches = (smpl_post_result == oracle_result) and (
                    baseline_cache == oracle_result
                )
                if not boundary_matches:
                    mismatch_count += 1
                    if first_failing_boundary is None:
                        first_failing_boundary = idx
                    freshness_state = "mismatch"
                    ui_badge = "Mismatch"
                elif was_invalidated:
                    freshness_state = "rebuilt_verified"
                    ui_badge = (
                        "Unsupported"
                        if primary_decision.kind == DecisionKind.UNSUPPORTED
                        else "Invalidate / Recompute"
                    )
                elif primary_decision.kind == DecisionKind.REPAIR:
                    freshness_state = "repaired_verified"
                    ui_badge = "Repair"
                else:
                    freshness_state = "preserved_verified"
                    ui_badge = "Preserve"

            recomp_avoided = baseline_recomputations - smplcache_rebuilds
            recomp_avoided_pct = (
                round(100.0 * recomp_avoided / baseline_recomputations, 1)
                if baseline_recomputations > 0
                else None
            )
            src_queries_avoided = baseline_source_queries - smplcache_source_queries
            src_queries_avoided_pct = (
                round(100.0 * src_queries_avoided / baseline_source_queries, 1)
                if baseline_source_queries > 0
                else None
            )

            aux_telemetry: dict[str, Any] | None = None
            if is_join and aux_state is not None:
                evicted_total = sum(len(s) for s in aux_state.evicted_pks.values())
                aux_telemetry = {
                    "resident_aux_rows": aux_state.total_rows(),
                    "aux_max_total_rows": aux_state.max_total_rows,
                    "rows_by_table": {
                        rel: len(rows)
                        for rel, rows in sorted(aux_state.table_rows.items())
                    },
                    "evicted_aux_keys_count": evicted_total,
                    "eviction_policy": aux_state.eviction_policy,
                    "affected_fanout_count": affected_fanout_count,
                }

            # Extract before/after evidence for the inspector
            if "event" in step:
                ev_obj: WriteEvent = step["event"]
                before_img = dict(ev_obj.old) if ev_obj.old else None
                after_img = dict(ev_obj.new) if ev_obj.new else None
            else:
                last_wal = step["wal_records"][-1]
                before_img = last_wal.get("before")
                after_img = last_wal.get("after")

            plain_reason = PLAIN_LANGUAGE_REASONS.get(
                primary_decision.reason,
                f"{ui_badge} — decision reason: {primary_decision.reason}",
            )

            boundaries.append(
                {
                    "boundary_index": idx,
                    "step_number": step["step_number"],
                    "boundary_id": step["boundary_id"],
                    "title": step["title"],
                    "action_label": step["action_label"],
                    "narration": step["narration"],
                    "table_written": step["table_written"],
                    "operation": step["operation"],
                    "highlighted_order_id": step.get("highlighted_order_id"),
                    "delta_summary": step["delta_summary"],
                    "before_image": before_img,
                    "after_image": after_img,
                    "decision_kind": primary_decision.kind.value,
                    "ui_badge": ui_badge,
                    "reason_code": primary_decision.reason,
                    "plain_language_reason": plain_reason,
                    "certificate": _serialize_certificate(primary_decision, idx),
                    "all_certificates": [
                        _serialize_certificate(d, idx) for d in emitted_decisions
                    ],
                    "stale_value_withheld": bool(was_invalidated),
                    "smplcache_pre_rebuild_result": (
                        None if was_invalidated else smpl_post_result
                    ),
                    "freshness_state": freshness_state,
                    "baseline_result_cents": dict(baseline_cache),
                    "smplcache_result_cents": (
                        dict(smpl_post_result) if smpl_post_result is not None else None
                    ),
                    "oracle_result_cents": dict(oracle_result),
                    "total_revenue_cents": (
                        sum(smpl_post_result.values())
                        if smpl_post_result is not None
                        else sum(oracle_result.values())
                    ),
                    "total_revenue_formatted": _format_cents_usd(
                        sum(smpl_post_result.values())
                        if smpl_post_result is not None
                        else sum(oracle_result.values())
                    ),
                    "oracle_match": boundary_matches,
                    "boundary_counters": {
                        "baseline_recomputations": b_base_recomp,
                        "baseline_source_queries": b_base_queries,
                        "smplcache_rebuilds": b_smpl_rebuilds,
                        "smplcache_source_queries": b_smpl_queries,
                        "oracle_queries": 1,
                    },
                    "cumulative_counters": {
                        "baseline_recomputations": baseline_recomputations,
                        "baseline_source_queries": baseline_source_queries,
                        "smplcache_rebuilds": smplcache_rebuilds,
                        "smplcache_source_queries": smplcache_source_queries,
                        "full_result_recomputations_avoided": recomp_avoided,
                        "full_result_recomputations_avoided_pct": recomp_avoided_pct,
                        "source_queries_avoided": src_queries_avoided,
                        "source_queries_avoided_pct": src_queries_avoided_pct,
                        "preserve_count": preserve_count,
                        "repair_count": repair_count,
                        "invalidate_count": invalidate_count,
                        "unsupported_count": unsupported_count,
                        "oracle_checks": oracle_checks,
                        "oracle_queries": oracle_queries,
                        "mismatch_count": mismatch_count,
                    },
                    "aux_telemetry": aux_telemetry,
                    "sample_orders": verifier.fetch_sample_orders(limit=7),
                }
            )

            # Halt immediately if any oracle mismatch occurred!
            if not boundary_matches:
                break

        # Final summary counters
        final_recomp_avoided = baseline_recomputations - smplcache_rebuilds
        final_recomp_avoided_pct = (
            round(100.0 * final_recomp_avoided / baseline_recomputations, 1)
            if baseline_recomputations > 0
            else None
        )
        final_src_avoided = baseline_source_queries - smplcache_source_queries
        final_src_avoided_pct = (
            round(100.0 * final_src_avoided / baseline_source_queries, 1)
            if baseline_source_queries > 0
            else None
        )

        # Verify query log reconciliation against counters
        logged_baseline = sum(
            1 for q in verifier.query_log if q["category"] == "baseline"
        )
        logged_rebuild = sum(
            1 for q in verifier.query_log if q["category"] == "smplcache_rebuild"
        )
        logged_oracle = sum(
            1 for q in verifier.query_log if q["category"] == "oracle_verification"
        )
        assert logged_baseline == baseline_source_queries
        assert logged_rebuild == smplcache_source_queries
        assert logged_oracle == oracle_queries

        ver_meta = get_version_metadata()
        deterministic_payload: dict[str, Any] = {
            "api_version": API_VERSION,
            "scenario_id": config.scenario_id,
            "scenario_version": SCENARIO_VERSION,
            "title": meta["title"],
            "description": meta["description"],
            "seed": config.seed,
            "config": {
                "scenario_id": config.scenario_id,
                "seed": config.seed,
                "dataset_size": config.dataset_size,
                "aux_max_total_rows": eff_aux_cap,
                "max_steps": len(raw_steps),
                "evidence_mode": config.evidence_mode,
                "defer_rebuild": config.defer_rebuild,
            },
            "app_commit": ver_meta["app_commit"],
            "engine_commit": ver_meta["engine_commit"],
            "sql": sql,
            "shape": shape_summary,
            "initial_state": {
                "dataset_size_orders": len(orders),
                "dataset_size_customers": len(customers),
                "dataset_size_regions": len(regions),
                "initialization_queries": init_queries_count,
                "baseline_result_cents": _serialize_group_map(oracle_initial),
                "smplcache_result_cents": smpl_initial,
                "oracle_result_cents": oracle_initial,
                "total_revenue_cents": sum(oracle_initial.values()),
                "total_revenue_formatted": _format_cents_usd(
                    sum(oracle_initial.values())
                ),
                "oracle_match": initial_matches,
                "sample_orders": initial_sample_orders,
                "aux_telemetry": (
                    {
                        "resident_aux_rows": (
                            len(orders) + len(customers) + len(regions)
                            if eff_aux_cap is None
                            else min(
                                eff_aux_cap,
                                len(orders) + len(customers) + len(regions),
                            )
                        ),
                        "aux_max_total_rows": eff_aux_cap,
                    }
                    if is_join
                    else None
                ),
            },
            "boundaries": boundaries,
            "summary": {
                "status": "mismatch" if mismatch_count > 0 else "verified",
                "halted_on_mismatch": mismatch_count > 0,
                "first_failing_boundary": first_failing_boundary,
                "boundaries_executed": len(boundaries),
                "initialization_queries": init_queries_count,
                "baseline_recomputations": baseline_recomputations,
                "baseline_source_queries": baseline_source_queries,
                "smplcache_rebuilds": smplcache_rebuilds,
                "smplcache_source_queries": smplcache_source_queries,
                "full_result_recomputations_avoided": final_recomp_avoided,
                "full_result_recomputations_avoided_pct": final_recomp_avoided_pct,
                "source_queries_avoided": final_src_avoided,
                "source_queries_avoided_pct": final_src_avoided_pct,
                "preserve_count": preserve_count,
                "repair_count": repair_count,
                "invalidate_count": invalidate_count,
                "unsupported_count": unsupported_count,
                "oracle_checks": oracle_checks,
                "oracle_queries": oracle_queries,
                "mismatch_count": mismatch_count,
                "correctness_headline": (
                    f"0 mismatches in {oracle_checks} checked boundaries (scoped to this run)"
                    if mismatch_count == 0
                    else f"{mismatch_count} mismatch detected at boundary {first_failing_boundary} (run halted)"
                ),
            },
            "counter_definitions": {
                "initialization_queries": (
                    "Initial table/query reads executed at Boundary 0 to seed both "
                    "policies. Excluded from post-initialization policy comparisons."
                ),
                "baseline_recomputations": (
                    "Full SQL query executions performed by the table-invalidation "
                    "baseline after initialization (1 query on the next read after "
                    "any committed write to a participating table)."
                ),
                "smplcache_rebuilds": (
                    "Full result rebuilds performed by SmplCache after initialization "
                    "when a change event triggers invalidate or unsupported."
                ),
                "full_result_recomputations_avoided": (
                    "baseline_recomputations - smplcache_rebuilds."
                ),
                "smplcache_source_queries": (
                    "Source table snapshot queries executed by SmplCache during "
                    "rebuilds (1 query for single-table orders; 3 queries for the "
                    "3-table orders -> customers -> regions join)."
                ),
                "source_queries_avoided": (
                    "baseline_source_queries - smplcache_source_queries. Can be "
                    "negative when a multi-table join rebuild reads 3 source tables "
                    "while the baseline executes 1 joined SQL query."
                ),
                "oracle_queries": (
                    "Independent native SQLite queries executed by the verification "
                    "harness to check both policies at every committed boundary. "
                    "Excluded from both policies' query counts."
                ),
            },
            "claim_hygiene": {
                "what_it_supports": (
                    "Demonstrates in this synthetic SQLite setting that SmplCache "
                    "preserves or incrementally repairs admitted SPJA query results "
                    "when complete row evidence is present, and falls back to snapshot "
                    "recomputation when evidence is omitted or auxiliary rows are evicted."
                ),
                "what_it_does_not_support": (
                    "Does not establish arbitrary-SQL equivalence, database collation "
                    "or floating-point parity across external RDBMS engines, total RAM "
                    "byte bounds from row limits, or durable exactly-once live CDC delivery."
                ),
                "narrowest_defensible_claim": (
                    "For the admitted SQL shape and seeded committed event sequence in "
                    "this run, every preserved, repaired, and rebuilt state matched "
                    "independent native SQLite integer recomputation across all "
                    "checked boundaries."
                ),
                "next_test_needed": (
                    "Live PostgreSQL logical replication testing under concurrent "
                    "multi-connection write workloads and snapshot/LSN offset alignment."
                ),
            },
            "query_log": verifier.query_log,
        }

        canonical_json = json.dumps(
            deterministic_payload, sort_keys=True, separators=(",", ":")
        )
        canonical_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        total_ms = (time.perf_counter() - t0) * 1000.0
        return {
            **deterministic_payload,
            "canonical_hash": canonical_hash,
            "timings_ms": {
                "seed_and_init_ms": round(max(0.0, seed_and_init_ms), 3),
                "policy_execution_ms": round(policy_time_ms, 3),
                "oracle_verification_ms": round(oracle_time_ms, 3),
                "total_ms": round(total_ms, 3),
                "workload_note": (
                    f"Measured on server monotonic clock ({platform.python_implementation()} "
                    f"{platform.python_version()}, SQLite {sqlite3.sqlite_version}, "
                    f"{config.dataset_size} seeded orders). Oracle verification overhead "
                    "is separated above; synthetic demo timings are not a production "
                    "latency claim."
                ),
            },
        }
    finally:
        verifier.close()
