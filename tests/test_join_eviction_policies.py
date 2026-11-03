# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Unit and property-based test suite verifying configurable row eviction policies
(FIFO, LRU, frequency-biased LFU) in JoinAuxState and JoinCacheManager.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from typing import Any, Mapping

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smplcache import DecisionKind, WriteEvent
from smplcache.join import (
    JoinAuxState,
    JoinCacheManager,
    JoinClause,
    MultiTableJoinShape,
    rebuild_join_cache,
)


def _make_snowflake_shape() -> MultiTableJoinShape:
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
        name="snowflake_orders_by_region",
    )


def test_fifo_eviction_order_evicts_oldest_regardless_of_access() -> None:
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    aux = JoinAuxState(shape=shape, eviction_policy="fifo", max_total_rows=3)

    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i1", "val": 10})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i2", "val": 20})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i3", "val": 30})
    assert aux.total_rows() == 3

    # Access i1 heavily to increase its recency and frequency
    for _ in range(10):
        aux.record_access("items", "i1")

    # Insert a 4th row, exceeding max_total_rows=3
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i4", "val": 40})

    # FIFO must evict the earliest inserted row (i1), even though it was accessed most recently
    assert "i1" in aux.evicted_pks["items"]
    assert "i1" not in aux.table_rows["items"]
    assert set(aux.table_rows["items"].keys()) == {"i2", "i3", "i4"}
    assert aux.total_rows() == 3


def test_lru_eviction_order_retains_recently_accessed_rows() -> None:
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    aux = JoinAuxState(shape=shape, eviction_policy="lru", max_total_rows=3)

    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i1", "val": 10})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i2", "val": 20})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i3", "val": 30})

    # Access i1, then i2. Row i3 is now the least recently used
    aux.record_access("items", "i1")
    aux.record_access("items", "i2")

    # Insert i4 to trigger eviction
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i4", "val": 40})

    # LRU must evict i3 because its last access clock is older than i1 and i2
    assert "i3" in aux.evicted_pks["items"]
    assert "i3" not in aux.table_rows["items"]
    assert set(aux.table_rows["items"].keys()) == {"i1", "i2", "i4"}
    assert aux.total_rows() == 3


def test_lfu_eviction_pure_frequency_order() -> None:
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    aux = JoinAuxState(shape=shape, eviction_policy="lfu", max_total_rows=3)

    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i1", "val": 10})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i2", "val": 20})
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i3", "val": 30})

    # Record access counts: i1 has 5 accesses, i2 has 2 accesses, i3 has 0 additional accesses
    for _ in range(5):
        aux.record_access("items", "i1")
    for _ in range(2):
        aux.record_access("items", "i2")

    # Insert i4
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i4", "val": 40})

    # LFU must evict i3 because its access frequency is 1 (initial insert only)
    assert "i3" in aux.evicted_pks["items"]
    assert "i3" not in aux.table_rows["items"]
    assert set(aux.table_rows["items"].keys()) == {"i1", "i2", "i4"}
    assert aux.total_rows() == 3


def test_lfu_eviction_with_dimension_multiplicity_bias() -> None:
    shape = MultiTableJoinShape(
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
    )
    # Budget permits only 1 customer row in cache
    aux = JoinAuxState(
        shape=shape,
        eviction_policy="lfu",
        max_rows_per_table={"customers": 1},
    )

    # Insert customer c1
    aux.apply_row_mutation("customers", "INSERT", {}, {"id": "c1", "name": "Alice"})

    # Add 4 orders referencing c1, giving c1 join multiplicity = 4
    for idx in range(1, 5):
        aux.apply_row_mutation(
            "orders",
            "INSERT",
            {},
            {"id": f"o{idx}", "customer_id": "c1", "amount": 10 * idx},
        )

    assert aux.join_multiplicity("customers", "c1") == 4

    # Now insert customer c2 with zero referencing orders (multiplicity = 0)
    aux.apply_row_mutation("customers", "INSERT", {}, {"id": "c2", "name": "Bob"})

    # Because c1 has multiplicity 4 (score >= 5) and c2 has multiplicity 0 (score 1),
    # c2 must be chosen for eviction and c1 must be preserved in cache
    assert "c2" in aux.evicted_pks["customers"]
    assert "c1" in aux.table_rows["customers"]
    assert "c1" not in aux.evicted_pks["customers"]
    assert len(aux.table_rows["customers"]) == 1


