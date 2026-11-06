# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0
"""
Live Container Integration Tests for SmplCache + Simplex Normal Forms (Milestone 5).

Tests against real database engines:
- PostgreSQL 16 (`psycopg`)
- SQL Server 2022 (`pymssql`)
- MongoDB 7 (`pymongo`)

When `REQUIRE_LIVE_DB_TESTS=1` (as in the GitHub Actions `live-databases` job),
missing drivers or unreachable servers cause an immediate test failure (zero skips allowed).
On local developer workstations without running Docker containers (`REQUIRE_LIVE_DB_TESTS != "1"`),
unreachable servers skip cleanly.
"""

from __future__ import annotations

from decimal import Decimal
import os
import socket
from typing import Any

import pytest

from smplcache import (
    AggregateCache,
    DecisionKind,
    FunctionalDependency,
    RelationalSchema,
    SharedJoinAuxPool,
    WalStreamProcessor,
    WriteEvent,
    adapt_wal_payload,
    encode_debezium_envelope,
    generate_sql_ddl,
    parse_join_sql,
    parse_sql,
    process_event,
    synthesize_3nf,
    tableau_chase,
)

REQUIRE_LIVE = os.environ.get("REQUIRE_LIVE_DB_TESTS", "0") == "1"


def _check_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _skip_or_fail(msg: str) -> None:
    if REQUIRE_LIVE:
        pytest.fail(f"[REQUIRE_LIVE_DB_TESTS=1] {msg}")
    else:
        pytest.skip(msg)


