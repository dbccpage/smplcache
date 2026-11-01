"""Counterexamples found during PR #7 review; failures must never certify repair."""

from copy import deepcopy
from decimal import Decimal
import sqlite3

import pytest

from smplcache import (
    AggregateCache,
    DecisionKind,
    JoinAuxState,
    QueryShape,
    SharedJoinAuxPool,
    WalStreamProcessor,
    WriteEvent,
    certify_event,
    certify_join_event,
    parse_sql,
    parse_join_sql,
    process_event,
    process_join_event,
    rebuild_join_cache,
)


def sum_shape():
    return QueryShape(relation="orders", agg_fn="sum", agg_col="amount")


def join_fixture():
    shape = parse_join_sql(
        "SELECT SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.id"
    ).shape
    tables = {
        "orders": [{"id": 1, "customer_id": 1, "amount": 10}],
        "customers": [{"id": 1}],
    }
    return shape, tables, JoinAuxState.from_tables(shape, tables)


@pytest.mark.parametrize("op", ["UPSERT", "BEGIN", "COMMIT", "TYPO"])
def test_unknown_operation_is_not_disjoint(op):
    event = WriteEvent(
        "orders",
        old={"id": 1, "amount": 10},
        new={"id": 1, "amount": 20},
        explicit_operation=op,
    )
    assert certify_event(sum_shape(), event).kind == DecisionKind.INVALIDATE
    shape, _, aux = join_fixture()
    assert certify_join_event(shape, event, aux).kind == DecisionKind.INVALIDATE


def test_declared_change_cannot_disappear_from_partial_images():
    event = WriteEvent(
        "orders",
        old={"id": 1},
        new={"id": 1, "notes": "x"},
        changed_cols={"amount", "notes"},
    )
    assert certify_event(sum_shape(), event).kind == DecisionKind.INVALIDATE
    shape, _, aux = join_fixture()
    assert certify_join_event(shape, event, aux).kind == DecisionKind.INVALIDATE


@pytest.mark.parametrize("relation", ["", "*"])
def test_unidentified_data_relation_fails_closed(relation):
    event = WriteEvent(relation, new={"amount": 20}, explicit_operation="INSERT")
    assert certify_event(sum_shape(), event).kind == DecisionKind.INVALIDATE
    shape, _, aux = join_fixture()
    assert certify_join_event(shape, event, aux).kind == DecisionKind.INVALIDATE


def test_schema_identity_is_not_erased():
    shape = parse_sql("SELECT SUM(amount) FROM tenant_a.orders").shape
    assert shape.relation == "tenant_a.orders"
    cache = AggregateCache.from_rows(shape, [{"amount": 10}])
    dec = process_event(shape, cache, WriteEvent("tenant_b.orders", new={"amount": 50}))
    assert dec.kind == DecisionKind.PRESERVE
    assert cache.values == {(): Decimal(10)}
    assert (
        certify_event(shape, WriteEvent("orders", new={"amount": 50})).kind
        == DecisionKind.INVALIDATE
    )


def test_join_schema_identity_and_real_sqlite_recomputation():
    shape = parse_join_sql(
        "SELECT SUM(o.amount) FROM tenant_a.orders o JOIN tenant_a.customers c ON o.customer_id = c.id"
    ).shape
    tables = {
        "tenant_a.orders": [{"id": 1, "customer_id": 1, "amount": 10}],
        "tenant_a.customers": [{"id": 1}],
    }
    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)
    other = WriteEvent("tenant_b.orders", new={"id": 2, "customer_id": 1, "amount": 50})
    assert process_join_event(shape, cache, other, aux).kind == DecisionKind.PRESERVE
    own = WriteEvent("tenant_a.orders", new={"id": 2, "customer_id": 1, "amount": 20})
    assert process_join_event(shape, cache, own, aux).kind == DecisionKind.REPAIR
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH ':memory:' AS tenant_a")
        db.execute("CREATE TABLE tenant_a.orders (id, customer_id, amount)")
        db.execute("CREATE TABLE tenant_a.customers (id)")
        db.executemany(
            "INSERT INTO tenant_a.orders VALUES (?, ?, ?)", [(1, 1, 10), (2, 1, 20)]
        )
        db.execute("INSERT INTO tenant_a.customers VALUES (1)")
        expected = db.execute(
            "SELECT SUM(o.amount) FROM tenant_a.orders o JOIN tenant_a.customers c ON o.customer_id=c.id"
        ).fetchone()[0]
    assert cache.values == {(): Decimal(expected)}