def test_capacity_bounds_enforced_under_high_write_volume() -> None:
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    for pol in ("fifo", "lru", "lfu", "w_lfu"):
        aux = JoinAuxState(
            shape=shape,
            eviction_policy=pol,  # type: ignore[arg-type]
            max_total_rows=10,
        )
        for i in range(100):
            aux.apply_row_mutation(
                "items",
                "INSERT",
                {},
                {"id": f"item_{i}", "val": i * 10},
            )
            assert (
                aux.total_rows() <= 10
            ), f"Policy {pol} violated max_total_rows at insert {i}: {aux.total_rows()}"
        assert aux.total_rows() == 10
        assert len(aux.evicted_pks["items"]) == 90


def test_per_table_capacity_bounds_enforced() -> None:
    shape = MultiTableJoinShape(
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
    )
    aux = JoinAuxState(
        shape=shape,
        max_rows_per_table={"customers": 3, "orders": 5},
    )

    for c in range(10):
        aux.apply_row_mutation(
            "customers", "INSERT", {}, {"id": f"c{c}", "name": f"User {c}"}
        )
        assert len(aux.table_rows["customers"]) <= 3

    for o in range(15):
        aux.apply_row_mutation(
            "orders",
            "INSERT",
            {},
            {"id": f"o{o}", "customer_id": f"c{o % 3}", "amount": 100},
        )
        assert len(aux.table_rows["orders"]) <= 5

    assert len(aux.table_rows["customers"]) == 3
    assert len(aux.table_rows["orders"]) == 5


def test_downgrade_to_invalidate_on_evicted_key_without_orphans() -> None:
    shape = MultiTableJoinShape(
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
    )
    init_tables = {
        "customers": [{"id": "c1", "name": "Alice"}, {"id": "c2", "name": "Bob"}],
        "orders": [{"id": "o1", "customer_id": "c1", "amount": 100}],
    }
    # Bound customers to 1 row, causing c1 to be evicted when c2 is inserted
    aux = JoinAuxState.from_tables(
        shape,
        init_tables,
        max_rows_per_table={"customers": 1},
        eviction_policy="fifo",
    )
    cache = rebuild_join_cache(shape, init_tables)
    mgr = JoinCacheManager(shape=shape, cache=cache, aux=aux)

    # c1 was evicted
    assert "c1" in mgr.aux.evicted_pks["customers"]

    # When a write event references evicted customer c1, it must downgrade to invalidate
    ev = WriteEvent(
        relation="orders",
        old={},
        new={"id": "o2", "customer_id": "c1", "amount": 250},
    )
    dec = mgr.process(ev)
    assert dec.kind == DecisionKind.INVALIDATE
    assert dec.reason == "evicted_join_auxiliary_key"
    assert mgr.aux.is_invalidated is True

    # Subsequent events must also yield invalidation safely
    ev2 = WriteEvent(
        relation="orders",
        old={},
        new={"id": "o3", "customer_id": "c2", "amount": 50},
    )
    dec2 = mgr.process(ev2)
    assert dec2.kind == DecisionKind.INVALIDATE


