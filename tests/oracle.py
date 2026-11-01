# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Brute-force ground-truth SQLite oracle for SmplCache.

Supports both:
1. Single-table v1 `QueryShape` execution (`query_oracle`, `shape_to_sql`).
2. Multi-table foreign-key `JOIN` shapes (`JoinClause`, `MultiTableJoinShape`,
   `join_shape_to_sql`, `query_multi_table_oracle`, `evaluate_join_reference`)
   for pre-Upgrade 3 verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import re
import sqlite3
from typing import Any, Literal, Mapping, Sequence

from smplcache import (
    NULL_GROUP_KEY,
    AggregateCache,
    JoinAuxState,
    JoinClause,
    JoinType,
    MultiTableJoinShape,
    Predicate,
    QueryShape,
    normalize_relation,
    rebuild_join_cache,
)
from smplcache.smplcache import _normalize_group_atom, _to_decimal, _values_equal


class _SqliteDecimalSum:
    def __init__(self) -> None:
        self.total = Decimal(0)
        self.count = 0

    def step(self, value: Any) -> None:
        if value is not None:
            self.total += Decimal(str(value))
            self.count += 1

    def finalize(self) -> str | None:
        if self.count == 0:
            return None
        return str(self.total)


class _SqliteDecimalAvg:
    def __init__(self) -> None:
        self.total = Decimal(0)
        self.count = 0

    def step(self, value: Any) -> None:
        if value is not None:
            self.total += Decimal(str(value))
            self.count += 1

    def finalize(self) -> str | None:
        if self.count == 0:
            return None
        return str(self.total / Decimal(self.count))


_ORACLE_CONN: sqlite3.Connection | None = None
_ACTIVE_SCHEMA: tuple[str, tuple[str, ...]] | None = None

_MULTI_ORACLE_CONN: sqlite3.Connection | None = None
_ACTIVE_MULTI_SCHEMA: (
    tuple[
        tuple[tuple[str, tuple[str, ...]], ...],
        tuple[tuple[str, str, str, str], ...],
        bool,
    ]
    | None
) = None


def _attach_namespace(con: sqlite3.Connection, relation: str) -> None:
    if "." in relation:
        schema = relation.split(".")[0]
        if schema not in {row[1] for row in con.execute("PRAGMA database_list")}:
            con.execute(f'ATTACH DATABASE ":memory:" AS "{schema}"')


def _get_oracle_conn(
    relation: str, ordered_cols: tuple[str, ...]
) -> sqlite3.Connection:
    global _ORACLE_CONN, _ACTIVE_SCHEMA
    if _ORACLE_CONN is None:
        _ORACLE_CONN = sqlite3.connect(":memory:")
        _ORACLE_CONN.create_aggregate("SUM", 1, _SqliteDecimalSum)
        _ORACLE_CONN.create_aggregate("AVG", 1, _SqliteDecimalAvg)

    _attach_namespace(_ORACLE_CONN, relation)
    target_schema = (relation, ordered_cols)
    if _ACTIVE_SCHEMA != target_schema:
        if _ACTIVE_SCHEMA is not None:
            _ORACLE_CONN.execute(f"DROP TABLE IF EXISTS {_ACTIVE_SCHEMA[0]}")
        _ORACLE_CONN.execute(f"DROP TABLE IF EXISTS {relation}")
        col_defs = ", ".join(f"{c} NUMERIC" for c in ordered_cols)
        _ORACLE_CONN.execute(f"CREATE TABLE {relation} ({col_defs})")
        _ACTIVE_SCHEMA = target_schema
    else:
        _ORACLE_CONN.execute(f"DELETE FROM {relation}")

    return _ORACLE_CONN


def _to_sqlite_param(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, Decimal):
        if val == val.to_integral_value():
            return int(val)
        return float(val)
    if isinstance(val, float):
        return val
    return str(val)


def _from_sqlite_group_key(val: Any) -> Any:
    if val is None:
        return NULL_GROUP_KEY
    if isinstance(val, float):
        d = Decimal(str(val))
        if d == d.to_integral_value():
            return int(d)
        return d
    return val


