# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Debezium and PostgreSQL logical replication (pgoutput / wal2json) WAL adapter
and transaction/schema-evolution stream harness for SmplCache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from smplcache.join import (
    JoinAuxState,
    MultiTableJoinShape,
    certify_join_event,
    process_join_event,
)
from smplcache.smplcache import (
    AggregateCache,
    Certificate,
    Decision,
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
    normalize_relation,
    process_event,
)

DEBEZIUM_UNAVAILABLE_VALUE = "__debezium_unavailable_value"
WAL2JSON_UNAVAILABLE_VALUE = "__toast_unchanged__"

_DDL_TABLE_RE = re.compile(
    r"\b(?:ALTER|DROP|TRUNCATE|CREATE)\s+TABLE\s+(?:IF\s+EXISTS\s+)?([A-Za-z0-9_.\"]+)",
    re.IGNORECASE,
)


def _clean_ident(raw: str) -> str:
    return raw.replace('"', "").strip()


def _extract_debezium_schema_types(
    schema_block: Mapping[str, Any] | None,
) -> dict[str, str]:
    """
    Extract `{col_name: logical_or_field_type}` from a Debezium envelope's
    `schema` section when present.
    """
    if not isinstance(schema_block, Mapping):
        return {}
    fields = schema_block.get("fields")
    if not isinstance(fields, Sequence):
        return {}

    col_types: dict[str, str] = {}
    for top_field in fields:
        if not isinstance(top_field, Mapping):
            continue
        if top_field.get("field") in ("before", "after"):
            nested_fields = top_field.get("fields")
            if isinstance(nested_fields, Sequence):
                for nf in nested_fields:
                    if not isinstance(nf, Mapping):
                        continue
                    c_name = str(nf.get("field", "")).lower()
                    if not c_name:
                        continue
                    logical_name = str(nf.get("name", "")).lower()
                    f_type = str(nf.get("type", "")).lower()
                    col_types[c_name] = (
                        f"{f_type}:{logical_name}" if logical_name else f_type
                    )
    return col_types


def _coerce_wal_value(
    col_name: str,
    val: Any,
    col_type: str | None,
    numeric_cols_set: frozenset[str],
) -> Any:
    if val is None:
        return None
    c_type = (col_type or "").lower()
    is_float_type = "decimal" not in c_type and any(
        tok in c_type for tok in ("float", "double", "real")
    )
    is_decimal_type = (
        any(
            tok in c_type
            for tok in (
                "decimal",
                "numeric",
                "money",
            )
        )
        or col_name in numeric_cols_set
    )
    is_int_type = (
        any(
            tok in c_type
            for tok in (
                "int2",
                "int4",
                "int8",
                "int16",
                "int32",
                "int64",
                "integer",
                "bigint",
                "smallint",
            )
        )
        and not is_decimal_type
    )

    if isinstance(val, Mapping) and "value" in val and "scale" in val:
        # Debezium VariableScaleDecimal / precise struct representation
        raw_v = val.get("value")
        scale = val.get("scale", 0)
        try:
            unscaled = Decimal(str(raw_v))
            if not unscaled.is_finite():
                return val
            parts = unscaled.as_tuple()
            return Decimal((parts.sign, parts.digits, int(parts.exponent) - int(scale)))
        except (ValueError, InvalidOperation, TypeError):
            return val

    if isinstance(val, str):
        if is_float_type:
            try:
                return float(val)
            except ValueError:
                return val
        if is_decimal_type:
            try:
                return Decimal(val)
            except InvalidOperation:
                return val
        if is_int_type:
            try:
                return int(val)
            except ValueError:
                return val
    # Keep JSON floats as floats: converting them to Decimal would conceal
    # lost precision from the certifier for numeric and float database columns.
    return val


def _extract_row_and_toast(
    raw_row: Mapping[str, Any] | None,
    col_types: Mapping[str, str],
    toast_placeholders: frozenset[str],
    numeric_cols_set: frozenset[str],
) -> tuple[dict[str, Any], set[str]]:
    if not isinstance(raw_row, Mapping):
        return {}, set()
    clean_row: dict[str, Any] = {}
    unavail: set[str] = set()
    for k, v in raw_row.items():
        col = str(k).lower()
        if isinstance(v, str) and v in toast_placeholders:
            unavail.add(col)
            continue
        if isinstance(v, Mapping) and v.get("__debezium_unavailable") is True:
            unavail.add(col)
            continue
        clean_row[col] = _coerce_wal_value(col, v, col_types.get(col), numeric_cols_set)
    return clean_row, unavail


