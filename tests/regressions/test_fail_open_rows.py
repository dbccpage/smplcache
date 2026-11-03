# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Regression tests for the 8 confirmed fail-open paths in the review table
plus the must_reject.sql corpus.
"""

from decimal import Decimal
from pathlib import Path
import pytest

from smplcache import (
    AggregateCache,
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
    process_event,
)
from smplcache.extractor import parse_sql
from smplcache.sqlshape import SqlShapeContext, check_cdc_evidence


def test_row_1_security_group_and_non_status_predicate_changes():
    """
    Row 1: Change to a security, group, or non-`status` predicate column
    previously returned `preserve noop_update`.
    """
    # 1a. Security column change on an aggregate shape must invalidate, not preserve
    res_sec = parse_sql(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res_sec.is_supported and res_sec.shape is not None
    from dataclasses import replace

    shape_sec = replace(res_sec.shape, security_cols=frozenset({"tenant_id"}))
    evt_sec = WriteEvent(
        relation="orders",
        changed_cols={"tenant_id"},
        old={
            "customer_id": "c1",
            "status": "paid",
            "amount": 100,
            "tenant_id": "t1",
        },
        new={
            "customer_id": "c1",
            "status": "paid",
            "amount": 100,
            "tenant_id": "t2",
        },
    )
    dec_sec = certify_event(shape_sec, evt_sec)
    assert dec_sec.kind == DecisionKind.INVALIDATE

    # 1b. Group column other than `customer_id` (e.g. `region_id`) changing must repair, not noop_update
    res_grp = parse_sql(
        "SELECT region_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY region_id"
    )
    assert res_grp.is_supported and res_grp.shape is not None
    cache_grp = AggregateCache.from_rows(
        res_grp.shape, [{"region_id": "us", "status": "paid", "amount": 100}]
    )
    evt_grp = WriteEvent(
        relation="orders",
        changed_cols={"region_id"},
        old={"region_id": "us", "status": "paid", "amount": 100},
        new={"region_id": "eu", "status": "paid", "amount": 100},
    )
    dec_grp = process_event(res_grp.shape, cache_grp, evt_grp)
    assert dec_grp.kind == DecisionKind.REPAIR
    assert cache_grp.values.get("eu") == Decimal(100)

    # 1c. Non-`status` predicate column (e.g. `warehouse_id = 'w1'`) changing must repair, not noop_update
    res_pred = parse_sql(
        "SELECT item_id, SUM(quantity) FROM inventory WHERE warehouse_id = 'w1' GROUP BY item_id"
    )
    assert res_pred.is_supported and res_pred.shape is not None
    cache_pred = AggregateCache.from_rows(
        res_pred.shape, [{"item_id": "item_1", "warehouse_id": "w1", "quantity": 50}]
    )
    evt_pred = WriteEvent(
        relation="inventory",
        changed_cols={"warehouse_id"},
        old={"item_id": "item_1", "warehouse_id": "w2", "quantity": 25},
        new={"item_id": "item_1", "warehouse_id": "w1", "quantity": 25},
    )
    dec_pred = process_event(res_pred.shape, cache_pred, evt_pred)
    assert dec_pred.kind == DecisionKind.REPAIR
    assert cache_pred.values["item_1"] == Decimal(75)


def test_row_2_insert_delete_empty_or_mismatched_changed_cols():
    """
    Row 2: INSERT/DELETE with empty `changed_cols` (caller-supplied, never derived)
    previously returned `preserve disjoint_columns`.
    Also if caller supplies `changed_cols` that disagree with `old`/`new`, return
    `invalidate changed_cols_mismatch`.
    """
    res = parse_sql(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res.is_supported and res.shape is not None
    shape = res.shape

    # INSERT with empty changed_cols must not preserve as disjoint_columns
    evt_insert = WriteEvent(
        relation="orders",
        changed_cols=set(),
        old={},
        new={"customer_id": "c1", "status": "paid", "amount": 100},
    )
    dec_ins = certify_event(shape, evt_insert)
    assert dec_ins.kind == DecisionKind.REPAIR

    # DELETE with empty changed_cols must not preserve as disjoint_columns
    evt_delete = WriteEvent(
        relation="orders",
        changed_cols=set(),
        old={"customer_id": "c1", "status": "paid", "amount": 100},
        new={},
    )
    dec_del = certify_event(shape, evt_delete)
    assert dec_del.kind == DecisionKind.REPAIR

    # Caller supplies changed_cols that disagree with derived old/new diff
    evt_mismatch = WriteEvent(
        relation="orders",
        changed_cols={"shipping_address"},
        old={"customer_id": "c1", "status": "paid", "amount": 100},
        new={"customer_id": "c1", "status": "paid", "amount": 200},
    )
    dec_mis = certify_event(shape, evt_mismatch)
    assert dec_mis.kind == DecisionKind.INVALIDATE
    assert dec_mis.reason == "changed_cols_mismatch"


def test_row_3_schema_qualified_case_and_joins():
    """
    Row 3: `FROM public.orders`, `FROM Orders`, any JOIN
    previously returned `preserve unrelated_relation`.
    """
    evt = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"customer_id": "c1", "status": "paid", "amount": 100},
        new={"customer_id": "c1", "status": "paid", "amount": 150},
    )

    dec_schema = check_cdc_evidence(
        "SELECT customer_id, SUM(amount) FROM public.orders WHERE status = 'paid' GROUP BY customer_id",
        WriteEvent(relation="public.orders", old=evt.old, new=evt.new),
    )
    assert dec_schema.kind == DecisionKind.REPAIR

    dec_case = check_cdc_evidence(
        "SELECT Customer_Id, SUM(Amount) FROM Orders WHERE Status = 'paid' GROUP BY Customer_Id",
        evt,
    )
    assert dec_case.kind == DecisionKind.REPAIR

    res_join = parse_sql(
        "SELECT o.customer_id, SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.flag = 1 GROUP BY o.customer_id"
    )
    assert not res_join.is_supported


def test_row_4_select_star_count_star_order_by():
    """
    Row 4: `SELECT *`, `COUNT(*)`, ORDER BY columns previously missed columns
    in fingerprint and treated changes as disjoint.
    """
    # SELECT * without schema is rejected
    assert not parse_sql("SELECT * FROM orders").is_supported

    # SELECT * with schema includes all columns in fingerprint
    res_star = parse_sql(
        "SELECT * FROM orders",
        schema={"orders": ["id", "customer_id", "amount", "status"]},
    )
    assert res_star.is_supported and res_star.shape is not None
    assert res_star.shape.fingerprint() == frozenset(
        {"id", "customer_id", "amount", "status"}
    )

    # ORDER BY is rejected in v1
    assert not parse_sql(
        "SELECT customer_id FROM orders ORDER BY created_at"
    ).is_supported

    # COUNT(*) repairs on INSERT/DELETE even when no columns are named in SELECT
    res_cnt = parse_sql("SELECT COUNT(*) FROM orders")
    assert res_cnt.is_supported and res_cnt.shape is not None
    cache_cnt = AggregateCache.from_rows(res_cnt.shape, [{"id": 1}, {"id": 2}])
    evt_ins = WriteEvent(
        relation="orders",
        changed_cols={"id"},
        old={},
        new={"id": 3},
    )
    dec_cnt = process_event(res_cnt.shape, cache_cnt, evt_ins)
    assert dec_cnt.kind == DecisionKind.REPAIR
    assert cache_cnt.values[()] == Decimal(3)


def test_row_5_update_without_replica_identity_full():
    """
    Row 5: UPDATE with `old={}` (no REPLICA IDENTITY FULL) or partial `old`
    was classed as INSERT or unioned with `new`, causing double-counting (100 + 150 = 250).
    """
    res = parse_sql(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res.is_supported and res.shape is not None
    shape = res.shape
    cache = AggregateCache(values={"c1": Decimal(100)})

    # Case A: UPDATE with old={} and changed_cols={"amount"}
    evt_empty_old = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={},
        new={"customer_id": "c1", "status": "paid", "amount": 150},
    )
    dec_a = process_event(shape, cache, evt_empty_old)
    assert dec_a.kind == DecisionKind.INVALIDATE
    assert cache.values["c1"] == Decimal(100)

    # Case B: UPDATE with partial old={"id": 1} (missing required evidence in old)
    evt_partial_old = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"id": 1},
        new={"id": 1, "customer_id": "c1", "status": "paid", "amount": 150},
    )
    dec_b = process_event(shape, cache, evt_partial_old)
    assert dec_b.kind == DecisionKind.INVALIDATE
    assert dec_b.reason == "missing_evidence_for_repair"
    assert cache.values["c1"] == Decimal(100)


def test_row_6_decimal_amounts_null_and_zero_group_keys():
    """
    Row 6: Float/Decimal amounts, NULL or `0` group keys
    previously truncated sums and dropped NULL / 0 groups while reporting `repair`.
    """
    from smplcache import NULL_GROUP_KEY

    res = parse_sql(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res.is_supported and res.shape is not None
    shape = res.shape
    cache = AggregateCache.from_rows(
        shape,
        [
            {"customer_id": 0, "status": "paid", "amount": Decimal("10.25")},
            {"customer_id": None, "status": "paid", "amount": Decimal("5.50")},
        ],
    )
    assert cache.values[0] == Decimal("10.25")
    assert cache.values[NULL_GROUP_KEY] == Decimal("5.50")

    # Update group 0 with fractional Decimal
    evt_zero = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"customer_id": 0, "status": "paid", "amount": Decimal("10.25")},
        new={"customer_id": 0, "status": "paid", "amount": Decimal("20.75")},
    )
    dec_zero = process_event(shape, cache, evt_zero)
    assert dec_zero.kind == DecisionKind.REPAIR
    assert cache.values[0] == Decimal("20.75")

    # Update NULL group with fractional Decimal
    evt_null = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"customer_id": None, "status": "paid", "amount": Decimal("5.50")},
        new={"customer_id": None, "status": "paid", "amount": Decimal("8.125")},
    )
    dec_null = process_event(shape, cache, evt_null)
    assert dec_null.kind == DecisionKind.REPAIR
    assert cache.values[NULL_GROUP_KEY] == Decimal("8.125")


def test_row_7_avg_and_count_shapes():
    """
    Row 7: AVG or COUNT shapes were previously repaired with the SUM program.
    """
    # COUNT(amount) ignores NULL amount and increments by 1, not by amount!
    res_count = parse_sql(
        "SELECT customer_id, COUNT(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res_count.is_supported and res_count.shape is not None
    cache_count = AggregateCache.from_rows(
        res_count.shape,
        [{"customer_id": "c1", "status": "paid", "amount": 100}],
    )
    assert cache_count.values["c1"] == Decimal(1)

    evt_ins = WriteEvent(
        relation="orders",
        changed_cols={"customer_id", "status", "amount"},
        old={},
        new={"customer_id": "c1", "status": "paid", "amount": 250},
    )
    dec_cnt = process_event(res_count.shape, cache_count, evt_ins)
    assert dec_cnt.kind == DecisionKind.REPAIR
    assert cache_count.values["c1"] == Decimal(2)

    # AVG(amount) maintains (sum, n) and reports sum/n
    res_avg = parse_sql(
        "SELECT customer_id, AVG(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res_avg.is_supported and res_avg.shape is not None
    cache_avg = AggregateCache.from_rows(
        res_avg.shape,
        [{"customer_id": "c1", "status": "paid", "amount": Decimal(100)}],
    )
    assert cache_avg.values["c1"] == Decimal(100)

    dec_avg = process_event(res_avg.shape, cache_avg, evt_ins)
    assert dec_avg.kind == DecisionKind.REPAIR
    assert cache_avg.values["c1"] == Decimal(175)


def test_row_8_plain_projection_select_touching_changed_col():
    """
    Row 8: Plain `SELECT item_id FROM orders` touching a changed column
    previously returned `repair evidence_sufficient` with no repair program.
    """
    sql = "SELECT item_id FROM orders"
    evt = WriteEvent(
        relation="orders",
        changed_cols={"item_id"},
        old={"item_id": "a"},
        new={"item_id": "b"},
    )
    dec = check_cdc_evidence(sql, evt)
    assert dec.kind == DecisionKind.INVALIDATE
    assert dec.certificate.repair_program is None


def test_must_reject_corpus():
    corpus_path = Path(__file__).resolve().parent.parent / "corpus" / "must_reject.sql"
    lines = [
        line.strip()
        for line in corpus_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) >= 20
    dummy_event = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"customer_id": "c1", "status": "paid", "amount": 10},
        new={"customer_id": "c1", "status": "paid", "amount": 20},
    )
    for query in lines:
        res = parse_sql(query)
        assert not res.is_supported, f"Expected unsupported for query: {query}"
        dec = check_cdc_evidence(query, dummy_event)
        assert (
            dec.kind == DecisionKind.UNSUPPORTED
        ), f"Expected UNSUPPORTED decision for query: {query}"


@pytest.mark.parametrize(
    "construct_name,sql",
    [
        (
            "JOIN",
            "SELECT o.customer_id FROM orders o JOIN customers c ON o.customer_id = c.id",
        ),
        ("STAR", "SELECT * FROM orders"),
        (
            "HAVING",
            "SELECT customer_id, SUM(amount) FROM orders GROUP BY customer_id HAVING SUM(amount) > 10",
        ),
        ("DISTINCT", "SELECT DISTINCT customer_id FROM orders"),
        ("ORDER_BY", "SELECT customer_id FROM orders ORDER BY customer_id"),
        ("CASE", "SELECT CASE WHEN status = 'paid' THEN 1 ELSE 0 END FROM orders"),
        ("LIKE", "SELECT customer_id FROM orders WHERE status LIKE 'p%'"),
        ("BETWEEN", "SELECT customer_id FROM orders WHERE amount BETWEEN 1 AND 10"),
        (
            "OR",
            "SELECT customer_id FROM orders WHERE status = 'paid' OR status = 'pending'",
        ),
        ("NOT", "SELECT customer_id FROM orders WHERE NOT status = 'paid'"),
        (
            "MULTI_STATEMENT",
            "SELECT customer_id FROM orders; SELECT customer_id FROM orders",
        ),
        ("COMMENT", "SELECT customer_id FROM orders -- comment"),
        ("QUOTED_IDENT", 'SELECT "customer_id" FROM orders'),
    ],
)
def test_explicit_v1_rejections(construct_name: str, sql: str) -> None:
    res = parse_sql(sql)
    assert not res.is_supported, f"{construct_name} should be rejected: {sql}"


def test_tsql_dialect_and_dbo_normalization() -> None:
    res = parse_sql(
        "SELECT o.Customer_Id, SUM(o.Amount) FROM dbo.Orders AS o WHERE o.Status = 'paid' GROUP BY o.Customer_Id",
        dialect="tsql",
    )
    assert res.is_supported and res.shape is not None
    assert res.shape.relation == "dbo.orders"
    assert res.shape.agg_fn == "sum"
    assert res.shape.agg_col == "amount"
    assert res.shape.group_cols == ("customer_id",)


def test_type_errors_downgrade_to_invalidate_never_raise() -> None:
    res = parse_sql(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res.is_supported and res.shape is not None
    shape = res.shape
    cache = AggregateCache(values={"c1": Decimal(100)})

    # Non-numeric string in SUM column downgrades to invalidate
    evt_bad_amount = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"customer_id": "c1", "status": "paid", "amount": 100},
        new={"customer_id": "c1", "status": "paid", "amount": "not_a_number"},
    )
    dec1 = process_event(shape, cache, evt_bad_amount)
    assert dec1.kind == DecisionKind.INVALIDATE
    assert cache.values["c1"] == Decimal(100)

    # Boolean in numeric column downgrades to invalidate
    evt_bool_amount = WriteEvent(
        relation="orders",
        changed_cols={"amount"},
        old={"customer_id": "c1", "status": "paid", "amount": 100},
        new={"customer_id": "c1", "status": "paid", "amount": True},
    )
    dec2 = process_event(shape, cache, evt_bool_amount)
    assert dec2.kind == DecisionKind.INVALIDATE


def test_cli_workload_validation_rejects_malformed_input() -> None:
    from smplcache.cli import validate_workload_data

    with pytest.raises(ValueError):
        validate_workload_data(["not_a_dict"])

    with pytest.raises(ValueError):
        validate_workload_data({"shapes": "not_a_list", "events": []})

    with pytest.raises(ValueError):
        validate_workload_data({"shapes": [{"name": "s1"}], "events": []})