def _from_sqlite_agg_val(val: Any) -> Decimal | None:
    if val is None:
        return None
    return Decimal(str(val))


def shape_to_sql(shape: QueryShape) -> str:
    """Reconstruct canonical SQL for a single-table QueryShape."""
    select_parts: list[str] = []
    for g in shape.group_cols:
        select_parts.append(g)

    if shape.agg_fn == "sum":
        select_parts.append(f"SUM({shape.agg_col})")
    elif shape.agg_fn == "count":
        select_parts.append(f"COUNT({shape.agg_col})")
    elif shape.agg_fn == "count_star":
        select_parts.append("COUNT(*)")
    elif shape.agg_fn == "avg":
        select_parts.append(f"AVG({shape.agg_col})")
    else:
        for p in sorted(shape.projection_cols):
            if p not in select_parts:
                select_parts.append(p)

    if not select_parts:
        select_parts.append("1")

    sql = f"SELECT {', '.join(select_parts)} FROM {shape.relation}"

    if shape.predicate.terms:
        where_parts: list[str] = []
        for col, op, lit in shape.predicate.terms:
            sql_op = "=" if op in ("=", "==") else ("!=" if op in ("!=", "<>") else op)
            if isinstance(lit, str):
                escaped = lit.replace("'", "''")
                where_parts.append(f"{col} {sql_op} '{escaped}'")
            else:
                where_parts.append(f"{col} {sql_op} {lit}")
        sql += " WHERE " + " AND ".join(where_parts)

    if shape.group_cols:
        sql += " GROUP BY " + ", ".join(shape.group_cols)

    return sql


def query_oracle(
    shape: QueryShape,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str] | None = None,
    sql: str | None = None,
) -> dict[Any, Decimal | None] | list[tuple[Any, ...]]:
    """
    Load `rows` into an in-memory sqlite3 database, execute `sql` (or the
    canonical SQL for `shape`), and return `{group_key: value}` for aggregate
    shapes or a sorted list of tuples for non-aggregate projection shapes.
    """
    all_cols_set: set[str] = set(shape.fingerprint())
    if columns:
        all_cols_set.update(c.lower() for c in columns)
    for r in rows:
        all_cols_set.update(k.lower() for k in r.keys())
    if not all_cols_set:
        all_cols_set.add("id")
    ordered_cols = tuple(sorted(all_cols_set))

    con = _get_oracle_conn(shape.relation, ordered_cols)

    if rows:
        placeholders = ", ".join("?" for _ in ordered_cols)
        insert_sql = (
            f"INSERT INTO {shape.relation} ({', '.join(ordered_cols)}) "
            f"VALUES ({placeholders})"
        )
        param_rows = [
            tuple(
                _to_sqlite_param(
                    next(
                        (v for k, v in r.items() if k.lower() == c),
                        None,
                    )
                )
                for c in ordered_cols
            )
            for r in rows
        ]
        con.executemany(insert_sql, param_rows)

    raw_exec_sql = sql if sql is not None else shape_to_sql(shape)
    exec_sql = raw_exec_sql
    fetched = con.execute(exec_sql).fetchall()

    if shape.agg_fn is not None:
        result: dict[Any, Decimal | None] = {}
        num_groups = len(shape.group_cols)
        if num_groups == 0:
            if fetched:
                result[()] = _from_sqlite_agg_val(fetched[0][-1])
            else:
                result[()] = (
                    Decimal(0) if shape.agg_fn in ("count", "count_star") else None
                )
        elif num_groups == 1:
            for row_tuple in fetched:
                g_key = _from_sqlite_group_key(row_tuple[0])
                agg_val = _from_sqlite_agg_val(row_tuple[-1])
                result[g_key] = agg_val
        else:
            for row_tuple in fetched:
                g_key = tuple(_from_sqlite_group_key(x) for x in row_tuple[:num_groups])
                agg_val = _from_sqlite_agg_val(row_tuple[-1])
                result[g_key] = agg_val
        return result

    normalized_rows = [
        tuple(_from_sqlite_group_key(x) for x in row_tuple) for row_tuple in fetched
    ]
    return sorted(
        normalized_rows,
        key=lambda r: tuple(
            (0, "") if x is NULL_GROUP_KEY else (1, str(type(x)), str(x)) for x in r
        ),
    )


