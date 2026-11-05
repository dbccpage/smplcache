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


def test_non_pk_join_columns_customers_region_id_and_orders_customer_id_never_classify_keep():
    """
    A change to a join column on the side of a join that is not the primary key
    (`customers.region_id` for a customer with paid orders, or `orders.customer_id`)
    must be classified as FIX IN PLACE (`DecisionKind.REPAIR`) or REREAD
    (`DecisionKind.INVALIDATE`), never KEEP (`DecisionKind.PRESERVE`).
    """
    sql = (
        "SELECT r.name, SUM(o.amount_cents) "
        "FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "JOIN regions r ON c.region_id = r.id "
        "WHERE o.status = 'paid' "
        "GROUP BY r.name"
    )
    parsed = parse_join_sql(sql)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    # Dependency set must derive from the parsed query AST (columns read, join keys,
    # filter columns, grouping columns) and include non-PK join keys.
    deps = shape.dependency_set()
    assert deps["orders"]["join_keys"] == frozenset({"customer_id"})
    assert deps["orders"]["read_cols"] == frozenset({"amount_cents"})
    assert deps["orders"]["filter_cols"] == frozenset({"status"})
    assert deps["customers"]["join_keys"] == frozenset({"id", "region_id"})
    assert deps["regions"]["join_keys"] == frozenset({"id"})
    assert deps["regions"]["group_cols"] == frozenset({"name"})

    tables = {
        "orders": [
            {"id": 101, "customer_id": 1, "status": "paid", "amount_cents": 10000},
            {"id": 106, "customer_id": 1, "status": "paid", "amount_cents": 20000},
        ],
        "customers": [
            {"id": 1, "name": "Acme Industrial", "region_id": 1},
            {"id": 2, "name": "Nordic Freight", "region_id": 2},
            {"id": 5, "name": "Great Lakes Co", "region_id": 1},
        ],
        "regions": [
            {"id": 1, "name": "North America"},
            {"id": 2, "name": "Europe"},
        ],
    }

    # 1. Direct JoinAuxState construction with table_rows + changing customers.region_id
    direct_aux = JoinAuxState(
        shape=shape,
        table_rows={
            rel: {row["id"]: dict(row) for row in rows} for rel, rows in tables.items()
        },
    )
    evt_cust_region = WriteEvent(
        relation="customers",
        old={"id": 1, "name": "Acme Industrial", "region_id": 1},
        new={"id": 1, "name": "Acme Industrial", "region_id": 2},
    )
    dec_direct = certify_join_event(shape, evt_cust_region, direct_aux)
    assert dec_direct.kind in (DecisionKind.REPAIR, DecisionKind.INVALIDATE)
    assert dec_direct.kind == DecisionKind.REPAIR

    # 2. Seeded cache + JoinAuxState: changing customers.region_id (full & missing evidence)
    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables)

    dec_cust_full = process_join_event(shape, cache, evt_cust_region, aux)
    assert dec_cust_full.kind == DecisionKind.REPAIR
    assert cache.values == {"Europe": 30000}

    evt_cust_missing_old = WriteEvent(
        relation="customers",
        changed_cols={"region_id"},
        old={"id": 1, "name": "Acme Industrial"},
        new={"id": 1, "name": "Acme Industrial", "region_id": 1},
    )
    dec_cust_missing = certify_join_event(shape, evt_cust_missing_old, aux)
    assert dec_cust_missing.kind == DecisionKind.INVALIDATE

    # 3. Changing orders.customer_id across regions (Customer 1 in Europe -> Customer 5 in North America)
    evt_order_reparent_diff = WriteEvent(
        relation="orders",
        old={"id": 101, "customer_id": 1, "status": "paid", "amount_cents": 10000},
        new={"id": 101, "customer_id": 5, "status": "paid", "amount_cents": 10000},
    )
    dec_order_diff = process_join_event(shape, cache, evt_order_reparent_diff, aux)
    assert dec_order_diff.kind == DecisionKind.REPAIR
    assert cache.values == {"North America": 10000, "Europe": 20000}

    # 4. Changing orders.customer_id between two customers in the SAME region
    # (Customer 5 in region 1 -> Customer 1 when both are in region 1):
    # Even when the group totals before and after are equal, fk_index must reparent in place,
    # so the move must be REPAIR (FIX IN PLACE), never PRESERVE (KEEP).
    tables_same_region = {
        "orders": [
            {"id": 101, "customer_id": 1, "status": "paid", "amount_cents": 10000},
        ],
        "customers": [
            {"id": 1, "name": "Acme Industrial", "region_id": 1},
            {"id": 5, "name": "Great Lakes Co", "region_id": 1},
        ],
        "regions": [
            {"id": 1, "name": "North America"},
            {"id": 2, "name": "Europe"},
        ],
    }
    cache_same = rebuild_join_cache(shape, tables_same_region)
    aux_same = JoinAuxState.from_tables(shape, tables_same_region)
    evt_order_same_region = WriteEvent(
        relation="orders",
        old={"id": 101, "customer_id": 1, "status": "paid", "amount_cents": 10000},
        new={"id": 101, "customer_id": 5, "status": "paid", "amount_cents": 10000},
    )
    dec_order_same = process_join_event(
        shape, cache_same, evt_order_same_region, aux_same
    )
    assert dec_order_same.kind == DecisionKind.REPAIR
    assert dec_order_same.reason == "foreign_key_reparent"

    # 5. Missing old customer_id on orders must REREAD (INVALIDATE), never KEEP
    evt_order_missing = WriteEvent(
        relation="orders",
        changed_cols={"customer_id"},
        old={"id": 101, "status": "paid", "amount_cents": 10000},
        new={"id": 101, "customer_id": 1, "status": "paid", "amount_cents": 10000},
    )
    assert (
        certify_join_event(shape, evt_order_missing, aux_same).kind
        == DecisionKind.INVALIDATE
    )

    # 6. Changing customers.region_id for a customer with paid orders when the
    # GROUP BY is on c.name (so group sums do not change, but c.region_id join key does):
    # must still be REPAIR (dimension_update), never PRESERVE (KEEP).
    sql_by_cust = (
        "SELECT c.name, SUM(o.amount_cents) "
        "FROM orders o "
        "JOIN customers c ON o.customer_id = c.id "
        "JOIN regions r ON c.region_id = r.id "
        "WHERE o.status = 'paid' "
        "GROUP BY c.name"
    )
    parsed_by_cust = parse_join_sql(sql_by_cust)
    assert parsed_by_cust.is_supported and parsed_by_cust.shape is not None
    shape_by_cust = parsed_by_cust.shape
    cache_by_cust = rebuild_join_cache(shape_by_cust, tables_same_region)
    aux_by_cust = JoinAuxState.from_tables(shape_by_cust, tables_same_region)
    dec_cust_same_grp = process_join_event(
        shape_by_cust, cache_by_cust, evt_cust_region, aux_by_cust
    )
    assert dec_cust_same_grp.kind == DecisionKind.REPAIR
    assert dec_cust_same_grp.reason == "dimension_update"

    # 7. CLI workload shape extraction from SQL must derive join_cols (including
    # customers.region_id and orders.customer_id) from the SQL AST without a
    # hand-written column list, never classifying changes to them as PRESERVE (KEEP).
    from smplcache.cli import _shape_from_dict
    from smplcache.smplcache import certify_event

    cli_cust_shape = _shape_from_dict(
        {"name": "rev_by_region_cust", "relation": "customers", "sql": sql}
    )
    assert "region_id" in cli_cust_shape.fingerprint()
    assert certify_event(cli_cust_shape, evt_cust_region).kind != DecisionKind.PRESERVE

    cli_order_shape = _shape_from_dict(
        {"name": "rev_by_region_ord", "relation": "orders", "sql": sql}
    )
    assert "customer_id" in cli_order_shape.fingerprint()
    assert (
        certify_event(cli_order_shape, evt_order_same_region).kind
        != DecisionKind.PRESERVE
    )
