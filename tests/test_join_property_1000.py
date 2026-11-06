# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
1,000-iteration seeded property test over random small datasets and random
change streams (updates, inserts, deletes on every table in the 3-table join
query: `orders`, `customers`, `regions`).

After each change, the cached answer must equal the answer recomputed from the
SQLite database, or the move must be REREAD (`DecisionKind.INVALIDATE`) with a
stated reason and the answer must equal the database after the reread.
"""

from __future__ import annotations

from decimal import Decimal
import os
import random
import sqlite3
from typing import Any

import pytest

from smplcache import (
    DecisionKind,
    JoinAuxState,
    WriteEvent,
    parse_join_sql,
    process_join_event,
    rebuild_join_cache,
)

FRONT_PAGE_JOIN_SQL = (
    "SELECT r.name, SUM(o.amount_cents) "
    "FROM orders o "
    "JOIN customers c ON o.customer_id = c.id "
    "JOIN regions r ON c.region_id = r.id "
    "WHERE o.status = 'paid' "
    "GROUP BY r.name"
)

PROP_ITERATIONS = int(os.environ.get("SMPLCACHE_JOIN_PROP_ITERATIONS", "1000"))
BASE_SEED = int(os.environ.get("SMPLCACHE_PROP_SEED", "20261105"))


def _query_sqlite_oracle(conn: sqlite3.Connection) -> dict[str, Decimal]:
    rows = conn.execute(FRONT_PAGE_JOIN_SQL).fetchall()
    return {str(r[0]): Decimal(int(r[1])) for r in rows if r[1] is not None}


def _fetch_all_tables(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    conn.row_factory = sqlite3.Row
    return {
        "regions": [
            dict(r)
            for r in conn.execute("SELECT * FROM regions ORDER BY id").fetchall()
        ],
        "customers": [
            dict(r)
            for r in conn.execute("SELECT * FROM customers ORDER BY id").fetchall()
        ],
        "orders": [
            dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
        ],
    }


def run_seeded_join_stream_iteration(seed: int) -> dict[str, int]:
    """
    Execute one seeded random dataset + random change stream across `orders`,
    `customers`, and `regions`, checking every step against native SQLite.
    """
    rng = random.Random(seed)
    parsed = parse_join_sql(FRONT_PAGE_JOIN_SQL)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE regions (id INTEGER PRIMARY KEY, code TEXT, name TEXT)"
        )
        conn.execute(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, region_id INTEGER, tier TEXT)"
        )
        conn.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, amount_cents INTEGER, notes TEXT)"
        )

        region_pool = [
            (1, "na", "North America"),
            (2, "eu", "Europe"),
            (3, "apac", "Asia Pacific"),
            (4, "latam", "Latin America"),
        ]
        n_regions = rng.randint(1, len(region_pool))
        for rid, code, rname in rng.sample(region_pool, n_regions):
            conn.execute(
                "INSERT INTO regions (id, code, name) VALUES (?, ?, ?)",
                (rid, code, rname),
            )

        n_customers = rng.randint(1, 6)
        tiers = ("enterprise", "growth", "standard")
        for cid in range(1, n_customers + 1):
            rid_choice = rng.choice([1, 2, 3, 4, 99])
            conn.execute(
                "INSERT INTO customers (id, name, region_id, tier) VALUES (?, ?, ?, ?)",
                (cid, f"Cust_{cid}", rid_choice, rng.choice(tiers)),
            )

        n_orders = rng.randint(1, 10)
        statuses = ("paid", "paid", "pending", "refunded")
        for oid in range(101, 101 + n_orders):
            cid_choice = rng.choice([*range(1, n_customers + 2), 99])
            amt = rng.choice([0, 500, 1200, 4500, 10000, 25000])
            conn.execute(
                "INSERT INTO orders (id, customer_id, status, amount_cents, notes) VALUES (?, ?, ?, ?, ?)",
                (oid, cid_choice, rng.choice(statuses), amt, f"note_{oid}"),
            )
        conn.commit()

        max_total_rows = rng.choice([None, None, None, 8, 12])
        init_tables = _fetch_all_tables(conn)
        cache = rebuild_join_cache(shape, init_tables)
        aux = JoinAuxState.from_tables(
            shape, init_tables, max_total_rows=max_total_rows
        )

        db_before = _query_sqlite_oracle(conn)
        assert (
            cache.values == db_before
        ), f"[seed={seed}] Initial cache mismatch: cache={cache.values} vs db={db_before}"

        next_oid = 200
        next_cid = 20
        next_rid = 10
        counts = {"KEEP": 0, "FIX_IN_PLACE": 0, "REREAD": 0}
        n_steps = rng.randint(6, 12)

        for step_idx in range(1, n_steps + 1):
            tables_now = _fetch_all_tables(conn)
            target_table = rng.choice(("orders", "customers", "regions"))
            rows = tables_now[target_table]

            op_choices = ["insert"]
            if rows:
                op_choices.extend(["update", "update", "update", "delete"])
            op = rng.choice(op_choices)

            if target_table == "orders":
                if op == "insert":
                    oid = next_oid
                    next_oid += 1
                    new_row = {
                        "id": oid,
                        "customer_id": rng.choice([1, 2, 3, 4, 5, 99]),
                        "status": rng.choice(statuses),
                        "amount_cents": rng.choice([0, 1000, 3500, 12500]),
                        "notes": f"ins_{step_idx}",
                    }
                    conn.execute(
                        "INSERT INTO orders (id, customer_id, status, amount_cents, notes) VALUES (:id, :customer_id, :status, :amount_cents, :notes)",
                        new_row,
                    )
                    evt = WriteEvent(
                        relation="orders",
                        old={},
                        new=new_row,
                        event_id=f"s{seed}_step{step_idx}",
                    )
                elif op == "delete":
                    old_row = dict(rng.choice(rows))
                    conn.execute("DELETE FROM orders WHERE id = ?", (old_row["id"],))
                    drop_ev = rng.random() < 0.15
                    old_payload = {"id": old_row["id"]} if drop_ev else dict(old_row)
                    evt = WriteEvent(
                        relation="orders",
                        old=old_payload,
                        new={},
                        event_id=f"s{seed}_step{step_idx}",
                        explicit_operation="DELETE",
                    )
                else:
                    old_row = dict(rng.choice(rows))
                    new_row = dict(old_row)
                    kind = rng.choice(
                        (
                            "notes",
                            "amount_cents",
                            "status",
                            "customer_id",
                            "partial_old",
                        )
                    )
                    if kind == "notes":
                        new_row["notes"] = f"note_edit_{step_idx}"
                    elif kind in ("amount_cents", "partial_old"):
                        new_row["amount_cents"] = rng.choice([0, 800, 5000, 19000])
                    elif kind == "status":
                        new_row["status"] = rng.choice(statuses)
                    elif kind == "customer_id":
                        new_row["customer_id"] = rng.choice([1, 2, 3, 4, 5, 99])
                    conn.execute(
                        "UPDATE orders SET customer_id = :customer_id, status = :status, amount_cents = :amount_cents, notes = :notes WHERE id = :id",
                        new_row,
                    )
                    old_payload = (
                        {"id": old_row["id"], "status": old_row["status"]}
                        if kind == "partial_old"
                        else (
                            {"id": old_row["id"], "notes": old_row["notes"]}
                            if kind == "notes" and rng.random() < 0.5
                            else dict(old_row)
                        )
                    )
                    new_payload = (
                        {"id": new_row["id"], "notes": new_row["notes"]}
                        if kind == "notes" and "status" not in old_payload
                        else new_row
                    )
                    evt = WriteEvent(
                        relation="orders",
                        old=old_payload,
                        new=new_payload,
                        event_id=f"s{seed}_step{step_idx}",
                        explicit_operation="UPDATE",
                    )

            elif target_table == "customers":
                if op == "insert":
                    cid = next_cid
                    next_cid += 1
                    new_row = {
                        "id": cid,
                        "name": f"NewCust_{cid}",
                        "region_id": rng.choice([1, 2, 3, 4, 99]),
                        "tier": rng.choice(tiers),
                    }
                    conn.execute(
                        "INSERT INTO customers (id, name, region_id, tier) VALUES (:id, :name, :region_id, :tier)",
                        new_row,
                    )
                    evt = WriteEvent(
                        relation="customers",
                        old={},
                        new=new_row,
                        event_id=f"s{seed}_step{step_idx}",
                    )
                elif op == "delete":
                    old_row = dict(rng.choice(rows))
                    conn.execute("DELETE FROM customers WHERE id = ?", (old_row["id"],))
                    evt = WriteEvent(
                        relation="customers",
                        old=old_row,
                        new={},
                        event_id=f"s{seed}_step{step_idx}",
                    )
                else:
                    old_row = dict(rng.choice(rows))
                    new_row = dict(old_row)
                    kind = rng.choice(("name", "region_id", "partial_old"))
                    if kind == "name":
                        new_row["name"] = f"Renamed_{step_idx}"
                    else:
                        new_row["region_id"] = rng.choice([1, 2, 3, 4, 99])
                    conn.execute(
                        "UPDATE customers SET name = :name, region_id = :region_id, tier = :tier WHERE id = :id",
                        new_row,
                    )
                    old_payload = (
                        {"id": old_row["id"], "name": old_row["name"]}
                        if kind == "partial_old"
                        else dict(old_row)
                    )
                    evt = WriteEvent(
                        relation="customers",
                        old=old_payload,
                        new=new_row,
                        event_id=f"s{seed}_step{step_idx}",
                        explicit_operation="UPDATE",
                    )

            else:  # regions
                if op == "insert":
                    rid = next_rid
                    next_rid += 1
                    new_row = {
                        "id": rid,
                        "code": f"r_{rid}",
                        "name": f"Region_{rid}",
                    }
                    conn.execute(
                        "INSERT INTO regions (id, code, name) VALUES (:id, :code, :name)",
                        new_row,
                    )
                    evt = WriteEvent(
                        relation="regions",
                        old={},
                        new=new_row,
                        event_id=f"s{seed}_step{step_idx}",
                    )
                elif op == "delete":
                    old_row = dict(rng.choice(rows))
                    conn.execute("DELETE FROM regions WHERE id = ?", (old_row["id"],))
                    evt = WriteEvent(
                        relation="regions",
                        old=old_row,
                        new={},
                        event_id=f"s{seed}_step{step_idx}",
                    )
                else:
                    old_row = dict(rng.choice(rows))
                    new_row = dict(old_row)
                    kind = rng.choice(("code", "name", "partial_old"))
                    if kind == "code":
                        new_row["code"] = f"code_{step_idx}"
                    else:
                        new_row["name"] = rng.choice(
                            (
                                "North America",
                                "Europe",
                                "Asia Pacific",
                                "Latin America",
                                f"Region_Custom_{step_idx}",
                            )
                        )
                    conn.execute(
                        "UPDATE regions SET code = :code, name = :name WHERE id = :id",
                        new_row,
                    )
                    old_payload = (
                        {"id": old_row["id"], "code": old_row["code"]}
                        if kind == "partial_old"
                        else dict(old_row)
                    )
                    evt = WriteEvent(
                        relation="regions",
                        old=old_payload,
                        new=new_row,
                        event_id=f"s{seed}_step{step_idx}",
                        explicit_operation="UPDATE",
                    )

            conn.commit()
            db_after = _query_sqlite_oracle(conn)
            decision = process_join_event(shape, cache, evt, aux)

            if decision.kind == DecisionKind.PRESERVE:
                counts["KEEP"] += 1
                assert cache.values == db_after, (
                    f"[seed={seed} step={step_idx}] KEEP answer differed from DB! "
                    f"event={evt} reason={decision.reason} cache={cache.values} db={db_after}"
                )
            elif decision.kind == DecisionKind.REPAIR:
                counts["FIX_IN_PLACE"] += 1
                assert cache.values == db_after, (
                    f"[seed={seed} step={step_idx}] FIX IN PLACE answer differed from DB! "
                    f"event={evt} reason={decision.reason} cache={cache.values} db={db_after}"
                )
            elif decision.kind == DecisionKind.INVALIDATE:
                counts["REREAD"] += 1
                assert (
                    decision.reason and decision.reason.strip()
                ), f"[seed={seed} step={step_idx}] REREAD missing stated reason!"
                fresh_tables = _fetch_all_tables(conn)
                cache = rebuild_join_cache(shape, fresh_tables)
                aux = JoinAuxState.from_tables(
                    shape,
                    fresh_tables,
                    max_total_rows=max_total_rows,
                    prior_state=aux,
                )
                assert cache.values == db_after, (
                    f"[seed={seed} step={step_idx}] REREAD answer differed from DB after reread! "
                    f"reason={decision.reason} cache={cache.values} db={db_after}"
                )
            else:
                pytest.fail(
                    f"[seed={seed} step={step_idx}] Unexpected decision kind: {decision.kind}"
                )

            db_before = db_after

        return counts
    finally:
        conn.close()


def test_property_1000_seeded_random_datasets_and_change_streams(
    record_property: Any,
) -> None:
    """
    Run 1,000 seeded iterations of random small datasets and random change streams
    (updates, inserts, deletes on every table in the query). Keeps the base seed
    and last seed in test output/properties so any failure can be replayed.
    """
    record_property("base_seed", BASE_SEED)
    record_property("iterations", PROP_ITERATIONS)
    total_counts = {"KEEP": 0, "FIX_IN_PLACE": 0, "REREAD": 0}

    for i in range(PROP_ITERATIONS):
        seed = BASE_SEED + i
        try:
            step_counts = run_seeded_join_stream_iteration(seed)
            for k, v in step_counts.items():
                total_counts[k] += v
        except Exception as exc:
            raise AssertionError(
                f"Property test failed at iteration {i} with seed={seed}. "
                f"Replay with: SMPLCACHE_PROP_SEED={seed} SMPLCACHE_JOIN_PROP_ITERATIONS=1 "
                f"pytest tests/test_join_property_1000.py"
            ) from exc

    print(
        f"Completed {PROP_ITERATIONS} seeded join property iterations "
        f"(base_seed={BASE_SEED}, seeds={BASE_SEED}..{BASE_SEED + PROP_ITERATIONS - 1}): "
        f"KEEP={total_counts['KEEP']}, FIX_IN_PLACE={total_counts['FIX_IN_PLACE']}, "
        f"REREAD={total_counts['REREAD']}"
    )
    assert total_counts["KEEP"] > 0
    assert total_counts["FIX_IN_PLACE"] > 0
    assert total_counts["REREAD"] > 0


def test_replay_seeded_iteration_is_deterministic() -> None:
    c1 = run_seeded_join_stream_iteration(20261105)
    c2 = run_seeded_join_stream_iteration(20261105)
    assert c1 == c2


def test_disjoint_column_partial_update_on_evicted_row_does_not_corrupt_aux_state() -> (
    None
):
    parsed = parse_join_sql(FRONT_PAGE_JOIN_SQL)
    assert parsed.is_supported and parsed.shape is not None
    shape = parsed.shape

    tables = {
        "regions": [
            {"id": 1, "code": "na", "name": "North America"},
            {"id": 2, "code": "eu", "name": "Europe"},
        ],
        "customers": [
            {"id": 1, "name": "Acme", "region_id": 1, "tier": "enterprise"},
            {"id": 2, "name": "Globex", "region_id": 2, "tier": "growth"},
        ],
        "orders": [
            {
                "id": 101,
                "customer_id": 1,
                "status": "paid",
                "amount_cents": 1000,
                "notes": "n1",
            },
            {
                "id": 102,
                "customer_id": 2,
                "status": "paid",
                "amount_cents": 2000,
                "notes": "n2",
            },
        ],
    }
    cache = rebuild_join_cache(shape, tables)
    aux = JoinAuxState.from_tables(shape, tables, max_rows_per_table={"orders": 1})
    assert 101 in aux.evicted_pks.get("orders", set())
    assert 102 in aux.table_rows.get("orders", {})

    # Partial CDC update to an unrelated column on evicted row 101 must KEEP
    # without re-inserting partial row 101 or evicting resident row 102.
    dec1 = process_join_event(
        shape,
        cache,
        WriteEvent(
            relation="orders",
            old={"id": 101, "notes": "n1"},
            new={"id": 101, "notes": "n1_edited"},
            event_id="ev_disjoint",
            explicit_operation="UPDATE",
        ),
        aux,
    )
    assert dec1.kind == DecisionKind.PRESERVE
    assert dec1.reason == "disjoint_columns"
    assert 102 in aux.table_rows["orders"]
    assert 101 not in aux.table_rows["orders"]

    # Resident row 102 can still be repaired in place afterward.
    dec2 = process_join_event(
        shape,
        cache,
        WriteEvent(
            relation="orders",
            old={
                "id": 102,
                "customer_id": 2,
                "status": "paid",
                "amount_cents": 2000,
                "notes": "n2",
            },
            new={
                "id": 102,
                "customer_id": 2,
                "status": "paid",
                "amount_cents": 2500,
                "notes": "n2",
            },
            event_id="ev_repair_102",
            explicit_operation="UPDATE",
        ),
        aux,
    )
    assert dec2.kind == DecisionKind.REPAIR
    assert cache.values == {"North America": Decimal(1000), "Europe": Decimal(2500)}
