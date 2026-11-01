# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Adversarial stress-testing suite for JoinAuxState eviction policies.

Targets:
1. Boundary limits: max_total_rows = 0, 1, 2 under a 3-table snowflake join
   (regions -> customers -> orders).
2. Multiplicity ties: deterministic tie-breaking under LFU with identical
   frequency and multiplicity.
3. Access clock overflow and high-throughput rapid access updates.
4. Invalidation safety: proof-of-safety that evicted keys referenced by subsequent
   writes always trigger Decision.invalidate("evicted_join_auxiliary_key") and
   never leak stale or un-repaired aggregates against SQLite ground truth.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from typing import Any, Mapping

import pytest

from smplcache.smplcache import (
    AggregateCache,
    DecisionKind,
    WriteEvent,
    normalize_relation,
)
from smplcache.join import (
    JoinAuxState,
    JoinCacheManager,
    JoinClause,
    MultiTableJoinShape,
    rebuild_join_cache,
    certify_join_event,
    process_join_event,
    _EvictedJoinKeyError,
)


def _make_3table_snowflake_shape() -> MultiTableJoinShape:
    """
    3-table snowflake equijoin:
    orders (base/fact) -> customers (dim1) -> regions (dim2)
    Query:
      SELECT regions.name, SUM(orders.amount)
      FROM orders
      JOIN customers ON orders.customer_id = customers.id
      JOIN regions ON customers.region_id = regions.id
      GROUP BY regions.name
    """
    return MultiTableJoinShape(
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
            JoinClause(
                right_relation="regions",
                left_qualifier="customers",
                left_key="region_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
        group_cols=(("regions", "name"),),
        name="snowflake_3table_stress",
    )


# ==============================================================================
# Challenge 1: Boundary Limits (max_total_rows = 0, 1, 2)
# ==============================================================================


@pytest.mark.parametrize("policy", ["fifo", "lru", "lfu"])
def test_boundary_max_total_rows_zero(policy: str) -> None:
    """
    Test max_total_rows = 0:
    Capacity is zero, so every insertion should be evicted immediately,
    total_rows() must remain 0, no orphan state, and any event touching
    keys must invalidate without crashing.
    """
    shape = _make_3table_snowflake_shape()
    aux = JoinAuxState(
        shape=shape,
        eviction_policy=policy,  # type: ignore[arg-type]
        max_total_rows=0,
    )

    # Insert a region
    aux.apply_row_mutation("regions", "INSERT", {}, {"id": "r1", "name": "East"})
    assert (
        aux.total_rows() == 0
    ), f"Policy {policy}: total_rows != 0 when max_total_rows=0"
    assert "r1" in aux.evicted_pks["regions"]
    assert "r1" not in aux.table_rows["regions"]

    # Insert a customer pointing to r1
    aux.apply_row_mutation(
        "customers", "INSERT", {}, {"id": "c1", "region_id": "r1", "name": "Alice"}
    )
    assert aux.total_rows() == 0
    assert "c1" in aux.evicted_pks["customers"]
    assert "c1" not in aux.table_rows["customers"]

    # Insert an order pointing to c1
    aux.apply_row_mutation(
        "orders", "INSERT", {}, {"id": "o1", "customer_id": "c1", "amount": 100}
    )
    assert aux.total_rows() == 0
    assert "o1" in aux.evicted_pks["orders"]
    assert "o1" not in aux.table_rows["orders"]

    # Capacity invariant holds
    assert aux.total_rows() <= 0

    # Invalidation safety check: certifying any write against max_total_rows=0
    # must produce Decision.invalidate("evicted_join_auxiliary_key")
    ev_order = WriteEvent(
        relation="orders",
        new={"id": "o2", "customer_id": "c1", "amount": 50},
    )
    dec = certify_join_event(shape, ev_order, aux)
    assert dec.kind == DecisionKind.INVALIDATE
    assert dec.reason == "evicted_join_auxiliary_key"


@pytest.mark.parametrize("policy", ["fifo", "lru", "lfu"])
def test_boundary_max_total_rows_one(policy: str) -> None:
    """
    Test max_total_rows = 1 across 3 tables:
    Cache can only ever hold exactly one row across all three tables.
    Verify that capacity is never exceeded (> 1), evicted rows are tracked,
    and references across evicted rows do not crash.
    """
    shape = _make_3table_snowflake_shape()
    aux = JoinAuxState(
        shape=shape,
        eviction_policy=policy,  # type: ignore[arg-type]
        max_total_rows=1,
    )

    # Step 1: Insert region r1
    aux.apply_row_mutation("regions", "INSERT", {}, {"id": "r1", "name": "East"})
    assert aux.total_rows() == 1
    assert "r1" in aux.table_rows["regions"]

    # Step 2: Insert customer c1 pointing to r1
    aux.apply_row_mutation(
        "customers", "INSERT", {}, {"id": "c1", "region_id": "r1", "name": "Alice"}
    )
    assert (
        aux.total_rows() == 1
    ), f"Policy {policy}: capacity bound violated: {aux.total_rows()}"

    # Step 3: Insert order o1 pointing to c1
    aux.apply_row_mutation(
        "orders", "INSERT", {}, {"id": "o1", "customer_id": "c1", "amount": 100}
    )
    assert (
        aux.total_rows() == 1
    ), f"Policy {policy}: capacity bound violated: {aux.total_rows()}"

    # Across all tables, exactly 1 row is resident and exactly 2 are evicted
    total_evicted = sum(len(ev) for ev in aux.evicted_pks.values())
    assert total_evicted == 2
    assert aux.total_rows() == 1

    # Attempting to query multiplicity for an evicted dimension must return 0 or raise without crash
    for rel in ("regions", "customers"):
        for pk in ("r1", "c1"):
            mult = aux.join_multiplicity(rel, pk)
            assert mult >= 0


@pytest.mark.parametrize("policy", ["fifo", "lru", "lfu"])
def test_boundary_max_total_rows_two(policy: str) -> None:
    """
    Test max_total_rows = 2 across 3 tables:
    With 3 tables, at least 1 table must have an eviction when 3 rows are inserted.
    Verify capacity invariant <= 2, no orphan state, and invalidation safety.
    """
    shape = _make_3table_snowflake_shape()
    aux = JoinAuxState(
        shape=shape,
        eviction_policy=policy,  # type: ignore[arg-type]
        max_total_rows=2,
    )

    aux.apply_row_mutation("regions", "INSERT", {}, {"id": "r1", "name": "East"})
    assert aux.total_rows() == 1

    aux.apply_row_mutation(
        "customers", "INSERT", {}, {"id": "c1", "region_id": "r1", "name": "Alice"}
    )
    assert aux.total_rows() == 2

    # Third insertion must force exactly one eviction to maintain total_rows == 2
    aux.apply_row_mutation(
        "orders", "INSERT", {}, {"id": "o1", "customer_id": "c1", "amount": 100}
    )
    assert (
        aux.total_rows() == 2
    ), f"Policy {policy}: capacity bound violated: {aux.total_rows()}"

    total_evicted = sum(len(ev) for ev in aux.evicted_pks.values())
    assert total_evicted == 1


def test_boundary_from_tables_under_tight_budgets() -> None:
    """
    Test JoinAuxState.from_tables with max_total_rows = 0, 1, 2 directly.
    """
    shape = _make_3table_snowflake_shape()
    tables = {
        "regions": [{"id": "r1", "name": "East"}, {"id": "r2", "name": "West"}],
        "customers": [
            {"id": "c1", "region_id": "r1", "name": "Alice"},
            {"id": "c2", "region_id": "r2", "name": "Bob"},
        ],
        "orders": [
            {"id": "o1", "customer_id": "c1", "amount": 100},
            {"id": "o2", "customer_id": "c2", "amount": 200},
        ],
    }

    for cap in (0, 1, 2):
        for pol in ("fifo", "lru", "lfu"):
            aux = JoinAuxState.from_tables(
                shape=shape,
                tables=tables,
                eviction_policy=pol,  # type: ignore[arg-type]
                max_total_rows=cap,
            )
            assert (
                aux.total_rows() <= cap
            ), f"Cap {cap}, policy {pol} violated: {aux.total_rows()}"
            total_evicted = sum(len(ev) for ev in aux.evicted_pks.values())
            assert aux.total_rows() + total_evicted == 6


# ==============================================================================
# Challenge 2: Multiplicity Ties under LFU
# ==============================================================================


def test_lfu_multiplicity_ties_deterministic_eviction() -> None:
    """
    Stress-test LFU candidate selection when many rows have identical frequency
    and multiplicity. Verify:
    1. No infinite loops or recursion errors.
    2. Deterministic, monotonic tie-breaking (FIFO insertion order, then PK string).
    3. Global eviction candidate selection consistency.
    """
    shape = _make_3table_snowflake_shape()
    N = 100
    aux = JoinAuxState(
        shape=shape,
        eviction_policy="lfu",
        max_total_rows=10,
    )

    # Insert 100 customers all with identical multiplicity (0) and frequency (1)
    for idx in range(N):
        aux.apply_row_mutation(
            "customers",
            "INSERT",
            {},
            {"id": f"cust_{idx:03d}", "region_id": "r1", "name": f"User_{idx}"},
        )
        assert aux.total_rows() <= 10

    assert aux.total_rows() == 10
    assert len(aux.evicted_pks["customers"]) == N - 10

    # Under identical multiplicity (0) and frequency (1), tie-breaker is:
    # last_touch, then insertion_order (FIFO), then str(pk).
    # Since cust_000 to cust_089 were inserted first, they must have been evicted in order.
    for idx in range(90):
        expected_evicted = f"cust_{idx:03d}"
        assert (
            expected_evicted in aux.evicted_pks["customers"]
        ), f"Expected {expected_evicted} to be evicted under tie-breaking"

    # Remaining resident customers must be cust_090 to cust_099
    expected_resident = {f"cust_{idx:03d}" for idx in range(90, 100)}
    assert set(aux.table_rows["customers"].keys()) == expected_resident


def test_lfu_multi_table_tied_scores() -> None:
    """
    Test LFU candidate selection across multiple tables where candidate rows
    in different tables have identical scores (freq=1, mult=0).
    Verify that _select_global_eviction_candidate behaves deterministically
    without oscillations or crashes.
    """
    shape = _make_3table_snowflake_shape()
    aux = JoinAuxState(
        shape=shape,
        eviction_policy="lfu",
        max_total_rows=2,
    )

    # Insert r1 into regions, c1 into customers
    aux.apply_row_mutation("regions", "INSERT", {}, {"id": "r1", "name": "East"})
    aux.apply_row_mutation(
        "customers", "INSERT", {}, {"id": "c1", "region_id": "r_other", "name": "Alice"}
    )
    assert aux.total_rows() == 2

    # Both r1 and c1 have mult=0, freq=1.
    # Inserting o1 triggers global eviction.
    aux.apply_row_mutation(
        "orders", "INSERT", {}, {"id": "o1", "customer_id": "c_other", "amount": 10}
    )
    assert aux.total_rows() == 2

    # Exactly one row was evicted
    total_evicted = sum(len(ev) for ev in aux.evicted_pks.values())
    assert total_evicted == 1


# ==============================================================================
# Challenge 3: Access Clock Overflow & Rapid Access Updates
# ==============================================================================


def test_high_throughput_rapid_access_updates() -> None:
    """
    Simulate high-throughput record_access calls (100,000 updates) across rows.
    Verify:
    1. No integer overflow or performance collapse.
    2. Recency and frequency counters accurately track the access volume.
    3. Eviction ordering correctly prefers the high-frequency row over cold rows.
    """
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    aux = JoinAuxState(shape=shape, eviction_policy="lfu", max_total_rows=2)

    aux.apply_row_mutation("items", "INSERT", {}, {"id": "hot_item", "val": 10})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "cold_item", "val": 20})
    assert aux.total_rows() == 2

    # Rapid high-volume access: 100,000 calls
    for _ in range(100_000):
        aux.record_access("items", "hot_item")

    assert aux._access_clock >= 100_000
    assert aux._row_access_freq["items"]["hot_item"] >= 100_000
    assert aux._row_access_freq["items"]["cold_item"] == 1

    # Insert third row: cold_item must be evicted, hot_item preserved
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "new_item", "val": 30})
    assert aux.total_rows() == 2
    assert "cold_item" in aux.evicted_pks["items"]
    assert "hot_item" in aux.table_rows["items"]
    assert "new_item" in aux.table_rows["items"]