@dataclass(frozen=True)
class WalParsedRecord:
    """
    Normalized representation of a single WAL or Debezium record, which may be
    a transaction boundary (`BEGIN`, `COMMIT`, `ROLLBACK`), a `SCHEMA_CHANGE`,
    or a `DATA` event (`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`).
    """

    record_kind: str  # "BEGIN", "COMMIT", "ROLLBACK", "DATA", "SCHEMA_CHANGE"
    tx_id: str | None = None
    expected_event_count: int | None = None
    event: WriteEvent | None = None
    relation: str = ""
    column_types: dict[str, str] = field(default_factory=dict)


def parse_wal_record(
    payload: Mapping[str, Any],
    *,
    toast_placeholder: str = DEBEZIUM_UNAVAILABLE_VALUE,
    numeric_cols: Iterable[str] = (),
) -> WalParsedRecord:
    """
    Parse a Debezium envelope/payload or a PostgreSQL `wal2json` v2 / `pgoutput`
    message into a `WalParsedRecord`.
    """
    if not isinstance(payload, Mapping):
        evt = WriteEvent(
            relation="*",
            event_id="malformed_payload",
            explicit_operation="SCHEMA_CHANGE",
            schema_change=True,
        )
        return WalParsedRecord(record_kind="SCHEMA_CHANGE", event=evt, relation="*")

    toast_placeholders = frozenset(
        {toast_placeholder, DEBEZIUM_UNAVAILABLE_VALUE, WAL2JSON_UNAVAILABLE_VALUE}
    )
    numeric_cols_set = frozenset(str(c).lower() for c in numeric_cols)

    schema_block = (
        payload.get("schema")
        if isinstance(payload.get("schema"), Mapping)
        and isinstance(payload.get("payload"), Mapping)
        else None
    )
    body: Mapping[str, Any] = (
        payload["payload"] if isinstance(payload.get("payload"), Mapping) else payload
    )

    col_types = _extract_debezium_schema_types(schema_block)

    # 1. Debezium Transaction Metadata Topic (`status`: "BEGIN" | "END" | "ROLLBACK" | "ABORT")
    tx_status = str(body.get("status", "")).upper()
    if (
        tx_status in ("BEGIN", "END", "COMMIT", "ROLLBACK", "ABORT")
        and "op" not in body
    ):
        tx_id = str(body.get("id")) if body.get("id") is not None else None
        evt_cnt_raw = body.get("event_count")
        evt_cnt = int(evt_cnt_raw) if isinstance(evt_cnt_raw, int) else None
        if tx_status == "BEGIN":
            return WalParsedRecord(record_kind="BEGIN", tx_id=tx_id)
        if tx_status in ("END", "COMMIT"):
            return WalParsedRecord(
                record_kind="COMMIT",
                tx_id=tx_id,
                expected_event_count=evt_cnt,
            )
        return WalParsedRecord(record_kind="ROLLBACK", tx_id=tx_id)

    # 2. PostgreSQL wal2json v2 format (`action`: "B"|"C"|"R"|"I"|"U"|"D"|"T"|"M")
    if "action" in body and "op" not in body:
        action = str(body.get("action", "")).upper()
        xid = (
            str(body.get("xid"))
            if body.get("xid") is not None
            else (str(body.get("txId")) if body.get("txId") is not None else None)
        )
        lsn = str(body.get("lsn", body.get("nextlsn", "wal2json")))
        tbl_schema = str(body.get("schema", "")).strip()
        tbl_name = str(body.get("table", "")).strip()
        full_rel = (
            f"{tbl_schema}.{tbl_name}" if tbl_schema and tbl_name else (tbl_name or "*")
        )

        if action in ("B", "BEGIN"):
            return WalParsedRecord(record_kind="BEGIN", tx_id=xid)
        if action in ("C", "COMMIT"):
            evt_cnt_raw = body.get("event_count")
            evt_cnt = int(evt_cnt_raw) if isinstance(evt_cnt_raw, int) else None
            return WalParsedRecord(
                record_kind="COMMIT",
                tx_id=xid,
                expected_event_count=evt_cnt,
            )
        if action in ("R", "ROLLBACK", "ABORT"):
            return WalParsedRecord(record_kind="ROLLBACK", tx_id=xid)

        if action == "M":
            prefix = str(body.get("prefix", "")).lower()
            content = str(body.get("content", ""))
            m_rel = full_rel
            if m_rel == "*":
                match = _DDL_TABLE_RE.search(content)
                if match:
                    m_rel = _clean_ident(match.group(1))
            evt = WriteEvent(
                relation=m_rel,
                event_id=lsn,
                explicit_operation="SCHEMA_CHANGE",
                schema_change=True,
            )
            return WalParsedRecord(
                record_kind="SCHEMA_CHANGE",
                tx_id=xid,
                event=evt,
                relation=normalize_relation(m_rel) or "*",
            )

        if action == "T":
            evt = WriteEvent(
                relation=full_rel,
                event_id=lsn,
                explicit_operation="TRUNCATE",
            )
            return WalParsedRecord(
                record_kind="DATA",
                tx_id=xid,
                event=evt,
                relation=normalize_relation(full_rel),
            )

        w_old: dict[str, Any] = {}
        w_new: dict[str, Any] = {}
        w_unavail: set[str] = set()
        w_col_types: dict[str, str] = {}

        columns_list = body.get("columns")
        if isinstance(columns_list, Sequence):
            for col_entry in columns_list:
                if not isinstance(col_entry, Mapping):
                    continue
                c_name = str(col_entry.get("name", "")).lower()
                if not c_name:
                    continue
                c_type = str(col_entry.get("type", "")).lower()
                if c_type:
                    w_col_types[c_name] = c_type
                is_toast = bool(
                    col_entry.get("toast") or col_entry.get("unchanged_toast")
                )
                raw_val = col_entry.get("value")
                if (
                    is_toast
                    or "value" not in col_entry
                    or (isinstance(raw_val, str) and raw_val in toast_placeholders)
                ):
                    w_unavail.add(c_name)
                else:
                    w_new[c_name] = _coerce_wal_value(
                        c_name, raw_val, c_type, numeric_cols_set
                    )

        identity_list = body.get("identity")
        if isinstance(identity_list, Sequence):
            for id_entry in identity_list:
                if not isinstance(id_entry, Mapping):
                    continue
                c_name = str(id_entry.get("name", "")).lower()
                if not c_name:
                    continue
                c_type = str(id_entry.get("type", "")).lower()
                if c_type and c_name not in w_col_types:
                    w_col_types[c_name] = c_type
                is_toast = bool(
                    id_entry.get("toast") or id_entry.get("unchanged_toast")
                )
                raw_val = id_entry.get("value")
                if (
                    is_toast
                    or "value" not in id_entry
                    or (isinstance(raw_val, str) and raw_val in toast_placeholders)
                ):
                    w_unavail.add(c_name)
                else:
                    w_old[c_name] = _coerce_wal_value(
                        c_name, raw_val, c_type, numeric_cols_set
                    )

        if action == "I":
            exp_op = "INSERT"
            w_old = {}
        elif action == "D":
            exp_op = "DELETE"
            w_new = {}
        elif action == "U":
            exp_op = "UPDATE"
        else:
            exp_op = "SCHEMA_CHANGE"

        evt = WriteEvent(
            relation=full_rel,
            old=w_old,
            new=w_new,
            event_id=lsn,
            explicit_operation=exp_op,
            unavailable_cols=w_unavail,
            schema_change=(exp_op == "SCHEMA_CHANGE"),
        )
        return WalParsedRecord(
            record_kind="SCHEMA_CHANGE" if exp_op == "SCHEMA_CHANGE" else "DATA",
            tx_id=xid,
            event=evt,
            relation=normalize_relation(full_rel),
            column_types=w_col_types,
        )

    # 3. Explicit Debezium DDL / Schema Change event (`ddl` or `tableChanges`)
    if "ddl" in body or "tableChanges" in body or body.get("type") == "SCHEMA_CHANGE":
        raw_src = body.get("source")
        source: Mapping[str, Any] = raw_src if isinstance(raw_src, Mapping) else {}
        tbl = str(source.get("table", "")).strip()
        sch = str(source.get("schema", "")).strip()
        if (
            not tbl
            and isinstance(body.get("tableChanges"), Sequence)
            and body["tableChanges"]
        ):
            first_tc = body["tableChanges"][0]
            if isinstance(first_tc, Mapping):
                tbl = _clean_ident(str(first_tc.get("id", "")))
        if not tbl and isinstance(body.get("ddl"), str):
            match = _DDL_TABLE_RE.search(body["ddl"])
            if match:
                tbl = _clean_ident(match.group(1))
        full_rel = f"{sch}.{tbl}" if sch and tbl and "." not in tbl else (tbl or "*")
        lsn = str(source.get("lsn", body.get("ts_ms", "ddl")))
        tx_id = str(source.get("txId")) if source.get("txId") is not None else None
        evt = WriteEvent(
            relation=full_rel,
            event_id=lsn,
            explicit_operation="SCHEMA_CHANGE",
            schema_change=True,
        )
        return WalParsedRecord(
            record_kind="SCHEMA_CHANGE",
            tx_id=tx_id,
            event=evt,
            relation=normalize_relation(full_rel) or "*",
        )

    # 4. Standard Debezium Change Event (`op`: "c"|"r"|"u"|"d"|"t"|"m")
    op_code = str(body.get("op", "")).lower()
    raw_source = body.get("source")
    source = raw_source if isinstance(raw_source, Mapping) else {}
    raw_tx = body.get("transaction")
    tx_block: Mapping[str, Any] = raw_tx if isinstance(raw_tx, Mapping) else {}
    tx_id = (
        str(tx_block.get("id"))
        if tx_block.get("id") is not None
        else (str(source.get("txId")) if source.get("txId") is not None else None)
    )
    tbl_schema = str(source.get("schema", "")).strip()
    tbl_name = str(source.get("table", body.get("relation", ""))).strip()
    full_rel = (
        f"{tbl_schema}.{tbl_name}"
        if tbl_schema and tbl_name and "." not in tbl_name
        else (tbl_name or "*")
    )
    event_id = str(
        source.get("lsn")
        or tx_block.get("id")
        or body.get("ts_ms")
        or body.get("event_id")
        or "debezium_evt"
    )

    if op_code == "t":
        evt = WriteEvent(
            relation=full_rel,
            event_id=event_id,
            explicit_operation="TRUNCATE",
        )
        return WalParsedRecord(
            record_kind="DATA",
            tx_id=tx_id,
            event=evt,
            relation=normalize_relation(full_rel),
            column_types=col_types,
        )

    if op_code == "m":
        raw_msg = body.get("message")
        msg_block: Mapping[str, Any] = raw_msg if isinstance(raw_msg, Mapping) else {}
        content = str(msg_block.get("content", ""))
        m_rel = full_rel
        if m_rel == "*":
            match = _DDL_TABLE_RE.search(content)
            if match:
                m_rel = _clean_ident(match.group(1))
        evt = WriteEvent(
            relation=m_rel,
            event_id=event_id,
            explicit_operation="SCHEMA_CHANGE",
            schema_change=True,
        )
        return WalParsedRecord(
            record_kind="SCHEMA_CHANGE",
            tx_id=tx_id,
            event=evt,
            relation=normalize_relation(m_rel) or "*",
        )

    before_raw = body.get("before")
    after_raw = body.get("after")
    old_row, unavail_old = _extract_row_and_toast(
        before_raw if isinstance(before_raw, Mapping) else None,
        col_types,
        toast_placeholders,
        numeric_cols_set,
    )
    new_row, unavail_new = _extract_row_and_toast(
        after_raw if isinstance(after_raw, Mapping) else None,
        col_types,
        toast_placeholders,
        numeric_cols_set,
    )
    unavail_all = unavail_old | unavail_new

    if op_code in ("c", "r"):
        exp_op = "INSERT"
    elif op_code == "d":
        exp_op = "DELETE"
    elif op_code == "u":
        exp_op = "UPDATE"
    else:
        exp_op = "SCHEMA_CHANGE"

    evt = WriteEvent(
        relation=full_rel,
        old=old_row,
        new=new_row,
        event_id=event_id,
        explicit_operation=exp_op,
        unavailable_cols=unavail_all,
        schema_change=(exp_op == "SCHEMA_CHANGE"),
    )
    return WalParsedRecord(
        record_kind="SCHEMA_CHANGE" if exp_op == "SCHEMA_CHANGE" else "DATA",
        tx_id=tx_id,
        event=evt,
        relation=normalize_relation(full_rel),
        column_types=col_types,
    )