@pytest.mark.parametrize(
    "fn,value", [("sum", 30), ("count", 2), ("count_star", 2), ("avg", (30, 2))]
)
def test_totals_only_cache_cannot_invent_row_counts(fn, value):
    shape = QueryShape(
        relation="orders",
        agg_fn=fn,
        agg_col=None if fn == "count_star" else "amount",
        group_cols=("g",),
    )
    cache = AggregateCache(values={"a": value})
    before = deepcopy(cache.values)
    dec = process_event(
        shape, cache, WriteEvent("orders", old={"g": "a", "amount": 10})
    )
    assert dec.kind == DecisionKind.INVALIDATE
    assert cache.values == before


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_float_never_repaired(value):
    assert (
        certify_event(sum_shape(), WriteEvent("orders", new={"amount": value})).kind
        == DecisionKind.INVALIDATE
    )


def test_wal_invalidation_requires_rebuild_before_further_repair():
    shape = sum_shape()
    cache = AggregateCache.from_rows(shape, [{"id": 1, "amount": 10}])
    processor = WalStreamProcessor(shape)
    payload = lambda old, new: {
        "op": "u",
        "before": old,
        "after": new,
        "source": {"table": "orders"},
    }
    ds, cache = processor.ingest(payload({"id": 1}, {"id": 1, "amount": 20}), cache)
    assert ds[0].kind == DecisionKind.INVALIDATE
    ds, cache = processor.ingest(
        payload({"id": 1, "amount": 20}, {"id": 1, "amount": 30}), cache
    )
    assert ds[0].kind == DecisionKind.INVALIDATE
    assert cache.values == {(): Decimal(10)}
    ds, cache = processor.ingest(
        payload({"id": 1, "amount": 30}, {"id": 1, "amount": 40}),
        cache,
        on_invalidate=lambda: AggregateCache.from_rows(
            shape, [{"id": 1, "amount": 40}]
        ),
    )
    assert cache.values == {(): Decimal(40)}
    ds, cache = processor.ingest(
        payload({"id": 1, "amount": 40}, {"id": 1, "amount": 50}), cache
    )
    assert ds[0].kind == DecisionKind.REPAIR
    assert cache.values == {(): Decimal(50)}


def test_wal_unsupported_invokes_rebuild():
    shape = QueryShape(relation="orders", agg_fn="min", agg_col="amount")
    p = WalStreamProcessor(shape)
    calls = []

    def rebuild():
        calls.append(1)
        return AggregateCache()

    ds, _ = p.ingest(
        {"op": "c", "after": {"amount": 1}, "source": {"table": "orders"}},
        AggregateCache(),
        on_invalidate=rebuild,
    )
    assert ds[0].kind == DecisionKind.UNSUPPORTED
    assert calls == [1]


def test_join_seed_requires_complete_nonnull_unique_primary_keys():
    shape, tables, _ = join_fixture()
    for bad in [
        [{"id": 1}],
        [{"id": None, "customer_id": 1, "amount": 10}],
        [tables["orders"][0], tables["orders"][0]],
    ]:
        with pytest.raises(ValueError):
            JoinAuxState.from_tables(shape, {**tables, "orders": bad})