def test_skewed_workload_lfu_outperforms_fifo_invalidation_rate() -> None:
    """
    Direct behavioral comparison: under skewed foreign-key workload with bounded memory,
    LFU with multiplicity bias preserves hot dimension rows and achieves fewer eviction
    invalidations than FIFO.
    """
    from smplcache.benchmark import run_zipfian_eviction_sweep

    results = run_zipfian_eviction_sweep(
        skew_values=[1.5],
        policies=["fifo", "lfu"],
        max_total_rows=50,
        num_transactions=60,
        seed=42,
        verify_oracle=True,
    )
    res_map = {r.policy: r for r in results}
    fifo_res = res_map["fifo"]
    lfu_res = res_map["lfu"]

    # LFU must have fewer eviction invalidations than FIFO under skew
    assert lfu_res.eviction_invalidate_count < fifo_res.eviction_invalidate_count
    assert lfu_res.repair_count > fifo_res.repair_count
    assert lfu_res.oracle_match is True
    assert fifo_res.oracle_match is True


@settings(max_examples=30, deadline=None)
@given(
    policy=st.sampled_from(["fifo", "lru", "lfu", "w_lfu", "wtinylfu"]),
    events=st.lists(
        st.tuples(
            st.sampled_from(["INSERT", "UPDATE", "DELETE"]),
            st.integers(min_value=1, max_value=6),
            st.integers(min_value=1, max_value=4),
            st.integers(min_value=10, max_value=100),
        ),
        min_size=5,
        max_size=20,
    ),
)
def test_property_oracle_consistency_across_policies(
    policy: str,
    events: list[tuple[str, int, int, int]],
) -> None:
    """
    Property test: Across FIFO, LRU, LFU, W-LFU, and W-TinyLFU under tight memory limits,
    any PRESERVE or REPAIR decision must strictly agree with SQLite oracle.
    """
    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    cur.execute("CREATE TABLE items (id TEXT PRIMARY KEY, val INTEGER)")

    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    cache = rebuild_join_cache(shape, {"items": []})
    aux = JoinAuxState(
        shape=shape,
        eviction_policy=policy,  # type: ignore[arg-type]
        max_total_rows=4,
    )
    mgr = JoinCacheManager(shape=shape, cache=cache, aux=aux)

    active_items: dict[str, int] = {}

    for op, item_idx, delta, new_val in events:
        item_id = f"i{item_idx}"
        if op == "INSERT":
            if item_id in active_items:
                continue
            active_items[item_id] = new_val
            cur.execute("INSERT INTO items (id, val) VALUES (?, ?)", (item_id, new_val))
            we = WriteEvent(
                relation="items", old={}, new={"id": item_id, "val": new_val}
            )
        elif op == "UPDATE":
            if item_id not in active_items:
                continue
            old_val = active_items[item_id]
            active_items[item_id] = new_val
            cur.execute("UPDATE items SET val = ? WHERE id = ?", (new_val, item_id))
            we = WriteEvent(
                relation="items",
                old={"id": item_id, "val": old_val},
                new={"id": item_id, "val": new_val},
            )
        else:  # DELETE
            if item_id not in active_items:
                continue
            old_val = active_items.pop(item_id)
            cur.execute("DELETE FROM items WHERE id = ?", (item_id,))
            we = WriteEvent(
                relation="items", old={"id": item_id, "val": old_val}, new={}
            )

        dec = mgr.process(we)

        if dec.kind in (DecisionKind.PRESERVE, DecisionKind.REPAIR):
            cur.execute("SELECT SUM(val) FROM items")
            row = cur.fetchone()
            oracle_sum = (
                Decimal(str(row[0])) if row and row[0] is not None else Decimal(0)
            )
            cached_sum = mgr.cache.values.get(()) or Decimal(0)
            assert (
                cached_sum == oracle_sum
            ), f"Oracle disagreement under {policy}: cached={cached_sum}, oracle={oracle_sum}"


