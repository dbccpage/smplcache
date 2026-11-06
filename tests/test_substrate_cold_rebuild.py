# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from decimal import Decimal

from smplcache import (
    AggregateCache,
    ColdRebuildSubstrate,
    DecisionKind,
    JoinCacheManager,
    Predicate,
    QueryShape,
    SortedMultisetExtremumCache,
    WriteEvent,
    parse_join_sql,
    parse_sql,
    rebuild_join_cache,
)


def test_substrate_cold_rebuild_full_lifecycle_sum_and_avg() -> None:
    substrate = ColdRebuildSubstrate(backend="sqlite")
    initial_rows = [
        {
            "id": 1,
            "region": "us",
            "status": "paid",
            "amount": Decimal("100.00"),
            "notes": "a",
        },
        {
            "id": 2,
            "region": "us",
            "status": "paid",
            "amount": Decimal("50.00"),
            "notes": "b",
        },
        {
            "id": 3,
            "region": "eu",
            "status": "paid",
            "amount": Decimal("200.00"),
            "notes": "c",
        },
        {
            "id": 4,
            "region": "eu",
            "status": "pending",
            "amount": Decimal("90.00"),
            "notes": "d",
        },
    ]
    substrate.create_and_seed_table("orders", initial_rows, pk_col="id")

    sum_shape = parse_sql(
        "SELECT region, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY region",
        name="sum_by_region",
    ).shape
    avg_shape = parse_sql(
        "SELECT region, AVG(amount) FROM orders WHERE status = 'paid' GROUP BY region",
        name="avg_by_region",
    ).shape
    assert sum_shape is not None
    assert avg_shape is not None

    sum_cache = substrate.rebuild_aggregate_cache(sum_shape)
    avg_cache = substrate.rebuild_aggregate_cache(avg_shape)
    assert sum_cache._from_rows is True
    assert avg_cache._from_rows is True
    assert sum_cache.values == {"us": Decimal("150.00"), "eu": Decimal("200.00")}
    assert avg_cache.values == {"us": Decimal("75.00"), "eu": Decimal("200.00")}

    # Step 1: KEEP (PRESERVE on disjoint column 'notes')
    ev_keep = WriteEvent(
        relation="orders",
        old={
            "id": 1,
            "region": "us",
            "status": "paid",
            "amount": Decimal("100.00"),
            "notes": "a",
        },
        new={
            "id": 1,
            "region": "us",
            "status": "paid",
            "amount": Decimal("100.00"),
            "notes": "updated",
        },
        changed_cols={"notes"},
        explicit_operation="UPDATE",
    )
    d1_sum, sum_cache, rebuilt1 = substrate.process_or_rebuild(
        sum_shape, sum_cache, ev_keep, apply_to_substrate=True
    )
    d1_avg, avg_cache, _ = substrate.process_or_rebuild(
        avg_shape, avg_cache, ev_keep, apply_to_substrate=False
    )
    assert d1_sum.kind == DecisionKind.PRESERVE
    assert d1_avg.kind == DecisionKind.PRESERVE
    assert rebuilt1 is False

    # Step 2: FIX IN PLACE (REPAIR on amount update)
    ev_repair = WriteEvent(
        relation="orders",
        old={
            "id": 2,
            "region": "us",
            "status": "paid",
            "amount": Decimal("50.00"),
            "notes": "b",
        },
        new={
            "id": 2,
            "region": "us",
            "status": "paid",
            "amount": Decimal("110.00"),
            "notes": "b",
        },
        changed_cols={"amount"},
        explicit_operation="UPDATE",
    )
    d2_sum, sum_cache, rebuilt2 = substrate.process_or_rebuild(
        sum_shape, sum_cache, ev_repair, apply_to_substrate=True
    )
    d2_avg, avg_cache, _ = substrate.process_or_rebuild(
        avg_shape, avg_cache, ev_repair, apply_to_substrate=False
    )
    assert d2_sum.kind == DecisionKind.REPAIR
    assert d2_avg.kind == DecisionKind.REPAIR
    assert rebuilt2 is False
    assert sum_cache.values == {"us": Decimal("210.00"), "eu": Decimal("200.00")}
    assert avg_cache.values == {"us": Decimal("105.00"), "eu": Decimal("200.00")}

    # Step 3: REREAD (INVALIDATE on incomplete WAL event after underlying DB mutation)
    substrate.apply_write_event(
        WriteEvent(
            relation="orders",
            old={
                "id": 4,
                "region": "eu",
                "status": "pending",
                "amount": Decimal("90.00"),
                "notes": "d",
            },
            new={
                "id": 4,
                "region": "eu",
                "status": "paid",
                "amount": Decimal("100.00"),
                "notes": "d",
            },
            explicit_operation="UPDATE",
        )
    )
    ev_incomplete = WriteEvent(
        relation="orders",
        old={"id": 4},
        new={"id": 4, "status": "paid"},
        changed_cols={"status", "amount"},
        unavailable_cols={"amount"},
        explicit_operation="UPDATE",
    )
    d3_sum, sum_cache, rebuilt3 = substrate.process_or_rebuild(
        sum_shape, sum_cache, ev_incomplete, apply_to_substrate=False
    )
    d3_avg, avg_cache, rebuilt3_avg = substrate.process_or_rebuild(
        avg_shape, avg_cache, ev_incomplete, apply_to_substrate=False
    )
    assert d3_sum.kind == DecisionKind.INVALIDATE
    assert d3_avg.kind == DecisionKind.INVALIDATE
    assert rebuilt3 is True
    assert rebuilt3_avg is True
    assert sum_cache._from_rows is True
    assert avg_cache._from_rows is True
    assert sum_cache.values == {"us": Decimal("210.00"), "eu": Decimal("300.00")}
    assert avg_cache.values == {"us": Decimal("105.00"), "eu": Decimal("150.00")}

    # Step 4: FIX IN PLACE after cold rebuild (verify _group_state sums and counts are exact)
    ev_post_rebuild = WriteEvent(
        relation="orders",
        old={
            "id": 3,
            "region": "eu",
            "status": "paid",
            "amount": Decimal("200.00"),
            "notes": "c",
        },
        new={
            "id": 3,
            "region": "eu",
            "status": "paid",
            "amount": Decimal("260.00"),
            "notes": "c",
        },
        changed_cols={"amount"},
        explicit_operation="UPDATE",
    )
    d4_sum, sum_cache, rebuilt4 = substrate.process_or_rebuild(
        sum_shape, sum_cache, ev_post_rebuild, apply_to_substrate=True
    )
    d4_avg, avg_cache, rebuilt4_avg = substrate.process_or_rebuild(
        avg_shape, avg_cache, ev_post_rebuild, apply_to_substrate=False
    )
    assert d4_sum.kind == DecisionKind.REPAIR
    assert d4_avg.kind == DecisionKind.REPAIR
    assert rebuilt4 is False
    assert rebuilt4_avg is False

    oracle_rows = substrate.fetch_table_rows("orders")
    oracle_sum = AggregateCache.from_rows(sum_shape, oracle_rows)
    oracle_avg = AggregateCache.from_rows(avg_shape, oracle_rows)
    assert sum_cache.values == oracle_sum.values
    assert avg_cache.values == oracle_avg.values
    substrate.close()


