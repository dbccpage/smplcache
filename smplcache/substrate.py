# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0
"""
Cold-Rebuild SQL Substrate Adapter for SmplCache (Milestone 4).

Executes grouped cold-miss rebuild queries (`SELECT <group_cols>, SUM(...), COUNT(...), COUNT(*) ...`)
against a SQLite (`sqlite3`) or DuckDB (`duckdb`) substrate and seeds
`AggregateCache.from_group_state()`, `SortedMultisetExtremumCache`, and `JoinCacheManager`
without pulling raw base table scans into Python when only group statistics are needed.
"""

from __future__ import annotations

from decimal import Decimal
import importlib
import re
import sqlite3
from typing import Any, Literal, Mapping, Sequence

from smplcache.join import JoinCacheManager, MultiTableJoinShape
from smplcache.normal_forms import SortedMultisetExtremumCache
from smplcache.smplcache import (
    AggregateCache,
    Decision,
    DecisionKind,
    QueryShape,
    WriteEvent,
    normalize_relation,
    process_event,
)

_duckdb_mod: Any = None
try:
    _duckdb_mod = importlib.import_module("duckdb")
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


def _quote_ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())
    if not cleaned:
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f'"{cleaned.lower()}"'


def _format_sql_literal(val: Any) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, Decimal)):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def _db_bind_val(val: Any) -> Any:
    if isinstance(val, Decimal):
        return str(val)
    return val


class _SqliteDecimalSum:
    """Custom SQLite aggregate function accumulating exact `Decimal` sums without IEEE-754 float drift."""

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.has_value = False

    def step(self, value: Any) -> None:
        if value is not None:
            self.total += Decimal(str(value))
            self.has_value = True

    def finalize(self) -> str | None:
        return str(self.total) if self.has_value else None