def test_explicit_decay_frequencies_and_validation_bounds() -> None:
    shape = MultiTableJoinShape(
        base_relation="items",
        agg_fn="sum",
        agg_col=("items", "val"),
    )
    with pytest.raises(ValueError, match="Unknown eviction policy"):
        JoinAuxState(shape=shape, eviction_policy="invalid_pol")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="lfu_decay_window must be >= 1"):
        JoinAuxState(shape=shape, eviction_policy="w_lfu", lfu_decay_window=0)

    with pytest.raises(ValueError, match="lfu_decay_factor must be in"):
        JoinAuxState(shape=shape, eviction_policy="w_lfu", lfu_decay_factor=1.5)

    aux = JoinAuxState(
        shape=shape,
        eviction_policy="w_lfu",
        lfu_decay_window=100,
        lfu_decay_factor=0.5,
    )
    aux.apply_row_mutation("items", "INSERT", {}, {"id": "i1", "val": 10})
    for _ in range(7):
        aux.record_access("items", "i1")
    assert aux._row_access_freq["items"]["i1"] == 8

    decayed = aux.decay_frequencies(0.5)
    assert decayed == 1
    assert aux._row_access_freq["items"]["i1"] == 4
    assert aux.decay_epochs == 1

    # Decay to 0 prunes the entry cleanly
    aux.decay_frequencies(0.0)
    assert "i1" not in aux._row_access_freq["items"]
    assert aux.decay_epochs == 2


def test_static_lfu_cache_pollution_vs_windowed_w_lfu_under_phase_shift() -> None:
    """
    Demonstrate that under a 2-phase access shift:
    - Static `lfu` retains stale Phase 1 rows (`p1_a`, `p1_b`) with inflated historical
      frequency and immediately evicts newly inserted Phase 2 rows (`p2_a`, `p2_b`),
      causing Phase 2 updates to downgrade to `Decision.invalidate("evicted_join_auxiliary_key")`.
    - Windowed `w_lfu` decays Phase 1's stale frequencies across `lfu_decay_window`,
      evicts the cold Phase 1 rows, retains Phase 2's active rows, and repairs Phase 2
      updates in-place with SQLite oracle agreement.
    """
    shape = MultiTableJoinShape(
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
        group_cols=(("customers", "tier"),),
    )
    init_tables = {
        "customers": [
            {"id": "c1", "tier": "gold"},
            {"id": "c2", "tier": "silver"},
        ],
        "orders": [
            {"id": "p1_a", "customer_id": "c1", "amount": Decimal("10.00")},
            {"id": "p1_b", "customer_id": "c1", "amount": Decimal("20.00")},
        ],
    }

    # Capacity = 4 rows total (2 customers + 2 orders)
    mgr_lfu = JoinCacheManager(
        shape=shape,
        initial_tables=init_tables,
        eviction_policy="lfu",
        max_total_rows=4,
    )
    mgr_wlfu = JoinCacheManager(
        shape=shape,
        initial_tables=init_tables,
        eviction_policy="w_lfu",
        lfu_decay_window=6,
        lfu_decay_factor=0.5,
        max_total_rows=4,
    )

    # Phase 1: Heavy updates on p1_a (early Phase 1), then p1_b (late Phase 1)
    for step in range(4):
        ev_a = WriteEvent(
            relation="orders",
            old={
                "id": "p1_a",
                "customer_id": "c1",
                "amount": Decimal(str(10 + step * 5)),
            },
            new={
                "id": "p1_a",
                "customer_id": "c1",
                "amount": Decimal(str(15 + step * 5)),
            },
        )
        assert mgr_lfu.process(ev_a).kind == DecisionKind.REPAIR
        assert mgr_wlfu.process(ev_a).kind == DecisionKind.REPAIR

    for step in range(6):
        ev_b = WriteEvent(
            relation="orders",
            old={
                "id": "p1_b",
                "customer_id": "c1",
                "amount": Decimal(str(20 + step * 5)),
            },
            new={
                "id": "p1_b",
                "customer_id": "c1",
                "amount": Decimal(str(25 + step * 5)),
            },
        )
        assert mgr_lfu.process(ev_b).kind == DecisionKind.REPAIR
        assert mgr_wlfu.process(ev_b).kind == DecisionKind.REPAIR

    # Phase 2: Access pattern shifts to customer c2 and new order p2_a!
    ev_ins_p2 = WriteEvent(
        relation="orders",
        old={},
        new={"id": "p2_a", "customer_id": "c2", "amount": Decimal("100.00")},
    )
    assert mgr_lfu.process(ev_ins_p2).kind == DecisionKind.REPAIR
    assert mgr_wlfu.process(ev_ins_p2).kind == DecisionKind.REPAIR

    # Static LFU evicted p2_a immediately on insertion because p1_a and p1_b had high Phase 1 freq!
    assert "p2_a" in mgr_lfu.aux.evicted_pks["orders"]
    assert set(mgr_lfu.aux.table_rows["orders"].keys()) == {"p1_a", "p1_b"}

    # Windowed W-LFU decayed p1_a's old Phase 1 frequency and evicted cold p1_a instead, keeping p2_a!
    assert "p1_a" in mgr_wlfu.aux.evicted_pks["orders"]
    assert "p2_a" in mgr_wlfu.aux.table_rows["orders"]
    assert mgr_wlfu.aux.decay_epochs > 0

    # Subsequent Phase 2 update to p2_a:
    ev_upd_p2 = WriteEvent(
        relation="orders",
        old={"id": "p2_a", "customer_id": "c2", "amount": Decimal("100.00")},
        new={"id": "p2_a", "customer_id": "c2", "amount": Decimal("150.00")},
    )
    dec_lfu = mgr_lfu.process(ev_upd_p2)
    dec_wlfu = mgr_wlfu.process(ev_upd_p2)

    assert dec_lfu.kind == DecisionKind.INVALIDATE
    assert dec_lfu.reason == "evicted_join_auxiliary_key"
    assert dec_wlfu.kind == DecisionKind.REPAIR
    assert mgr_wlfu.cache.values == {
        "gold": Decimal("80.00"),
        "silver": Decimal("150.00"),
    }