def test_substrate_cold_rebuild_join_and_extremum() -> None:
    substrate = ColdRebuildSubstrate(backend="sqlite")
    customers = [
        {"id": 10, "region": "us", "tier": "gold"},
        {"id": 20, "region": "eu", "tier": "silver"},
    ]
    orders = [
        {"id": 1, "customer_id": 10, "status": "paid", "amount": Decimal("40.00")},
        {"id": 2, "customer_id": 10, "status": "paid", "amount": Decimal("60.00")},
        {"id": 3, "customer_id": 20, "status": "paid", "amount": Decimal("75.00")},
    ]
    substrate.create_and_seed_table("customers", customers, pk_col="id")
    substrate.create_and_seed_table("orders", orders, pk_col="id")

    jshape = parse_join_sql(
        "SELECT c.region, SUM(o.amount) "
        "FROM orders o JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'paid' GROUP BY c.region"
    ).shape
    assert jshape is not None

    direct_join_cache = substrate.rebuild_aggregate_cache(jshape)
    oracle_join_cache = rebuild_join_cache(
        jshape,
        {
            "orders": substrate.fetch_table_rows("orders"),
            "customers": substrate.fetch_table_rows("customers"),
        },
    )
    assert direct_join_cache.values == oracle_join_cache.values

    mgr = JoinCacheManager(jshape, max_total_rows=20, eviction_policy="wtinylfu")
    rebuilt_mgr_cache = substrate.rebuild_join_manager(mgr)
    assert rebuilt_mgr_cache.values == oracle_join_cache.values

    max_shape = QueryShape(
        name="max_order_by_cust",
        relation="orders",
        predicate_cols={"status"},
        aggregate_cols={"amount"},
        group_cols=("customer_id",),
        agg_fn="max",
        agg_col="amount",
        predicate=Predicate.from_eq("status", "paid"),
    )
    ext_cache = SortedMultisetExtremumCache(max_shape, max_window_per_group=2)
    substrate.rebuild_extremum_cache(ext_cache)
    assert ext_cache.values == {10: Decimal("60.00"), 20: Decimal("75.00")}
    substrate.close()
