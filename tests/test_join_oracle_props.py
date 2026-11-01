# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Property-based and regression test suite for Upgrade 3 foreign-key `JOIN`
certification (`smplcache.join`, `parse_join_sql`) against the multi-table
SQLite oracle (`query_multi_table_oracle`).

Verifies:
1. Interleaved Fact & Dimension Stream Soundness (`test_join_stream_soundness_against_multi_table_oracle`):
   - Across generated 2-table and 3-table `INNER JOIN` and `LEFT JOIN` shapes
     (parsed via `parse_join_sql`) and interleaved write event streams on
     `orders` (fact table: insert, delete, value update, foreign-key reparenting,
     disjoint update, partial-old update) and `customers` / `warehouses`
     (dimension tables: insert, delete, 1-to-k fanout update, zero-multiplicity
     update, disjoint update, partial-old update):
   - After `REPAIR`, `cache.values == query_multi_table_oracle(...)`.
   - After `PRESERVE`, `oracle_before == oracle_after`.
   - After `INVALIDATE`, rebuilding `cache` and `JoinAuxState` from the tables
     restores `cache.values == oracle_after` and the stream continues.
2. Evidence & Auxiliary State Necessity (`test_join_evidence_and_aux_state_necessity`):
   - Without `JoinAuxState` (or when `aux_state.is_invalidated`), no write touching
     a participating table's fingerprint ever returns `REPAIR`.
   - Dropping any column in `shape.table_required_evidence(rel)` from `old` or
     `new` never returns `REPAIR`.