def rebuild_cache(
    shape: QueryShape,
    rows: Sequence[Mapping[str, Any]],
) -> AggregateCache:
    """Rebuild an AggregateCache for `shape` from `rows`."""
    return AggregateCache.from_rows(shape, rows)


# ---------------------------------------------------------------------------
# Multi-Table Foreign-Key JOIN Oracle Extensions (Upgrade 3)
# ---------------------------------------------------------------------------


def join_shape_to_sql(
    shape: MultiTableJoinShape,
    *,
    schema_prefix: str = "",
) -> str:
    """Render canonical SQL for a `MultiTableJoinShape`."""
    select_parts: list[str] = []
    for q, c in shape.group_cols:
        select_parts.append(f"{q}.{c}")

    if shape.agg_fn == "sum" and shape.agg_col is not None:
        select_parts.append(f"SUM({shape.agg_col[0]}.{shape.agg_col[1]})")
    elif shape.agg_fn == "count" and shape.agg_col is not None:
        select_parts.append(f"COUNT({shape.agg_col[0]}.{shape.agg_col[1]})")
    elif shape.agg_fn == "count_star":
        select_parts.append("COUNT(*)")
    elif shape.agg_fn == "avg" and shape.agg_col is not None:
        select_parts.append(f"AVG({shape.agg_col[0]}.{shape.agg_col[1]})")
    else:
        for q, c in shape.projection_cols:
            col_ref = f"{q}.{c}"
            if col_ref not in select_parts:
                select_parts.append(col_ref)

    if not select_parts:
        select_parts.append("1")

    base_tbl = f"{schema_prefix}{shape.base_relation}"
    if shape.base_alias:
        from_sql = f"{base_tbl} AS {shape.base_alias}"
    else:
        from_sql = base_tbl

    join_sql_parts: list[str] = []
    for j in shape.joins:
        r_tbl = f"{schema_prefix}{j.right_relation}"
        r_target = f"{r_tbl} AS {j.right_alias}" if j.right_alias else r_tbl
        r_qual = j.effective_right_qualifier
        j_kw = "LEFT JOIN" if j.join_type.upper() == "LEFT" else "INNER JOIN"
        join_sql_parts.append(
            f"{j_kw} {r_target} ON {j.left_qualifier}.{j.left_key} = {r_qual}.{j.right_key}"
        )

    sql = f"SELECT {', '.join(select_parts)} FROM {from_sql}"
    if join_sql_parts:
        sql += " " + " ".join(join_sql_parts)

    if shape.predicate_terms:
        where_parts: list[str] = []
        for q, col, op, lit in shape.predicate_terms:
            sql_op = "=" if op in ("=", "==") else ("!=" if op in ("!=", "<>") else op)
            if isinstance(lit, str):
                escaped = lit.replace("'", "''")
                where_parts.append(f"{q}.{col} {sql_op} '{escaped}'")
            else:
                where_parts.append(f"{q}.{col} {sql_op} {lit}")
        sql += " WHERE " + " AND ".join(where_parts)

    if shape.group_cols:
        sql += " GROUP BY " + ", ".join(f"{q}.{c}" for q, c in shape.group_cols)

    return sql


def _fk_topological_depth(
    tbl: str,
    fk_edges: Sequence[tuple[str, str, str, str]],
    visited: set[str] | None = None,
) -> int:
    seen = visited if visited is not None else set()
    if tbl in seen:
        return 0
    seen.add(tbl)
    parents = [pt for ct, _, pt, _ in fk_edges if ct == tbl and pt != tbl]
    if not parents:
        return 0
    return 1 + max(_fk_topological_depth(p, fk_edges, set(seen)) for p in parents)