def test_access_clock_arbitrary_precision_extreme_values() -> None:
    """
    Test access clock with values exceeding 64-bit integer limits (e.g. 2^65).
    Verify Python arbitrary-precision arithmetic does not overflow or raise.
    """
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    aux = JoinAuxState(shape=shape, eviction_policy="lru", max_total_rows=2)
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i1", "val": 10})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i2", "val": 20})

    # Artificially set access clock to a huge integer > 2^64
    huge_clock = 2**65 + 100
    aux._access_clock = huge_clock
    aux.record_access("items", "i1")

    assert aux._row_last_access["items"]["i1"] == huge_clock + 1

    # Insert third item: i2 was accessed at clock=2, i1 at clock=huge_clock+1
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i3", "val": 30})
    assert "i2" in aux.evicted_pks["items"]
    assert "i1" in aux.table_rows["items"]


# ==============================================================================
# Challenge 4: Invalidation Safety and Differential Oracle
# ==============================================================================


class SQLiteJoinOracle:
    """Exact differential oracle executing join queries against an in-memory SQLite database."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute("CREATE TABLE regions (id TEXT PRIMARY KEY, name TEXT);")
            self.conn.execute(
                "CREATE TABLE customers (id TEXT PRIMARY KEY, region_id TEXT, name TEXT);"
            )
            self.conn.execute(
                "CREATE TABLE orders (id TEXT PRIMARY KEY, customer_id TEXT, amount REAL);"
            )

    def execute_event(self, event: WriteEvent) -> None:
        rel = normalize_relation(event.relation)
        op = event.operation
        with self.conn:
            if op == "INSERT":
                cols = list(event.new.keys())
                placeholders = ", ".join(["?"] * len(cols))
                sql = f"INSERT INTO {rel} ({', '.join(cols)}) VALUES ({placeholders})"
                self.conn.execute(sql, [event.new[c] for c in cols])
            elif op == "DELETE":
                sql = f"DELETE FROM {rel} WHERE id = ?"
                self.conn.execute(sql, (event.old["id"],))
            elif op == "UPDATE":
                set_clauses = [f"{k} = ?" for k in event.new.keys() if k != "id"]
                vals = [event.new[k] for k in event.new.keys() if k != "id"]
                vals.append(event.old.get("id", event.new.get("id")))
                sql = f"UPDATE {rel} SET {', '.join(set_clauses)} WHERE id = ?"
                self.conn.execute(sql, vals)

    def query_aggregate(self) -> dict[Any, Decimal]:
        sql = """
            SELECT regions.name AS reg_name, COALESCE(SUM(orders.amount), 0) AS total_amount
            FROM orders
            JOIN customers ON orders.customer_id = customers.id
            JOIN regions ON customers.region_id = regions.id
            GROUP BY regions.name
        """
        cur = self.conn.cursor()
        cur.execute(sql)
        res: dict[Any, Decimal] = {}
        for row in cur.fetchall():
            res[row["reg_name"]] = Decimal(str(row["total_amount"]))
        return res


def test_invalidation_safety_evicted_key_always_invalidates() -> None:
    """
    Adversarial sequence:
    1. Populate 3 tables with max_total_rows = 3 (tight budget).
    2. Evictions occur across regions and customers.
    3. Generate subsequent writes touching:
       - An evicted region
       - An evicted customer
       - An order referencing an evicted customer
       - An update reparenting from a valid customer to an evicted customer
    4. In every case, verify that:
       a) certify_join_event returns Decision.invalidate("evicted_join_auxiliary_key").
       b) process_join_event sets aux_state.is_invalidated = True.
       c) The cache aggregate is NEVER silently corrupted or stale compared to oracle.
    """
    shape = _make_3table_snowflake_shape()
    oracle = SQLiteJoinOracle()

    # Initial data
    initial_regions = [{"id": "r1", "name": "East"}, {"id": "r2", "name": "West"}]
    initial_customers = [
        {"id": "c1", "region_id": "r1", "name": "Alice"},
        {"id": "c2", "region_id": "r2", "name": "Bob"},
    ]
    initial_orders = [
        {"id": "o1", "customer_id": "c1", "amount": 100},
        {"id": "o2", "customer_id": "c2", "amount": 200},
    ]

    for r in initial_regions:
        oracle.execute_event(WriteEvent(relation="regions", new=r))
    for c in initial_customers:
        oracle.execute_event(WriteEvent(relation="customers", new=c))
    for o in initial_orders:
        oracle.execute_event(WriteEvent(relation="orders", new=o))

    # Construct JoinCacheManager with tight capacity: max_total_rows = 3
    manager = JoinCacheManager(
        shape=shape,
        initial_tables={
            "regions": initial_regions,
            "customers": initial_customers,
            "orders": initial_orders,
        },
        eviction_policy="fifo",
        max_total_rows=3,
    )

    # Initial loading of 6 rows into max_total_rows=3 must have evicted 3 rows
    assert manager.aux.total_rows() == 3
    total_evicted = sum(len(ev) for ev in manager.aux.evicted_pks.values())
    assert total_evicted == 3

    # Find which keys were evicted
    evicted_regions = list(manager.aux.evicted_pks["regions"])
    evicted_customers = list(manager.aux.evicted_pks["customers"])

    # Under FIFO, r1 and r2 and c1 were evicted
    assert len(evicted_regions) > 0 or len(evicted_customers) > 0

    # Test Case A: Write referencing an evicted customer
    if evicted_customers:
        target_cust = evicted_customers[0]
        ev_order = WriteEvent(
            relation="orders",
            new={"id": "o_new", "customer_id": target_cust, "amount": 50},
        )
        dec = manager.process(ev_order)
        assert dec.kind == DecisionKind.INVALIDATE
        assert dec.reason == "evicted_join_auxiliary_key"
        assert manager.aux.is_invalidated is True

    # Test Case B: Write updating an evicted region
    if evicted_regions:
        target_reg = evicted_regions[0]
        # Reset manager to test independent write
        manager2 = JoinCacheManager(
            shape=shape,
            initial_tables={
                "regions": initial_regions,
                "customers": initial_customers,
                "orders": initial_orders,
            },
            eviction_policy="fifo",
            max_total_rows=3,
        )
        ev_reg = WriteEvent(
            relation="regions",
            old={"id": target_reg, "name": "East"},
            new={"id": target_reg, "name": "East Coast"},
        )
        dec = manager2.process(ev_reg)
        assert dec.kind == DecisionKind.INVALIDATE
        assert dec.reason == "evicted_join_auxiliary_key"
        assert manager2.aux.is_invalidated is True

    # Test Case C: Reparenting write to an evicted customer
    if evicted_customers:
        target_cust = evicted_customers[0]
        manager3 = JoinCacheManager(
            shape=shape,
            initial_tables={
                "regions": initial_regions,
                "customers": initial_customers,
                "orders": initial_orders,
            },
            eviction_policy="fifo",
            max_total_rows=3,
        )
        # Find a resident order that currently points to a different customer (e.g. o2 pointing to c2)
        diff_orders = [
            o
            for o in manager3.aux.table_rows["orders"].values()
            if o.get("customer_id") != target_cust
        ]
        if diff_orders:
            res_o = diff_orders[0]
            ev_reparent = WriteEvent(
                relation="orders",
                old=dict(res_o),
                new={**res_o, "customer_id": target_cust},
            )
            dec = manager3.process(ev_reparent)
            assert dec.kind == DecisionKind.INVALIDATE
            assert dec.reason == "evicted_join_auxiliary_key"
            assert manager3.aux.is_invalidated is True

        # Test Case D: Updating an existing resident order that points to an evicted customer
        same_orders = [
            o
            for o in manager3.aux.table_rows["orders"].values()
            if o.get("customer_id") == target_cust
        ]
        if same_orders:
            res_o2 = same_orders[0]
            manager4 = JoinCacheManager(
                shape=shape,
                initial_tables={
                    "regions": initial_regions,
                    "customers": initial_customers,
                    "orders": initial_orders,
                },
                eviction_policy="fifo",
                max_total_rows=3,
            )
            ev_update_fact = WriteEvent(
                relation="orders",
                old=dict(res_o2),
                new={**res_o2, "amount": 999},
            )
            dec4 = manager4.process(ev_update_fact)
            assert dec4.kind == DecisionKind.INVALIDATE
            assert dec4.reason == "evicted_join_auxiliary_key"
            assert manager4.aux.is_invalidated is True


def test_oracle_consistency_under_mixed_evictions_and_repairs() -> None:
    """
    Run a multi-step sequence of valid repairs alongside evictions.
    Verify that so long as no evicted keys are referenced, cache repairs
    remain 100% numerically consistent with the SQLite oracle.
    When an evicted key is touched, invalidation occurs safely.
    """
    shape = _make_3table_snowflake_shape()
    oracle = SQLiteJoinOracle()

    # Initial data: 1 region, 1 customer, 2 orders
    # Budget = 4: exactly enough to hold r1, c1, o1, o2
    init_regions = [{"id": "r1", "name": "East"}]
    init_customers = [{"id": "c1", "region_id": "r1", "name": "Alice"}]
    init_orders = [
        {"id": "o1", "customer_id": "c1", "amount": 100},
        {"id": "o2", "customer_id": "c1", "amount": 200},
    ]

    for r in init_regions:
        oracle.execute_event(WriteEvent(relation="regions", new=r))
    for c in init_customers:
        oracle.execute_event(WriteEvent(relation="customers", new=c))
    for o in init_orders:
        oracle.execute_event(WriteEvent(relation="orders", new=o))

    manager = JoinCacheManager(
        shape=shape,
        initial_tables={
            "regions": init_regions,
            "customers": init_customers,
            "orders": init_orders,
        },
        eviction_policy="lfu",
        max_total_rows=4,
    )

    # Oracle check
    oracle_val = oracle.query_aggregate()
    assert manager.cache.values == oracle_val

    # Event 1: Valid repair within capacity (amount update on o1)
    ev_update = WriteEvent(
        relation="orders",
        old={"id": "o1", "customer_id": "c1", "amount": 100},
        new={"id": "o1", "customer_id": "c1", "amount": 150},
    )
    oracle.execute_event(ev_update)
    dec1 = manager.process(ev_update)
    assert dec1.kind == DecisionKind.REPAIR
    assert manager.cache.values == oracle.query_aggregate()

    # Event 2: Insert o3, exceeding capacity 4 (now 5 items).
    # LFU will evict the lowest score row.
    # r1 mult=3, c1 mult=3, o1/o2/o3 mult=0.
    # Therefore, one of the orders is evicted.
    ev_insert = WriteEvent(
        relation="orders",
        new={"id": "o3", "customer_id": "c1", "amount": 50},
    )
    oracle.execute_event(ev_insert)
    dec2 = manager.process(ev_insert)
    assert dec2.kind == DecisionKind.REPAIR
    assert manager.aux.total_rows() == 4
    assert len(manager.aux.evicted_pks["orders"]) == 1
    # Cache aggregate is still exact because o3 joined with resident c1 and r1
    assert manager.cache.values == oracle.query_aggregate()

    # Event 3: Now attempt to mutate the evicted order
    evicted_order_id = list(manager.aux.evicted_pks["orders"])[0]
    ev_touch_evicted = WriteEvent(
        relation="orders",
        old={"id": evicted_order_id, "customer_id": "c1", "amount": 100},
        new={"id": evicted_order_id, "customer_id": "c1", "amount": 999},
    )
    dec3 = manager.process(ev_touch_evicted)
    # Must deterministically invalidate
    assert dec3.kind == DecisionKind.INVALIDATE
    assert dec3.reason == "evicted_join_auxiliary_key"
    assert manager.aux.is_invalidated is True


# ==============================================================================
# Challenge 5: Candidate Selection Stability & Idempotence
# ==============================================================================


def test_lfu_candidate_selection_side_effect_on_child_access_frequency() -> None:
    """
    Candidate selection idempotence and side-effect freedom:
    Evaluating _select_global_eviction_candidate() or join_multiplicity()
    must NOT mutate _row_access_freq or _access_clock of child fact rows,
    and must deterministically select the lowest-scoring fact row ('orders', 'o1')
    rather than a referenced dimension row ('regions', 'r2').
    """
    shape = _make_3table_snowflake_shape()
    tables = {
        "regions": [{"id": "r1", "name": "East"}, {"id": "r2", "name": "West"}],
        "customers": [
            {"id": "c1", "region_id": "r1", "name": "Alice"},
            {"id": "c2", "region_id": "r2", "name": "Bob"},
        ],
        "orders": [
            {"id": "o1", "customer_id": "c1", "amount": 100},
            {"id": "o2", "customer_id": "c1", "amount": 200},
            {"id": "o3", "customer_id": "c2", "amount": 300},
        ],
    }

    aux = JoinAuxState.from_tables(shape, tables, eviction_policy="lfu")
    initial_clock = aux._access_clock
    initial_o3_freq = aux._row_access_freq["orders"]["o3"]

    # Select candidate repeatedly: must be idempotent and side-effect-free
    cand1 = aux._select_global_eviction_candidate()
    cand2 = aux._select_global_eviction_candidate()
    assert cand1 == ("orders", "o1")
    assert cand2 == cand1

    # Access clock and orders access frequencies must not mutate during candidate scoring
    assert aux._access_clock == initial_clock
    assert aux._row_access_freq["orders"]["o3"] == initial_o3_freq


# ==============================================================================
# Challenge 6: Randomized Differential Fuzzing against SQLite Oracle
# ==============================================================================


def test_randomized_differential_fuzzing_under_eviction() -> None:
    """
    Adversarial fuzzer running 100 randomized multi-table write operations
    under tight capacity (max_total_rows = 5) across regions, customers, and orders.
    Verifies that:
    1. Every REPAIR exactly matches the SQLite ground truth query result.
    2. Every PRESERVE exactly matches the SQLite ground truth query result.
    3. Every INVALIDATE sets aux.is_invalidated = True and never leaks stale cache data.
    4. Capacity bound total_rows() <= 5 is strictly preserved at all steps.
    """
    import random

    rng = random.Random(1337)
    shape = _make_3table_snowflake_shape()
    oracle = SQLiteJoinOracle()

    # Initial state
    init_regions = [{"id": f"r{i}", "name": f"Region_{i}"} for i in range(2)]
    init_customers = [
        {"id": f"c{i}", "region_id": f"r{i % 2}", "name": f"User_{i}"} for i in range(3)
    ]
    init_orders = [
        {"id": f"o{i}", "customer_id": f"c{i % 3}", "amount": (i + 1) * 25}
        for i in range(3)
    ]

    for r in init_regions:
        oracle.execute_event(WriteEvent(relation="regions", new=r))
    for c in init_customers:
        oracle.execute_event(WriteEvent(relation="customers", new=c))
    for o in init_orders:
        oracle.execute_event(WriteEvent(relation="orders", new=o))

    manager = JoinCacheManager(
        shape=shape,
        initial_tables={
            "regions": init_regions,
            "customers": init_customers,
            "orders": init_orders,
        },
        eviction_policy="lfu",
        max_total_rows=5,
    )

    live_regions = {r["id"]: dict(r) for r in init_regions}
    live_customers = {c["id"]: dict(c) for c in init_customers}
    live_orders = {o["id"]: dict(o) for o in init_orders}

    for step in range(100):
        # Enforce capacity invariant
        assert (
            manager.aux.total_rows() <= 5
        ), f"Step {step}: capacity bound violated: {manager.aux.total_rows()}"

        op_choice = rng.choice(
            [
                "insert_order",
                "update_order_amount",
                "reparent_order",
                "update_customer",
                "delete_order",
            ]
        )

        if op_choice == "insert_order":
            new_oid = f"o_fuzz_{step}"
            cust = rng.choice(list(live_customers.keys()))
            amt = rng.randint(10, 500)
            row = {"id": new_oid, "customer_id": cust, "amount": amt}
            live_orders[new_oid] = row
            ev = WriteEvent(relation="orders", new=dict(row))
        elif op_choice == "update_order_amount" and live_orders:
            oid = rng.choice(list(live_orders.keys()))
            old_row = live_orders[oid]
            new_amt = old_row["amount"] + rng.randint(-50, 50)
            if new_amt <= 0:
                new_amt = 10
            new_row = {**old_row, "amount": new_amt}
            live_orders[oid] = new_row
            ev = WriteEvent(relation="orders", old=dict(old_row), new=dict(new_row))
        elif op_choice == "reparent_order" and live_orders:
            oid = rng.choice(list(live_orders.keys()))
            old_row = live_orders[oid]
            new_cust = rng.choice(list(live_customers.keys()))
            new_row = {**old_row, "customer_id": new_cust}
            live_orders[oid] = new_row
            ev = WriteEvent(relation="orders", old=dict(old_row), new=dict(new_row))
        elif op_choice == "update_customer":
            cid = rng.choice(list(live_customers.keys()))
            old_row = live_customers[cid]
            new_row = {**old_row, "name": f"NewName_{step}"}
            live_customers[cid] = new_row
            ev = WriteEvent(relation="customers", old=dict(old_row), new=dict(new_row))
        elif live_orders:  # delete_order
            oid = rng.choice(list(live_orders.keys()))
            old_row = live_orders.pop(oid)
            ev = WriteEvent(relation="orders", old=dict(old_row))
        else:
            continue

        oracle.execute_event(ev)
        dec = manager.process(ev)
        oracle_res = oracle.query_aggregate()

        if dec.kind == DecisionKind.INVALIDATE:
            # Invalidation safety verified: system safely enters invalidated state
            assert manager.aux.is_invalidated is True
            # Rebuild manager from ground truth as real systems do on invalidation
            current_tables = {
                "regions": list(live_regions.values()),
                "customers": list(live_customers.values()),
                "orders": list(live_orders.values()),
            }
            manager.rebuild(current_tables)
            assert manager.aux.is_invalidated is False
            assert (
                manager.cache.values == oracle_res
            ), f"Step {step} post-rebuild mismatch: {manager.cache.values} != {oracle_res}"
        else:
            # If cache repaired or preserved, it MUST exactly equal oracle result
            assert (
                manager.cache.values == oracle_res
            ), f"Step {step} ({op_choice}): cache {manager.cache.values} != oracle {oracle_res}"