3. Multi-Table Oracle vs Relational Reference & Cross-Table Fingerprint Soundness.
4. V1 Single-Table Gate (`parse_sql` without `parse_join_sql` rejects `JOIN`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
import os
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest

from smplcache import (
    NULL_GROUP_KEY,
    AggregateCache,
    DecisionKind,
    JoinAuxState,
    JoinClause,
    MultiTableJoinShape,
    WalStreamProcessor,
    WriteEvent,
    certify_join_event,
    check_cdc_evidence,
    parse_join_sql,
    parse_sql,
    process_join_event,
    rebuild_join_cache,
)
from tests.oracle import (
    evaluate_join_reference,
    join_shape_to_sql,
    query_multi_table_oracle,
)

JOIN_MAX_EXAMPLES = int(os.environ.get("SMPLCACHE_JOIN_HYPOTHESIS_EXAMPLES", "1000"))

MULTI_TABLE_SCHEMAS: dict[str, tuple[str, ...]] = {
    "customers": ("id", "tier", "region", "credit_limit", "notes"),
    "warehouses": ("id", "country", "capacity", "notes"),
    "orders": (
        "id",
        "customer_id",
        "warehouse_id",
        "status",
        "amount",
        "quantity",
        "notes",
    ),
}

CUSTOMER_ID_POOL = ["c1", "c2", "c3", "c4"]
WAREHOUSE_ID_POOL = ["w1", "w2", "w3"]
TIER_STRATEGY = st.sampled_from([None, "", "gold", "silver", "bronze"])
REGION_STRATEGY = st.sampled_from([None, 0, "", "us", "eu"])
COUNTRY_STRATEGY = st.sampled_from([None, "usa", "de", "jp"])
STATUS_STRATEGY = st.sampled_from([None, "", "paid", "pending", "cancelled"])
DEC_NUMERIC_STRATEGY = st.one_of(
    st.none(),
    st.just(0),
    st.just(Decimal("0.00")),
    st.integers(min_value=-100, max_value=300),
    st.decimals(
        min_value=-100,
        max_value=300,
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)


@st.composite
def customer_row_strategy(draw: st.DrawFn, cid: str) -> dict[str, Any]:
    return {
        "id": cid,
        "tier": draw(TIER_STRATEGY),
        "region": draw(REGION_STRATEGY),
        "credit_limit": draw(DEC_NUMERIC_STRATEGY),
        "notes": draw(st.sampled_from([None, "vip", "standard"])),
    }


@st.composite
def warehouse_row_strategy(draw: st.DrawFn, wid: str) -> dict[str, Any]:
    return {
        "id": wid,
        "country": draw(COUNTRY_STRATEGY),
        "capacity": draw(DEC_NUMERIC_STRATEGY),
        "notes": draw(st.sampled_from([None, "cold", "dry"])),
    }


@st.composite
def order_row_strategy(
    draw: st.DrawFn,
    row_id: int,
    valid_cids: list[str],
    valid_wids: list[str],
    strict_fk: bool,
) -> dict[str, Any]:
    if strict_fk:
        cid_choices: list[Any] = [None, *valid_cids]
        wid_choices: list[Any] = [None, *valid_wids]
    else:
        cid_choices = [None, "orphan_c", *CUSTOMER_ID_POOL]
        wid_choices = [None, "orphan_w", *WAREHOUSE_ID_POOL]

    return {
        "id": row_id,
        "customer_id": draw(st.sampled_from(cid_choices)),
        "warehouse_id": draw(st.sampled_from(wid_choices)),
        "status": draw(STATUS_STRATEGY),
        "amount": draw(DEC_NUMERIC_STRATEGY),
        "quantity": draw(DEC_NUMERIC_STRATEGY),
        "notes": draw(st.sampled_from([None, "rush", "normal"])),
    }


@st.composite
def multi_table_db_strategy(
    draw: st.DrawFn,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    strict_fk = draw(st.booleans())
    active_cids = draw(
        st.lists(
            st.sampled_from(CUSTOMER_ID_POOL),
            min_size=0,
            max_size=4,
            unique=True,
        )
    )
    active_wids = draw(
        st.lists(
            st.sampled_from(WAREHOUSE_ID_POOL),
            min_size=0,
            max_size=3,
            unique=True,
        )
    )

    customers = [draw(customer_row_strategy(cid)) for cid in active_cids]
    warehouses = [draw(warehouse_row_strategy(wid)) for wid in active_wids]

    n_orders = draw(st.integers(min_value=0, max_value=12))
    orders = [
        draw(
            order_row_strategy(
                row_id=i + 1,
                valid_cids=active_cids,
                valid_wids=active_wids,
                strict_fk=strict_fk,
            )
        )
        for i in range(n_orders)
    ]

    return (
        {
            "customers": customers,
            "warehouses": warehouses,
            "orders": orders,
        },
        strict_fk,
    )


@st.composite
def join_shape_strategy(draw: st.DrawFn) -> MultiTableJoinShape:
    use_aliases = draw(st.booleans())
    num_joins = draw(st.sampled_from([1, 2]))
    join_type_1 = draw(st.sampled_from(["INNER", "LEFT"]))
    join_type_2 = draw(st.sampled_from(["INNER", "LEFT"]))

    base_alias = "o" if use_aliases else None
    o_qual = "o" if use_aliases else "orders"
    c_alias = "c" if use_aliases else None
    c_qual = "c" if use_aliases else "customers"
    w_alias = "w" if use_aliases else None
    w_qual = "w" if use_aliases else "warehouses"

    joins = [
        JoinClause(
            right_relation="customers",
            left_qualifier=o_qual,
            left_key="customer_id",
            right_key="id",
            join_type=join_type_1,
            right_alias=c_alias,
        )
    ]
    if num_joins == 2:
        joins.append(
            JoinClause(
                right_relation="warehouses",
                left_qualifier=o_qual,
                left_key="warehouse_id",
                right_key="id",
                join_type=join_type_2,
                right_alias=w_alias,
            )
        )

    is_agg = draw(st.booleans())
    if is_agg:
        agg_fn = draw(st.sampled_from(["sum", "count", "count_star", "avg"]))
        if agg_fn == "count_star":
            agg_col = None
        else:
            agg_col = draw(
                st.sampled_from(
                    [
                        (o_qual, "amount"),
                        (o_qual, "quantity"),
                        (c_qual, "credit_limit"),
                    ]
                )
            )
        avail_groups = [
            (o_qual, "status"),
            (c_qual, "tier"),
            (c_qual, "region"),
        ]
        if num_joins == 2:
            avail_groups.append((w_qual, "country"))
        group_cols = tuple(
            draw(
                st.lists(
                    st.sampled_from(avail_groups),
                    min_size=0,
                    max_size=2,
                    unique=True,
                )
            )
        )
        proj_cols: tuple[tuple[str, str], ...] = ()
    else:
        agg_fn = None
        agg_col = None
        group_cols = ()
        avail_projs = [
            (o_qual, "id"),
            (o_qual, "amount"),
            (c_qual, "tier"),
            (c_qual, "region"),
        ]
        if num_joins == 2:
            avail_projs.append((w_qual, "country"))
        proj_cols = tuple(
            draw(
                st.lists(
                    st.sampled_from(avail_projs),
                    min_size=1,
                    max_size=3,
                    unique=True,
                )
            )
        )

    pred_terms: list[tuple[str, str, str, Any]] = []
    if draw(st.booleans()):
        pred_terms.append(
            (
                o_qual,
                "status",
                draw(st.sampled_from(["=", "!="])),
                draw(st.sampled_from(["paid", "pending"])),
            )
        )
    if draw(st.booleans()):
        pred_terms.append(
            (
                c_qual,
                "tier",
                draw(st.sampled_from(["=", "!="])),
                draw(st.sampled_from(["gold", "silver"])),
            )
        )
    if num_joins == 2 and draw(st.booleans()):
        pred_terms.append(
            (
                w_qual,
                "country",
                "=",
                draw(st.sampled_from(["usa", "de"])),
            )
        )

    sec_cols = frozenset({(c_qual, "region")}) if draw(st.booleans()) else frozenset()

    return MultiTableJoinShape(
        base_relation="orders",
        base_alias=base_alias,
        joins=tuple(joins),
        agg_fn=agg_fn,
        agg_col=agg_col,
        group_cols=group_cols,
        predicate_terms=tuple(pred_terms),
        projection_cols=proj_cols,
        security_cols=sec_cols,
        name="join_shape",
    )


@st.composite
def interleaved_join_stream_strategy(
    draw: st.DrawFn,
) -> tuple[
    MultiTableJoinShape,
    str,
    dict[str, list[dict[str, Any]]],
    list[tuple[str, str, int | None, dict[str, Any], WriteEvent, bool]],
]:
    """
    Generates `(shape, sql, initial_tables, steps)` where each step is
    `(op_kind, target_table, row_idx, new_row, write_event, omit_aux_state)`.
    """
    raw_shape = draw(join_shape_strategy())
    dialect = draw(st.sampled_from(["postgres", "tsql"]))
    use_prefix = draw(st.booleans())
    prefix = ("public." if dialect == "postgres" else "dbo.") if use_prefix else ""
    sql = join_shape_to_sql(raw_shape, schema_prefix=prefix)

    parsed = parse_join_sql(
        sql,
        dialect=dialect,
        name=raw_shape.name,
        security_cols=raw_shape.security_cols,
    )
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    initial_tables, _ = draw(multi_table_db_strategy())
    sim_tables = {k: [dict(r) for r in rows] for k, rows in initial_tables.items()}
    next_order_id = len(sim_tables["orders"]) + 1

    n_steps = draw(st.integers(min_value=1, max_value=6))
    steps: list[tuple[str, str, int | None, dict[str, Any], WriteEvent, bool]] = []

    for s_idx in range(n_steps):
        action = draw(
            st.sampled_from(
                [
                    "fact_insert",
                    "fact_delete",
                    "fact_update_value",
                    "fact_reparent_fk",
                    "fact_update_disjoint",
                    "fact_update_partial_old",
                    "dim_customer_insert",
                    "dim_customer_delete",
                    "dim_customer_update",
                    "dim_customer_disjoint",
                    "dim_customer_partial_old",
                    "dim_warehouse_update",
                    "unrelated_relation",
                ]
            )
        )
        omit_aux = draw(st.sampled_from([False, False, False, True]))

        if action == "unrelated_relation":
            evt = WriteEvent(
                relation="shipments",
                changed_cols={"carrier"},
                old={"id": 1, "carrier": "ups"},
                new={"id": 1, "carrier": "fedex"},
                event_id=f"j_evt_{s_idx}",
            )
            steps.append(("unrelated", "shipments", None, {}, evt, omit_aux))
            continue

        # --- Fact table (`orders`) actions ---
        if action.startswith("fact_"):
            orders_list = sim_tables["orders"]
            if action == "fact_insert" or not orders_list:
                new_o = draw(
                    order_row_strategy(
                        row_id=next_order_id,
                        valid_cids=CUSTOMER_ID_POOL,
                        valid_wids=WAREHOUSE_ID_POOL,
                        strict_fk=False,
                    )
                )
                next_order_id += 1
                evt = WriteEvent(
                    relation="orders",
                    old={},
                    new=dict(new_o),
                    event_id=f"j_evt_{s_idx}",
                )
                orders_list.append(dict(new_o))
                steps.append(("insert", "orders", None, dict(new_o), evt, omit_aux))
                continue

            o_idx = draw(st.integers(0, len(orders_list) - 1))
            old_o = dict(orders_list[o_idx])

            if action == "fact_delete":
                evt = WriteEvent(
                    relation="orders",
                    old=dict(old_o),
                    new={},
                    event_id=f"j_evt_{s_idx}",
                )
                orders_list.pop(o_idx)
                steps.append(("delete", "orders", o_idx, {}, evt, omit_aux))
                continue

            new_o = dict(old_o)
            if action == "fact_update_value":
                new_o["amount"] = draw(DEC_NUMERIC_STRATEGY)
                new_o["quantity"] = draw(DEC_NUMERIC_STRATEGY)
                new_o["status"] = draw(STATUS_STRATEGY)
            elif action == "fact_reparent_fk":
                new_o["customer_id"] = draw(
                    st.sampled_from([None, "orphan_c", *CUSTOMER_ID_POOL])
                )
                new_o["warehouse_id"] = draw(
                    st.sampled_from([None, "orphan_w", *WAREHOUSE_ID_POOL])
                )
            elif action == "fact_update_disjoint":
                new_o["notes"] = (
                    "updated_rush" if old_o["notes"] != "updated_rush" else "normal"
                )
            elif action == "fact_update_partial_old":
                new_o["amount"] = draw(DEC_NUMERIC_STRATEGY)

            old_payload = (
                {"id": old_o["id"]}
                if action == "fact_update_partial_old"
                else dict(old_o)
            )
            evt = WriteEvent(
                relation="orders",
                old=old_payload,
                new=dict(new_o),
                event_id=f"j_evt_{s_idx}",
            )
            orders_list[o_idx] = dict(new_o)
            steps.append(("update", "orders", o_idx, dict(new_o), evt, omit_aux))
            continue

        # --- Dimension table (`customers` or `warehouses`) actions ---
        if action.startswith("dim_customer_"):
            cust_list = sim_tables["customers"]
            existing_cids = {r["id"] for r in cust_list}
            missing_cids = [
                c for c in [*CUSTOMER_ID_POOL, "orphan_c"] if c not in existing_cids
            ]

            if (action == "dim_customer_insert" and missing_cids) or not cust_list:
                new_cid = missing_cids[0] if missing_cids else f"c_extra_{s_idx}"
                new_c = draw(customer_row_strategy(new_cid))
                evt = WriteEvent(
                    relation="customers",
                    old={},
                    new=dict(new_c),
                    event_id=f"j_evt_{s_idx}",
                )
                cust_list.append(dict(new_c))
                steps.append(("insert", "customers", None, dict(new_c), evt, omit_aux))
                continue

            c_idx = draw(st.integers(0, len(cust_list) - 1))
            old_c = dict(cust_list[c_idx])

            if action == "dim_customer_delete":
                evt = WriteEvent(
                    relation="customers",
                    old=dict(old_c),
                    new={},
                    event_id=f"j_evt_{s_idx}",
                )
                cust_list.pop(c_idx)
                steps.append(("delete", "customers", c_idx, {}, evt, omit_aux))
                continue

            new_c = dict(old_c)
            if action == "dim_customer_disjoint":
                new_c["notes"] = (
                    "updated_vip" if old_c["notes"] != "updated_vip" else "standard"
                )
            else:
                new_c["tier"] = draw(TIER_STRATEGY)
                new_c["region"] = draw(REGION_STRATEGY)
                new_c["credit_limit"] = draw(DEC_NUMERIC_STRATEGY)

            old_c_payload = (
                {"id": old_c["id"]}
                if action == "dim_customer_partial_old"
                else dict(old_c)
            )
            evt = WriteEvent(
                relation="customers",
                old=old_c_payload,
                new=dict(new_c),
                event_id=f"j_evt_{s_idx}",
            )
            cust_list[c_idx] = dict(new_c)
            steps.append(("update", "customers", c_idx, dict(new_c), evt, omit_aux))
            continue

        # Warehouse update/insert
        wh_list = sim_tables["warehouses"]
        if not wh_list:
            new_w = draw(warehouse_row_strategy("w1"))
            evt = WriteEvent(
                relation="warehouses",
                old={},
                new=dict(new_w),
                event_id=f"j_evt_{s_idx}",
            )
            wh_list.append(dict(new_w))
            steps.append(("insert", "warehouses", None, dict(new_w), evt, omit_aux))
        else:
            w_idx = draw(st.integers(0, len(wh_list) - 1))
            old_w = dict(wh_list[w_idx])
            new_w = dict(old_w)
            new_w["country"] = draw(COUNTRY_STRATEGY)
            new_w["capacity"] = draw(DEC_NUMERIC_STRATEGY)
            evt = WriteEvent(
                relation="warehouses",
                old=dict(old_w),
                new=dict(new_w),
                event_id=f"j_evt_{s_idx}",
            )
            wh_list[w_idx] = dict(new_w)
            steps.append(("update", "warehouses", w_idx, dict(new_w), evt, omit_aux))

    if prefix:
        initial_tables = {prefix + k: v for k, v in initial_tables.items()}
        steps = [
            (
                kind,
                prefix + table,
                idx,
                row,
                replace(event, relation=prefix + event.relation),
                omit,
            )
            for kind, table, idx, row, event, omit in steps
        ]
    return shape, sql, initial_tables, steps


@settings(
    max_examples=JOIN_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(scenario=interleaved_join_stream_strategy())
def test_join_stream_soundness_against_multi_table_oracle(
    scenario: tuple[
        MultiTableJoinShape,
        str,
        dict[str, list[dict[str, Any]]],
        list[tuple[str, str, int | None, dict[str, Any], WriteEvent, bool]],
    ],
) -> None:
    shape, sql, initial_tables, steps = scenario
    tables = {k: [dict(r) for r in rows] for k, rows in initial_tables.items()}
    cache = rebuild_join_cache(shape, tables)
    aux_state = JoinAuxState.from_tables(shape, tables)

    oracle_before = query_multi_table_oracle(
        shape, tables, schemas=MULTI_TABLE_SCHEMAS, sql=sql
    )
    if shape.agg_fn is not None:
        assert cache.values == oracle_before

    for op_kind, target_tbl, row_idx, new_row, event, omit_aux in steps:
        eff_aux = None if omit_aux else aux_state
        decision = process_join_event(shape, cache, event, aux_state=eff_aux)
        if omit_aux and decision.kind == DecisionKind.INVALIDATE:
            aux_state.is_invalidated = True
        elif omit_aux and decision.kind == DecisionKind.PRESERVE:
            # If preserved without aux_state on a participating table (e.g. disjoint column),
            # apply the row change to aux_state so subsequent steps have the current row
            if target_tbl in tables and op_kind in ("insert", "delete", "update"):
                aux_state.apply_row_mutation(
                    target_tbl,
                    op_kind.upper(),
                    dict(event.old),
                    dict(event.new),
                )

        if op_kind == "insert":
            tables[target_tbl].append(dict(new_row))
        elif op_kind == "delete" and row_idx is not None:
            tables[target_tbl].pop(row_idx)
        elif op_kind == "update" and row_idx is not None:
            tables[target_tbl][row_idx] = dict(new_row)

        oracle_after = query_multi_table_oracle(
            shape, tables, schemas=MULTI_TABLE_SCHEMAS, sql=sql
        )

        if decision.kind == DecisionKind.REPAIR:
            assert shape.agg_fn is not None
            assert not omit_aux
            assert not aux_state.is_invalidated
            assert cache.values == oracle_after
        elif decision.kind == DecisionKind.PRESERVE:
            assert oracle_before == oracle_after
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        elif decision.kind == DecisionKind.INVALIDATE:
            cache = rebuild_join_cache(shape, tables)
            aux_state = JoinAuxState.from_tables(shape, tables)
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        else:
            raise AssertionError(f"Unexpected decision kind: {decision.kind}")

        oracle_before = oracle_after


@settings(
    max_examples=JOIN_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    shape=join_shape_strategy(),
    db_info=multi_table_db_strategy(),
    target_rel=st.sampled_from(["orders", "customers", "warehouses"]),
)
def test_join_evidence_and_aux_state_necessity(
    shape: MultiTableJoinShape,
    db_info: tuple[dict[str, list[dict[str, Any]]], bool],
    target_rel: str,
) -> None:
    if target_rel not in shape.relations():
        return
    tables, _ = db_info
    aux_state = JoinAuxState.from_tables(shape, tables)

    sample_old = {
        "id": "c1" if target_rel != "orders" else 1,
        "customer_id": "c1",
        "warehouse_id": "w1",
        "status": "paid",
        "amount": Decimal("10.00"),
        "quantity": Decimal("1.00"),
        "tier": "silver",
        "region": "us",
        "credit_limit": Decimal("100.00"),
        "country": "usa",
        "capacity": Decimal("50.00"),
        "notes": "n1",
    }
    sample_new = {
        "id": "c1" if target_rel != "orders" else 1,
        "customer_id": "c2",
        "warehouse_id": "w2",
        "status": "pending",
        "amount": Decimal("25.00"),
        "quantity": Decimal("2.00"),
        "tier": "gold",
        "region": "eu",
        "credit_limit": Decimal("200.00"),
        "country": "de",
        "capacity": Decimal("75.00"),
        "notes": "n2",
    }

    # 1. Without JoinAuxState, an UPDATE touching all columns on `target_rel` must NEVER return REPAIR
    evt_full = WriteEvent(relation=target_rel, old=sample_old, new=sample_new)
    dec_no_aux = certify_join_event(shape, evt_full, aux_state=None)
    assert dec_no_aux.kind != DecisionKind.REPAIR

    # 2. Dropping any required evidence column from `old` or `new` must NEVER return REPAIR
    req_cols = shape.table_required_evidence(target_rel)
    for missing_col in req_cols:
        for drop_old, drop_new in [(True, False), (False, True), (True, True)]:
            s_old = (
                {k: v for k, v in sample_old.items() if k != missing_col}
                if drop_old
                else dict(sample_old)
            )
            s_new = (
                {k: v for k, v in sample_new.items() if k != missing_col}
                if drop_new
                else dict(sample_new)
            )
            evt_missing = WriteEvent(relation=target_rel, old=s_old, new=s_new)
            dec_missing = certify_join_event(shape, evt_missing, aux_state=aux_state)
            assert dec_missing.kind != DecisionKind.REPAIR


@settings(
    max_examples=JOIN_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    shape=join_shape_strategy(),
    dialect=st.sampled_from(["postgres", "tsql"]),
    use_schema_prefix=st.booleans(),
)
def test_join_shapes_default_deny_on_all_participating_tables(
    shape: MultiTableJoinShape,
    dialect: str,
    use_schema_prefix: bool,
) -> None:
    prefix = (
        ("public." if dialect == "postgres" else "dbo.") if use_schema_prefix else ""
    )
    sql = join_shape_to_sql(shape, schema_prefix=prefix)

    # Single-table v1 `parse_sql` rejects JOINs; Upgrade 3 `parse_join_sql` parses them
    res_single = parse_sql(sql, dialect=dialect)
    assert not res_single.is_supported

    res_join = parse_join_sql(sql, dialect=dialect)
    assert res_join.is_supported and res_join.shape is not None

    for rel in shape.relations():
        evt = WriteEvent(
            relation=rel,
            changed_cols={"id", "tier", "amount", "country"},
            old={"id": "c1", "tier": "silver", "amount": 10, "country": "usa"},
            new={"id": "c1", "tier": "gold", "amount": 20, "country": "de"},
        )
        dec = check_cdc_evidence(sql, evt)
        assert dec.kind == DecisionKind.UNSUPPORTED


@settings(
    max_examples=JOIN_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    shape=join_shape_strategy(),
    db_info=multi_table_db_strategy(),
)
def test_join_oracle_matches_relational_reference(
    shape: MultiTableJoinShape,
    db_info: tuple[dict[str, list[dict[str, Any]]], bool],
) -> None:
    tables, strict_fk = db_info
    fks = [
        ("orders", "customer_id", "customers", "id"),
        ("orders", "warehouse_id", "warehouses", "id"),
    ]
    oracle_res = query_multi_table_oracle(
        shape,
        tables,
        schemas=MULTI_TABLE_SCHEMAS,
        foreign_keys=fks,
        enforce_fk=strict_fk,
    )
    ref_res = evaluate_join_reference(shape, tables)
    assert oracle_res == ref_res


@settings(
    max_examples=JOIN_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    shape=join_shape_strategy(),
    db_info=multi_table_db_strategy(),
    target_table=st.sampled_from(["orders", "customers", "warehouses"]),
)
def test_join_oracle_cross_table_dependency_and_fingerprint_soundness(
    shape: MultiTableJoinShape,
    db_info: tuple[dict[str, list[dict[str, Any]]], bool],
    target_table: str,
) -> None:
    tables, _ = db_info
    rows = tables.get(target_table, [])
    if not rows:
        return

    oracle_before = query_multi_table_oracle(shape, tables, schemas=MULTI_TABLE_SCHEMAS)

    candidate_mutations: dict[str, Any] = {
        "id": "mutated_id_999",
        "customer_id": "c1" if rows[0].get("customer_id") != "c1" else "c2",
        "warehouse_id": "w1" if rows[0].get("warehouse_id") != "w1" else "w2",
        "status": "paid" if rows[0].get("status") != "paid" else "cancelled",
        "amount": Decimal("777.25"),
        "quantity": Decimal("42.00"),
        "tier": "gold" if rows[0].get("tier") != "gold" else "bronze",
        "region": "eu" if rows[0].get("region") != "eu" else "us",
        "credit_limit": Decimal("999.50"),
        "country": "de" if rows[0].get("country") != "de" else "usa",
        "capacity": Decimal("500.00"),
        "notes": "mutated_disjoint_note",
    }

    for col in MULTI_TABLE_SCHEMAS[target_table]:
        mutated_tables = {t: [dict(r) for r in r_list] for t, r_list in tables.items()}
        mutated_tables[target_table][0][col] = candidate_mutations[col]
        oracle_after = query_multi_table_oracle(
            shape, mutated_tables, schemas=MULTI_TABLE_SCHEMAS
        )
        if oracle_before != oracle_after:
            assert col in shape.table_fingerprint(
                target_table
            ), f"Cross-table dependency witness: mutating {target_table}.{col} changed oracle for {join_shape_to_sql(shape)}, but table_fingerprint({target_table}) is {shape.table_fingerprint(target_table)}"


def test_upgrade3_dimension_fanout_and_fk_reparenting_end_to_end() -> None:
    """
    Deterministic end-to-end verification of Upgrade 3 foreign-key JOIN repair:
    1. 1-to-k dimension fanout (`customers.tier: 'silver' -> 'gold'` repairing
       3 matching `orders` rows in one step).
    2. Zero-multiplicity dimension update (`c_unused` with 0 orders ->
       `PRESERVE zero_join_multiplicity`).
    3. Foreign-key reparenting (`orders.customer_id: 'c1' -> 'c2'` moving
       contribution between dimension groups with `REPAIR foreign_key_reparent`).
    4. `LEFT JOIN` dimension `DELETE` and `INSERT` transitioning referencing
       fact rows into and out of `NULL_GROUP_KEY`.
    """
    sql = (
        "SELECT c.region, SUM(o.amount) "
        "FROM orders AS o "
        "INNER JOIN customers AS c ON o.customer_id = c.id "
        "WHERE o.status = 'paid' AND c.tier = 'gold' "
        "GROUP BY c.region"
    )
    parsed = parse_join_sql(sql)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    tables: dict[str, list[dict[str, Any]]] = {
        "customers": [
            {"id": "c1", "tier": "silver", "region": "us", "notes": "n1"},
            {"id": "c2", "tier": "gold", "region": "eu", "notes": "n2"},
            {"id": "c_unused", "tier": "silver", "region": "apac", "notes": "n3"},
        ],
        "orders": [
            {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("10.50"),
                "notes": "a",
            },
            {
                "id": 2,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("20.25"),
                "notes": "b",
            },
            {
                "id": 3,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("30.00"),
                "notes": "c",
            },
            {
                "id": 4,
                "customer_id": "c2",
                "status": "paid",
                "amount": Decimal("5.00"),
                "notes": "d",
            },
        ],
    }

    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)
    assert aux.join_multiplicity("customers", "c1") == 3
    assert aux.join_multiplicity("customers", "c_unused") == 0
    assert cache.values == {"eu": Decimal("5.00")}

    # Step 1: 1-to-3 dimension fanout update on `c1` (`silver` -> `gold`)
    evt_dim_fanout = WriteEvent(
        relation="customers",
        old={"id": "c1", "tier": "silver", "region": "us", "notes": "n1"},
        new={"id": "c1", "tier": "gold", "region": "us", "notes": "n1"},
    )
    dec1 = process_join_event(shape, cache, evt_dim_fanout, aux)
    tables["customers"][0]["tier"] = "gold"
    assert dec1.kind == DecisionKind.REPAIR
    assert dec1.reason == "dimension_update"
    assert cache.values == query_multi_table_oracle(
        shape, tables, schemas=MULTI_TABLE_SCHEMAS
    )
    assert cache.values == {"us": Decimal("60.75"), "eu": Decimal("5.00")}

    # Step 2: Zero-multiplicity dimension update on `c_unused`
    evt_zero_mult = WriteEvent(
        relation="customers",
        old={"id": "c_unused", "tier": "silver", "region": "apac", "notes": "n3"},
        new={"id": "c_unused", "tier": "gold", "region": "apac", "notes": "n3"},
    )
    dec2 = process_join_event(shape, cache, evt_zero_mult, aux)
    tables["customers"][2]["tier"] = "gold"
    assert dec2.kind == DecisionKind.PRESERVE
    assert dec2.reason == "zero_join_multiplicity"
    assert cache.values == query_multi_table_oracle(
        shape, tables, schemas=MULTI_TABLE_SCHEMAS
    )

    # Step 3: Foreign-key reparenting on Order #1 (`customer_id: 'c1' -> 'c2'`)
    evt_reparent = WriteEvent(
        relation="orders",
        old={
            "id": 1,
            "customer_id": "c1",
            "status": "paid",
            "amount": Decimal("10.50"),
            "notes": "a",
        },
        new={
            "id": 1,
            "customer_id": "c2",
            "status": "paid",
            "amount": Decimal("10.50"),
            "notes": "a",
        },
    )
    dec3 = process_join_event(shape, cache, evt_reparent, aux)
    tables["orders"][0]["customer_id"] = "c2"
    assert dec3.kind == DecisionKind.REPAIR
    assert dec3.reason == "foreign_key_reparent"
    assert aux.join_multiplicity("customers", "c1") == 2
    assert aux.join_multiplicity("customers", "c2") == 2
    assert cache.values == query_multi_table_oracle(
        shape, tables, schemas=MULTI_TABLE_SCHEMAS
    )
    assert cache.values == {"us": Decimal("50.25"), "eu": Decimal("15.50")}

    # Step 4: LEFT JOIN dimension DELETE and INSERT transitioning through NULL_GROUP_KEY
    left_sql = (
        "SELECT c.region, SUM(o.amount) "
        "FROM orders AS o "
        "LEFT JOIN customers AS c ON o.customer_id = c.id "
        "WHERE o.status = 'paid' "
        "GROUP BY c.region"
    )
    left_parsed = parse_join_sql(left_sql)
    assert left_parsed.is_supported and left_parsed.shape is not None
    left_shape = left_parsed.shape
    left_cache = rebuild_join_cache(left_shape, tables)
    left_aux = JoinAuxState.from_tables(left_shape, tables)

    evt_del_c2 = WriteEvent(
        relation="customers",
        old={"id": "c2", "tier": "gold", "region": "eu", "notes": "n2"},
        new={},
    )
    dec_del = process_join_event(left_shape, left_cache, evt_del_c2, left_aux)
    deleted_c2 = tables["customers"].pop(1)
    assert dec_del.kind == DecisionKind.REPAIR
    assert dec_del.reason == "dimension_delete"
    assert left_cache.values == query_multi_table_oracle(
        left_shape, tables, schemas=MULTI_TABLE_SCHEMAS
    )
    assert left_cache.values == {
        "us": Decimal("50.25"),
        NULL_GROUP_KEY: Decimal("15.50"),
    }

    evt_ins_c2 = WriteEvent(
        relation="customers",
        old={},
        new=deleted_c2,
    )
    dec_ins = process_join_event(left_shape, left_cache, evt_ins_c2, left_aux)
    tables["customers"].insert(1, deleted_c2)
    assert dec_ins.kind == DecisionKind.REPAIR
    assert dec_ins.reason == "dimension_insert"
    assert left_cache.values == query_multi_table_oracle(
        left_shape, tables, schemas=MULTI_TABLE_SCHEMAS
    )
    assert left_cache.values == {
        "us": Decimal("50.25"),
        "eu": Decimal("15.50"),
    }


@pytest.mark.parametrize(
    "bad_join_sql",
    [
        "SELECT o.customer_id FROM orders o CROSS JOIN customers c",
        "SELECT o.customer_id FROM orders o RIGHT JOIN customers c ON o.customer_id = c.id",
        "SELECT o.customer_id FROM orders o FULL JOIN customers c ON o.customer_id = c.id",
        "SELECT o.customer_id FROM orders o JOIN customers c USING (customer_id)",
        "SELECT o.customer_id FROM orders o JOIN customers c ON o.amount > c.credit_limit",
        "SELECT customer_id, SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY customer_id",
        "SELECT o.customer_id FROM orders o JOIN customers o ON o.customer_id = o.id",
    ],
)
def test_parse_join_sql_rejects_unsupported_join_constructs(
    bad_join_sql: str,
) -> None:
    res = parse_join_sql(bad_join_sql)
    assert not res.is_supported, f"Expected unsupported for: {bad_join_sql}"


# ---------------------------------------------------------------------------
# Property 5 & Regression Suite: Multi-Table WAL Transactions + JoinAuxState
# Memory-Budget & Cardinality Eviction Bounds
# ---------------------------------------------------------------------------


def _event_to_wal_payload(
    event: WriteEvent,
    *,
    wire_format: str = "debezium",
    tx_id: str | None = None,
    lsn: int = 1000,
) -> dict[str, Any]:
    """Encode a `WriteEvent` on any table into a Debezium or `wal2json` WAL payload."""
    rel = event.relation.split(".")[-1].lower()
    table_cols = MULTI_TABLE_SCHEMAS.get(rel, ("id", "notes"))

    def _enc_dict(d: Mapping[str, Any]) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}

    if wire_format == "debezium":
        fields = []
        for col in table_cols:
            if col in ("amount", "quantity", "credit_limit", "capacity"):
                fields.append(
                    {
                        "field": col,
                        "type": "string",
                        "name": "org.apache.kafka.connect.data.Decimal",
                    }
                )
            elif col == "id" and rel == "orders":
                fields.append({"field": col, "type": "int32"})
            else:
                fields.append({"field": col, "type": "string"})
        schema_block = {
            "type": "struct",
            "fields": [
                {"field": "before", "type": "struct", "fields": fields},
                {"field": "after", "type": "struct", "fields": fields},
            ],
        }
        if event.old and event.new:
            op = "u"
            before: dict[str, Any] | None = _enc_dict(event.old)
            after: dict[str, Any] | None = _enc_dict(event.new)
        elif event.new:
            op = "c"
            before = None
            after = _enc_dict(event.new)
        else:
            op = "d"
            before = _enc_dict(event.old)
            after = None

        payload: dict[str, Any] = {
            "op": op,
            "before": before,
            "after": after,
            "source": {
                "schema": "",
                "table": rel,
                "lsn": lsn,
                "txId": tx_id,
            },
        }
        if tx_id is not None:
            payload["transaction"] = {"id": tx_id}
        return {"schema": schema_block, "payload": payload}

    # wal2json v2 format
    def _enc_w2j_cols(d: Mapping[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for k, v in d.items():
            c_type = (
                "numeric"
                if k in ("amount", "quantity", "credit_limit", "capacity")
                else ("integer" if k == "id" and rel == "orders" else "text")
            )
            out.append(
                {
                    "name": k,
                    "type": c_type,
                    "value": str(v) if isinstance(v, Decimal) else v,
                }
            )
        return out

    if event.old and event.new:
        w2j_msg: dict[str, Any] = {
            "action": "U",
            "schema": "",
            "table": rel,
            "lsn": f"0/{lsn}",
            "identity": _enc_w2j_cols(event.old),
            "columns": _enc_w2j_cols(event.new),
        }
    elif event.new:
        w2j_msg = {
            "action": "I",
            "schema": "",
            "table": rel,
            "lsn": f"0/{lsn}",
            "columns": _enc_w2j_cols(event.new),
        }
    else:
        w2j_msg = {
            "action": "D",
            "schema": "",
            "table": rel,
            "lsn": f"0/{lsn}",
            "identity": _enc_w2j_cols(event.old),
        }
    if tx_id is not None:
        w2j_msg["xid"] = tx_id
    return w2j_msg


@st.composite
def multi_table_wal_tx_scenario_strategy(
    draw: st.DrawFn,
) -> tuple[
    MultiTableJoinShape,
    dict[str, list[dict[str, Any]]],
    list[
        tuple[
            str,
            list[dict[str, Any]],
            list[tuple[str, str, int | None, dict[str, Any]]],
        ]
    ],
    int | None,
    int | None,
    int | None,
]:
    """
    Generates `(shape, initial_tables, tx_blocks, max_total_rows, max_rows_per_table, max_fk_fanout)`
    where each item in `tx_blocks` is `(block_mode, wal_messages, db_mutations)`
    and a single transaction spans interleaved writes across `customers`,
    `warehouses`, and `orders`.
    """
    raw_shape = draw(join_shape_strategy())
    sql = join_shape_to_sql(raw_shape)
    parsed = parse_join_sql(sql)
    assert parsed.is_supported and parsed.shape is not None
    shape = replace(parsed.shape, security_cols=raw_shape.security_cols)

    initial_tables, _ = draw(multi_table_db_strategy())
    sim_tables: dict[str, list[dict[str, Any]]] = {
        rel: [dict(r) for r in rows] for rel, rows in initial_tables.items()
    }
    next_order_id = len(sim_tables["orders"]) + 1
    next_cust_idx = len(sim_tables["customers"]) + 10

    wire_format = draw(st.sampled_from(["debezium", "wal2json"]))
    max_total_rows = draw(st.sampled_from([None, None, 4, 8, 15]))
    max_rows_per_table = draw(st.sampled_from([None, None, 2, 4, 8]))
    max_fk_fanout = draw(st.sampled_from([None, None, 1, 2, 4]))

    n_blocks = draw(st.integers(min_value=1, max_value=4))
    tx_blocks: list[
        tuple[
            str,
            list[dict[str, Any]],
            list[tuple[str, str, int | None, dict[str, Any]]],
        ]
    ] = []

    lsn_counter = 2000
    for b_idx in range(n_blocks):
        tx_id = f"join_tx_{b_idx + 1}"
        block_mode = draw(
            st.sampled_from(
                [
                    "committed_multi_table_tx",
                    "committed_multi_table_tx",
                    "rolled_back_multi_table_tx",
                    "bad_event_count_tx",
                    "autocommit_single",
                ]
            )
        )
        n_steps = (
            1
            if block_mode == "autocommit_single"
            else draw(st.integers(min_value=2, max_value=4))
        )
        tx_tables: dict[str, list[dict[str, Any]]] = {
            rel: [dict(r) for r in rows] for rel, rows in sim_tables.items()
        }
        step_events: list[WriteEvent] = []
        db_mutations: list[tuple[str, str, int | None, dict[str, Any]]] = []

        for s_idx in range(n_steps):
            # Explicitly alternate between dimension and fact tables in multi-table txs
            if s_idx == 0 and n_steps >= 2:
                target_choice = "customers"
            elif s_idx == 1 and n_steps >= 2:
                target_choice = "orders"
            else:
                target_choice = draw(
                    st.sampled_from(["orders", "customers", "warehouses"])
                )

            if target_choice == "orders":
                op_kind = draw(
                    st.sampled_from(
                        [
                            "fact_insert",
                            "fact_delete",
                            "fact_update_val",
                            "fact_reparent_fk",
                            "fact_partial_old",
                        ]
                    )
                )
                if op_kind == "fact_insert" or not tx_tables["orders"]:
                    new_row = draw(
                        order_row_strategy(
                            next_order_id,
                            CUSTOMER_ID_POOL,
                            WAREHOUSE_ID_POOL,
                            False,
                        )
                    )
                    next_order_id += 1
                    tx_tables["orders"].append(dict(new_row))
                    step_events.append(
                        WriteEvent(relation="orders", old={}, new=dict(new_row))
                    )
                    db_mutations.append(("orders", "insert", None, dict(new_row)))
                elif op_kind == "fact_delete":
                    idx = draw(
                        st.integers(min_value=0, max_value=len(tx_tables["orders"]) - 1)
                    )
                    old_row = dict(tx_tables["orders"].pop(idx))
                    step_events.append(
                        WriteEvent(relation="orders", old=old_row, new={})
                    )
                    db_mutations.append(("orders", "delete", idx, {}))
                elif op_kind == "fact_reparent_fk":
                    idx = draw(
                        st.integers(min_value=0, max_value=len(tx_tables["orders"]) - 1)
                    )
                    old_row = dict(tx_tables["orders"][idx])
                    new_row = dict(old_row)
                    new_row["customer_id"] = draw(
                        st.one_of(st.none(), st.sampled_from(CUSTOMER_ID_POOL))
                    )
                    tx_tables["orders"][idx] = dict(new_row)
                    step_events.append(
                        WriteEvent(relation="orders", old=old_row, new=dict(new_row))
                    )
                    db_mutations.append(("orders", "update", idx, dict(new_row)))
                elif op_kind == "fact_partial_old":
                    idx = draw(
                        st.integers(min_value=0, max_value=len(tx_tables["orders"]) - 1)
                    )
                    old_row = dict(tx_tables["orders"][idx])
                    new_row = dict(old_row)
                    new_row["amount"] = draw(DEC_NUMERIC_STRATEGY)
                    tx_tables["orders"][idx] = dict(new_row)
                    partial_old = {"id": old_row["id"]}
                    step_events.append(
                        WriteEvent(
                            relation="orders",
                            old=partial_old,
                            new=dict(new_row),
                        )
                    )
                    db_mutations.append(("orders", "update", idx, dict(new_row)))
                else:
                    idx = draw(
                        st.integers(min_value=0, max_value=len(tx_tables["orders"]) - 1)
                    )
                    old_row = dict(tx_tables["orders"][idx])
                    new_row = dict(old_row)
                    new_row["amount"] = draw(DEC_NUMERIC_STRATEGY)
                    new_row["status"] = draw(STATUS_STRATEGY)
                    tx_tables["orders"][idx] = dict(new_row)
                    step_events.append(
                        WriteEvent(relation="orders", old=old_row, new=dict(new_row))
                    )
                    db_mutations.append(("orders", "update", idx, dict(new_row)))

            elif target_choice == "customers":
                dim_op = draw(
                    st.sampled_from(["dim_update", "dim_insert", "dim_delete"])
                )
                if dim_op == "dim_insert" or not tx_tables["customers"]:
                    new_cid = f"c_tx_{next_cust_idx}"
                    next_cust_idx += 1
                    new_c_row = draw(customer_row_strategy(new_cid))
                    tx_tables["customers"].append(dict(new_c_row))
                    step_events.append(
                        WriteEvent(
                            relation="customers",
                            old={},
                            new=dict(new_c_row),
                        )
                    )
                    db_mutations.append(("customers", "insert", None, dict(new_c_row)))
                elif dim_op == "dim_delete":
                    idx = draw(
                        st.integers(
                            min_value=0, max_value=len(tx_tables["customers"]) - 1
                        )
                    )
                    old_c_row = dict(tx_tables["customers"].pop(idx))
                    step_events.append(
                        WriteEvent(relation="customers", old=old_c_row, new={})
                    )
                    db_mutations.append(("customers", "delete", idx, {}))
                else:
                    idx = draw(
                        st.integers(
                            min_value=0, max_value=len(tx_tables["customers"]) - 1
                        )
                    )
                    old_c_row = dict(tx_tables["customers"][idx])
                    new_c_row = dict(old_c_row)
                    new_c_row["tier"] = draw(TIER_STRATEGY)
                    new_c_row["region"] = draw(REGION_STRATEGY)
                    tx_tables["customers"][idx] = dict(new_c_row)
                    step_events.append(
                        WriteEvent(
                            relation="customers",
                            old=old_c_row,
                            new=dict(new_c_row),
                        )
                    )
                    db_mutations.append(("customers", "update", idx, dict(new_c_row)))
            else:
                # warehouses insert or update
                if not tx_tables["warehouses"]:
                    new_wid = f"w_tx_{next_cust_idx}"
                    next_cust_idx += 1
                    new_w_row = draw(warehouse_row_strategy(new_wid))
                    tx_tables["warehouses"].append(dict(new_w_row))
                    step_events.append(
                        WriteEvent(
                            relation="warehouses",
                            old={},
                            new=dict(new_w_row),
                        )
                    )
                    db_mutations.append(("warehouses", "insert", None, dict(new_w_row)))
                else:
                    idx = draw(
                        st.integers(
                            min_value=0, max_value=len(tx_tables["warehouses"]) - 1
                        )
                    )
                    old_w_row = dict(tx_tables["warehouses"][idx])
                    new_w_row = dict(old_w_row)
                    new_w_row["country"] = draw(COUNTRY_STRATEGY)
                    tx_tables["warehouses"][idx] = dict(new_w_row)
                    step_events.append(
                        WriteEvent(
                            relation="warehouses",
                            old=old_w_row,
                            new=dict(new_w_row),
                        )
                    )
                    db_mutations.append(("warehouses", "update", idx, dict(new_w_row)))

        if block_mode == "autocommit_single":
            lsn_counter += 1
            msg = _event_to_wal_payload(
                step_events[0],
                wire_format=wire_format,
                tx_id=None,
                lsn=lsn_counter,
            )
            sim_tables = tx_tables
            tx_blocks.append((block_mode, [msg], db_mutations))
            continue

        # Build BEGIN + data messages + COMMIT/ROLLBACK
        block_msgs: list[dict[str, Any]] = []
        lsn_counter += 1
        if wire_format == "debezium":
            block_msgs.append(
                {
                    "payload": {
                        "status": "BEGIN",
                        "id": tx_id,
                        "ts_ms": 1700000000000 + lsn_counter,
                    }
                }
            )
        else:
            block_msgs.append({"action": "B", "xid": tx_id, "lsn": f"0/{lsn_counter}"})

        for ev in step_events:
            lsn_counter += 1
            block_msgs.append(
                _event_to_wal_payload(
                    ev,
                    wire_format=wire_format,
                    tx_id=tx_id,
                    lsn=lsn_counter,
                )
            )

        lsn_counter += 1
        if block_mode == "committed_multi_table_tx":
            if wire_format == "debezium":
                block_msgs.append(
                    {
                        "payload": {
                            "status": "END",
                            "id": tx_id,
                            "event_count": len(step_events),
                        }
                    }
                )
            else:
                block_msgs.append(
                    {"action": "C", "xid": tx_id, "lsn": f"0/{lsn_counter}"}
                )
            sim_tables = tx_tables
            tx_blocks.append((block_mode, block_msgs, db_mutations))
        elif block_mode == "rolled_back_multi_table_tx":
            if wire_format == "debezium":
                block_msgs.append({"payload": {"status": "ROLLBACK", "id": tx_id}})
            else:
                block_msgs.append(
                    {"action": "R", "xid": tx_id, "lsn": f"0/{lsn_counter}"}
                )
            # Do NOT update sim_tables or db_mutations on rollback
            tx_blocks.append((block_mode, block_msgs, []))
        else:
            # bad_event_count_tx
            block_msgs.append(
                {
                    "payload": {
                        "status": "END",
                        "id": tx_id,
                        "event_count": len(step_events) + 5,
                    }
                }
            )
            sim_tables = tx_tables
            tx_blocks.append((block_mode, block_msgs, db_mutations))

    return (
        shape,
        initial_tables,
        tx_blocks,
        max_total_rows,
        max_rows_per_table,
        max_fk_fanout,
    )


@settings(
    max_examples=max(50, JOIN_MAX_EXAMPLES // 4),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(scenario=multi_table_wal_tx_scenario_strategy())
def test_multi_table_wal_join_transactions_soundness(
    scenario: tuple[
        MultiTableJoinShape,
        dict[str, list[dict[str, Any]]],
        list[
            tuple[
                str,
                list[dict[str, Any]],
                list[tuple[str, str, int | None, dict[str, Any]]],
            ]
        ],
        int | None,
        int | None,
        int | None,
    ],
) -> None:
    """
    Property 5 (Multi-Table WAL Transactions + Bounded JoinAuxState Soundness):
    When `WalStreamProcessor` is combined with `MultiTableJoinShape` and
    `JoinAuxState` (including optional memory-budget / cardinality eviction bounds):
    1. Multi-table `BEGIN ... ROLLBACK` blocks discard buffered writes to both
       `customers` and `orders` without mutating `cache` or `aux_state`.
    2. Multi-table `BEGIN ... COMMIT` blocks apply buffered writes in order across
       `customers`, `warehouses`, and `orders` (or rebuild once at commit if any
       record invalidates or hits an evicted key), converging to exact agreement
       with `query_multi_table_oracle`.
    """
    (
        shape,
        initial_tables,
        tx_blocks,
        max_total_rows,
        max_rows_per_table,
        max_fk_fanout,
    ) = scenario
    live_tables: dict[str, list[dict[str, Any]]] = {
        rel: [dict(r) for r in rows] for rel, rows in initial_tables.items()
    }

    def _build_fresh() -> tuple[AggregateCache, JoinAuxState]:
        return (
            rebuild_join_cache(shape, live_tables),
            JoinAuxState.from_tables(
                shape,
                live_tables,
                max_total_rows=max_total_rows,
                max_rows_per_table=max_rows_per_table,
                max_fk_fanout=max_fk_fanout,
            ),
        )

    cache, aux = _build_fresh()
    proc = WalStreamProcessor(shape, aux_state=aux)

    for block_mode, wal_msgs, db_mutations in tx_blocks:
        oracle_before = query_multi_table_oracle(
            shape, live_tables, schemas=MULTI_TABLE_SCHEMAS
        )
        # Apply ground-truth mutations for committed blocks before COMMIT fires
        for rel, op, idx, row_data in db_mutations:
            if op == "insert":
                live_tables[rel].append(dict(row_data))
            elif op == "delete":
                assert idx is not None
                live_tables[rel].pop(idx)
            elif op == "update":
                assert idx is not None
                live_tables[rel][idx] = dict(row_data)

        oracle_after = query_multi_table_oracle(
            shape, live_tables, schemas=MULTI_TABLE_SCHEMAS
        )

        block_decisions = []
        for msg in wal_msgs:
            decs, cache = proc.ingest(msg, cache, on_invalidate=_build_fresh)
            block_decisions.extend(decs)

        if block_mode == "rolled_back_multi_table_tx":
            assert len(block_decisions) == 1
            assert block_decisions[0].kind == DecisionKind.PRESERVE
            assert block_decisions[0].reason == "transaction_rollback"
            assert oracle_before == oracle_after
            if shape.agg_fn is not None:
                assert cache.values == oracle_before
                assert cache.values == oracle_after
        elif block_mode == "bad_event_count_tx":
            assert any(
                d.kind == DecisionKind.INVALIDATE
                and d.reason == "transaction_boundary_violation"
                for d in block_decisions
            )
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        else:
            if shape.agg_fn is None:
                assert all(d.kind != DecisionKind.REPAIR for d in block_decisions)
                if all(d.kind == DecisionKind.PRESERVE for d in block_decisions):
                    assert oracle_before == oracle_after
            else:
                assert cache.values == oracle_after, (
                    f"Multi-table WAL divergence on {block_mode} for shape {shape}: "
                    f"cache={cache.values} != oracle={oracle_after}; "
                    f"decisions={[(d.kind.value, d.reason) for d in block_decisions]}"
                )


def test_multi_table_wal_transaction_commit_and_rollback() -> None:
    """
    Deterministic regression test verifying `WalStreamProcessor` + `MultiTableJoinShape`
    + `JoinAuxState` on multi-table transactions spanning `customers` and `orders`:
    1. A multi-table transaction that inserts Customer `c_new` and Order #100
       referencing `c_new` and then rolls back leaves `cache` and `aux_state`
       completely unchanged.
    2. A multi-table transaction that inserts Customer `c_new` (`region='apac'`)
       and Order #100 (`customer_id='c_new'`, `amount=40.00`), and updates
       Customer `c1`'s region (`'us' -> 'latam'`), commits atomically and
       repairs the join cache to match `query_multi_table_oracle`.
    """
    sql = (
        "SELECT c.region, SUM(o.amount) "
        "FROM orders AS o "
        "INNER JOIN customers AS c ON o.customer_id = c.id "
        "WHERE o.status = 'paid' AND c.tier = 'gold' "
        "GROUP BY c.region"
    )
    parsed = parse_join_sql(sql)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    tables: dict[str, list[dict[str, Any]]] = {
        "customers": [
            {"id": "c1", "tier": "gold", "region": "us", "notes": "n1"},
            {"id": "c2", "tier": "gold", "region": "eu", "notes": "n2"},
        ],
        "orders": [
            {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("25.00"),
                "notes": "o1",
            },
            {
                "id": 2,
                "customer_id": "c2",
                "status": "paid",
                "amount": Decimal("15.00"),
                "notes": "o2",
            },
        ],
    }

    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)
    proc = WalStreamProcessor(shape, aux_state=aux)

    assert cache.values == {"us": Decimal("25.00"), "eu": Decimal("15.00")}

    # 1. Multi-table transaction that rolls back
    decs, cache = proc.ingest({"action": "B", "xid": "tx_rb", "lsn": "0/10"}, cache)
    assert decs == []
    decs, cache = proc.ingest(
        _event_to_wal_payload(
            WriteEvent(
                relation="customers",
                old={},
                new={"id": "c_new", "tier": "gold", "region": "apac", "notes": ""},
            ),
            wire_format="wal2json",
            tx_id="tx_rb",
            lsn=11,
        ),
        cache,
    )
    assert decs == []
    decs, cache = proc.ingest(
        _event_to_wal_payload(
            WriteEvent(
                relation="orders",
                old={},
                new={
                    "id": 100,
                    "customer_id": "c_new",
                    "status": "paid",
                    "amount": Decimal("99.00"),
                    "notes": "",
                },
            ),
            wire_format="wal2json",
            tx_id="tx_rb",
            lsn=12,
        ),
        cache,
    )
    assert decs == []
    # Rollback discards both buffered events
    rb_decs, cache = proc.ingest({"action": "R", "xid": "tx_rb", "lsn": "0/13"}, cache)
    assert len(rb_decs) == 1
    assert rb_decs[0].kind == DecisionKind.PRESERVE
    assert rb_decs[0].reason == "transaction_rollback"
    assert cache.values == {"us": Decimal("25.00"), "eu": Decimal("15.00")}
    assert "c_new" not in aux.table_rows["customers"]
    assert 100 not in aux.table_rows["orders"]

    # 2. Multi-table transaction that commits across both `customers` and `orders`
    c_new_row = {"id": "c_new", "tier": "gold", "region": "apac", "notes": ""}
    o_100_row = {
        "id": 100,
        "customer_id": "c_new",
        "status": "paid",
        "amount": Decimal("40.00"),
        "notes": "",
    }
    c1_old = dict(tables["customers"][0])
    c1_new = {"id": "c1", "tier": "gold", "region": "latam", "notes": "n1"}

    decs, cache = proc.ingest(
        {"payload": {"status": "BEGIN", "id": "tx_commit", "ts_ms": 100}},
        cache,
    )
    assert decs == []
    decs, cache = proc.ingest(
        _event_to_wal_payload(
            WriteEvent(relation="customers", old={}, new=c_new_row),
            wire_format="debezium",
            tx_id="tx_commit",
            lsn=21,
        ),
        cache,
    )
    assert decs == []
    decs, cache = proc.ingest(
        _event_to_wal_payload(
            WriteEvent(relation="orders", old={}, new=o_100_row),
            wire_format="debezium",
            tx_id="tx_commit",
            lsn=22,
        ),
        cache,
    )
    assert decs == []
    decs, cache = proc.ingest(
        _event_to_wal_payload(
            WriteEvent(relation="customers", old=c1_old, new=c1_new),
            wire_format="debezium",
            tx_id="tx_commit",
            lsn=23,
        ),
        cache,
    )
    assert decs == []

    # Apply ground-truth changes to `tables` and commit
    tables["customers"].append(c_new_row)
    tables["orders"].append(o_100_row)
    tables["customers"][0] = c1_new

    commit_decs, cache = proc.ingest(
        {"payload": {"status": "END", "id": "tx_commit", "event_count": 3}},
        cache,
    )
    assert len(commit_decs) == 3
    assert [d.kind for d in commit_decs] == [
        DecisionKind.PRESERVE,  # c_new has 0 orders at the moment of its insert
        DecisionKind.REPAIR,  # o_100 joins with c_new -> +40.00 to 'apac'
        DecisionKind.REPAIR,  # c1 region 'us' -> 'latam' moves o_1's 25.00
    ]
    expected_oracle = query_multi_table_oracle(
        shape, tables, schemas=MULTI_TABLE_SCHEMAS
    )
    assert cache.values == expected_oracle
    assert cache.values == {
        "eu": Decimal("15.00"),
        "apac": Decimal("40.00"),
        "latam": Decimal("25.00"),
    }


def test_join_aux_state_memory_and_cardinality_eviction_downgrades_to_invalidate() -> (
    None
):
    """
    Deterministic regression test verifying `JoinAuxState` memory-budget and
    cardinality bounds (`max_rows_per_table`, `max_total_rows`, `max_fk_fanout`,
    `evict_row`, `evict_fk_bucket`):
    1. Explicit `evict_row("customers", "c1")` marks `c1` as evicted; a dimension
       update on `c1` OR a fact insert referencing `c1` downgrades to
       `Decision.invalidate("evicted_join_auxiliary_key")`, while updates on
       non-evicted `c2` still repair accurately.
    2. Explicit or automatic eviction of a fact row (`evict_row("orders", 1)`)
       marks both Order #1 and its FK bucket `('orders', 'customer_id') -> 'c1'`
       as evicted so a dimension update on `c1` cannot under-repair using an
       incomplete FK child set, downgrading instead to
       `Decision.invalidate("evicted_join_auxiliary_key")`.
    3. `max_fk_fanout=1` evicts the FK bucket for `c1` when a second order for
       `c1` arrives, downgrading dimension fanout updates on `c1` to
       `Decision.invalidate("evicted_join_auxiliary_key")`.
    4. `max_rows_per_table=2` automatically evicts the oldest row when a 3rd
       row is inserted, and touching the evicted key downgrades to
       `Decision.invalidate("evicted_join_auxiliary_key")`.
    """
    sql = (
        "SELECT c.region, SUM(o.amount) "
        "FROM orders AS o "
        "INNER JOIN customers AS c ON o.customer_id = c.id "
        "WHERE o.status = 'paid' "
        "GROUP BY c.region"
    )
    parsed = parse_join_sql(sql)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    tables: dict[str, list[dict[str, Any]]] = {
        "customers": [
            {"id": "c1", "tier": "gold", "region": "us", "notes": "n1"},
            {"id": "c2", "tier": "gold", "region": "eu", "notes": "n2"},
        ],
        "orders": [
            {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("10.00"),
                "notes": "o1",
            },
            {
                "id": 2,
                "customer_id": "c2",
                "status": "paid",
                "amount": Decimal("20.00"),
                "notes": "o2",
            },
        ],
    }

    # Case 1: Evicting dimension key `c1` downgrades both dimension writes on `c1`
    # and fact writes referencing `c1`, while `c2` continues to repair.
    cache1 = rebuild_join_cache(shape, tables)
    aux1 = JoinAuxState.from_tables(shape, tables)
    assert aux1.evict_row("customers", "c1") is True

    # Non-evicted `c2` update still repairs
    evt_c2 = WriteEvent(
        relation="customers",
        old={"id": "c2", "tier": "gold", "region": "eu", "notes": "n2"},
        new={"id": "c2", "tier": "gold", "region": "uk", "notes": "n2"},
    )
    dec_c2 = process_join_event(shape, cache1, evt_c2, aux1)
    assert dec_c2.kind == DecisionKind.REPAIR
    assert cache1.values == {"us": Decimal("10.00"), "uk": Decimal("20.00")}

    # Dimension update on evicted `c1` downgrades to INVALIDATE
    evt_c1 = WriteEvent(
        relation="customers",
        old={"id": "c1", "tier": "gold", "region": "us", "notes": "n1"},
        new={"id": "c1", "tier": "gold", "region": "ca", "notes": "n1"},
    )
    dec_c1 = certify_join_event(shape, evt_c1, aux1)
    assert dec_c1.kind == DecisionKind.INVALIDATE
    assert dec_c1.reason == "evicted_join_auxiliary_key"

    # Fact insert referencing evicted `c1` also downgrades to INVALIDATE
    evt_o_ref_c1 = WriteEvent(
        relation="orders",
        old={},
        new={
            "id": 99,
            "customer_id": "c1",
            "status": "paid",
            "amount": Decimal("5.00"),
            "notes": "o99",
        },
    )
    dec_o_ref_c1 = certify_join_event(shape, evt_o_ref_c1, aux1)
    assert dec_o_ref_c1.kind == DecisionKind.INVALIDATE
    assert dec_o_ref_c1.reason == "evicted_join_auxiliary_key"

    # Case 2: Evicting fact row `orders.id = 1` poisons the FK bucket for `c1`
    aux2 = JoinAuxState.from_tables(shape, tables)
    assert aux2.evict_row("orders", 1) is True
    assert "c1" in aux2.evicted_fk_buckets[("orders", "customer_id")]
    dec_dim_after_fact_evict = certify_join_event(shape, evt_c1, aux2)
    assert dec_dim_after_fact_evict.kind == DecisionKind.INVALIDATE
    assert dec_dim_after_fact_evict.reason == "evicted_join_auxiliary_key"

    # Case 3: `max_fk_fanout=1` evicts the FK bucket when a 2nd child row for `c1` is indexed
    tables_fanout = {
        "customers": list(tables["customers"]),
        "orders": [
            dict(tables["orders"][0]),
            {
                "id": 3,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("15.00"),
                "notes": "o3",
            },
        ],
    }
    aux3 = JoinAuxState.from_tables(shape, tables_fanout, max_fk_fanout=1)
    assert "c1" in aux3.evicted_fk_buckets[("orders", "customer_id")]
    dec_fanout = certify_join_event(shape, evt_c1, aux3)
    assert dec_fanout.kind == DecisionKind.INVALIDATE
    assert dec_fanout.reason == "evicted_join_auxiliary_key"

    # Case 4: `max_rows_per_table=2` evicts the oldest customer (`c1`) when `c3` is inserted
    aux4 = JoinAuxState.from_tables(shape, tables, max_rows_per_table=2)
    assert len(aux4.table_rows["customers"]) == 2
    aux4.apply_row_mutation(
        "customers",
        "INSERT",
        {},
        {"id": "c3", "tier": "gold", "region": "apac", "notes": "n3"},
    )
    assert len(aux4.table_rows["customers"]) == 2
    assert "c1" in aux4.evicted_pks["customers"]
    dec_evicted_oldest = certify_join_event(shape, evt_c1, aux4)
    assert dec_evicted_oldest.kind == DecisionKind.INVALIDATE
    assert dec_evicted_oldest.reason == "evicted_join_auxiliary_key"


def test_snowflake_multi_hop_join_chain_against_oracle() -> None:
    """
    Verify snowflaked 3-table multi-hop join chain (`orders -> customers -> regions`)
    against `query_multi_table_oracle` and `evaluate_join_reference` across:
    1. Root dimension (`regions`) group-key (`macro_region`) update fanning out
       2 hops through `customers` to `orders`.
    2. Root dimension (`regions`) predicate (`active`) exit and entry.
    3. Intermediate dimension (`customers`) foreign-key reparenting (`region_id`)
       moving all orders of `c1` from one region to another.
    4. Eviction of an intermediate `customers` row or root `regions` row
       downgrading subsequent dependent updates to `invalidate("evicted_join_auxiliary_key")`.
    """
    from smplcache import SNOWFLAKE_BENCHMARK_SQL, SNOWFLAKE_SCHEMAS

    parsed = parse_join_sql(SNOWFLAKE_BENCHMARK_SQL)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape
    assert shape.relations() == frozenset({"orders", "customers", "regions"})

    tables: dict[str, list[dict[str, Any]]] = {
        "regions": [
            {"id": "r1", "macro_region": "americas", "active": 1, "notes": "n1"},
            {"id": "r2", "macro_region": "emea", "active": 1, "notes": "n2"},
            {"id": "r3", "macro_region": "apac", "active": 0, "notes": "n3"},
        ],
        "customers": [
            {"id": "c1", "region_id": "r1", "tier": "gold", "notes": "cn1"},
            {"id": "c2", "region_id": "r1", "tier": "silver", "notes": "cn2"},
            {"id": "c3", "region_id": "r2", "tier": "gold", "notes": "cn3"},
        ],
        "orders": [
            {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("10.00"),
                "notes": "o1",
            },
            {
                "id": 2,
                "customer_id": "c1",
                "status": "paid",
                "amount": Decimal("20.00"),
                "notes": "o2",
            },
            {
                "id": 3,
                "customer_id": "c2",
                "status": "paid",
                "amount": Decimal("30.00"),
                "notes": "o3",
            },
            {
                "id": 4,
                "customer_id": "c3",
                "status": "paid",
                "amount": Decimal("15.00"),
                "notes": "o4",
            },
        ],
    }
    fks = (
        ("customers", "region_id", "regions", "id"),
        ("orders", "customer_id", "customers", "id"),
    )

    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)

    oracle_init = query_multi_table_oracle(
        shape,
        tables,
        schemas=SNOWFLAKE_SCHEMAS,
        foreign_keys=fks,
        enforce_fk=True,
    )
    assert cache.values == oracle_init
    assert cache.values == evaluate_join_reference(shape, tables)
    assert cache.values == {
        "americas": Decimal("60.00"),
        "emea": Decimal("15.00"),
    }
    # 2-hop join multiplicity of `r1` is 3 orders (2 via `c1`, 1 via `c2`)
    assert aux.join_multiplicity("regions", "r1") == 3
    assert aux.join_multiplicity("regions", "r2") == 1
    assert aux.join_multiplicity("regions", "r3") == 0

    # 1. Intermediate dimension (`customers`) FK reparenting: `c1.region_id: 'r1' -> 'r2'`
    evt_reparent_c1 = WriteEvent(
        relation="customers",
        old={"id": "c1", "region_id": "r1", "tier": "gold", "notes": "cn1"},
        new={"id": "c1", "region_id": "r2", "tier": "gold", "notes": "cn1"},
    )
    dec1 = process_join_event(shape, cache, evt_reparent_c1, aux)
    tables["customers"][0]["region_id"] = "r2"
    assert dec1.kind == DecisionKind.REPAIR
    assert cache.values == query_multi_table_oracle(
        shape, tables, schemas=SNOWFLAKE_SCHEMAS, foreign_keys=fks, enforce_fk=True
    )
    assert cache.values == {
        "americas": Decimal("30.00"),
        "emea": Decimal("45.00"),
    }
    assert aux.join_multiplicity("regions", "r1") == 1
    assert aux.join_multiplicity("regions", "r2") == 3

    # 2. Root dimension (`regions`) 2-hop group move: `r2.macro_region: 'emea' -> 'eu_west'`
    evt_reg_move = WriteEvent(
        relation="regions",
        old={"id": "r2", "macro_region": "emea", "active": 1, "notes": "n2"},
        new={"id": "r2", "macro_region": "eu_west", "active": 1, "notes": "n2"},
    )
    dec2 = process_join_event(shape, cache, evt_reg_move, aux)
    tables["regions"][1]["macro_region"] = "eu_west"
    assert dec2.kind == DecisionKind.REPAIR
    assert cache.values == query_multi_table_oracle(
        shape, tables, schemas=SNOWFLAKE_SCHEMAS, foreign_keys=fks, enforce_fk=True
    )
    assert cache.values == {
        "americas": Decimal("30.00"),
        "eu_west": Decimal("45.00"),
    }

    # 3. Root dimension (`regions`) predicate exit: `r1.active: 1 -> 0`
    evt_reg_deact = WriteEvent(
        relation="regions",
        old={"id": "r1", "macro_region": "americas", "active": 1, "notes": "n1"},
        new={"id": "r1", "macro_region": "americas", "active": 0, "notes": "n1"},
    )
    dec3 = process_join_event(shape, cache, evt_reg_deact, aux)
    tables["regions"][0]["active"] = 0
    assert dec3.kind == DecisionKind.REPAIR
    assert cache.values == query_multi_table_oracle(
        shape, tables, schemas=SNOWFLAKE_SCHEMAS, foreign_keys=fks, enforce_fk=True
    )
    assert cache.values == {"eu_west": Decimal("45.00")}

    # 4. Evicting intermediate customer `c1` poisons `r2`'s FK bucket in `('customers', 'region_id')`
    assert aux.evict_row("customers", "c1") is True
    evt_r2_touch = WriteEvent(
        relation="regions",
        old={"id": "r2", "macro_region": "eu_west", "active": 1, "notes": "n2"},
        new={"id": "r2", "macro_region": "emea", "active": 1, "notes": "n2"},
    )
    dec_evict = certify_join_event(shape, evt_r2_touch, aux)
    assert dec_evict.kind == DecisionKind.INVALIDATE
    assert dec_evict.reason == "evicted_join_auxiliary_key"


def test_debezium_zipf_snowflake_benchmark_and_cli_ingest(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    End-to-end test for `run_debezium_ingest_benchmark` and the CLI
    `smplcache ingest --from debezium` command across memory budgets
    (`max_total_rows` and `max_fk_fanout`) and Zipfian foreign-key skew:
    1. Every budget configuration maintains `oracle_verified is True`.
    2. `peak_aux_rows <= max_total_rows` and `peak_fk_bucket_size <= max_fk_fanout`.
    3. Unbounded memory `(None, None)` achieves 0% invalidations, while tighter
       budgets monotonically increase `eviction_invalidate_count` and
       `invalidation_rate`.
    4. Under a tight `max_fk_fanout=2` cap, high Zipfian skew (`zipf_s=1.8`)
       triggers at least as many eviction invalidations as uniform skew (`zipf_s=0.0`).
    5. `smplcache.cli.main(["ingest", "--from", "debezium", ...])` works in both
       `--benchmark` mode and file-stream ingestion mode.
    """
    import json
    from smplcache import (
        generate_zipf_snowflake_debezium_stream,
        run_debezium_ingest_benchmark,
    )
    from smplcache.cli import main as cli_main

    budgets: list[tuple[int | None, int | None]] = [
        (None, None),
        (80, 16),
        (50, 8),
        (30, 4),
        (15, 2),
    ]
    results = run_debezium_ingest_benchmark(
        zipf_s=1.2,
        budgets=budgets,
        num_transactions=40,
        seed=42,
        verify_oracle=True,
    )
    assert len(results) == 5

    # Unbounded run has 0 invalidations
    assert results[0].max_total_rows is None
    assert results[0].max_fk_fanout is None
    assert results[0].invalidate_count == 0
    assert results[0].eviction_invalidate_count == 0
    assert results[0].invalidation_rate == 0.0
    assert results[0].oracle_verified is True

    # Monotonic non-decreasing invalidation rate and respected memory caps
    prev_inv_rate = -1.0
    prev_evict_cnt = -1
    for r in results:
        assert r.oracle_verified is True
        if r.max_total_rows is not None:
            assert r.peak_aux_rows <= r.max_total_rows
        if r.max_fk_fanout is not None:
            assert r.peak_fk_bucket_size <= r.max_fk_fanout
        assert r.invalidation_rate >= prev_inv_rate
        assert r.eviction_invalidate_count >= prev_evict_cnt
        prev_inv_rate = r.invalidation_rate
        prev_evict_cnt = r.eviction_invalidate_count

    assert results[-1].invalidation_rate > 0.0
    assert results[-1].eviction_invalidate_count > 0

    # Compare uniform (zipf_s=0.0) vs highly skewed (zipf_s=1.8) under tight fanout cap
    uniform_res = run_debezium_ingest_benchmark(
        zipf_s=0.0,
        budgets=[(None, 2)],
        num_transactions=40,
        seed=7,
    )[0]
    skewed_res = run_debezium_ingest_benchmark(
        zipf_s=1.8,
        budgets=[(None, 2)],
        num_transactions=40,
        seed=7,
    )[0]
    assert uniform_res.oracle_verified and skewed_res.oracle_verified
    assert skewed_res.eviction_invalidate_count >= uniform_res.eviction_invalidate_count

    # CLI `--benchmark --format json`
    capsys.readouterr()
    cli_main(
        [
            "ingest",
            "--from",
            "debezium",
            "--benchmark",
            "--transactions",
            "20",
            "--budgets",
            "none:none,30:4,15:2",
            "--format",
            "json",
        ]
    )
    bench_out = json.loads(capsys.readouterr().out)
    assert bench_out["benchmark"] == "debezium_zipf_snowflake_join"
    assert len(bench_out["results"]) == 3
    assert bench_out["results"][0]["invalidate_count"] == 0
    assert bench_out["results"][2]["eviction_invalidate_count"] > 0

    # CLI file ingestion mode (`smplcache ingest --from debezium <stream.json>`)
    bundle = generate_zipf_snowflake_debezium_stream(
        zipf_s=1.2,
        num_transactions=15,
        seed=42,
    )
    stream_file = tmp_path / "debezium_snowflake_stream.json"
    stream_file.write_text(
        json.dumps(
            {
                "sql": bundle.sql,
                "initial_tables": {
                    rel: [
                        {
                            k: (str(v) if isinstance(v, Decimal) else v)
                            for k, v in row.items()
                        }
                        for row in rows
                    ]
                    for rel, rows in bundle.initial_tables.items()
                },
                "records": bundle.all_wal_messages(),
            }
        ),
        encoding="utf-8",
    )
    cli_main(
        [
            "ingest",
            str(stream_file),
            "--from",
            "debezium",
            "--max-total-rows",
            "25",
            "--max-fk-fanout",
            "3",
            "--format",
            "json",
        ]
    )
    file_out = json.loads(capsys.readouterr().out)
    assert file_out["total_wal_messages"] == len(bundle.all_wal_messages())
    assert file_out["peak_aux_rows"] <= 25
    assert file_out["peak_fk_bucket_size"] <= 3
    assert file_out["total_decisions"] > 0