class ColdRebuildSubstrate:
    """
    SQL substrate adapter (supporting built-in `sqlite3` and optional `duckdb`)
    that executes grouped cold-miss rebuild queries and seeds `AggregateCache.from_group_state()`,
    `SortedMultisetExtremumCache`, and `JoinCacheManager`.
    """

    def __init__(
        self,
        connection: Any | None = None,
        *,
        backend: Literal["sqlite", "duckdb"] = "sqlite",
        database: str = ":memory:",
    ) -> None:
        self.backend = backend
        if connection is not None:
            self.conn = connection
            self._owns_conn = False
        elif backend == "sqlite":
            self.conn = sqlite3.connect(database)
            self._owns_conn = True
        elif backend == "duckdb":
            if not HAS_DUCKDB or _duckdb_mod is None:
                raise RuntimeError(
                    "duckdb is not installed in this Python environment."
                )
            self.conn = _duckdb_mod.connect(database)
            self._owns_conn = True
        else:
            raise ValueError(f"Unsupported substrate backend: {backend!r}")

        if self.backend == "sqlite" and hasattr(self.conn, "create_aggregate"):
            self.conn.create_aggregate("SMPL_DEC_SUM", 1, _SqliteDecimalSum)

        self._table_columns: dict[str, tuple[str, ...]] = {}
        self._table_decimal_cols: dict[str, set[str]] = {}
        self._table_pk_cols: dict[str, str] = {}
        self.cold_rebuild_count: int = 0

    def close(self) -> None:
        if self._owns_conn and self.conn is not None:
            self.conn.close()

    def create_and_seed_table(
        self,
        table_name: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
        pk_col: str = "id",
    ) -> None:
        rel = normalize_relation(table_name)
        q_table = _quote_ident(rel)
        lower_rows = [{str(k).lower(): v for k, v in r.items()} for r in rows]

        col_order: list[str] = []
        if columns:
            for c in columns:
                c_low = str(c).lower()
                if c_low not in col_order:
                    col_order.append(c_low)
        for r in lower_rows:
            for k_low in r.keys():
                if k_low not in col_order:
                    col_order.append(k_low)

        if not col_order:
            col_order = [pk_col.lower()]

        dec_cols: set[str] = set()
        col_defs: list[str] = []
        for c in col_order:
            sql_t = "TEXT"
            for r in lower_rows:
                v = r.get(c)
                if v is None:
                    continue
                if isinstance(v, bool):
                    sql_t = "INTEGER"
                elif isinstance(v, int):
                    sql_t = "BIGINT"
                elif isinstance(v, Decimal):
                    sql_t = "DECIMAL(38, 10)" if self.backend == "duckdb" else "NUMERIC"
                    dec_cols.add(c)
                elif isinstance(v, float):
                    sql_t = "DOUBLE"
                else:
                    sql_t = "TEXT"
                break
            col_defs.append(f"{_quote_ident(c)} {sql_t}")

        self._table_columns[rel] = tuple(col_order)
        self._table_decimal_cols[rel] = dec_cols
        self._table_pk_cols[rel] = pk_col.lower()

        self.conn.execute(f"DROP TABLE IF EXISTS {q_table}")
        self.conn.execute(f"CREATE TABLE {q_table} ({', '.join(col_defs)})")

        if lower_rows:
            placeholders = ", ".join(["?"] * len(col_order))
            q_cols = ", ".join(_quote_ident(c) for c in col_order)
            insert_sql = f"INSERT INTO {q_table} ({q_cols}) VALUES ({placeholders})"
            batch = [
                tuple(_db_bind_val(r.get(c)) for c in col_order) for r in lower_rows
            ]
            self.conn.executemany(insert_sql, batch)

        if hasattr(self.conn, "commit"):
            self.conn.commit()

    def apply_write_event(
        self,
        event: WriteEvent,
        *,
        pk_col: str | None = None,
    ) -> None:
        rel = normalize_relation(event.relation)
        q_table = _quote_ident(rel)
        eff_pk = (pk_col or self._table_pk_cols.get(rel, "id")).lower()
        old_row = {str(k).lower(): v for k, v in (event.old or {}).items()}
        new_row = {str(k).lower(): v for k, v in (event.new or {}).items()}

        op = (event.explicit_operation or "").upper()
        if not op:
            if not old_row and new_row:
                op = "INSERT"
            elif old_row and not new_row:
                op = "DELETE"
            elif old_row and new_row:
                op = "UPDATE"

        if op == "TRUNCATE":
            self.conn.execute(f"DELETE FROM {q_table}")
        elif op == "INSERT" and new_row:
            cols = sorted(new_row.keys())
            for c in cols:
                if isinstance(new_row[c], Decimal):
                    self._table_decimal_cols.setdefault(rel, set()).add(c)
            q_cols = ", ".join(_quote_ident(c) for c in cols)
            placeholders = ", ".join(["?"] * len(cols))
            vals = tuple(_db_bind_val(new_row[c]) for c in cols)
            self.conn.execute(
                f"INSERT INTO {q_table} ({q_cols}) VALUES ({placeholders})", vals
            )
        elif op == "DELETE" and old_row:
            if eff_pk in old_row:
                self.conn.execute(
                    f"DELETE FROM {q_table} WHERE {_quote_ident(eff_pk)} = ?",
                    (_db_bind_val(old_row[eff_pk]),),
                )
        elif op == "UPDATE" and new_row:
            pk_val = old_row.get(eff_pk, new_row.get(eff_pk))
            if pk_val is not None:
                cols = sorted(new_row.keys())
                for c in cols:
                    if isinstance(new_row[c], Decimal):
                        self._table_decimal_cols.setdefault(rel, set()).add(c)
                set_clause = ", ".join(f"{_quote_ident(c)} = ?" for c in cols)
                vals = tuple(_db_bind_val(new_row[c]) for c in cols) + (
                    _db_bind_val(pk_val),
                )
                self.conn.execute(
                    f"UPDATE {q_table} SET {set_clause} WHERE {_quote_ident(eff_pk)} = ?",
                    vals,
                )

        if hasattr(self.conn, "commit"):
            self.conn.commit()

    def fetch_table_rows(self, table_name: str) -> list[dict[str, Any]]:
        rel = normalize_relation(table_name)
        q_table = _quote_ident(rel)
        cur = self.conn.execute(f"SELECT * FROM {q_table}")
        col_names = [str(desc[0]).lower() for desc in cur.description]
        dec_cols = self._table_decimal_cols.get(rel, set())
        out: list[dict[str, Any]] = []
        for raw_row in cur.fetchall():
            row_dict: dict[str, Any] = {}
            for c_name, val in zip(col_names, raw_row):
                if val is not None and c_name in dec_cols:
                    row_dict[c_name] = Decimal(str(val))
                elif isinstance(val, float):
                    row_dict[c_name] = Decimal(str(val))
                else:
                    row_dict[c_name] = val
            out.append(row_dict)
        return out

    def build_group_state_sql(
        self, shape: QueryShape | MultiTableJoinShape
    ) -> tuple[str, list[str]]:
        sum_fn = "SMPL_DEC_SUM" if self.backend == "sqlite" else "SUM"
        if isinstance(shape, MultiTableJoinShape):
            base_rel = normalize_relation(shape.base_relation)
            base_qual = shape.effective_base_qualifier
            from_sql = f"{_quote_ident(base_rel)} AS {_quote_ident(base_qual)}"
            for j in shape.joins:
                r_rel = normalize_relation(j.right_relation)
                r_qual = j.effective_right_qualifier
                l_qual = j.left_qualifier.lower()
                j_kw = "LEFT JOIN" if j.join_type == "LEFT" else "INNER JOIN"
                from_sql += (
                    f" {j_kw} {_quote_ident(r_rel)} AS {_quote_ident(r_qual)} "
                    f"ON {_quote_ident(l_qual)}.{_quote_ident(j.left_key)} = "
                    f"{_quote_ident(r_qual)}.{_quote_ident(j.right_key)}"
                )

            select_exprs: list[str] = []
            group_exprs: list[str] = []
            group_col_keys: list[str] = []
            for idx, (q, c) in enumerate(shape.group_cols):
                q_expr = f"{_quote_ident(q)}.{_quote_ident(c)}"
                alias = f"g_{idx}"
                select_exprs.append(f"{q_expr} AS {_quote_ident(alias)}")
                group_exprs.append(q_expr)
                group_col_keys.append(alias)

            if shape.agg_fn in ("sum", "avg") and shape.agg_col is not None:
                aq, ac = shape.agg_col
                agg_expr = f"{_quote_ident(aq)}.{_quote_ident(ac)}"
                select_exprs.append(f"COALESCE({sum_fn}({agg_expr}), 0) AS sum_val")
                select_exprs.append(f"COUNT({agg_expr}) AS non_null_count")
            elif shape.agg_fn == "count" and shape.agg_col is not None:
                aq, ac = shape.agg_col
                agg_expr = f"{_quote_ident(aq)}.{_quote_ident(ac)}"
                select_exprs.append("0 AS sum_val")
                select_exprs.append(f"COUNT({agg_expr}) AS non_null_count")
            else:
                select_exprs.append("0 AS sum_val")
                select_exprs.append("COUNT(*) AS non_null_count")
            select_exprs.append("COUNT(*) AS row_count")

            where_parts: list[str] = []
            for q, c, op, lit in shape.predicate_terms:
                sql_op = "=" if op == "==" else op
                where_parts.append(
                    f"{_quote_ident(q)}.{_quote_ident(c)} {sql_op} {_format_sql_literal(lit)}"
                )

            sql = f"SELECT {', '.join(select_exprs)} FROM {from_sql}"
            if where_parts:
                sql += f" WHERE {' AND '.join(where_parts)}"
            if group_exprs:
                sql += f" GROUP BY {', '.join(group_exprs)}"
            return sql, group_col_keys

        rel = normalize_relation(shape.relation)
        q_table = _quote_ident(rel)
        select_exprs = []
        group_exprs = []
        group_col_keys = []
        for c in shape.group_cols:
            c_low = c.lower()
            select_exprs.append(_quote_ident(c_low))
            group_exprs.append(_quote_ident(c_low))
            group_col_keys.append(c_low)

        if shape.agg_fn in ("sum", "avg") and shape.agg_col is not None:
            q_agg = _quote_ident(shape.agg_col)
            select_exprs.append(f"COALESCE({sum_fn}({q_agg}), 0) AS sum_val")
            select_exprs.append(f"COUNT({q_agg}) AS non_null_count")
        elif shape.agg_fn == "count" and shape.agg_col is not None:
            q_agg = _quote_ident(shape.agg_col)
            select_exprs.append("0 AS sum_val")
            select_exprs.append(f"COUNT({q_agg}) AS non_null_count")
        else:
            select_exprs.append("0 AS sum_val")
            select_exprs.append("COUNT(*) AS non_null_count")
        select_exprs.append("COUNT(*) AS row_count")

        where_parts = []
        for col, op, lit in shape.predicate.terms:
            sql_op = "=" if op == "==" else op
            where_parts.append(
                f"{_quote_ident(col)} {sql_op} {_format_sql_literal(lit)}"
            )

        sql = f"SELECT {', '.join(select_exprs)} FROM {q_table}"
        if where_parts:
            sql += f" WHERE {' AND '.join(where_parts)}"
        if group_exprs:
            sql += f" GROUP BY {', '.join(group_exprs)}"
        return sql, group_col_keys

    def _normalize_group_val(self, val: Any) -> Any:
        if isinstance(val, float):
            return Decimal(str(val))
        return val

    def query_group_state(
        self, shape: QueryShape | MultiTableJoinShape
    ) -> dict[Any, tuple[Decimal, int, int]]:
        sql, group_col_keys = self.build_group_state_sql(shape)
        cur = self.conn.execute(sql)
        rows = cur.fetchall()
        num_g = len(group_col_keys)
        is_grouped = num_g > 0
        group_stats: dict[Any, tuple[Decimal, int, int]] = {}
        if not is_grouped:
            group_stats[()] = (Decimal(0), 0, 0)

        for raw in rows:
            g_vals = tuple(self._normalize_group_val(v) for v in raw[:num_g])
            raw_sum = raw[num_g]
            raw_nn = int(raw[num_g + 1] or 0)
            raw_rc = int(raw[num_g + 2] or 0)
            if not is_grouped and raw_rc == 0:
                continue
            sum_dec = Decimal(str(raw_sum)) if raw_sum is not None else Decimal(0)
            if not is_grouped:
                g_key: Any = ()
            elif num_g == 1:
                g_key = g_vals[0]
            else:
                g_key = g_vals
            group_stats[g_key] = (sum_dec, raw_nn, raw_rc)
        return group_stats

    def rebuild_aggregate_cache(
        self, shape: QueryShape | MultiTableJoinShape
    ) -> AggregateCache:
        self.cold_rebuild_count += 1
        stats = self.query_group_state(shape)
        return AggregateCache.from_group_state(shape, stats)

    def process_or_rebuild(
        self,
        shape: QueryShape,
        cache: AggregateCache,
        event: WriteEvent,
        *,
        apply_to_substrate: bool = False,
        pk_col: str | None = None,
    ) -> tuple[Decision, AggregateCache, bool]:
        """
        Certify and process `event` against `(shape, cache)`.
        If `apply_to_substrate=True`, first applies `event` to the SQL substrate.
        When the boundary decision is `PRESERVE` or `REPAIR`, returns `(decision, cache, False)`.
        When the boundary decision is `INVALIDATE` or `UNSUPPORTED` (`REREAD`), executes
        a grouped cold rebuild on the SQL substrate via `AggregateCache.from_group_state()`
        and returns `(decision, rebuilt_cache, True)`.
        """
        if apply_to_substrate:
            self.apply_write_event(event, pk_col=pk_col)

        decision = process_event(shape, cache, event)
        if decision.kind in (DecisionKind.PRESERVE, DecisionKind.REPAIR):
            return decision, cache, False

        rebuilt = self.rebuild_aggregate_cache(shape)
        cache.values = dict(rebuilt.values)
        cache._group_state = dict(rebuilt._group_state)
        cache._from_rows = True
        return decision, cache, True

    def rebuild_join_manager(self, manager: JoinCacheManager) -> AggregateCache:
        self.cold_rebuild_count += 1
        tables = {rel: self.fetch_table_rows(rel) for rel in manager.shape.relations()}
        manager.rebuild(tables)
        return manager.cache

    def rebuild_extremum_cache(
        self, ext_cache: SortedMultisetExtremumCache
    ) -> SortedMultisetExtremumCache:
        self.cold_rebuild_count += 1
        rows = self.fetch_table_rows(ext_cache.shape.relation)
        ext_cache.rebuild(rows)
        return ext_cache


ColdRebuildAdapter = ColdRebuildSubstrate

__all__ = [
    "ColdRebuildAdapter",
    "ColdRebuildSubstrate",
    "HAS_DUCKDB",
]