def test_live_postgres16_3nf_lift_wal_and_cold_rebuild() -> None:
    host = os.environ.get("PGHOST", "127.0.0.1")
    port = int(os.environ.get("PGPORT", "5432"))
    user = os.environ.get("PGUSER", "postgres")
    password = os.environ.get("PGPASSWORD", "postgres")
    dbname = os.environ.get("PGDATABASE", "postgres")

    if not _check_tcp(host, port):
        _skip_or_fail(f"PostgreSQL 16 not reachable at {host}:{port}")

    try:
        import psycopg
    except ImportError as exc:
        _skip_or_fail(f"psycopg not installed: {exc}")
        return

    schema = RelationalSchema(
        name="pg_orders_raw",
        attributes=frozenset(
            {"order_id", "customer_id", "region", "tier", "status", "amount"}
        ),
        functional_dependencies=(
            FunctionalDependency(
                determinant=frozenset({"order_id"}),
                dependent=frozenset({"customer_id", "status", "amount"}),
            ),
            FunctionalDependency(
                determinant=frozenset({"customer_id"}),
                dependent=frozenset({"region", "tier"}),
            ),
        ),
        column_types={
            "order_id": "INTEGER",
            "customer_id": "INTEGER",
            "region": "VARCHAR(64)",
            "tier": "VARCHAR(64)",
            "status": "VARCHAR(64)",
            "amount": "NUMERIC(12, 2)",
        },
    )

    tables_3nf = synthesize_3nf(schema)
    chase_res = tableau_chase(schema, [t.attributes for t in tables_3nf])
    assert chase_res.is_lossless is True

    ddl_bundle = generate_sql_ddl(tables_3nf, dialect="postgres")
    assert len(ddl_bundle.table_order) == 2

    conn_str = (
        f"host={host} port={port} user={user} password={password} dbname={dbname}"
    )
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            for t_name in reversed(ddl_bundle.table_order):
                cur.execute(f"DROP TABLE IF EXISTS {t_name} CASCADE;")
            for stmt in ddl_bundle.statements:
                cur.execute(stmt)

            cust_table = next(t.name for t in tables_3nf if "region" in t.attributes)
            ord_table = next(t.name for t in tables_3nf if "order_id" in t.attributes)

            cur.execute(
                f"INSERT INTO {cust_table} (customer_id, region, tier) VALUES "
                "(10, 'us', 'gold'), (20, 'eu', 'silver');"
            )
            cur.execute(
                f"INSERT INTO {ord_table} (order_id, customer_id, status, amount) VALUES "
                "(1, 10, 'paid', 100.00), "
                "(2, 10, 'paid', 50.00), "
                "(3, 20, 'paid', 200.00);"
            )

            # Verify FK constraint enforcement on live PostgreSQL 16
            with pytest.raises(psycopg.Error):
                cur.execute(
                    f"INSERT INTO {ord_table} (order_id, customer_id, status, amount) "
                    "VALUES (999, 9999, 'paid', 10.00);"
                )

            join_sql = (
                f"SELECT c.region, SUM(o.amount) "
                f"FROM {ord_table} o JOIN {cust_table} c ON o.customer_id = c.customer_id "
                f"WHERE o.status = 'paid' GROUP BY c.region"
            )
            pk_map = {ord_table: "order_id", cust_table: "customer_id"}
            jshape = parse_join_sql(join_sql, primary_keys=pk_map).shape
            assert jshape is not None

            pool = SharedJoinAuxPool(
                [jshape],
                max_total_rows=50,
                eviction_policy="wtinylfu",
                primary_keys=pk_map,
            )
            pool.rebuild(
                {
                    cust_table: [
                        {"customer_id": 10, "region": "us", "tier": "gold"},
                        {"customer_id": 20, "region": "eu", "tier": "silver"},
                    ],
                    ord_table: [
                        {
                            "order_id": 1,
                            "customer_id": 10,
                            "status": "paid",
                            "amount": Decimal("100.00"),
                        },
                        {
                            "order_id": 2,
                            "customer_id": 10,
                            "status": "paid",
                            "amount": Decimal("50.00"),
                        },
                        {
                            "order_id": 3,
                            "customer_id": 20,
                            "status": "paid",
                            "amount": Decimal("200.00"),
                        },
                    ],
                }
            )

            # 1. Disjoint column update on customer tier -> PRESERVE (Theorem 9 lift benefit)
            cur.execute(
                f"UPDATE {cust_table} SET tier = 'platinum' WHERE customer_id = 10;"
            )
            wal_proc = WalStreamProcessor(
                shape=jshape,
                aux_state=pool.aux_state,
                numeric_cols=frozenset({"amount"}),
            )
            ev_tier = adapt_wal_payload(
                encode_debezium_envelope(
                    WriteEvent(
                        relation=cust_table,
                        old={"customer_id": 10, "region": "us", "tier": "gold"},
                        new={"customer_id": 10, "region": "us", "tier": "platinum"},
                    ),
                    tx_id="501",
                    lsn=1001,
                ),
                numeric_cols={"amount"},
            )
            assert ev_tier is not None
            assert ev_tier.boundary_clock == "1001"
            d_tier = pool.process_event(ev_tier)[jshape.name]
            assert d_tier.kind == DecisionKind.PRESERVE
            assert d_tier.certificate.boundary_clock == "1001"
            assert wal_proc.boundary_clock is None

            # 2. Repairable order amount update -> REPAIR
            cur.execute(f"UPDATE {ord_table} SET amount = 85.00 WHERE order_id = 2;")
            ev_amt = adapt_wal_payload(
                encode_debezium_envelope(
                    WriteEvent(
                        relation=ord_table,
                        old={
                            "order_id": 2,
                            "customer_id": 10,
                            "status": "paid",
                            "amount": "50.00",
                        },
                        new={
                            "order_id": 2,
                            "customer_id": 10,
                            "status": "paid",
                            "amount": "85.00",
                        },
                    ),
                    tx_id="502",
                    lsn=1002,
                ),
                numeric_cols={"amount"},
            )
            assert ev_amt is not None
            d_amt = pool.process_event(ev_amt)[jshape.name]
            assert d_amt.kind == DecisionKind.REPAIR

            cur.execute(
                f"SELECT c.region, SUM(o.amount) "
                f"FROM {ord_table} o JOIN {cust_table} c ON o.customer_id = c.customer_id "
                f"WHERE o.status = 'paid' GROUP BY c.region"
            )
            pg_live_groups = {row[0]: Decimal(str(row[1])) for row in cur.fetchall()}
            assert pool.caches[jshape.name].values == pg_live_groups

            for t_name in reversed(ddl_bundle.table_order):
                cur.execute(f"DROP TABLE IF EXISTS {t_name} CASCADE;")


