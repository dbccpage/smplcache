# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Property-based and regression test suite for the Debezium / PostgreSQL logical
replication WAL adapter and stream harness (`smplcache.debezium`).

Covers:
1. Real Debezium envelopes (`schema` + `payload`, `op` in c/r/u/d/t/m, string
   and `VariableScaleDecimal` encodings) and PostgreSQL `wal2json` v2 payloads.
2. TOAST unavailable columns (`__debezium_unavailable_value` and `toast: true`):
   - Unavailable TOAST values on any column in `shape.fingerprint()` always fail
     closed (`INVALIDATE`), never `REPAIR` or `PRESERVE`.
   - Unavailable TOAST values on columns disjoint from `shape.fingerprint()`
     (e.g. `notes`) preserve or repair accurately against the SQLite oracle.
3. Transaction boundaries (`BEGIN`, `COMMIT`/`END`, `ROLLBACK`/`ABORT`):
   - Rolled-back transactions discard uncommitted writes without mutating cache.
   - Committed transactions apply buffered events in order and match the oracle.
   - Boundary violations (mismatched `tx_id`, mismatched `event_count`, nested
     `BEGIN`, or dangling uncommitted transactions in strict mode) fail closed
     (`INVALIDATE` with `transaction_boundary_violation`).
4. Schema evolution events:
   - Explicit DDL messages (`ALTER TABLE`, `wal2json` action `M`, `tableChanges`),
     `TRUNCATE` (`op="t"`, `action="T"`), and implicit mid-stream column-type or
     column-set drift fail closed (`INVALIDATE` with `schema_evolution` or
     `truncate_event`).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import os
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st

from smplcache import (
    DEBEZIUM_UNAVAILABLE_VALUE,
    AggregateCache,
    DecisionKind,
    QueryShape,
    WalStreamProcessor,
    adapt_wal_payload,
    certify_event,
    parse_sql,
    process_event,
)
from tests.oracle import query_oracle, rebuild_cache
from tests.test_oracle_props import (
    GROUP_KEY_STRATEGY,
    NOTES_STRATEGY,
    NUMERIC_STRATEGY,
    REGION_STRATEGY,
    STATUS_STRATEGY,
    TABLE_COLUMNS,
    fragment_query_strategy,
    row_strategy,
    table_strategy,
)

WAL_MAX_EXAMPLES = int(os.environ.get("SMPLCACHE_WAL_HYPOTHESIS_EXAMPLES", "1000"))


def _build_debezium_schema_block(
    extra_col: str | None = None,
    amount_type: str = "double",
) -> dict[str, Any]:
    fields = [
        {"field": "id", "type": "int32"},
        {"field": "customer_id", "type": "string"},
        {"field": "region", "type": "string"},
        {"field": "status", "type": "string"},
        {
            "field": "amount",
            "type": amount_type,
            "name": "org.apache.kafka.connect.data.Decimal",
        },
        {
            "field": "quantity",
            "type": amount_type,
            "name": "org.apache.kafka.connect.data.Decimal",
        },
        {"field": "notes", "type": "string"},
    ]
    if extra_col:
        fields.append({"field": extra_col, "type": "string"})
    return {
        "type": "struct",
        "fields": [
            {"field": "before", "type": "struct", "fields": fields},
            {"field": "after", "type": "struct", "fields": fields},
        ],
    }


def _encode_debezium_row(
    row: dict[str, Any] | None,
    *,
    toast_cols: set[str] = frozenset(),  # type: ignore[assignment]
    decimal_mode: str = "string",
) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in toast_cols:
            out[k] = DEBEZIUM_UNAVAILABLE_VALUE
            continue
        if isinstance(v, Decimal):
            if decimal_mode == "string":
                out[k] = str(v)
            elif decimal_mode == "struct":
                tup = v.as_tuple()
                scale = -int(tup.exponent) if isinstance(tup.exponent, int) else 0
                unscaled = int(v * (Decimal(10) ** scale))
                out[k] = {"scale": scale, "value": str(unscaled)}
            else:
                out[k] = float(v)
        else:
            out[k] = v
    return out


