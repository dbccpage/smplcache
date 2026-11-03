import sqlite3
from decimal import Decimal

import pytest

from smplcache import (
    AggregateCache,
    DecisionKind,
    QueryShape,
    WriteEvent,
    process_event,
)
from smplcache.extractor import parse_join_sql
from smplcache.debezium import _coerce_wal_value
from smplcache.join import JoinAuxState, process_join_event, rebuild_join_cache


def test_sqlite_float_sum_is_not_decimal_sum_and_seed_is_rejected():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE amounts (amount REAL)")
    db.executemany("INSERT INTO amounts VALUES (?)", [(0.1,), (0.2,)])
    assert db.execute("SELECT SUM(amount) FROM amounts").fetchone()[0] != float(
        Decimal("0.3")
    )
    shape = QueryShape(relation="amounts", agg_fn="sum", agg_col="amount")
    with pytest.raises(TypeError, match="Float"):
        AggregateCache.from_rows(shape, [{"amount": 0.1}, {"amount": 0.2}])
    cache = AggregateCache.from_rows(shape, [{"amount": Decimal("0.1")}])
    decision = process_event(shape, cache, WriteEvent("amounts", new={"amount": 0.2}))
    assert decision.kind == DecisionKind.INVALIDATE
    assert cache.values == {(): Decimal("0.1")}


def test_wal_float_origin_and_precise_decimal_are_preserved():
    assert isinstance(_coerce_wal_value("amount", 0.1, "numeric", frozenset()), float)
    assert isinstance(
        _coerce_wal_value("amount", "0.1", "double precision", frozenset()), float
    )
    exact = _coerce_wal_value(
        "amount",
        {"value": "123456789012345678901234567891", "scale": 1},
        "numeric",
        frozenset(),
    )
    assert exact == Decimal("12345678901234567890123456789.1")


@pytest.mark.parametrize("key", [True, False, 1.0, 0.1])
def test_ambiguous_group_values_are_rejected_at_seed_and_event(key):
    shape = QueryShape(relation="amounts", agg_fn="count_star", group_cols=("g",))
    with pytest.raises(TypeError, match="group keys"):
        AggregateCache.from_rows(shape, [{"g": key}])
    cache = AggregateCache.from_rows(shape, [{"g": 1}])
    decision = process_event(
        shape, cache, WriteEvent("amounts", old={"g": 1}, new={"g": key})
    )
    assert decision.kind == DecisionKind.INVALIDATE
    assert cache.values == {1: Decimal(1)}


def test_join_rejects_float_sum_and_boolean_group():
    shape = parse_join_sql(
        "SELECT c.region, SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.region"
    ).shape
    assert shape is not None
    tables = {
        "orders": [{"id": 1, "customer_id": 2, "amount": 1}],
        "customers": [{"id": 2, "region": "east"}],
    }
    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)
    decision = process_join_event(
        shape,
        cache,
        WriteEvent(
            "orders",
            old=tables["orders"][0],
            new={"id": 1, "customer_id": 2, "amount": 0.2},
        ),
        aux,
    )
    assert decision.kind == DecisionKind.INVALIDATE
    assert cache.values == {"east": Decimal(1)}
    tables["customers"][0]["region"] = True
    with pytest.raises(TypeError, match="group keys"):
        rebuild_join_cache(shape, tables)


def test_float_predicates_and_join_keys_fail_closed():
    from smplcache import Predicate

    shape = QueryShape(
        relation="amounts",
        agg_fn="count_star",
        predicate=Predicate(terms=(("amount", ">", Decimal("0.1")),)),
    )
    with pytest.raises(TypeError, match="Float predicate"):
        AggregateCache.from_rows(shape, [{"amount": 0.2}])
    cache = AggregateCache.from_rows(shape, [{"amount": Decimal("0.2")}])
    assert (
        process_event(shape, cache, WriteEvent("amounts", new={"amount": 0.2})).kind
        == DecisionKind.INVALIDATE
    )

    join = parse_join_sql(
        "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id"
    ).shape
    assert join is not None
    tables = {"orders": [{"id": 1, "customer_id": 2.0}], "customers": [{"id": 2}]}
    with pytest.raises(ValueError, match="join keys"):
        JoinAuxState.from_tables(join, tables)