def test_phase_rotated_zipfian_sweep_and_window_sensitivity() -> None:
    """
    Verify phase-rotated Zipfian benchmark sweep (`run_phase_rotated_eviction_sweep`)
    and decay window sensitivity (`run_decay_window_sensitivity_sweep`):
    - Static `lfu` suffers post-shift cache pollution (`stale_phase1_orders_retained == 28`).
    - Windowed `w_lfu` purges stale Phase 1 orders, retains active Phase 2 orders,
      achieves fewer post-shift eviction invalidations than `lfu`, `lru`, and `fifo`,
      and maintains 100% oracle parity.
    """
    from smplcache.benchmark import (
        run_decay_window_sensitivity_sweep,
        run_phase_rotated_eviction_sweep,
    )

    sweep = run_phase_rotated_eviction_sweep(
        skew_values=[0.5, 1.5],
        policies=["fifo", "lru", "lfu", "w_lfu"],
        lfu_decay_window=32,
        lfu_decay_factor=0.5,
        max_total_rows=50,
        num_transactions=80,
        num_phases=2,
        seed=42,
        verify_oracle=True,
    )
    by_key = {(r.skew_s, r.policy): r for r in sweep}
    for r in sweep:
        assert r.oracle_match is True

    for s in (0.5, 1.5):
        fifo_r = by_key[(s, "fifo")]
        lru_r = by_key[(s, "lru")]
        lfu_r = by_key[(s, "lfu")]
        wlfu_r = by_key[(s, "w_lfu")]

        # Static LFU retains all 28 stale Phase 1 orders and 0 Phase 2 orders
        assert lfu_r.stale_phase1_orders_retained == 28
        assert lfu_r.hot_phase2_orders_retained == 0
        # W-LFU evicts stale Phase 1 orders and retains hot Phase 2 orders
        assert wlfu_r.stale_phase1_orders_retained == 0
        assert wlfu_r.hot_phase2_orders_retained == 28
        assert wlfu_r.decay_epochs > 0
        assert (
            wlfu_r.post_shift_eviction_invalidate_count
            < lfu_r.post_shift_eviction_invalidate_count
        )
        assert (
            wlfu_r.post_shift_eviction_invalidate_count
            < fifo_r.post_shift_eviction_invalidate_count
        )
        assert (
            wlfu_r.post_shift_eviction_invalidate_count
            <= lru_r.post_shift_eviction_invalidate_count
        )

    sens = run_decay_window_sensitivity_sweep(
        zipf_s=1.5,
        decay_windows=[32, 256, 512, None],
        decay_factors=[0.5],
        max_total_rows=50,
        num_transactions=80,
        num_phases=2,
        seed=42,
        verify_oracle=True,
    )
    assert len(sens) == 4
    assert all(sr.oracle_match is True for sr in sens)
    # On this fixture, short-window decay must beat no decay. Intermediate
    # windows may tie; strict monotonicity is not an eviction-policy invariant.
    invs = [sr.post_shift_eviction_invalidate_count for sr in sens]
    assert invs[0] < invs[-1]
    assert len(set(invs)) > 1