def adapt_wal_payload(
    payload: Mapping[str, Any],
    *,
    toast_placeholder: str = DEBEZIUM_UNAVAILABLE_VALUE,
    numeric_cols: Iterable[str] = (),
) -> WriteEvent:
    """
    Convert a single Debezium or PostgreSQL `wal2json` / `pgoutput` payload into
    a `WriteEvent`.
    """
    rec = parse_wal_record(
        payload,
        toast_placeholder=toast_placeholder,
        numeric_cols=numeric_cols,
    )
    if rec.event is not None:
        return rec.event
    return WriteEvent(
        relation="*",
        event_id=str(rec.tx_id or rec.record_kind),
        explicit_operation=rec.record_kind,
    )


InvalidateCallback = Callable[[], AggregateCache | tuple[AggregateCache, JoinAuxState]]


@dataclass
class WalStreamProcessor:
    """
    Stateful WAL stream processor enforcing transaction boundaries (`BEGIN`,
    `COMMIT`/`END`, `ROLLBACK`/`ABORT`) and detecting mid-stream schema
    evolution (both explicit DDL messages and implicit column-set / column-type
    drift) across both single-table `QueryShape` and multi-table
    `MultiTableJoinShape` (with `aux_state: JoinAuxState`).
    """

    shape: QueryShape | MultiTableJoinShape
    aux_state: JoinAuxState | None = None
    toast_placeholder: str = DEBEZIUM_UNAVAILABLE_VALUE
    numeric_cols: frozenset[str] = field(default_factory=frozenset)
    strict_transactions: bool = False
    is_invalidated: bool = field(default=False, init=False)
    _active_tx_id: str | None = field(default=None, init=False)
    _tx_buffer: list[WalParsedRecord] = field(default_factory=list, init=False)
    _known_table_types: dict[str, dict[str, str]] = field(
        default_factory=dict, init=False
    )
    _known_table_cols: dict[str, frozenset[str]] = field(
        default_factory=dict, init=False
    )

    def _shape_relation_name(self) -> str:
        if isinstance(self.shape, MultiTableJoinShape):
            return self.shape.base_relation
        return self.shape.relation

    def _invoke_on_invalidate(
        self,
        cache: AggregateCache,
        on_invalidate: InvalidateCallback | None,
    ) -> AggregateCache:
        self.is_invalidated = True
        if self.aux_state is not None:
            self.aux_state.is_invalidated = True
        if on_invalidate is None:
            return cache
        rebuilt = on_invalidate()
        if isinstance(rebuilt, tuple) and len(rebuilt) == 2:
            self.aux_state = rebuilt[1]
            rebuilt_cache = rebuilt[0]
        else:
            rebuilt_cache = rebuilt
        if isinstance(self.shape, MultiTableJoinShape) and (
            self.aux_state is None or self.aux_state.is_invalidated
        ):
            return rebuilt_cache
        self.is_invalidated = False
        return rebuilt_cache

    def _certify_only(self, evt: WriteEvent) -> Decision:
        if isinstance(self.shape, MultiTableJoinShape):
            return certify_join_event(self.shape, evt, self.aux_state)
        return certify_event(self.shape, evt)

    def _process_only(self, cache: AggregateCache, evt: WriteEvent) -> Decision:
        if isinstance(self.shape, MultiTableJoinShape):
            return process_join_event(self.shape, cache, evt, self.aux_state)
        return process_event(self.shape, cache, evt)

    def _make_tx_violation_decision(self, reason: str, tx_id: str | None) -> Decision:
        cert = Certificate(
            shape=self.shape.name,
            event_id=str(tx_id or "tx_boundary"),
            relation=self._shape_relation_name(),
            decision_kind=DecisionKind.INVALIDATE,
            reason_code=reason,
        )
        return Decision.invalidate(reason, cert)

    def _make_preserve_tx_decision(self, reason: str, tx_id: str | None) -> Decision:
        cert = Certificate(
            shape=self.shape.name,
            event_id=str(tx_id or "tx_boundary"),
            relation=self._shape_relation_name(),
            decision_kind=DecisionKind.PRESERVE,
            reason_code=reason,
        )
        return Decision.preserve(reason, cert)

    def _check_implicit_schema_evolution(self, rec: WalParsedRecord) -> bool:
        """
        Returns True if `rec` exhibits column-type or full-row column-set drift
        for `rec.relation` compared to previously observed schema metadata.
        """
        rel = rec.relation
        if not rel or rel == "*":
            return False

        # 1. Explicit column type metadata drift (from Debezium schema or wal2json columns)
        if rec.column_types:
            prev_types = self._known_table_types.get(rel)
            if prev_types is None:
                self._known_table_types[rel] = dict(rec.column_types)
            else:
                if set(prev_types.keys()) != set(rec.column_types.keys()):
                    self._known_table_types[rel] = dict(rec.column_types)
                    return True
                for c_name, c_type in rec.column_types.items():
                    if prev_types.get(c_name) != c_type:
                        self._known_table_types[rel] = dict(rec.column_types)
                        return True

        # 2. Full-row column key-set drift across INSERT or full-row UPDATE events
        evt = rec.event
        if evt is not None and evt.operation in ("INSERT", "UPDATE") and evt.new:
            observed_cols = frozenset(evt.new.keys()) | frozenset(
                str(c).lower() for c in evt.unavailable_cols
            )
            prev_cols = self._known_table_cols.get(rel)
            if prev_cols is None:
                self._known_table_cols[rel] = observed_cols
            elif observed_cols != prev_cols:
                self._known_table_cols[rel] = observed_cols
                return True

        return False

    def _apply_single_record(
        self,
        rec: WalParsedRecord,
        cache: AggregateCache,
        on_invalidate: InvalidateCallback | None = None,
    ) -> tuple[Decision, AggregateCache]:
        if self._check_implicit_schema_evolution(rec):
            schema_evt = WriteEvent(
                relation=rec.relation or "*",
                event_id=rec.event.event_id if rec.event else "schema_drift",
                explicit_operation="SCHEMA_CHANGE",
                schema_change=True,
            )
            dec = self._certify_only(schema_evt)
            if dec.kind == DecisionKind.INVALIDATE:
                cache = self._invoke_on_invalidate(cache, on_invalidate)
            return dec, cache

        assert rec.event is not None
        if self.is_invalidated:
            dec = self._make_tx_violation_decision("cache_requires_rebuild", rec.tx_id)
            return dec, self._invoke_on_invalidate(cache, on_invalidate)
        dec = self._process_only(cache, rec.event)
        if dec.kind in (DecisionKind.INVALIDATE, DecisionKind.UNSUPPORTED):
            cache = self._invoke_on_invalidate(cache, on_invalidate)
        return dec, cache

    def ingest(
        self,
        payload: Mapping[str, Any],
        cache: AggregateCache,
        on_invalidate: InvalidateCallback | None = None,
    ) -> tuple[list[Decision], AggregateCache]:
        """
        Ingest a single WAL payload. Returns `(decisions_emitted, updated_cache)`.
        While inside an open `BEGIN ...` transaction, data events (including
        multi-table writes across `customers`, `orders`, `warehouses`) are buffered
        until `COMMIT` (or discarded on `ROLLBACK`).
        """
        rec = parse_wal_record(
            payload,
            toast_placeholder=self.toast_placeholder,
            numeric_cols=self.numeric_cols,
        )

        if rec.record_kind == "BEGIN":
            if self._active_tx_id is not None:
                self._tx_buffer.clear()
                self._active_tx_id = None
                dec = self._make_tx_violation_decision(
                    "transaction_boundary_violation", rec.tx_id
                )
                cache = self._invoke_on_invalidate(cache, on_invalidate)
                return [dec], cache
            self._active_tx_id = rec.tx_id or "__anonymous_tx__"
            self._tx_buffer = []
            return [], cache

        if rec.record_kind == "ROLLBACK":
            if self._active_tx_id is None:
                if self.strict_transactions:
                    dec = self._make_tx_violation_decision(
                        "transaction_boundary_violation", rec.tx_id
                    )
                    cache = self._invoke_on_invalidate(cache, on_invalidate)
                    return [dec], cache
                return [
                    self._make_preserve_tx_decision("transaction_rollback", rec.tx_id)
                ], cache

            if (
                rec.tx_id is not None
                and self._active_tx_id != "__anonymous_tx__"
                and rec.tx_id != self._active_tx_id
            ):
                self._tx_buffer.clear()
                self._active_tx_id = None
                dec = self._make_tx_violation_decision(
                    "transaction_boundary_violation", rec.tx_id
                )
                cache = self._invoke_on_invalidate(cache, on_invalidate)
                return [dec], cache

            self._tx_buffer.clear()
            self._active_tx_id = None
            return [
                self._make_preserve_tx_decision("transaction_rollback", rec.tx_id)
            ], cache

        if rec.record_kind == "COMMIT":
            if self._active_tx_id is None:
                dec = self._make_tx_violation_decision(
                    "transaction_boundary_violation", rec.tx_id
                )
                cache = self._invoke_on_invalidate(cache, on_invalidate)
                return [dec], cache

            if (
                rec.tx_id is not None
                and self._active_tx_id != "__anonymous_tx__"
                and rec.tx_id != self._active_tx_id
            ):
                self._tx_buffer.clear()
                self._active_tx_id = None
                dec = self._make_tx_violation_decision(
                    "transaction_boundary_violation", rec.tx_id
                )
                cache = self._invoke_on_invalidate(cache, on_invalidate)
                return [dec], cache

            if (
                rec.expected_event_count is not None
                and rec.expected_event_count != len(self._tx_buffer)
            ):
                self._tx_buffer.clear()
                self._active_tx_id = None
                dec = self._make_tx_violation_decision(
                    "transaction_boundary_violation", rec.tx_id
                )
                cache = self._invoke_on_invalidate(cache, on_invalidate)
                return [dec], cache

            buffered = list(self._tx_buffer)
            self._tx_buffer.clear()
            self._active_tx_id = None

            decisions: list[Decision] = []
            tx_invalidated = False
            for item in buffered:
                if tx_invalidated:
                    self._check_implicit_schema_evolution(item)
                    d = self._make_tx_violation_decision(
                        "cache_requires_rebuild", item.tx_id
                    )
                    decisions.append(d)
                else:
                    d, cache = self._apply_single_record(item, cache, None)
                    decisions.append(d)
                    if d.kind in (DecisionKind.INVALIDATE, DecisionKind.UNSUPPORTED):
                        tx_invalidated = True

            if tx_invalidated:
                cache = self._invoke_on_invalidate(cache, on_invalidate)

            if not decisions:
                decisions.append(
                    self._make_preserve_tx_decision("empty_transaction", rec.tx_id)
                )
            return decisions, cache

        # DATA or SCHEMA_CHANGE record
        if self._active_tx_id is not None:
            if (
                rec.tx_id is not None
                and self._active_tx_id != "__anonymous_tx__"
                and rec.tx_id != self._active_tx_id
            ):
                self._tx_buffer.clear()
                self._active_tx_id = None
                dec = self._make_tx_violation_decision(
                    "transaction_boundary_violation", rec.tx_id
                )
                cache = self._invoke_on_invalidate(cache, on_invalidate)
                return [dec], cache
            self._tx_buffer.append(rec)
            return [], cache

        if self.strict_transactions:
            dec = self._make_tx_violation_decision(
                "transaction_boundary_violation", rec.tx_id
            )
            cache = self._invoke_on_invalidate(cache, on_invalidate)
            return [dec], cache

        d, cache = self._apply_single_record(rec, cache, on_invalidate)
        return [d], cache

    def finalize(
        self,
        cache: AggregateCache,
        on_invalidate: InvalidateCallback | None = None,
    ) -> tuple[list[Decision], AggregateCache]:
        """
        Check for dangling uncommitted transactions at the end of a WAL stream.
        If `strict_transactions` is True, an unclosed transaction triggers
        `invalidate("transaction_boundary_violation")`; otherwise uncommitted
        events are discarded as rolled back.
        """
        if self._active_tx_id is None:
            return [], cache

        dangling_tx = self._active_tx_id
        self._tx_buffer.clear()
        self._active_tx_id = None
        if self.strict_transactions:
            dec = self._make_tx_violation_decision(
                "transaction_boundary_violation", dangling_tx
            )
            cache = self._invoke_on_invalidate(cache, on_invalidate)
            return [dec], cache

        return [
            self._make_preserve_tx_decision(
                "uncommitted_transaction_discarded", dangling_tx
            )
        ], cache