def test_live_mssql2022_3nf_bcnf_lift_and_join_oracle() -> None:
    host = os.environ.get("MSSQL_HOST", "127.0.0.1")
    port = int(os.environ.get("MSSQL_PORT", "1433"))
    user = os.environ.get("MSSQL_USER", "sa")
    password = os.environ.get("MSSQL_SA_PASSWORD", "SmplCache!StrongPass2026")

    if not _check_tcp(host, port):
        _skip_or_fail(f"SQL Server 2022 not reachable at {host}:{port}")

    try:
        import pymssql
    except ImportError as exc:
        _skip_or_fail(f"pymssql not installed: {exc}")
        return

    schema = RelationalSchema(
        name="ms_orders_raw",
        attributes=frozenset(
            {"order_id", "customer_id", "region", "tier", "status", "amount"}
        ),
        functional_dependencies=(
            FunctionalDependency(
                determinant=frozenset({"order_id"}),
                dependent=frozenset({"customer_id", "status", "amount"}),
            ),
            FunctionalDependency(
                determinant=frozenset({"customer_id"}),
                dependent=frozenset({"region", "tier"}),
            ),
        ),
        column_types={
            "order_id": "INT",
            "customer_id": "INT",
            "region": "NVARCHAR(64)",
            "tier": "NVARCHAR(64)",
            "status": "NVARCHAR(64)",
            "amount": "DECIMAL(12, 2)",
        },
    )

    tables_3nf = synthesize_3nf(schema)
    chase_res = tableau_chase(schema, [t.attributes for t in tables_3nf])
    assert chase_res.is_lossless is True

    ddl_bundle = generate_sql_ddl(tables_3nf, dialect="mssql")

    conn = pymssql.connect(
        server=host,
        port=str(port),
        user=user,
        password=password,
        database="tempdb",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            for t_name in reversed(ddl_bundle.table_order):
                cur.execute(f"DROP TABLE IF EXISTS {t_name};")
            for stmt in ddl_bundle.statements:
                cur.execute(stmt)

            cust_table = next(t.name for t in tables_3nf if "region" in t.attributes)
            ord_table = next(t.name for t in tables_3nf if "order_id" in t.attributes)

            cur.execute(
                f"INSERT INTO {cust_table} (customer_id, region, tier) VALUES "
                "(10, 'us', 'gold'), (20, 'eu', 'silver');"
            )
            cur.execute(
                f"INSERT INTO {ord_table} (order_id, customer_id, status, amount) VALUES "
                "(1, 10, 'paid', 120.00), "
                "(2, 10, 'paid', 80.00), "
                "(3, 20, 'paid', 150.00);"
            )

            # Seed AggregateCache.from_group_state() directly from live SQL Server 2022 GROUP BY
            ord_shape = parse_sql(
                f"SELECT customer_id, AVG(amount) FROM {ord_table} WHERE status = 'paid' GROUP BY customer_id"
            ).shape
            assert ord_shape is not None

            cur.execute(
                f"SELECT customer_id, SUM(amount) AS sum_val, COUNT(amount) AS non_null_count, COUNT(*) AS row_count "
                f"FROM {ord_table} WHERE status = 'paid' GROUP BY customer_id"
            )
            group_rows = [
                {
                    "customer_id": int(r[0]),
                    "sum_val": Decimal(str(r[1])),
                    "non_null_count": int(r[2]),
                    "row_count": int(r[3]),
                }
                for r in cur.fetchall()
            ]
            cache = AggregateCache.from_group_state(ord_shape, group_rows)
            assert cache.values == {10: Decimal("100.00"), 20: Decimal("150.00")}

            # Apply live update in SQL Server 2022 and repair in place in SmplCache
            cur.execute(f"UPDATE {ord_table} SET amount = 140.00 WHERE order_id = 2;")
            ev = WriteEvent(
                relation=ord_table,
                old={
                    "order_id": 2,
                    "customer_id": 10,
                    "status": "paid",
                    "amount": Decimal("80.00"),
                },
                new={
                    "order_id": 2,
                    "customer_id": 10,
                    "status": "paid",
                    "amount": Decimal("140.00"),
                },
                changed_cols={"amount"},
                explicit_operation="UPDATE",
            )
            d = process_event(ord_shape, cache, ev)
            assert d.kind == DecisionKind.REPAIR

            cur.execute(
                f"SELECT customer_id, AVG(amount) FROM {ord_table} WHERE status = 'paid' GROUP BY customer_id"
            )
            mssql_live_avg = {int(r[0]): Decimal(str(r[1])) for r in cur.fetchall()}
            assert cache.values == mssql_live_avg

            for t_name in reversed(ddl_bundle.table_order):
                cur.execute(f"DROP TABLE IF EXISTS {t_name};")
    finally:
        conn.close()


def test_live_mongodb7_json_schema_and_cdc_projection_certification() -> None:
    host = os.environ.get("MONGO_HOST", "127.0.0.1")
    port = int(os.environ.get("MONGO_PORT", "27017"))

    if not _check_tcp(host, port):
        _skip_or_fail(f"MongoDB 7 not reachable at {host}:{port}")

    try:
        import pymongo
    except ImportError as exc:
        _skip_or_fail(f"pymongo not installed: {exc}")
        return

    client: Any = pymongo.MongoClient(
        host=host, port=port, serverSelectionTimeoutMS=3000
    )
    try:
        db = client["smplcache_live_test"]
        coll_name = "orders_normalized"
        db.drop_collection(coll_name)

        validator = {
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["order_id", "region", "status", "amount_cents"],
                "properties": {
                    "order_id": {"bsonType": "int"},
                    "region": {"bsonType": "string"},
                    "status": {"bsonType": "string"},
                    "amount_cents": {"bsonType": "int"},
                    "FulfillmentNotes": {"bsonType": "string"},
                },
            }
        }
        coll = db.create_collection(coll_name, validator=validator)

        initial_docs = [
            {
                "order_id": 1,
                "region": "us",
                "status": "paid",
                "amount_cents": 1000,
                "FulfillmentNotes": "ship-fast",
            },
            {
                "order_id": 2,
                "region": "us",
                "status": "paid",
                "amount_cents": 2500,
                "FulfillmentNotes": "standard",
            },
            {
                "order_id": 3,
                "region": "eu",
                "status": "paid",
                "amount_cents": 4000,
                "FulfillmentNotes": "priority",
            },
        ]
        coll.insert_many(initial_docs)

        shape = parse_sql(
            f"SELECT region, SUM(amount_cents) FROM {coll_name} WHERE status = 'paid' GROUP BY region"
        ).shape
        assert shape is not None

        cache = AggregateCache.from_rows(shape, initial_docs)

        # 1. Update disjoint document field FulfillmentNotes -> PRESERVE
        coll.update_one({"order_id": 1}, {"$set": {"FulfillmentNotes": "delivered"}})
        ev_preserve = WriteEvent(
            relation=coll_name,
            old={
                "order_id": 1,
                "region": "us",
                "status": "paid",
                "amount_cents": 1000,
                "FulfillmentNotes": "ship-fast",
            },
            new={
                "order_id": 1,
                "region": "us",
                "status": "paid",
                "amount_cents": 1000,
                "FulfillmentNotes": "delivered",
            },
            changed_cols={"FulfillmentNotes"},
            explicit_operation="UPDATE",
        )
        d1 = process_event(shape, cache, ev_preserve)
        assert d1.kind == DecisionKind.PRESERVE

        # 2. Update amount_cents -> REPAIR
        coll.update_one({"order_id": 2}, {"$set": {"amount_cents": 3000}})
        ev_repair = WriteEvent(
            relation=coll_name,
            old={
                "order_id": 2,
                "region": "us",
                "status": "paid",
                "amount_cents": 2500,
            },
            new={
                "order_id": 2,
                "region": "us",
                "status": "paid",
                "amount_cents": 3000,
            },
            changed_cols={"amount_cents"},
            explicit_operation="UPDATE",
        )
        d2 = process_event(shape, cache, ev_repair)
        assert d2.kind == DecisionKind.REPAIR

        pipeline = [
            {"$match": {"status": "paid"}},
            {
                "$group": {
                    "_id": "$region",
                    "total_cents": {"$sum": "$amount_cents"},
                }
            },
        ]
        mongo_live = {
            doc["_id"]: Decimal(int(doc["total_cents"]))
            for doc in coll.aggregate(pipeline)
        }
        assert cache.values == mongo_live
        db.drop_collection(coll_name)
    finally:
        client.close()