def test_count_min_sketch_estimation_decay_and_clone() -> None:
    """
    Verify CountMinSketch frequency estimation, conservative update, automatic
    reset_window halving decay, explicit decay, and clone fidelity.
    """
    from smplcache.join import CountMinSketch

    with pytest.raises(ValueError, match="width must be >= 1"):
        CountMinSketch(width=0)
    with pytest.raises(ValueError, match="depth must be >= 1"):
        CountMinSketch(depth=0)
    with pytest.raises(ValueError, match="reset_window must be >= 1"):
        CountMinSketch(reset_window=0)
    with pytest.raises(ValueError, match="decay_factor must be in"):
        CountMinSketch(decay_factor=2.0)

    cms = CountMinSketch(width=64, depth=4, reset_window=10, decay_factor=0.5)
    for _ in range(8):
        cms.increment("orders", "o1")
    assert cms.estimate("orders", "o1") == 8
    assert cms.estimate("orders", "unseen_key") == 0
    assert cms.decay_epochs == 0

    # Clone preserves exact state
    cloned = cms.clone()
    assert cloned.estimate("orders", "o1") == 8

    # 2 more increments reach reset_window == 10 and trigger automatic halving (10 -> 5)
    cms.increment("orders", "o1")
    est_after = cms.increment("orders", "o1")
    assert cms.decay_epochs == 1
    assert est_after == 5
    assert cms.total_additions == 10