def _encode_wal2json_columns(
    row: dict[str, Any],
    *,
    toast_cols: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    col_types = {
        "id": "integer",
        "customer_id": "text",
        "region": "text",
        "status": "text",
        "amount": "numeric",
        "quantity": "numeric",
        "notes": "text",
    }
    cols: list[dict[str, Any]] = []
    for k, v in row.items():
        c_type = col_types.get(k, "text")
        if k in toast_cols:
            cols.append({"name": k, "type": c_type, "toast": True})
        else:
            enc_v = str(v) if isinstance(v, Decimal) else v
            cols.append({"name": k, "type": c_type, "value": enc_v})
    return cols


@st.composite
def wal_stream_scenario_strategy(
    draw: st.DrawFn,
) -> tuple[
    QueryShape,
    str,
    list[dict[str, Any]],
    list[
        tuple[str, list[dict[str, Any]], list[tuple[str, int | None, dict[str, Any]]]]
    ],
]:
    """
    Generates `(shape, sql, initial_rows, tx_blocks)` where each item in
    `tx_blocks` is `(block_kind, wal_messages, db_mutations)`.
    `db_mutations` are applied to the ground-truth SQLite table ONLY if the
    transaction commits (or is an auto-committed event).
    """
    sql, dialect, sec_cols = draw(fragment_query_strategy())
    res = parse_sql(sql, dialect=dialect)
    assert res.is_supported and res.shape is not None
    shape = replace(res.shape, security_cols=sec_cols)

    initial_rows = draw(table_strategy())
    sim_rows = [dict(r) for r in initial_rows]
    next_id = len(sim_rows) + 1

    wire_format = draw(st.sampled_from(["debezium", "wal2json"]))
    decimal_mode = draw(st.sampled_from(["string", "struct", "double"]))
    n_blocks = draw(st.integers(min_value=1, max_value=4))

    tx_blocks: list[
        tuple[
            str,
            list[dict[str, Any]],
            list[tuple[str, int | None, dict[str, Any]]],
        ]
    ] = []

    for b_idx in range(n_blocks):
        tx_id = f"tx_{b_idx + 1}"
        block_mode = draw(
            st.sampled_from(
                [
                    "committed_tx",
                    "rolled_back_tx",
                    "bad_event_count_tx",
                    "autocommit_single",
                    "ddl_schema_change",
                    "truncate_table",
                ]
            )
        )

        if block_mode == "ddl_schema_change":
            target_tbl = draw(st.sampled_from(["public.orders", "public.customers"]))
            if wire_format == "debezium":
                msg = {
                    "schema": {"type": "struct", "fields": []},
                    "payload": {
                        "source": {
                            "schema": "public",
                            "table": target_tbl.split(".")[-1],
                            "lsn": 1000 + b_idx,
                        },
                        "ddl": f"ALTER TABLE {target_tbl} ADD COLUMN promo_code TEXT",
                    },
                }
            else:
                msg = {
                    "action": "M",
                    "lsn": f"0/{1000 + b_idx}",
                    "prefix": "ddl",
                    "content": f"ALTER TABLE {target_tbl} ADD COLUMN promo_code TEXT",
                }
            tx_blocks.append(("ddl", [msg], []))
            continue

        if block_mode == "truncate_table":
            if wire_format == "debezium":
                msg = {
                    "payload": {
                        "op": "t",
                        "source": {
                            "schema": "public",
                            "table": "orders",
                            "lsn": 2000 + b_idx,
                        },
                    }
                }
            else:
                msg = {
                    "action": "T",
                    "schema": "public",
                    "table": "orders",
                    "lsn": f"0/{2000 + b_idx}",
                }
            sim_rows.clear()
            tx_blocks.append(("truncate", [msg], [("truncate", None, {})]))
            continue

        n_ops = 1 if block_mode == "autocommit_single" else draw(st.integers(1, 3))
        wal_msgs: list[dict[str, Any]] = []
        planned_mutations: list[tuple[str, int | None, dict[str, Any]]] = []
        working_rows = [dict(r) for r in sim_rows]

        if block_mode in ("committed_tx", "rolled_back_tx", "bad_event_count_tx"):
            if wire_format == "debezium":
                wal_msgs.append({"status": "BEGIN", "id": tx_id})
            else:
                wal_msgs.append({"action": "B", "xid": tx_id})

        for op_idx in range(n_ops):
            op_kind = draw(
                st.sampled_from(
                    [
                        "insert",
                        "delete",
                        "update_full",
                        "update_partial_replica_default",
                        "update_toast_notes",
                        "update_toast_status",
                    ]
                )
            )
            if op_kind == "insert" or not working_rows:
                new_r = draw(row_strategy(row_id=next_id))
                next_id += 1
                if wire_format == "debezium":
                    wal_msgs.append(
                        {
                            "schema": _build_debezium_schema_block(),
                            "payload": {
                                "op": "c",
                                "before": None,
                                "after": _encode_debezium_row(
                                    new_r, decimal_mode=decimal_mode
                                ),
                                "source": {
                                    "schema": "public",
                                    "table": "orders",
                                    "lsn": 3000 + b_idx * 10 + op_idx,
                                },
                                "transaction": {"id": tx_id},
                            },
                        }
                    )
                else:
                    wal_msgs.append(
                        {
                            "action": "I",
                            "xid": tx_id,
                            "schema": "public",
                            "table": "orders",
                            "columns": _encode_wal2json_columns(new_r),
                        }
                    )
                working_rows.append(dict(new_r))
                planned_mutations.append(("insert", None, dict(new_r)))
                continue

            t_idx = draw(st.integers(0, len(working_rows) - 1))
            old_r = dict(working_rows[t_idx])

            if op_kind == "delete":
                if wire_format == "debezium":
                    wal_msgs.append(
                        {
                            "schema": _build_debezium_schema_block(),
                            "payload": {
                                "op": "d",
                                "before": _encode_debezium_row(
                                    old_r, decimal_mode=decimal_mode
                                ),
                                "after": None,
                                "source": {
                                    "schema": "public",
                                    "table": "orders",
                                    "lsn": 3000 + b_idx * 10 + op_idx,
                                },
                                "transaction": {"id": tx_id},
                            },
                        }
                    )
                else:
                    wal_msgs.append(
                        {
                            "action": "D",
                            "xid": tx_id,
                            "schema": "public",
                            "table": "orders",
                            "identity": _encode_wal2json_columns(old_r),
                        }
                    )
                working_rows.pop(t_idx)
                planned_mutations.append(("delete", t_idx, {}))
                continue

            new_r = dict(old_r)
            new_r["amount"] = draw(NUMERIC_STRATEGY)
            new_r["status"] = draw(STATUS_STRATEGY)
            new_r["customer_id"] = draw(GROUP_KEY_STRATEGY)
            new_r["region"] = draw(REGION_STRATEGY)
            new_r["notes"] = draw(NOTES_STRATEGY)

            toast_set: set[str] = set()
            before_for_wal: dict[str, Any] = dict(old_r)
            if op_kind == "update_partial_replica_default":
                before_for_wal = {"id": old_r["id"]}
            elif op_kind == "update_toast_notes":
                new_r["notes"] = old_r["notes"]
                toast_set = {"notes"}
            elif op_kind == "update_toast_status":
                new_r["status"] = old_r["status"]
                toast_set = {"status"}

            if wire_format == "debezium":
                wal_msgs.append(
                    {
                        "schema": _build_debezium_schema_block(),
                        "payload": {
                            "op": "u",
                            "before": _encode_debezium_row(
                                before_for_wal,
                                toast_cols=toast_set,
                                decimal_mode=decimal_mode,
                            ),
                            "after": _encode_debezium_row(
                                new_r,
                                toast_cols=toast_set,
                                decimal_mode=decimal_mode,
                            ),
                            "source": {
                                "schema": "public",
                                "table": "orders",
                                "lsn": 3000 + b_idx * 10 + op_idx,
                            },
                            "transaction": {"id": tx_id},
                        },
                    }
                )
            else:
                wal_msgs.append(
                    {
                        "action": "U",
                        "xid": tx_id,
                        "schema": "public",
                        "table": "orders",
                        "identity": _encode_wal2json_columns(
                            before_for_wal, toast_cols=toast_set
                        ),
                        "columns": _encode_wal2json_columns(
                            new_r, toast_cols=toast_set
                        ),
                    }
                )

            working_rows[t_idx] = dict(new_r)
            planned_mutations.append(("update", t_idx, dict(new_r)))

        if block_mode == "committed_tx":
            if wire_format == "debezium":
                wal_msgs.append({"status": "END", "id": tx_id, "event_count": n_ops})
            else:
                wal_msgs.append({"action": "C", "xid": tx_id, "event_count": n_ops})
            sim_rows = working_rows
            tx_blocks.append(("commit", wal_msgs, planned_mutations))
        elif block_mode == "rolled_back_tx":
            if wire_format == "debezium":
                wal_msgs.append({"status": "ROLLBACK", "id": tx_id})
            else:
                wal_msgs.append({"action": "R", "xid": tx_id})
            # Rolled back: sim_rows is NOT updated and planned_mutations is empty!
            tx_blocks.append(("rollback", wal_msgs, []))
        elif block_mode == "bad_event_count_tx":
            if wire_format == "debezium":
                wal_msgs.append(
                    {"status": "END", "id": tx_id, "event_count": n_ops + 5}
                )
            else:
                wal_msgs.append({"action": "C", "xid": tx_id, "event_count": n_ops + 5})
            sim_rows = working_rows
            tx_blocks.append(("bad_tx", wal_msgs, planned_mutations))
        else:
            sim_rows = working_rows
            tx_blocks.append(("autocommit", wal_msgs, planned_mutations))

    return shape, sql, initial_rows, tx_blocks


@settings(
    max_examples=WAL_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(scenario=wal_stream_scenario_strategy())
def test_debezium_wal_stream_soundness(
    scenario: tuple[
        QueryShape,
        str,
        list[dict[str, Any]],
        list[
            tuple[
                str,
                list[dict[str, Any]],
                list[tuple[str, int | None, dict[str, Any]]],
            ]
        ],
    ],
) -> None:
    shape, sql, initial_rows, tx_blocks = scenario
    table = [dict(r) for r in initial_rows]
    cache = rebuild_cache(shape, table)
    processor = WalStreamProcessor(
        shape=shape,
        numeric_cols=frozenset({"amount", "quantity"}),
    )

    oracle_before = query_oracle(shape, table, TABLE_COLUMNS, sql=sql)
    if shape.agg_fn is not None:
        assert cache.values == oracle_before

    for block_kind, wal_msgs, db_mutations in tx_blocks:
        # Apply ground-truth mutations that took effect at the end of this block
        for m_kind, m_idx, m_row in db_mutations:
            if m_kind == "insert":
                table.append(dict(m_row))
            elif m_kind == "delete" and m_idx is not None:
                table.pop(m_idx)
            elif m_kind == "update" and m_idx is not None:
                table[m_idx] = dict(m_row)
            elif m_kind == "truncate":
                table.clear()

        block_decisions = []
        for msg in wal_msgs:
            decs, cache = processor.ingest(
                msg,
                cache,
                on_invalidate=lambda: rebuild_cache(shape, table),
            )
            block_decisions.extend(decs)

        oracle_after = query_oracle(shape, table, TABLE_COLUMNS, sql=sql)

        if block_kind == "rollback":
            assert oracle_before == oracle_after
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        elif block_kind == "bad_tx":
            assert any(
                d.kind == DecisionKind.INVALIDATE
                and d.reason == "transaction_boundary_violation"
                for d in block_decisions
            )
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        elif block_kind == "truncate":
            assert any(
                d.kind == DecisionKind.INVALIDATE and d.reason == "truncate_event"
                for d in block_decisions
            )
            if shape.agg_fn is not None:
                assert cache.values == oracle_after
        else:
            if all(d.kind == DecisionKind.PRESERVE for d in block_decisions):
                assert oracle_before == oracle_after
            if shape.agg_fn is not None:
                assert cache.values == oracle_after

        oracle_before = oracle_after


@settings(
    max_examples=WAL_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    query_info=fragment_query_strategy(),
    old_row=row_strategy(row_id=1),
    new_row=row_strategy(row_id=1),
    wire_format=st.sampled_from(["debezium", "wal2json"]),
)
def test_toast_unavailable_on_fingerprint_never_repairs_or_preserves(
    query_info: tuple[str, str, frozenset[str]],
    old_row: dict[str, Any],
    new_row: dict[str, Any],
    wire_format: str,
) -> None:
    sql, dialect, sec_cols = query_info
    res = parse_sql(sql, dialect=dialect)
    assert res.is_supported and res.shape is not None
    shape = replace(res.shape, security_cols=sec_cols)

    fp = shape.fingerprint()
    for col in fp:
        if wire_format == "debezium":
            payload = {
                "schema": _build_debezium_schema_block(),
                "payload": {
                    "op": "u",
                    "before": _encode_debezium_row(old_row, toast_cols={col}),
                    "after": _encode_debezium_row(new_row, toast_cols={col}),
                    "source": {"schema": "public", "table": "orders", "lsn": 1},
                },
            }
        else:
            payload = {
                "action": "U",
                "schema": "public",
                "table": "orders",
                "identity": _encode_wal2json_columns(old_row, toast_cols={col}),
                "columns": _encode_wal2json_columns(new_row, toast_cols={col}),
            }

        evt = adapt_wal_payload(payload, numeric_cols=("amount", "quantity"))
        assert col in evt.unavailable_cols
        assert col not in evt.old
        assert col not in evt.new

        dec = certify_event(shape, evt)
        assert dec.kind == DecisionKind.INVALIDATE


def test_disjoint_toast_column_repairs_and_preserves_correctly() -> None:
    res = parse_sql(
        "SELECT customer_id, SUM(amount) FROM public.orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res.is_supported and res.shape is not None
    shape = res.shape
    cache = AggregateCache(values={"c1": Decimal("100.00")})

    # 1. `notes` is TOASTed (unavailable) while `amount` changes -> REPAIR
    payload_repair = {
        "schema": _build_debezium_schema_block(),
        "payload": {
            "op": "u",
            "before": {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": "100.00",
                "notes": DEBEZIUM_UNAVAILABLE_VALUE,
            },
            "after": {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": "150.25",
                "notes": DEBEZIUM_UNAVAILABLE_VALUE,
            },
            "source": {"schema": "public", "table": "orders", "lsn": 10},
        },
    }
    evt_repair = adapt_wal_payload(payload_repair, numeric_cols=("amount",))
    dec_repair = process_event(shape, cache, evt_repair)
    assert dec_repair.kind == DecisionKind.REPAIR
    assert cache.values["c1"] == Decimal("150.25")

    # 2. Only `notes` is TOASTed and no fingerprint column changed -> PRESERVE disjoint_columns
    payload_preserve = {
        "schema": _build_debezium_schema_block(),
        "payload": {
            "op": "u",
            "before": {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": "150.25",
                "notes": DEBEZIUM_UNAVAILABLE_VALUE,
            },
            "after": {
                "id": 1,
                "customer_id": "c1",
                "status": "paid",
                "amount": "150.25",
                "notes": DEBEZIUM_UNAVAILABLE_VALUE,
            },
            "source": {"schema": "public", "table": "orders", "lsn": 11},
        },
    }
    evt_preserve = adapt_wal_payload(payload_preserve, numeric_cols=("amount",))
    dec_preserve = process_event(shape, cache, evt_preserve)
    assert dec_preserve.kind == DecisionKind.PRESERVE
    assert dec_preserve.reason == "disjoint_columns"


def test_implicit_schema_evolution_and_strict_transaction_finalize() -> None:
    res = parse_sql(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )
    assert res.is_supported and res.shape is not None
    shape = res.shape
    cache = AggregateCache(values={"c1": Decimal(100)})
    proc = WalStreamProcessor(
        shape=shape,
        numeric_cols=frozenset({"amount"}),
        strict_transactions=True,
    )

    # First transaction establishes schema
    proc.ingest({"status": "BEGIN", "id": "tx1"}, cache)
    proc.ingest(
        {
            "schema": _build_debezium_schema_block(amount_type="double"),
            "payload": {
                "op": "c",
                "before": None,
                "after": {
                    "id": 2,
                    "customer_id": "c1",
                    "region": "us",
                    "status": "paid",
                    "amount": "50.00",
                    "quantity": "1",
                    "notes": "ok",
                },
                "source": {"schema": "public", "table": "orders", "lsn": 1},
                "transaction": {"id": "tx1"},
            },
        },
        cache,
    )
    decs_tx1, cache = proc.ingest(
        {"status": "END", "id": "tx1", "event_count": 1}, cache
    )
    assert len(decs_tx1) == 1
    assert decs_tx1[0].kind == DecisionKind.REPAIR
    assert cache.values["c1"] == Decimal("150.00")

    # Second transaction introduces a new column `discount` without explicit DDL -> implicit schema evolution!
    proc.ingest({"status": "BEGIN", "id": "tx2"}, cache)
    proc.ingest(
        {
            "schema": _build_debezium_schema_block(
                extra_col="discount", amount_type="double"
            ),
            "payload": {
                "op": "c",
                "before": None,
                "after": {
                    "id": 3,
                    "customer_id": "c1",
                    "region": "us",
                    "status": "paid",
                    "amount": "25.00",
                    "quantity": "1",
                    "notes": "ok",
                    "discount": "5.00",
                },
                "source": {"schema": "public", "table": "orders", "lsn": 2},
                "transaction": {"id": "tx2"},
            },
        },
        cache,
    )
    decs_tx2, cache = proc.ingest(
        {"status": "END", "id": "tx2", "event_count": 1}, cache
    )
    assert len(decs_tx2) == 1
    assert decs_tx2[0].kind == DecisionKind.INVALIDATE
    assert decs_tx2[0].reason == "schema_evolution"

    # Third transaction is left open at stream end in strict mode -> transaction_boundary_violation
    proc.ingest({"status": "BEGIN", "id": "tx3"}, cache)
    decs_fin, cache = proc.finalize(cache)
    assert len(decs_fin) == 1
    assert decs_fin[0].kind == DecisionKind.INVALIDATE
    assert decs_fin[0].reason == "transaction_boundary_violation"