def _get_multi_table_oracle_conn(
    table_cols: dict[str, tuple[str, ...]],
    foreign_keys: tuple[tuple[str, str, str, str], ...] = (),
    enforce_fk: bool = False,
) -> sqlite3.Connection:
    """
    Maintain a reusable in-memory SQLite connection for multi-table JOIN queries.
    `foreign_keys` is a tuple of `(child_table, child_col, parent_table, parent_col)`.
    """
    global _MULTI_ORACLE_CONN, _ACTIVE_MULTI_SCHEMA
    if _MULTI_ORACLE_CONN is None:
        _MULTI_ORACLE_CONN = sqlite3.connect(":memory:")
        _MULTI_ORACLE_CONN.create_aggregate("SUM", 1, _SqliteDecimalSum)
        _MULTI_ORACLE_CONN.create_aggregate("AVG", 1, _SqliteDecimalAvg)

    schema_sig = (
        tuple((tbl, cols) for tbl, cols in sorted(table_cols.items())),
        foreign_keys,
        enforce_fk,
    )

    ordered_tables = sorted(
        table_cols.keys(),
        key=lambda t: (_fk_topological_depth(t, foreign_keys), t),
    )

    if _ACTIVE_MULTI_SCHEMA != schema_sig:
        _MULTI_ORACLE_CONN.commit()
        _MULTI_ORACLE_CONN.execute("PRAGMA foreign_keys = OFF")
        if _ACTIVE_MULTI_SCHEMA is not None:
            for old_tbl, _ in reversed(_ACTIVE_MULTI_SCHEMA[0]):
                _MULTI_ORACLE_CONN.execute(f"DROP TABLE IF EXISTS {old_tbl}")

        fk_map: dict[str, list[tuple[str, str, str]]] = {}
        parent_pk_cols: dict[str, set[str]] = {}
        for child_t, child_c, parent_t, parent_c in foreign_keys:
            fk_map.setdefault(child_t, []).append((child_c, parent_t, parent_c))
            parent_pk_cols.setdefault(parent_t, set()).add(parent_c)

        # Create parent tables first, then child tables by topological FK depth
        for tbl in ordered_tables:
            _attach_namespace(_MULTI_ORACLE_CONN, tbl)
            _MULTI_ORACLE_CONN.execute(f"DROP TABLE IF EXISTS {tbl}")
            cols = table_cols[tbl]
            pk_set = parent_pk_cols.get(tbl, set())
            col_defs = [
                f"{c} NUMERIC UNIQUE" if c in pk_set else f"{c} NUMERIC" for c in cols
            ]
            for child_c, parent_t, parent_c in fk_map.get(tbl, ()):
                col_defs.append(
                    f"FOREIGN KEY ({child_c}) REFERENCES {parent_t}({parent_c})"
                )
            _MULTI_ORACLE_CONN.execute(f"CREATE TABLE {tbl} ({', '.join(col_defs)})")

        _MULTI_ORACLE_CONN.commit()
        _MULTI_ORACLE_CONN.execute(
            f"PRAGMA foreign_keys = {'ON' if enforce_fk else 'OFF'}"
        )
        _ACTIVE_MULTI_SCHEMA = schema_sig
    else:
        _MULTI_ORACLE_CONN.commit()
        _MULTI_ORACLE_CONN.execute("PRAGMA foreign_keys = OFF")
        for tbl in reversed(ordered_tables):
            _MULTI_ORACLE_CONN.execute(f"DELETE FROM {tbl}")
        _MULTI_ORACLE_CONN.commit()
        _MULTI_ORACLE_CONN.execute(
            f"PRAGMA foreign_keys = {'ON' if enforce_fk else 'OFF'}"
        )

    return _MULTI_ORACLE_CONN