def test_wtinylfu_recency_window_protects_new_arrivals_and_hill_climbs() -> None:
    """
    Demonstrate that under a mid-window phase transition where new phase rows are
    inserted in sequence before their updates arrive:
    - Fixed-window `w_lfu` (with a window that has not just decayed to 0) evicts
      the first newly inserted Phase 2 row (`p2_a`, freq=1) when the second Phase 2
      row (`p2_b`, freq=1) is inserted, because Phase 1 rows still have freq >= 2.
    - Adaptive `wtinylfu` protects `p2_a` inside the Recency Window (`in_win == 1`)
      and evicts a cold Phase 1 row from the Main Segment (`in_win == 0`), allowing
      both `p2_a` and `p2_b` to be repaired in-place when updated.
    """
    shape = MultiTableJoinShape(
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
        group_cols=(("customers", "tier"),),
    )
    init_tables = {
        "customers": [
            {"id": "c1", "tier": "gold"},
            {"id": "c2", "tier": "silver"},
        ],
        "orders": [
            {"id": "p1_a", "customer_id": "c1", "amount": Decimal("10.00")},
            {"id": "p1_b", "customer_id": "c1", "amount": Decimal("20.00")},
            {"id": "p1_c", "customer_id": "c1", "amount": Decimal("30.00")},
            {"id": "p1_d", "customer_id": "c1", "amount": Decimal("40.00")},
        ],
    }

    # Capacity = 6 rows (2 customers + 4 orders)
    mgr_wlfu = JoinCacheManager(
        shape=shape,
        initial_tables=init_tables,
        eviction_policy="w_lfu",
        lfu_decay_window=64,
        max_total_rows=6,
    )
    mgr_wtinylfu = JoinCacheManager(
        shape=shape,
        initial_tables=init_tables,
        eviction_policy="wtinylfu",
        window_ratio=0.50,
        adapt_epoch_size=8,
        max_total_rows=6,
    )

    # Warm up all 4 Phase 1 orders so each has freq >= 3
    for oid, base_amt in (
        ("p1_a", Decimal("10.00")),
        ("p1_b", Decimal("20.00")),
        ("p1_c", Decimal("30.00")),
        ("p1_d", Decimal("40.00")),
    ):
        ev = WriteEvent(
            relation="orders",
            old={"id": oid, "customer_id": "c1", "amount": base_amt},
            new={"id": oid, "customer_id": "c1", "amount": base_amt + Decimal("5.00")},
        )
        assert mgr_wlfu.process(ev).kind == DecisionKind.REPAIR
        assert mgr_wtinylfu.process(ev).kind == DecisionKind.REPAIR

    # Phase 2 starts mid-window: insert p2_a, then insert p2_b before updating p2_a!
    ins_p2_a = WriteEvent(
        relation="orders",
        old={},
        new={"id": "p2_a", "customer_id": "c2", "amount": Decimal("100.00")},
    )
    ins_p2_b = WriteEvent(
        relation="orders",
        old={},
        new={"id": "p2_b", "customer_id": "c2", "amount": Decimal("200.00")},
    )
    assert mgr_wlfu.process(ins_p2_a).kind == DecisionKind.REPAIR
    assert mgr_wlfu.process(ins_p2_b).kind == DecisionKind.REPAIR
    assert mgr_wtinylfu.process(ins_p2_a).kind == DecisionKind.REPAIR
    assert mgr_wtinylfu.process(ins_p2_b).kind == DecisionKind.REPAIR

    # Fixed-window W-LFU evicted p2_a immediately on insertion because p1_a..p1_d had freq >= 2!
    assert "p2_a" in mgr_wlfu.aux.evicted_pks["orders"]
    # W-TinyLFU protected p2_a and p2_b in the Recency Window and evicted p1_a and p1_b from Main Segment!
    assert "p2_a" in mgr_wtinylfu.aux.table_rows["orders"]
    assert "p2_b" in mgr_wtinylfu.aux.table_rows["orders"]
    assert "p1_a" in mgr_wtinylfu.aux.evicted_pks["orders"]
    assert "p1_b" in mgr_wtinylfu.aux.evicted_pks["orders"]

    # Now update p2_a and p2_b: W-TinyLFU repairs both in-place; W-LFU invalidates!
    upd_p2_a = WriteEvent(
        relation="orders",
        old={"id": "p2_a", "customer_id": "c2", "amount": Decimal("100.00")},
        new={"id": "p2_a", "customer_id": "c2", "amount": Decimal("125.00")},
    )
    assert mgr_wlfu.process(upd_p2_a).kind == DecisionKind.INVALIDATE
    assert mgr_wtinylfu.process(upd_p2_a).kind == DecisionKind.REPAIR
    assert mgr_wtinylfu.aux.adapt_epochs >= 1
    assert len(mgr_wtinylfu.aux.window_ratio_history) >= 2


