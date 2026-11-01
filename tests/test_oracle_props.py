# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Property-based brute-force oracle test suite for SmplCache (Upgrade 2).

Checks three properties across 10,000 Hypothesis examples each:
1. Soundness: after `repair`, cache == oracle; after `preserve`, oracle_before == oracle_after;
   after `invalidate`, cache is rebuilt from the oracle and the stream continues.
2. Necessity: dropping any key in `required_evidence()` from an event on a touched shape
   never yields `repair`.
3. Extractor soundness: mutating a single column of a single row that changes the oracle
   result implies that column is in `shape.fingerprint()`.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import os
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st

from smplcache import (
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
    process_event,
)
from smplcache.extractor import parse_sql
from tests.oracle import query_oracle, rebuild_cache

MAX_EXAMPLES = int(os.environ.get("SMPLCACHE_HYPOTHESIS_EXAMPLES", "10000"))

TABLE_COLUMNS = (
    "id",
    "customer_id",
    "region",
    "status",
    "amount",
    "quantity",
    "notes",
)

GROUP_KEY_STRATEGY = st.sampled_from([None, 0, "", "c1", "c2", "C1", "cust_0"])
REGION_STRATEGY = st.sampled_from([None, "", "us", "eu", "US"])
STATUS_STRATEGY = st.sampled_from([None, "", "paid", "pending", "cancelled", "PAID"])
NUMERIC_STRATEGY = st.one_of(
    st.none(),
    st.just(0),
    st.just(Decimal("0.00")),
    st.integers(min_value=-200, max_value=500),
    st.decimals(
        min_value=-200,
        max_value=500,
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
NOTES_STRATEGY = st.sampled_from([None, "", "n1", "n2", "n3"])


@st.composite
def row_strategy(draw: st.DrawFn, row_id: int | None = None) -> dict[str, Any]:
    return {
        "id": row_id if row_id is not None else draw(st.integers(1, 1000)),
        "customer_id": draw(GROUP_KEY_STRATEGY),
        "region": draw(REGION_STRATEGY),
        "status": draw(STATUS_STRATEGY),
        "amount": draw(NUMERIC_STRATEGY),
        "quantity": draw(NUMERIC_STRATEGY),
        "notes": draw(NOTES_STRATEGY),
    }


@st.composite
def table_strategy(draw: st.DrawFn) -> list[dict[str, Any]]:
    n_rows = draw(st.integers(min_value=0, max_value=20))
    return [draw(row_strategy(row_id=i + 1)) for i in range(n_rows)]


def _case_variant(ident: str, variant: int) -> str:
    if variant == 1:
        return ident.upper()
    if variant == 2:
        return ident.capitalize()
    return ident


@st.composite
def fragment_query_strategy(draw: st.DrawFn) -> tuple[str, str, frozenset[str]]:
    """
    Returns (sql, dialect, security_cols) for a valid v1 fragment query.
    """
    dialect = draw(st.sampled_from(["postgres", "tsql"]))
    case_var = draw(st.sampled_from([0, 1, 2]))
    use_schema = draw(st.booleans())
    use_alias = draw(st.booleans())

    table_ident = _case_variant("orders", case_var)
    if use_schema:
        schema_prefix = "public." if dialect == "postgres" else "dbo."
        from_target = f"{schema_prefix}{table_ident}"
    else:
        from_target = table_ident

    if use_alias:
        from_clause = f"{from_target} AS o"
        col_prefix = "o."
    else:
        from_clause = from_target
        col_prefix = ""

    is_agg = draw(st.booleans())
    if is_agg:
        agg_choice = draw(
            st.sampled_from(
                [
                    ("SUM", "amount"),
                    ("SUM", "quantity"),
                    ("COUNT", "amount"),
                    ("COUNT", "status"),
                    ("COUNT", "*"),
                    ("AVG", "amount"),
                    ("AVG", "quantity"),
                ]
            )
        )
        group_choice = draw(
            st.sampled_from(
                [
                    (),
                    ("customer_id",),
                    ("region",),
                    ("customer_id", "region"),
                ]
            )
        )
        select_exprs = [
            f"{col_prefix}{_case_variant(g, case_var)}" for g in group_choice
        ]
        agg_fn_sql, agg_col_sql = agg_choice
        if agg_col_sql == "*":
            select_exprs.append(f"{agg_fn_sql}(*)")
        else:
            select_exprs.append(
                f"{agg_fn_sql}({col_prefix}{_case_variant(agg_col_sql, case_var)})"
            )
        group_sql = (
            " GROUP BY "
            + ", ".join(
                f"{col_prefix}{_case_variant(g, case_var)}" for g in group_choice
            )
            if group_choice
            else ""
        )
    else:
        proj_cols = draw(
            st.lists(
                st.sampled_from(["id", "customer_id", "region", "status", "amount"]),
                min_size=1,
                max_size=3,
                unique=True,
            )
        )
        select_exprs = [f"{col_prefix}{_case_variant(c, case_var)}" for c in proj_cols]
        group_sql = ""

    pred_kind = draw(st.integers(min_value=0, max_value=4))
    where_terms: list[str] = []
    if pred_kind in (1, 3):
        status_lit = draw(st.sampled_from(["paid", "pending", ""]))
        op = draw(st.sampled_from(["=", "!="]))
        where_terms.append(
            f"{col_prefix}{_case_variant('status', case_var)} {op} '{status_lit}'"
        )
    if pred_kind in (2, 3):
        num_col = draw(st.sampled_from(["amount", "quantity"]))
        num_op = draw(st.sampled_from(["=", "!=", ">", ">=", "<", "<="]))
        num_lit = draw(st.sampled_from([0, 10, -5, Decimal("12.50")]))
        where_terms.append(
            f"{col_prefix}{_case_variant(num_col, case_var)} {num_op} {num_lit}"
        )
    if pred_kind == 4:
        reg_lit = draw(st.sampled_from(["us", "eu"]))
        where_terms.append(
            f"{col_prefix}{_case_variant('region', case_var)} = '{reg_lit}'"
        )

    where_sql = " WHERE " + " AND ".join(where_terms) if where_terms else ""
    sql = f"SELECT {', '.join(select_exprs)} FROM {from_clause}{where_sql}{group_sql}"

    sec_cols = frozenset({"region"}) if draw(st.booleans()) else frozenset()
    return sql, dialect, sec_cols


@st.composite
def scenario_strategy(
    draw: st.DrawFn,
) -> tuple[
    QueryShape,
    str,
    list[dict[str, Any]],
    list[tuple[str, int | None, dict[str, Any], WriteEvent]],
]:
    sql, dialect, sec_cols = draw(fragment_query_strategy())
    res = parse_sql(sql, dialect=dialect)
    assert res.is_supported and res.shape is not None
    shape = replace(res.shape, security_cols=sec_cols)

    initial_rows = draw(table_strategy())
    sim_rows = [dict(r) for r in initial_rows]
    next_id = len(sim_rows) + 1

    n_events = draw(st.integers(min_value=1, max_value=5))
    steps: list[tuple[str, int | None, dict[str, Any], WriteEvent]] = []

    for step_idx in range(n_events):
        evt_kind = draw(
            st.sampled_from(
                [
                    "insert",
                    "delete",
                    "update_disjoint",
                    "update_value",
                    "update_group",
                    "update_predicate",
                    "update_noop",
                    "update_partial_old",
                    "unrelated_relation",
                ]
            )
        )
        if evt_kind == "unrelated_relation":
            evt = WriteEvent(
                relation="customers",
                changed_cols={"notes"},
                old={"id": 1, "notes": "a"},
                new={"id": 1, "notes": "b"},
                event_id=f"evt_{step_idx}",
            )
            steps.append(("unrelated", None, {}, evt))
            continue

        if evt_kind == "insert" or not sim_rows:
            new_row = draw(row_strategy(row_id=next_id))
            next_id += 1
            supply_changed = draw(st.booleans())
            changed = set(new_row.keys()) if supply_changed else set()
            evt = WriteEvent(
                relation="orders",
                changed_cols=changed,
                old={},
                new=dict(new_row),
                event_id=f"evt_{step_idx}",
            )
            sim_rows.append(dict(new_row))
            steps.append(("insert", None, dict(new_row), evt))
            continue

        target_idx = draw(st.integers(min_value=0, max_value=len(sim_rows) - 1))
        old_row = dict(sim_rows[target_idx])

        if evt_kind == "delete":
            supply_changed = draw(st.booleans())
            changed = set(old_row.keys()) if supply_changed else set()
            evt = WriteEvent(
                relation="orders",
                changed_cols=changed,
                old=dict(old_row),
                new={},
                event_id=f"evt_{step_idx}",
            )
            sim_rows.pop(target_idx)
            steps.append(("delete", target_idx, {}, evt))
            continue

        new_row = dict(old_row)
        if evt_kind == "update_disjoint":
            new_row["notes"] = (
                "updated_note" if old_row["notes"] != "updated_note" else "alt_note"
            )
        elif evt_kind == "update_value":
            new_row["amount"] = draw(NUMERIC_STRATEGY)
            new_row["quantity"] = draw(NUMERIC_STRATEGY)
        elif evt_kind == "update_group":
            new_row["customer_id"] = draw(GROUP_KEY_STRATEGY)
            new_row["region"] = draw(REGION_STRATEGY)
        elif evt_kind == "update_predicate":
            new_row["status"] = draw(STATUS_STRATEGY)
            new_row["amount"] = draw(NUMERIC_STRATEGY)
        elif evt_kind == "update_noop":
            pass
        elif evt_kind == "update_partial_old":
            new_row["amount"] = draw(NUMERIC_STRATEGY)

        diff_cols = {
            k
            for k in old_row.keys()
            if old_row[k] != new_row[k] or type(old_row[k]) is not type(new_row[k])
        }
        if evt_kind == "update_partial_old":
            partial_old = {"id": old_row["id"]}
            evt = WriteEvent(
                relation="orders",
                changed_cols=diff_cols or {"amount"},
                old=partial_old,
                new=dict(new_row),
                event_id=f"evt_{step_idx}",
            )
        else:
            supply_changed = draw(st.booleans())
            evt = WriteEvent(
                relation="orders",
                changed_cols=diff_cols if supply_changed else set(),
                old=dict(old_row),
                new=dict(new_row),
                event_id=f"evt_{step_idx}",
            )

        sim_rows[target_idx] = dict(new_row)
        steps.append(("update", target_idx, dict(new_row), evt))

    return shape, sql, initial_rows, steps


@settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(scenario=scenario_strategy())
def test_oracle_soundness(
    scenario: tuple[
        QueryShape,
        str,
        list[dict[str, Any]],
        list[tuple[str, int | None, dict[str, Any], WriteEvent]],
    ],
) -> None:
    shape, sql, initial_rows, steps = scenario
    table = [dict(r) for r in initial_rows]
    cache = rebuild_cache(shape, table)

    oracle_before = query_oracle(shape, table, TABLE_COLUMNS, sql=sql)
    if shape.agg_fn is not None:
        assert cache.values == oracle_before

    for op_kind, idx, new_row, event in steps:
        decision = process_event(shape, cache, event)

        if op_kind == "insert":
            table.append(dict(new_row))
        elif op_kind == "delete" and idx is not None:
            table.pop(idx)
        elif op_kind == "update" and idx is not None:
            table[idx] = dict(new_row)

        oracle_after = query_oracle(shape, table, TABLE_COLUMNS, sql=sql)

        if decision.kind == DecisionKind.REPAIR:
            assert shape.agg_fn is not None
            assert cache.values == oracle_after
        elif decision.kind == DecisionKind.PRESERVE:
            assert oracle_before == oracle_after
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        elif decision.kind == DecisionKind.INVALIDATE:
            cache = rebuild_cache(shape, table)
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        else:
            raise AssertionError(f"Unexpected decision kind: {decision.kind}")

        oracle_before = oracle_after


@settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    query_info=fragment_query_strategy(),
    old_row=row_strategy(row_id=1),
    new_row=row_strategy(row_id=1),
    op_mode=st.sampled_from(["insert", "delete", "update"]),
)
def test_evidence_necessity(
    query_info: tuple[str, str, frozenset[str]],
    old_row: dict[str, Any],
    new_row: dict[str, Any],
    op_mode: str,
) -> None:
    sql, dialect, sec_cols = query_info
    res = parse_sql(sql, dialect=dialect)
    assert res.is_supported and res.shape is not None
    shape = replace(res.shape, security_cols=sec_cols)

    req_evidence = shape.required_evidence()
    if not req_evidence:
        return

    for missing_col in req_evidence:
        if op_mode == "insert":
            stripped_new = {k: v for k, v in new_row.items() if k != missing_col}
            evt = WriteEvent(
                relation="orders",
                changed_cols=set(stripped_new.keys()),
                old={},
                new=stripped_new,
            )
            dec = certify_event(shape, evt)
            assert dec.kind != DecisionKind.REPAIR
        elif op_mode == "delete":
            stripped_old = {k: v for k, v in old_row.items() if k != missing_col}
            evt = WriteEvent(
                relation="orders",
                changed_cols=set(stripped_old.keys()),
                old=stripped_old,
                new={},
            )
            dec = certify_event(shape, evt)
            assert dec.kind != DecisionKind.REPAIR
        else:
            # Drop from old, from new, and from both
            for drop_old, drop_new in [(True, False), (False, True), (True, True)]:
                s_old = (
                    {k: v for k, v in old_row.items() if k != missing_col}
                    if drop_old
                    else dict(old_row)
                )
                s_new = (
                    {k: v for k, v in new_row.items() if k != missing_col}
                    if drop_new
                    else dict(new_row)
                )
                evt = WriteEvent(
                    relation="orders",
                    changed_cols=set(),
                    old=s_old,
                    new=s_new,
                )
                dec = certify_event(shape, evt)
                assert dec.kind != DecisionKind.REPAIR


@settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    query_info=fragment_query_strategy(),
    base_row=row_strategy(row_id=1),
    mutated_row=row_strategy(row_id=1),
    col_to_mutate=st.sampled_from(TABLE_COLUMNS),
)
def test_extractor_soundness(
    query_info: tuple[str, str, frozenset[str]],
    base_row: dict[str, Any],
    mutated_row: dict[str, Any],
    col_to_mutate: str,
) -> None:
    sql, dialect, sec_cols = query_info
    res = parse_sql(sql, dialect=dialect)
    assert res.is_supported and res.shape is not None
    shape = replace(res.shape, security_cols=sec_cols)

    row_after = dict(base_row)
    row_after[col_to_mutate] = mutated_row[col_to_mutate]

    oracle_before = query_oracle(shape, [base_row], TABLE_COLUMNS, sql=sql)
    oracle_after = query_oracle(shape, [row_after], TABLE_COLUMNS, sql=sql)

    if oracle_before != oracle_after:
        assert (
            col_to_mutate in shape.fingerprint()
        ), f"Missed dependency witness: mutating {col_to_mutate} changed oracle for {sql}, but fingerprint is {shape.fingerprint()}"