def query_multi_table_oracle(
    shape: MultiTableJoinShape,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    schemas: Mapping[str, Sequence[str]] | None = None,
    foreign_keys: Sequence[tuple[str, str, str, str]] = (),
    enforce_fk: bool = False,
    sql: str | None = None,
) -> dict[Any, Decimal | None] | list[tuple[Any, ...]]:
    """
    Load multiple tables into in-memory `sqlite3`, optionally enforce foreign-key
    constraints, execute the `MultiTableJoinShape` query, and return `{group_key: value}`
    for aggregate shapes or sorted row tuples for projection shapes.
    """
    norm_tables: dict[str, Sequence[Mapping[str, Any]]] = {
        normalize_relation(k): v for k, v in tables.items()
    }
    norm_schemas: dict[str, Sequence[str]] = (
        {normalize_relation(k): v for k, v in schemas.items()} if schemas else {}
    )
    norm_fks = tuple(
        (
            normalize_relation(ct),
            cc.lower(),
            normalize_relation(pt),
            pc.lower(),
        )
        for ct, cc, pt, pc in foreign_keys
    )

    all_rels = set(shape.relations()) | set(norm_tables.keys())
    table_cols: dict[str, tuple[str, ...]] = {}
    for rel in all_rels:
        c_set: set[str] = set(shape.table_fingerprint(rel))
        if rel in norm_schemas:
            c_set.update(c.lower() for c in norm_schemas[rel])
        for r in norm_tables.get(rel, ()):
            c_set.update(str(k).lower() for k in r.keys())
        for ct, cc, pt, pc in norm_fks:
            if ct == rel:
                c_set.add(cc)
            if pt == rel:
                c_set.add(pc)
        if not c_set:
            c_set.add("id")
        table_cols[rel] = tuple(sorted(c_set))

    con = _get_multi_table_oracle_conn(table_cols, norm_fks, enforce_fk)

    insert_order = sorted(
        all_rels,
        key=lambda t: (_fk_topological_depth(t, norm_fks), t),
    )

    for rel in insert_order:
        rows = norm_tables.get(rel, ())
        if not rows:
            continue
        ordered_cols = table_cols[rel]
        placeholders = ", ".join("?" for _ in ordered_cols)
        insert_sql = (
            f"INSERT INTO {rel} ({', '.join(ordered_cols)}) VALUES ({placeholders})"
        )
        param_rows = [
            tuple(
                _to_sqlite_param(
                    next(
                        (v for k, v in r.items() if str(k).lower() == c),
                        None,
                    )
                )
                for c in ordered_cols
            )
            for r in rows
        ]
        con.executemany(insert_sql, param_rows)

    raw_exec_sql = sql if sql is not None else join_shape_to_sql(shape)
    exec_sql = raw_exec_sql
    fetched = con.execute(exec_sql).fetchall()

    if shape.agg_fn is not None:
        result: dict[Any, Decimal | None] = {}
        num_groups = len(shape.group_cols)
        if num_groups == 0:
            if fetched:
                result[()] = _from_sqlite_agg_val(fetched[0][-1])
            else:
                result[()] = (
                    Decimal(0) if shape.agg_fn in ("count", "count_star") else None
                )
        elif num_groups == 1:
            for row_tuple in fetched:
                g_key = _from_sqlite_group_key(row_tuple[0])
                agg_val = _from_sqlite_agg_val(row_tuple[-1])
                result[g_key] = agg_val
        else:
            for row_tuple in fetched:
                g_key = tuple(_from_sqlite_group_key(x) for x in row_tuple[:num_groups])
                agg_val = _from_sqlite_agg_val(row_tuple[-1])
                result[g_key] = agg_val
        return result

    normalized_rows = [
        tuple(_from_sqlite_group_key(x) for x in row_tuple) for row_tuple in fetched
    ]
    return sorted(
        normalized_rows,
        key=lambda r: tuple(
            (0, "") if x is NULL_GROUP_KEY else (1, str(type(x)), str(x)) for x in r
        ),
    )