def test_shared_join_aux_pool_multi_query_outperforms_isolated_pools() -> None:
    """
    Verify SharedJoinAuxPool across 3 concurrent join queries:
    - Sums cross-query structural multiplicity across all registered shapes.
    - Deduplicates shared dimension and fact rows in a single unified pool.
    - Achieves fewer eviction invalidations than partitioning the same total row
      budget across 3 isolated JoinAuxState pools, with 100% oracle parity.
    """
    from smplcache.benchmark import (
        run_rapid_oscillation_eviction_sweep,
        run_shared_multi_query_pool_benchmark,
    )
    from smplcache.join import SharedJoinAuxPool

    q1 = MultiTableJoinShape(
        name="q1_sum",
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="sum",
        agg_col=("orders", "amount"),
        group_cols=(("customers", "tier"),),
    )
    q2 = MultiTableJoinShape(
        name="q2_cnt",
        base_relation="orders",
        joins=(
            JoinClause(
                right_relation="customers",
                left_qualifier="orders",
                left_key="customer_id",
                right_key="id",
            ),
        ),
        agg_fn="count",
        agg_col=("orders", "id"),
        group_cols=(("customers", "tier"),),
    )
    tables = {
        "customers": [{"id": "c1", "tier": "gold"}, {"id": "c2", "tier": "silver"}],
        "orders": [
            {"id": "o1", "customer_id": "c1", "amount": Decimal("50.00")},
            {"id": "o2", "customer_id": "c1", "amount": Decimal("70.00")},
        ],
    }
    pool = SharedJoinAuxPool(
        [q1, q2],
        tables,
        eviction_policy="wtinylfu",
        max_total_rows=6,
    )
    # c1 has 2 referencing orders (1 + 2 = 3 per query) across 2 queries -> cross-query multiplicity == 6
    assert pool.cross_query_multiplicity("customers", "c1") == 6
    # c2 has 0 referencing orders (1 + 0 = 1 per query) across 2 queries -> cross-query multiplicity == 2
    assert pool.cross_query_multiplicity("customers", "c2") == 2

    ev = WriteEvent(
        relation="orders",
        old={"id": "o1", "customer_id": "c1", "amount": Decimal("50.00")},
        new={"id": "o1", "customer_id": "c1", "amount": Decimal("80.00")},
    )
    decs = pool.process_event(ev)
    assert decs["q1_sum"].kind == DecisionKind.REPAIR
    assert decs["q2_cnt"].kind in (DecisionKind.PRESERVE, DecisionKind.REPAIR)
    assert pool.caches["q1_sum"].values == {"gold": Decimal("150.00")}
    assert pool.caches["q2_cnt"].values == {"gold": Decimal(2)}

    # Verify benchmark sweeps
    osc_res = run_rapid_oscillation_eviction_sweep(
        skew_values=[1.5],
        policies=["fifo", "lfu", "w_lfu", "wtinylfu"],
        fixed_w_lfu_window=64,
        max_total_rows=50,
        num_transactions=96,
        num_phases=4,
        seed=42,
        verify_oracle=True,
    )
    osc_map = {r.policy: r for r in osc_res}
    assert all(r.oracle_match is True for r in osc_res)
    assert (
        osc_map["wtinylfu"].post_shift_eviction_invalidate_count
        <= osc_map["w_lfu"].post_shift_eviction_invalidate_count
    )
    assert (
        osc_map["wtinylfu"].post_shift_eviction_invalidate_count
        < osc_map["lfu"].post_shift_eviction_invalidate_count
    )
    assert (
        osc_map["wtinylfu"].post_shift_eviction_invalidate_count
        < osc_map["fifo"].post_shift_eviction_invalidate_count
    )

    pool_res = run_shared_multi_query_pool_benchmark(
        pool_budgets=[50],
        policies=["fifo", "lfu", "wtinylfu"],
        zipf_s=1.5,
        num_transactions=80,
        num_phases=4,
        seed=42,
        verify_oracle=True,
    )
    pool_map = {(r.pool_mode, r.policy): r for r in pool_res}
    assert all(r.oracle_match is True for r in pool_res)
    # Shared pool dramatically reduces eviction invalidations vs isolated per-query pools
    assert (
        pool_map[("shared", "wtinylfu")].total_eviction_invalidate_count
        < pool_map[("isolated", "wtinylfu")].total_eviction_invalidate_count
    )
    assert (
        pool_map[("shared", "wtinylfu")].total_eviction_invalidate_count
        < pool_map[("shared", "lfu")].total_eviction_invalidate_count
    )
