from dataclasses import replace

import pytest

from smplcache import (
    AggregateCache,
    DecisionKind,
    QueryShape,
    WriteEvent,
    process_join_event,
)
from smplcache.extractor import parse_join_sql
from smplcache.join import JoinAuxState, certify_join_event, rebuild_join_cache


def test_aggregate_column_names_are_not_mistaken_for_min_or_max():
    for name in ("admin_fee", "maximum_price", "minimum"):
        assert QueryShape(relation="orders", aggregate_cols=[name]).is_supported
    assert not QueryShape(
        relation="orders", aggregate_cols=["MIN(amount)"]
    ).is_supported


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT c.region, SUM(o.amount) FROM orders o JOIN customers c ON o.cust_code = c.code GROUP BY c.region",
        "SELECT COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id",
    ],
)
def test_non_primary_right_join_key_is_unsupported(sql):
    result = parse_join_sql(sql)
    assert not result.is_supported


@pytest.mark.parametrize(
    "sql,pk_map,event,initial",
    [
        (
            "SELECT c.region, SUM(o.amount) FROM orders o JOIN customers c ON o.cust_code = c.code GROUP BY c.region",
            {"customers": "code"},
            WriteEvent(
                "customers",
                old={"id": 100, "code": "A", "region": "east"},
                new={"id": 100, "code": "A", "region": "west"},
            ),
            {"east": 15},
        ),
        (
            "SELECT COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id",
            {"orders": "customer_id"},
            WriteEvent("orders", new={"id": 12, "customer_id": 1}),
            {(): 2},
        ),
    ],
)
def test_unsafe_shapes_cannot_certify_either_reported_mutation(
    sql, pk_map, event, initial
):
    # A caller can construct a shape directly, independent of parser admission.
    parsed = parse_join_sql(sql, primary_keys=pk_map)
    assert parsed.shape is not None
    shape = parsed.shape
    cache = AggregateCache(values=initial)
    aux = JoinAuxState(shape=shape)  # Actual declared PKs default to id.
    before = dict(cache.values)
    decision = process_join_event(shape, cache, event, aux)
    assert decision.kind == DecisionKind.UNSUPPORTED
    assert cache.values == before


def test_manually_constructed_shape_cannot_bypass_right_key_check():
    parsed = parse_join_sql(
        "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id"
    )
    shape = parsed.shape
    assert shape is not None
    aux = JoinAuxState.from_tables(
        shape, {"orders": [{"id": 1, "customer_id": 100}], "customers": [{"id": 100}]}
    )
    unsafe = replace(shape, joins=(replace(shape.joins[0], right_key="code"),))
    event = WriteEvent(
        relation="customers", old={"id": 100, "code": "A"}, new={"id": 100, "code": "B"}
    )
    assert certify_join_event(unsafe, event, aux).kind == DecisionKind.UNSUPPORTED
    with pytest.raises(ValueError, match="primary key"):
        JoinAuxState.from_tables(
            unsafe,
            {
                "orders": [{"id": 1, "customer_id": "A"}],
                "customers": [{"id": 100, "code": "A"}],
            },
        )


def test_declared_custom_primary_key_can_be_used():
    sql = "SELECT COUNT(*) FROM orders o JOIN customers c ON o.cust_code = c.code"
    parsed = parse_join_sql(sql, primary_keys={"customers": "code"})
    assert parsed.is_supported and parsed.shape is not None
    with pytest.raises(ValueError, match="Duplicate primary key"):
        rebuild_join_cache(
            parsed.shape,
            {"orders": [], "customers": [{"code": "A"}, {"code": "A"}]},
            primary_keys={"customers": "code"},
        )