def evaluate_join_reference(
    shape: MultiTableJoinShape,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[Any, Decimal | None] | list[tuple[Any, ...]]:
    """
    Pure-Python relational algebra evaluator for `MultiTableJoinShape`
    (equijoins + three-valued `WHERE` + `Decimal` aggregation) to cross-check
    `query_multi_table_oracle`.
    """
    norm_tables: dict[str, list[dict[str, Any]]] = {
        normalize_relation(k): [
            {str(col).lower(): val for col, val in r.items()} for r in rows
        ]
        for k, rows in tables.items()
    }

    base_rel = normalize_relation(shape.base_relation)
    base_qual = shape.effective_base_qualifier
    composite_rows: list[dict[str, dict[str, Any]]] = [
        {base_rel: r, base_qual: r} for r in norm_tables.get(base_rel, [])
    ]

    for j in shape.joins:
        r_rel = normalize_relation(j.right_relation)
        r_qual = j.effective_right_qualifier
        l_qual = j.left_qualifier.lower()
        l_key = j.left_key.lower()
        r_key = j.right_key.lower()
        right_rows = norm_tables.get(r_rel, [])

        next_composite: list[dict[str, dict[str, Any]]] = []
        for env in composite_rows:
            l_val = env.get(l_qual, {}).get(l_key)
            matched: list[dict[str, Any]] = []
            if l_val is not None:
                for rr in right_rows:
                    r_val = rr.get(r_key)
                    if r_val is not None and _values_equal(l_val, r_val):
                        matched.append(rr)
            if matched:
                for rr in matched:
                    new_env = dict(env)
                    new_env[r_rel] = rr
                    new_env[r_qual] = rr
                    next_composite.append(new_env)
            elif j.join_type.upper() == "LEFT":
                new_env = dict(env)
                new_env[r_rel] = {}
                new_env[r_qual] = {}
                next_composite.append(new_env)
        composite_rows = next_composite

    filtered_rows: list[dict[str, dict[str, Any]]] = []
    for env in composite_rows:
        ok = True
        for q, col, op, lit in shape.predicate_terms:
            cell_val = env.get(q.lower(), {}).get(col.lower())
            single_pred = Predicate(terms=((col.lower(), op, lit),))
            if not single_pred.matches({col.lower(): cell_val}):
                ok = False
                break
        if ok:
            filtered_rows.append(env)

    if shape.agg_fn is None:
        proj_tuples: list[tuple[Any, ...]] = []
        for env in filtered_rows:
            row_vals = tuple(
                _normalize_group_atom(env.get(q.lower(), {}).get(c.lower()))
                for q, c in shape.projection_cols
            )
            proj_tuples.append(row_vals)
        return sorted(
            proj_tuples,
            key=lambda r: tuple(
                (0, "") if x is NULL_GROUP_KEY else (1, str(type(x)), str(x)) for x in r
            ),
        )

    is_grouped = len(shape.group_cols) > 0
    group_state: dict[Any, tuple[Decimal, int, int]] = {}
    if not is_grouped:
        group_state[()] = (Decimal(0), 0, 0)

    for env in filtered_rows:
        if not is_grouped:
            g_key: Any = ()
        elif len(shape.group_cols) == 1:
            q0, c0 = shape.group_cols[0]
            g_key = _normalize_group_atom(env.get(q0.lower(), {}).get(c0.lower()))
        else:
            g_key = tuple(
                _normalize_group_atom(env.get(q.lower(), {}).get(c.lower()))
                for q, c in shape.group_cols
            )

        s_cur, n_cur, r_cur = group_state.get(g_key, (Decimal(0), 0, 0))
        r_cur += 1
        if shape.agg_fn == "count_star":
            n_cur += 1
        elif shape.agg_col is not None:
            aq, ac = shape.agg_col
            raw_v = env.get(aq.lower(), {}).get(ac.lower())
            if raw_v is not None:
                n_cur += 1
                if shape.agg_fn in ("sum", "avg"):
                    s_cur += _to_decimal(raw_v)
        group_state[g_key] = (s_cur, n_cur, r_cur)

    out: dict[Any, Decimal | None] = {}
    for g_key, (s_val, n_cnt, r_cnt) in group_state.items():
        if is_grouped and r_cnt <= 0:
            continue
        if not is_grouped and r_cnt <= 0:
            out[g_key] = Decimal(0) if shape.agg_fn in ("count", "count_star") else None
            continue
        if shape.agg_fn == "count_star":
            out[g_key] = Decimal(r_cnt)
        elif shape.agg_fn == "count":
            out[g_key] = Decimal(n_cnt)
        elif shape.agg_fn == "sum":
            out[g_key] = s_val if n_cnt > 0 else None
        elif shape.agg_fn == "avg":
            out[g_key] = (s_val / Decimal(n_cnt)) if n_cnt > 0 else None

    return out