def test_certify_join_does_not_change_eviction_telemetry():
    shape, _, aux = join_fixture()
    before = deepcopy(aux)
    dec = certify_join_event(
        shape,
        WriteEvent(
            "orders",
            old={"id": 1, "customer_id": 1, "amount": 10},
            new={"id": 1, "customer_id": 1, "amount": 20},
        ),
        aux,
    )
    assert dec.kind == DecisionKind.REPAIR
    assert aux._access_clock == before._access_clock
    assert aux.cms.total_additions == before.cms.total_additions


def test_sum_retains_digits_beyond_ambient_decimal_precision():
    shape = sum_shape()
    large = Decimal("10000000000000000000000000000")
    cache = AggregateCache.from_rows(shape, [{"amount": large}, {"amount": 1}])
    assert cache.values[()] == Decimal("10000000000000000000000000001")
    dec = process_event(shape, cache, WriteEvent("orders", old={"amount": large}))
    assert dec.kind == DecisionKind.REPAIR
    assert cache.values[()] == Decimal(1)


def test_join_sum_retains_digits_beyond_ambient_decimal_precision():
    shape, tables, _ = join_fixture()
    tables["orders"][0]["amount"] = Decimal("10000000000000000000000000000")
    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)
    dec = process_join_event(
        shape,
        cache,
        WriteEvent("orders", new={"id": 2, "customer_id": 1, "amount": 1}),
        aux,
    )
    assert dec.kind == DecisionKind.REPAIR
    assert cache.values[()] == Decimal("10000000000000000000000000001")


def test_seed_distinguishes_missing_evidence_from_sql_null():
    with pytest.raises(ValueError, match="Incomplete aggregate seed"):
        AggregateCache.from_rows(sum_shape(), [{"id": 1}])
    assert AggregateCache.from_rows(sum_shape(), [{"amount": None}]).values == {
        (): None
    }


@pytest.mark.parametrize("op", ["INSERT", "DELETE"])
def test_empty_explicit_row_mutation_is_not_repairable(op):
    shape = QueryShape(relation="orders", agg_fn="count_star")
    assert (
        certify_event(shape, WriteEvent("orders", explicit_operation=op)).kind
        == DecisionKind.INVALIDATE
    )


@pytest.mark.parametrize("key", [[], {}, float("inf"), float("nan")])
def test_invalid_group_key_is_rejected_before_mutation(key):
    shape = QueryShape(
        relation="orders", agg_fn="sum", agg_col="amount", group_cols=("g",)
    )
    cache = AggregateCache.from_rows(shape, [{"g": "a", "amount": 10}])
    before = deepcopy(cache.values)
    dec = process_event(
        shape,
        cache,
        WriteEvent(
            "orders", old={"g": "a", "amount": 10}, new={"g": key, "amount": 20}
        ),
    )
    assert dec.kind == DecisionKind.INVALIDATE
    assert cache.values == before


def test_unbound_ddl_cannot_be_certified_unrelated():
    shape = parse_sql("SELECT SUM(amount) FROM tenant_a.orders").shape
    assert (
        certify_event(shape, WriteEvent("orders", schema_change=True)).kind
        == DecisionKind.INVALIDATE
    )


def test_join_column_schema_must_match_bound_table():
    result = parse_join_sql(
        "SELECT SUM(tenant_b.orders.amount) FROM tenant_a.orders JOIN tenant_a.customers ON orders.customer_id = customers.id"
    )
    assert not result.is_supported


def test_schema_qualified_join_without_aliases():
    shape = parse_join_sql(
        "SELECT SUM(orders.amount) FROM tenant_a.orders JOIN tenant_a.customers ON orders.customer_id = customers.id"
    ).shape
    tables = {
        "tenant_a.orders": [{"id": 1, "customer_id": 1, "amount": 10}],
        "tenant_a.customers": [{"id": 1}],
    }
    assert rebuild_join_cache(shape, tables).values == {(): Decimal(10)}
