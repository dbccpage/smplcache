# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Milestone 1 regression and performance verification suite:
1. SharedJoinAuxPool partial-update non-resurrection on evicted PKs.
2. O(1) dimension update lookup and secondary join key index in JoinAuxState.
3. Single execution of _compute_join_event_deltas and JoinAuxState.from_tables.
4. Certificate.boundary_clock watermark propagation in certify_event, certify_join_event, and WalStreamProcessor.
5. AggregateCache.from_group_state external SQL GROUP BY seeding.
6. Bounded tombstone cap (max_tombstones) with safe REREAD latch on overflow.
"""

from __future__ import annotations

from decimal import Decimal
import time
from unittest.mock import patch

import pytest

import smplcache.join as join_mod
from smplcache import (
    AggregateCache,
    DecisionKind,
    JoinAuxState,
    JoinCacheManager,
    MultiTableJoinShape,
    QueryShape,
    SharedJoinAuxPool,
    WalStreamProcessor,
    WriteEvent,
    parse_join_sql,
    parse_sql,
    process_event,
    process_join_event,
)


def extract_join_shape(sql: str, name: str = "join_shape") -> MultiTableJoinShape:
    res = parse_join_sql(sql, name=name)
    assert res.is_supported and res.shape is not None, res.reason
    return res.shape


def extract_shape(sql: str, name: str = "shape") -> QueryShape:
    res = parse_sql(sql, name=name)
    assert res.is_supported and res.shape is not None, res.reason
    return res.shape


def _sample_star_tables() -> dict[str, list[dict[str, object]]]:
    return {
        "customers": [
            {"id": 1, "tier": "enterprise", "notes": "n1"},
            {"id": 2, "tier": "startup", "notes": "n2"},
            {"id": 3, "tier": "enterprise", "notes": "n3"},
        ],
        "orders": [
            {
                "id": 101,
                "customer_id": 1,
                "status": "PAID",
                "amount": Decimal("100.00"),
                "memo": "m1",
            },
            {
                "id": 102,
                "customer_id": 2,
                "status": "PAID",
                "amount": Decimal("50.00"),
                "memo": "m2",
            },
            {
                "id": 103,
                "customer_id": 3,
                "status": "PAID",
                "amount": Decimal("75.00"),
                "memo": "m3",
            },
        ],
    }


def test_shared_pool_disjoint_partial_update_does_not_resurrect_evicted_pk() -> None:
    """
    Regression test for Bug 1:
    When a primary key is evicted from SharedJoinAuxPool.aux_state (recorded in
    evicted_pks and removed from table_rows), an UPDATE on a column disjoint from
    all query fingerprints (DecisionKind.PRESERVE, reason="disjoint_columns") with
    a partial row payload (e.g., {"id": 101, "memo": "updated"}) must NOT resurrect
    PK 101 into table_rows["orders"] with an incomplete dictionary.
    Subsequent fingerprint-touching updates on PK 101 must still return
    DecisionKind.INVALIDATE ("evicted_join_auxiliary_key").
    """
    q1 = extract_join_shape(
        "SELECT c.tier, SUM(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier",
        name="q_sum_by_tier",
    )
    q2 = extract_join_shape(
        "SELECT c.tier, COUNT(*) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier",
        name="q_cnt_by_tier",
    )
    tables = _sample_star_tables()
    pool = SharedJoinAuxPool(
        [q1, q2],
        tables,
        primary_keys={"customers": "id", "orders": "id"},
        max_rows_per_table={"orders": 2, "customers": 10},
        eviction_policy="fifo",
    )

    # With FIFO and max_rows_per_table["orders"] == 2, order 101 was evicted
    assert 101 in pool.aux_state.evicted_pks["orders"]
    assert 101 not in pool.aux_state.table_rows["orders"]

    # Send a partial UPDATE on disjoint column `memo` for evicted PK 101
    disjoint_evt = WriteEvent(
        relation="orders",
        old={"id": 101, "memo": "m1"},
        new={"id": 101, "memo": "m1_edited"},
        event_id="evt_disjoint_101",
    )
    decisions = pool.process_event(disjoint_evt)
    assert decisions["q_sum_by_tier"].kind == DecisionKind.PRESERVE
    assert decisions["q_sum_by_tier"].reason == "disjoint_columns"
    assert decisions["q_cnt_by_tier"].kind == DecisionKind.PRESERVE
    assert decisions["q_cnt_by_tier"].reason == "disjoint_columns"

    # Evicted PK 101 must NOT be resurrected in table_rows["orders"]
    assert 101 not in pool.aux_state.table_rows["orders"]
    assert 101 in pool.aux_state.evicted_pks["orders"]

    # Now send a fingerprint-relevant UPDATE on PK 101: must still INVALIDATE
    fp_evt = WriteEvent(
        relation="orders",
        old={
            "id": 101,
            "customer_id": 1,
            "status": "PAID",
            "amount": Decimal("100.00"),
        },
        new={
            "id": 101,
            "customer_id": 1,
            "status": "PAID",
            "amount": Decimal("120.00"),
        },
        event_id="evt_fp_101",
    )
    fp_decisions = pool.process_event(fp_evt)
    assert fp_decisions["q_sum_by_tier"].kind == DecisionKind.INVALIDATE
    assert fp_decisions["q_sum_by_tier"].reason == "evicted_join_auxiliary_key"


def test_apply_row_mutation_never_resurrects_evicted_pk_on_update() -> None:
    """
    Direct unit test on JoinAuxState.apply_row_mutation verifying that an UPDATE
    on an evicted PK does not re-insert the row into table_rows.
    """
    q1 = extract_join_shape(
        "SELECT c.tier, SUM(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier"
    )
    aux = JoinAuxState.from_tables(
        q1, _sample_star_tables(), primary_keys={"customers": "id", "orders": "id"}
    )
    assert aux.evict_row("orders", 101) is True
    assert 101 in aux.evicted_pks["orders"]
    assert 101 not in aux.table_rows["orders"]

    aux.apply_row_mutation(
        "orders",
        "UPDATE",
        {"id": 101, "memo": "old"},
        {"id": 101, "memo": "new"},
    )
    assert 101 not in aux.table_rows["orders"]


def test_single_delta_computation_and_single_aux_construction() -> None:
    """
    Verify Bottlenecks 2 and 3:
    - JoinCacheManager and SharedJoinAuxPool construct JoinAuxState.from_tables
      once when unbounded instead of N+1 times.
    - process_join_event and SharedJoinAuxPool.process_event execute
      _compute_join_event_deltas once per query instead of twice.
    """
    q1 = extract_join_shape(
        "SELECT c.tier, SUM(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier",
        name="q1",
    )
    q2 = extract_join_shape(
        "SELECT c.tier, AVG(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier",
        name="q2",
    )
    tables = _sample_star_tables()

    with patch.object(
        JoinAuxState, "from_tables", wraps=JoinAuxState.from_tables
    ) as spy_from_tables:
        pool = SharedJoinAuxPool(
            [q1, q2],
            tables,
            primary_keys={"customers": "id", "orders": "id"},
        )
        assert spy_from_tables.call_count == 1

    evt = WriteEvent(
        relation="customers",
        old={"id": 1, "tier": "enterprise"},
        new={"id": 1, "tier": "vip"},
        event_id="evt_dim_1",
    )
    with patch.object(
        join_mod,
        "_compute_join_event_deltas",
        wraps=join_mod._compute_join_event_deltas,
    ) as spy_deltas:
        decs = pool.process_event(evt)
        assert decs["q1"].kind == DecisionKind.REPAIR
        assert decs["q2"].kind == DecisionKind.REPAIR
        # 2 queries in pool -> _compute_join_event_deltas called once per query (2 total, not 4)
        assert spy_deltas.call_count == 2


def test_o1_dimension_lookup_speed_and_secondary_join_index() -> None:
    """
    Verify Bottleneck 1 fix:
    Dimension updates with 2,000 dimension rows use O(1) dict lookup rather than
    O(|D|) linear scan in evaluate_fact_row_contributions.
    """
    q_shape = extract_join_shape(
        "SELECT c.tier, SUM(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier"
    )
    customers = [
        {"id": i, "tier": "enterprise" if i % 2 == 0 else "startup"}
        for i in range(1, 2001)
    ]
    orders = [
        {
            "id": 10000 + i,
            "customer_id": (i % 2000) + 1,
            "status": "PAID",
            "amount": Decimal("10.00"),
        }
        for i in range(500)
    ]
    mgr = JoinCacheManager(
        q_shape,
        {"customers": customers, "orders": orders},
        primary_keys={"customers": "id", "orders": "id"},
    )
    t0 = time.perf_counter()
    for cid in range(1, 51):
        old_tier = "enterprise" if cid % 2 == 0 else "startup"
        new_tier = "vip"
        dec = mgr.process(
            WriteEvent(
                relation="customers",
                old={"id": cid, "tier": old_tier},
                new={"id": cid, "tier": new_tier},
                event_id=f"dim_upd_{cid}",
            )
        )
        assert dec.kind == DecisionKind.REPAIR
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert mgr.cache.values["vip"] == Decimal("500.00")


def test_boundary_clock_watermarks_and_from_group_state() -> None:
    """
    Verify Certificate.boundary_clock watermark propagation and
    AggregateCache.from_group_state external SQL GROUP BY seeding.
    """
    shape = extract_shape(
        "SELECT region, SUM(amount) FROM orders WHERE status = 'PAID' GROUP BY region"
    )
    # Seed from SQL GROUP BY row sequence without scanning base rows
    cache = AggregateCache.from_group_state(
        shape,
        [
            {
                "region": "us-east",
                "sum": Decimal("250.00"),
                "non_null_count": 2,
                "row_count": 2,
            },
            {
                "region": "eu-west",
                "sum": Decimal("100.00"),
                "non_null_count": 1,
                "row_count": 1,
            },
        ],
    )
    assert cache._from_rows is True
    assert cache.values == {"us-east": Decimal("250.00"), "eu-west": Decimal("100.00")}

    # Process an incremental update with explicit boundary_clock
    evt = WriteEvent(
        relation="orders",
        old={
            "id": 1,
            "region": "us-east",
            "status": "PAID",
            "amount": Decimal("50.00"),
        },
        new={
            "id": 1,
            "region": "us-east",
            "status": "PAID",
            "amount": Decimal("80.00"),
        },
        event_id="evt_99",
        boundary_clock="0/16B3748",
    )
    dec = process_event(shape, cache, evt)
    assert dec.kind == DecisionKind.REPAIR
    assert dec.certificate.boundary_clock == "0/16B3748"
    assert cache.values["us-east"] == Decimal("280.00")

    # Also test WalStreamProcessor LSN boundary_clock propagation
    proc = WalStreamProcessor(shape=shape)
    wal_msg = {
        "action": "U",
        "lsn": "0/16B3800",
        "table": "orders",
        "identity": [
            {"name": "id", "type": "int4", "value": 1},
            {"name": "region", "type": "text", "value": "us-east"},
            {"name": "status", "type": "text", "value": "PAID"},
            {"name": "amount", "type": "numeric", "value": "80.00"},
        ],
        "columns": [
            {"name": "id", "type": "int4", "value": 1},
            {"name": "region", "type": "text", "value": "us-east"},
            {"name": "status", "type": "text", "value": "PAID"},
            {"name": "amount", "type": "numeric", "value": "100.00"},
        ],
    }
    decs, cache = proc.ingest(wal_msg, cache)
    assert len(decs) == 1
    assert decs[0].kind == DecisionKind.REPAIR
    assert decs[0].certificate.boundary_clock == "0/16B3800"
    assert proc.boundary_clock == "0/16B3800"
    assert cache.values["us-east"] == Decimal("300.00")


def test_bounded_tombstone_cap_latches_to_reread_on_overflow() -> None:
    """
    Verify that JoinAuxState(max_tombstones=1) caps evicted_pks at 1 tombstone
    and safely latches tombstone_overflowed = True when a second eviction occurs,
    forcing subsequent join events to return Decision.invalidate("evicted_join_auxiliary_key").
    """
    q_shape = extract_join_shape(
        "SELECT c.tier, SUM(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier"
    )
    mgr = JoinCacheManager(
        q_shape,
        _sample_star_tables(),
        primary_keys={"customers": "id", "orders": "id"},
        max_rows_per_table={"orders": 1, "customers": 10},
        max_tombstones=1,
        eviction_policy="fifo",
    )
    # 3 orders seeded with max_rows_per_table=1 -> 2 evictions -> exceeds max_tombstones=1
    assert mgr.aux_state.total_tombstones() == 1
    assert mgr.aux_state.tombstone_overflowed is True

    # Even an update on the still-resident order 103 must safely latch to INVALIDATE (REREAD)
    dec = mgr.process(
        WriteEvent(
            relation="orders",
            old={
                "id": 103,
                "customer_id": 3,
                "status": "PAID",
                "amount": Decimal("75.00"),
            },
            new={
                "id": 103,
                "customer_id": 3,
                "status": "PAID",
                "amount": Decimal("90.00"),
            },
            event_id="evt_after_tombstone_overflow",
        )
    )
    assert dec.kind == DecisionKind.INVALIDATE
    assert dec.reason == "evicted_join_auxiliary_key"

    # Rebuilding with ample capacity clears tombstone_overflowed
    mgr.max_rows_per_table = None
    mgr.max_tombstones = 100
    mgr.rebuild(_sample_star_tables())
    assert mgr.aux_state.tombstone_overflowed is False
    assert mgr.aux_state.total_tombstones() == 0


def test_disjoint_update_synchronizes_fk_index_and_handles_unhashable_disjoint_values() -> (
    None
):
    """
    Verify that a disjoint_columns UPDATE on a resident row keeps fk_index
    synchronized when another query in SharedJoinAuxPool invalidated on an FK
    column, and safely ignores unhashable values in disjoint columns.
    """
    q1 = extract_join_shape(
        "SELECT c.tier, SUM(o.amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'PAID' GROUP BY c.tier",
        name="q1",
    )
    mgr = JoinCacheManager(
        q1,
        _sample_star_tables(),
        primary_keys={"customers": "id", "orders": "id"},
    )
    # Disjoint column update with an unhashable payload value (list) on resident order 101
    dec = mgr.process(
        WriteEvent(
            relation="orders",
            old={"id": 101, "memo": "m1"},
            new={"id": 101, "memo": ["unhashable", "tags"]},
            event_id="evt_unhashable_memo",
        )
    )
    assert dec.kind == DecisionKind.PRESERVE
    assert dec.reason == "disjoint_columns"
    assert mgr.aux_state.table_rows["orders"][101]["memo"] == ["unhashable", "tags"]
    assert 101 in mgr.aux_state.fk_index[("orders", "customer_id")][1]
