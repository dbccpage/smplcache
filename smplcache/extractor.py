# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
AST allowlist SQL extractor for SmplCache (Upgrade 1a).

Parses SQL with `sqlglot` (dialect configurable: postgres, tsql, sqlite),
requires a single `Select` statement, walks the AST against an explicit
node-type allowlist, resolves schema qualifiers and table aliases, normalizes
identifier case, and returns a typed `QueryShape` or an explicit `unsupported`
reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from smplcache.join import JoinClause, JoinType, MultiTableJoinShape
from smplcache.smplcache import AggFn, Predicate, QueryShape, normalize_relation

ALLOWED_AST_NODE_TYPES = {
    exp.Select,
    exp.From,
    exp.Table,
    exp.TableAlias,
    exp.Where,
    exp.Group,
    exp.Identifier,
    exp.Alias,
    exp.Paren,
    exp.Column,
    exp.Literal,
    exp.Neg,
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.And,
    exp.Sum,
    exp.Count,
    exp.Avg,
    exp.Star,
}

ALLOWED_JOIN_AST_NODE_TYPES = ALLOWED_AST_NODE_TYPES | {exp.Join}

COMPARISON_NODE_TYPES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
)

FLIP_OP: dict[str, str] = {
    "=": "=",
    "!=": "!=",
    ">": "<",
    ">=": "<=",
    "<": ">",
    "<=": ">=",
}


@dataclass
class ExtractorResult:
    is_supported: bool
    relation: str = ""
    predicate_cols: set[str] = field(default_factory=set)
    aggregate_cols: set[str] = field(default_factory=set)
    group_cols: set[str] = field(default_factory=set)
    projection_cols: set[str] = field(default_factory=set)
    join_cols: set[str] = field(default_factory=set)
    reason: str = ""
    shape: QueryShape | None = None


def _has_sql_comment_outside_strings(sql: str) -> bool:
    stripped = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return "--" in stripped or "/*" in stripped or "*/" in stripped


def _extract_literal_value(node: Any) -> tuple[bool, Any, str]:
    if type(node) is exp.Neg:
        inner = node.this
        if type(inner) is not exp.Literal or not inner.is_number:
            return False, None, "Neg"
        raw_num = str(inner.this)
        try:
            if "." in raw_num or "e" in raw_num.lower():
                return True, -Decimal(raw_num), ""
            return True, -int(raw_num), ""
        except (ValueError, InvalidOperation):
            return False, None, "Literal"

    if type(node) is exp.Literal:
        if node.is_string:
            return True, str(node.this), ""
        if node.is_number:
            raw_num = str(node.this)
            try:
                if "." in raw_num or "e" in raw_num.lower():
                    return True, Decimal(raw_num), ""
                return True, int(raw_num), ""
            except (ValueError, InvalidOperation):
                return False, None, "Literal"

    return False, None, type(node).__name__


def _comparison_op_symbol(node: exp.Expression) -> str:
    if type(node) is exp.EQ:
        return "="
    if type(node) is exp.NEQ:
        return "!="
    if type(node) is exp.GT:
        return ">"
    if type(node) is exp.GTE:
        return ">="
    if type(node) is exp.LT:
        return "<"
    if type(node) is exp.LTE:
        return "<="
    raise ValueError(f"Unsupported comparison node: {type(node).__name__}")


def _resolve_schema_columns(
    schema: Mapping[str, Any] | Iterable[str] | None,
    relation: str,
) -> list[str] | None:
    if schema is None:
        return None
    if isinstance(schema, Mapping):
        lower_map = {str(k).lower(): v for k, v in schema.items()}
        if relation in lower_map:
            entry = lower_map[relation]
            if isinstance(entry, Mapping):
                cols_obj = entry.get("columns", entry)
                if isinstance(cols_obj, Mapping):
                    return [str(c).lower() for c in cols_obj.keys()]
                if isinstance(cols_obj, Iterable) and not isinstance(
                    cols_obj, (str, bytes)
                ):
                    return [str(c).lower() for c in cols_obj]
            elif isinstance(entry, Iterable) and not isinstance(entry, (str, bytes)):
                return [str(c).lower() for c in entry]
            return None
        # Fallback if `schema` is directly a mapping of column_name -> col_spec
        if (
            all(isinstance(v, (str, Mapping)) for v in lower_map.values())
            and relation not in lower_map
        ):
            return None
        return None
    if isinstance(schema, Iterable) and not isinstance(schema, (str, bytes)):
        cols = [str(c).lower() for c in schema]
        return cols if cols else None
    return None


def parse_sql(
    sql: str,
    *,
    dialect: str = "postgres",
    schema: Mapping[str, Any] | Iterable[str] | None = None,
    name: str = "extracted_shape",
    security_cols: Iterable[str] = (),
) -> ExtractorResult:
    if not sql or not isinstance(sql, str) or not sql.strip():
        return ExtractorResult(False, reason="EmptyStatement")

    if _has_sql_comment_outside_strings(sql):
        return ExtractorResult(False, reason="Comment")

    try:
        statements = sqlglot.parse(
            sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE
        )
    except SqlglotError as exc:
        return ExtractorResult(False, reason=f"ParseError: {exc}")

    if len(statements) != 1:
        return ExtractorResult(False, reason="MultiStatement")

    stmt = statements[0]
    if stmt is None:
        return ExtractorResult(False, reason="EmptyStatement")

    if type(stmt) is not exp.Select:
        return ExtractorResult(False, reason=type(stmt).__name__)

    # Check disallowed Select-level clauses first so specific clause names are reported
    allowed_select_args = {"expressions", "from", "from_", "where", "group"}
    for arg_key, arg_val in stmt.args.items():
        if arg_key not in allowed_select_args and arg_val:
            if isinstance(arg_val, list) and arg_val:
                return ExtractorResult(False, reason=type(arg_val[0]).__name__)
            if isinstance(arg_val, exp.Expression):
                return ExtractorResult(False, reason=type(arg_val).__name__)
            return ExtractorResult(False, reason=str(arg_key))

    # Walk the entire AST against the strict node allowlist
    for node in stmt.walk():
        if getattr(node, "comments", None):
            return ExtractorResult(False, reason="Comment")
        if type(node) not in ALLOWED_AST_NODE_TYPES:
            return ExtractorResult(False, reason=type(node).__name__)
        if type(node) is exp.Identifier and node.quoted:
            return ExtractorResult(False, reason="QuotedIdentifier")
        if type(node) is exp.Neg:
            if type(node.this) is not exp.Literal or not node.this.is_number:
                return ExtractorResult(False, reason="Neg")

    from_clause = stmt.args.get("from") or stmt.args.get("from_")
    if from_clause is None or type(from_clause) is not exp.From:
        return ExtractorResult(False, reason="MissingFrom")

    if any(k != "this" and v for k, v in from_clause.args.items()):
        return ExtractorResult(False, reason="FromClauseModifier")

    table_node = from_clause.this
    if type(table_node) is not exp.Table:
        return ExtractorResult(
            False,
            reason=type(table_node).__name__ if table_node else "MissingTable",
        )

    for disallowed_tbl_arg in ("catalog", "joins", "pivots", "hints", "when", "sample"):
        if table_node.args.get(disallowed_tbl_arg):
            return ExtractorResult(False, reason=disallowed_tbl_arg)

    if not table_node.name:
        return ExtractorResult(False, reason="MissingTableName")

    raw_table = table_node.name.lower()
    schema_name = table_node.db.lower() if table_node.db else ""
    alias_name = ""
    alias_node = table_node.args.get("alias")
    if alias_node is not None:
        if type(alias_node) is exp.TableAlias:
            if alias_node.args.get("columns"):
                return ExtractorResult(False, reason="TableAliasColumns")
            alias_name = alias_node.name.lower()
        elif type(alias_node) is exp.Identifier:
            alias_name = alias_node.name.lower()
        else:
            return ExtractorResult(False, reason=type(alias_node).__name__)

    allowed_qualifiers = {raw_table}
    if alias_name:
        allowed_qualifiers.add(alias_name)

    def resolve_column(col_node: exp.Column) -> tuple[bool, str, str]:
        if col_node.args.get("catalog"):
            return False, "", "CatalogQualifier"
        if col_node.args.get("db"):
            col_db = col_node.db.lower()
            if not schema_name or col_db != schema_name:
                return False, "", "UnknownSchemaQualifier"
        if col_node.table:
            col_tbl = col_node.table.lower()
            if col_tbl not in allowed_qualifiers:
                return False, "", "UnknownTableQualifier"
        if type(col_node.this) is exp.Identifier:
            return True, col_node.this.name.lower(), ""
        return False, "", type(col_node.this).__name__

    if not stmt.expressions:
        return ExtractorResult(False, reason="EmptySelect")

    agg_fn: AggFn | None = None
    agg_col: str | None = None
    projection_cols: list[str] = []

    for expr in stmt.expressions:
        target = expr.this if type(expr) is exp.Alias else expr

        if type(target) is exp.Star or (
            type(target) is exp.Column and type(target.this) is exp.Star
        ):
            if type(target) is exp.Column and target.table:
                if target.table.lower() not in allowed_qualifiers:
                    return ExtractorResult(False, reason="UnknownTableQualifier")
            schema_cols = _resolve_schema_columns(schema, raw_table)
            if not schema_cols:
                return ExtractorResult(False, reason="Star")
            for sc in schema_cols:
                if sc not in projection_cols:
                    projection_cols.append(sc)
        elif type(target) is exp.Column:
            ok, c_name, err = resolve_column(target)
            if not ok:
                return ExtractorResult(False, reason=err)
            if c_name not in projection_cols:
                projection_cols.append(c_name)
        elif type(target) in (exp.Sum, exp.Count, exp.Avg):
            if agg_fn is not None:
                return ExtractorResult(False, reason="MultipleAggregates")
            if any(
                k not in ("this", "expressions", "big_int") and v
                for k, v in target.args.items()
            ):
                return ExtractorResult(False, reason="AggregateModifier")
            if target.args.get("expressions"):
                return ExtractorResult(False, reason="AggregateArguments")

            inner = target.this
            if type(target) is exp.Count and type(inner) is exp.Star:
                agg_fn = "count_star"
                agg_col = None
            elif type(inner) is exp.Column:
                ok, c_name, err = resolve_column(inner)
                if not ok:
                    return ExtractorResult(False, reason=err)
                agg_col = c_name
                if type(target) is exp.Sum:
                    agg_fn = "sum"
                elif type(target) is exp.Count:
                    agg_fn = "count"
                else:
                    agg_fn = "avg"
            else:
                return ExtractorResult(
                    False,
                    reason=type(inner).__name__ if inner else "EmptyAggregate",
                )
        else:
            return ExtractorResult(False, reason=type(target).__name__)

    group_cols_list: list[str] = []
    group_node = stmt.args.get("group")
    if group_node is not None:
        if any(k != "expressions" and v for k, v in group_node.args.items()):
            return ExtractorResult(False, reason="GroupModifier")
        if not group_node.expressions:
            return ExtractorResult(False, reason="EmptyGroup")
        for g_expr in group_node.expressions:
            if type(g_expr) is not exp.Column:
                return ExtractorResult(False, reason=type(g_expr).__name__)
            ok, c_name, err = resolve_column(g_expr)
            if not ok:
                return ExtractorResult(False, reason=err)
            if c_name not in group_cols_list:
                group_cols_list.append(c_name)

    if group_cols_list and agg_fn is None:
        return ExtractorResult(False, reason="GroupWithoutAggregate")

    if agg_fn is not None and not (set(projection_cols) <= set(group_cols_list)):
        return ExtractorResult(False, reason="UngroupedProjectionInAggregate")

    pred_terms: list[tuple[str, str, Any]] = []
    where_node = stmt.args.get("where")
    if where_node is not None:

        def parse_where_expr(w_expr: Any) -> tuple[bool, str]:
            while type(w_expr) is exp.Paren:
                w_expr = w_expr.this
            if type(w_expr) is exp.And:
                ok_l, err_l = parse_where_expr(w_expr.left)
                if not ok_l:
                    return False, err_l
                return parse_where_expr(w_expr.right)
            if isinstance(w_expr, COMPARISON_NODE_TYPES):
                left = w_expr.left
                right = w_expr.right
                while type(left) is exp.Paren:
                    left = left.this
                while type(right) is exp.Paren:
                    right = right.this
                op_sym = _comparison_op_symbol(w_expr)

                if type(left) is exp.Column:
                    ok_c, c_name, err_c = resolve_column(left)
                    if not ok_c:
                        return False, err_c
                    ok_lit, lit_val, err_lit = _extract_literal_value(right)
                    if not ok_lit:
                        return False, err_lit
                    pred_terms.append((c_name, op_sym, lit_val))
                    return True, ""
                if type(right) is exp.Column:
                    ok_c, c_name, err_c = resolve_column(right)
                    if not ok_c:
                        return False, err_c
                    ok_lit, lit_val, err_lit = _extract_literal_value(left)
                    if not ok_lit:
                        return False, err_lit
                    pred_terms.append((c_name, FLIP_OP[op_sym], lit_val))
                    return True, ""
                return False, "NonColumnComparison"
            return False, type(w_expr).__name__

        ok_w, err_w = parse_where_expr(where_node.this)
        if not ok_w:
            return ExtractorResult(False, reason=err_w)

    pred = Predicate(terms=tuple(pred_terms))
    shape = QueryShape(
        relation=f"{schema_name}.{raw_table}" if schema_name else raw_table,
        agg_fn=agg_fn,
        agg_col=agg_col,
        group_cols=tuple(group_cols_list),
        predicate=pred,
        projection_cols=frozenset(projection_cols),
        security_cols=frozenset(str(c).lower() for c in security_cols),
        name=name,
    )

    return ExtractorResult(
        is_supported=True,
        relation=shape.relation,
        predicate_cols=set(pred.columns),
        aggregate_cols={agg_col} if agg_col is not None else set(),
        group_cols=set(group_cols_list),
        projection_cols=set(projection_cols),
        join_cols=set(),
        reason="",
        shape=shape,
    )


@dataclass
class JoinExtractorResult:
    is_supported: bool
    reason: str = ""
    shape: MultiTableJoinShape | None = None


def parse_join_sql(
    sql: str,
    *,
    dialect: str = "postgres",
    name: str = "extracted_join_shape",
    security_cols: Iterable[tuple[str, str]] = (),
    primary_keys: Mapping[str, str] | None = None,
) -> JoinExtractorResult:
    """
    AST allowlist extractor for Upgrade 3 foreign-key `INNER JOIN` and `LEFT JOIN`
    queries. Requires explicit table/alias qualification on all column references
    and `ON left.col = right.col` equijoin predicates.

    Each right join key must be the declared primary key. The default is `id`;
    pass `primary_keys` for other names and use the same mapping when seeding.
    This function does not inspect the database schema: callers must supply
    accurate primary-key metadata.
    """
    if not sql or not isinstance(sql, str) or not sql.strip():
        return JoinExtractorResult(False, reason="EmptyStatement")

    if _has_sql_comment_outside_strings(sql):
        return JoinExtractorResult(False, reason="Comment")

    try:
        statements = sqlglot.parse(
            sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE
        )
    except SqlglotError as exc:
        return JoinExtractorResult(False, reason=f"ParseError: {exc}")

    if len(statements) != 1:
        return JoinExtractorResult(False, reason="MultiStatement")

    stmt = statements[0]
    if stmt is None:
        return JoinExtractorResult(False, reason="EmptyStatement")

    if type(stmt) is not exp.Select:
        return JoinExtractorResult(False, reason=type(stmt).__name__)

    allowed_select_args = {
        "expressions",
        "from",
        "from_",
        "joins",
        "where",
        "group",
    }
    for arg_key, arg_val in stmt.args.items():
        if arg_key not in allowed_select_args and arg_val:
            if isinstance(arg_val, list) and arg_val:
                return JoinExtractorResult(False, reason=type(arg_val[0]).__name__)
            if isinstance(arg_val, exp.Expression):
                return JoinExtractorResult(False, reason=type(arg_val).__name__)
            return JoinExtractorResult(False, reason=str(arg_key))

    for node in stmt.walk():
        if getattr(node, "comments", None):
            return JoinExtractorResult(False, reason="Comment")
        if type(node) not in ALLOWED_JOIN_AST_NODE_TYPES:
            return JoinExtractorResult(False, reason=type(node).__name__)
        if type(node) is exp.Identifier and node.quoted:
            return JoinExtractorResult(False, reason="QuotedIdentifier")
        if type(node) is exp.Neg:
            if type(node.this) is not exp.Literal or not node.this.is_number:
                return JoinExtractorResult(False, reason="Neg")

    from_clause = stmt.args.get("from") or stmt.args.get("from_")
    if from_clause is None or type(from_clause) is not exp.From:
        return JoinExtractorResult(False, reason="MissingFrom")

    if any(k != "this" and v for k, v in from_clause.args.items()):
        return JoinExtractorResult(False, reason="FromClauseModifier")

    base_node = from_clause.this
    if type(base_node) is not exp.Table or not base_node.name:
        return JoinExtractorResult(False, reason="MissingBaseTable")

    for disallowed_tbl_arg in ("catalog", "joins", "pivots", "hints", "when", "sample"):
        if base_node.args.get(disallowed_tbl_arg):
            return JoinExtractorResult(False, reason=disallowed_tbl_arg)

    base_rel = base_node.name.lower()
    base_alias: str | None = None
    base_alias_node = base_node.args.get("alias")
    if base_alias_node is not None:
        if type(base_alias_node) is exp.TableAlias:
            if base_alias_node.args.get("columns"):
                return JoinExtractorResult(False, reason="TableAliasColumns")
            base_alias = base_alias_node.name.lower()
        elif type(base_alias_node) is exp.Identifier:
            base_alias = base_alias_node.name.lower()
        else:
            return JoinExtractorResult(False, reason=type(base_alias_node).__name__)

    base_canonical_qual = base_alias or base_rel
    qualifier_to_canonical: dict[str, str] = {
        base_rel: base_canonical_qual,
        base_canonical_qual: base_canonical_qual,
    }
    used_canonical_quals: set[str] = {base_canonical_qual}
    used_relations: set[str] = {base_rel}

    raw_joins = stmt.args.get("joins")
    if not raw_joins or not isinstance(raw_joins, list):
        return JoinExtractorResult(False, reason="MissingJoinClause")

    join_clauses: list[JoinClause] = []
    for j_node in raw_joins:
        if type(j_node) is not exp.Join:
            return JoinExtractorResult(False, reason=type(j_node).__name__)

        for disallowed_j_arg in (
            "using",
            "method",
            "global",
            "pivots",
            "hints",
            "when",
        ):
            if j_node.args.get(disallowed_j_arg):
                return JoinExtractorResult(False, reason=f"Join_{disallowed_j_arg}")

        side = str(j_node.side or "").upper()
        kind = str(j_node.kind or "").upper()
        if kind == "CROSS":
            return JoinExtractorResult(False, reason="CrossJoin")
        if side == "" and kind in ("", "INNER"):
            j_type: JoinType = "INNER"
        elif side == "LEFT" and kind in ("", "OUTER"):
            j_type = "LEFT"
        else:
            return JoinExtractorResult(False, reason=f"UnsupportedJoin:{side}_{kind}")

        r_node = j_node.this
        if type(r_node) is not exp.Table or not r_node.name:
            return JoinExtractorResult(False, reason="MissingJoinTable")

        for disallowed_tbl_arg in (
            "catalog",
            "joins",
            "pivots",
            "hints",
            "when",
            "sample",
        ):
            if r_node.args.get(disallowed_tbl_arg):
                return JoinExtractorResult(False, reason=disallowed_tbl_arg)

        r_rel = r_node.name.lower()
        r_alias: str | None = None
        r_alias_node = r_node.args.get("alias")
        if r_alias_node is not None:
            if type(r_alias_node) is exp.TableAlias:
                if r_alias_node.args.get("columns"):
                    return JoinExtractorResult(False, reason="TableAliasColumns")
                r_alias = r_alias_node.name.lower()
            elif type(r_alias_node) is exp.Identifier:
                r_alias = r_alias_node.name.lower()
            else:
                return JoinExtractorResult(False, reason=type(r_alias_node).__name__)

        r_canonical_qual = r_alias or r_rel
        if r_canonical_qual in used_canonical_quals:
            return JoinExtractorResult(False, reason="DuplicateJoinQualifier")
        if r_rel in used_relations:
            return JoinExtractorResult(False, reason="DuplicateJoinRelation")

        on_expr = j_node.args.get("on")
        if on_expr is None:
            return JoinExtractorResult(False, reason="MissingJoinOn")
        while type(on_expr) is exp.Paren:
            on_expr = on_expr.this
        if type(on_expr) is not exp.EQ:
            return JoinExtractorResult(False, reason="NonEquiJoin")

        on_left = on_expr.left
        on_right = on_expr.right
        while type(on_left) is exp.Paren:
            on_left = on_left.this
        while type(on_right) is exp.Paren:
            on_right = on_right.this

        if type(on_left) is not exp.Column or type(on_right) is not exp.Column:
            return JoinExtractorResult(False, reason="NonColumnJoinOn")

        if not on_left.table or not on_right.table:
            return JoinExtractorResult(False, reason="UnqualifiedJoinColumn")

        q_left = on_left.table.lower()
        c_left = (
            on_left.this.name.lower() if type(on_left.this) is exp.Identifier else ""
        )
        q_right = on_right.table.lower()
        c_right = (
            on_right.this.name.lower() if type(on_right.this) is exp.Identifier else ""
        )
        if not c_left or not c_right:
            return JoinExtractorResult(False, reason="InvalidJoinColumn")

        r_valid_quals = {r_canonical_qual, r_rel}
        if q_left in qualifier_to_canonical and q_right in r_valid_quals:
            resolved_left_qual = qualifier_to_canonical[q_left]
            resolved_left_key = c_left
            resolved_right_key = c_right
        elif q_right in qualifier_to_canonical and q_left in r_valid_quals:
            resolved_left_qual = qualifier_to_canonical[q_right]
            resolved_left_key = c_right
            resolved_right_key = c_left
        else:
            return JoinExtractorResult(False, reason="UnresolvedJoinQualifiers")

        right_relation = f"{r_node.db.lower()}.{r_rel}" if r_node.db else r_rel
        pk_map = {
            normalize_relation(k): v.lower() for k, v in (primary_keys or {}).items()
        }
        if resolved_right_key != pk_map.get(normalize_relation(right_relation), "id"):
            return JoinExtractorResult(False, reason="RightJoinKeyNotPrimaryKey")

        join_clauses.append(
            JoinClause(
                right_relation=right_relation,
                left_qualifier=resolved_left_qual,
                left_key=resolved_left_key,
                right_key=resolved_right_key,
                join_type=j_type,
                right_alias=r_alias or r_rel,
            )
        )
        used_canonical_quals.add(r_canonical_qual)
        used_relations.add(r_rel)
        qualifier_to_canonical[r_canonical_qual] = r_canonical_qual
        if r_rel not in qualifier_to_canonical:
            qualifier_to_canonical[r_rel] = r_canonical_qual

    def resolve_join_col(col_node: exp.Column) -> tuple[bool, tuple[str, str], str]:
        if col_node.args.get("catalog"):
            return False, ("", ""), "CatalogQualifier"
        if not col_node.table:
            return False, ("", ""), "UnqualifiedJoinColumn"
        raw_q = col_node.table.lower()
        if raw_q not in qualifier_to_canonical:
            return False, ("", ""), "UnknownTableQualifier"
        if type(col_node.this) is not exp.Identifier:
            return False, ("", ""), type(col_node.this).__name__
        return True, (qualifier_to_canonical[raw_q], col_node.this.name.lower()), ""

    if not stmt.expressions:
        return JoinExtractorResult(False, reason="EmptySelect")

    agg_fn: AggFn | None = None
    agg_col: tuple[str, str] | None = None
    proj_cols: list[tuple[str, str]] = []

    for expr in stmt.expressions:
        target = expr.this if type(expr) is exp.Alias else expr
        if type(target) is exp.Star or (
            type(target) is exp.Column and type(target.this) is exp.Star
        ):
            return JoinExtractorResult(False, reason="Star")
        if type(target) is exp.Column:
            ok, qc, err = resolve_join_col(target)
            if not ok:
                return JoinExtractorResult(False, reason=err)
            if qc not in proj_cols:
                proj_cols.append(qc)
        elif type(target) in (exp.Sum, exp.Count, exp.Avg):
            if agg_fn is not None:
                return JoinExtractorResult(False, reason="MultipleAggregates")
            if any(
                k not in ("this", "expressions", "big_int") and v
                for k, v in target.args.items()
            ):
                return JoinExtractorResult(False, reason="AggregateModifier")
            if target.args.get("expressions"):
                return JoinExtractorResult(False, reason="AggregateArguments")

            inner = target.this
            if type(target) is exp.Count and type(inner) is exp.Star:
                agg_fn = "count_star"
                agg_col = None
            elif type(inner) is exp.Column:
                ok, qc, err = resolve_join_col(inner)
                if not ok:
                    return JoinExtractorResult(False, reason=err)
                agg_col = qc
                if type(target) is exp.Sum:
                    agg_fn = "sum"
                elif type(target) is exp.Count:
                    agg_fn = "count"
                else:
                    agg_fn = "avg"
            else:
                return JoinExtractorResult(
                    False,
                    reason=type(inner).__name__ if inner else "EmptyAggregate",
                )
        else:
            return JoinExtractorResult(False, reason=type(target).__name__)

    group_cols_list: list[tuple[str, str]] = []
    group_node = stmt.args.get("group")
    if group_node is not None:
        if any(k != "expressions" and v for k, v in group_node.args.items()):
            return JoinExtractorResult(False, reason="GroupModifier")
        if not group_node.expressions:
            return JoinExtractorResult(False, reason="EmptyGroup")
        for g_expr in group_node.expressions:
            if type(g_expr) is not exp.Column:
                return JoinExtractorResult(False, reason=type(g_expr).__name__)
            ok, qc, err = resolve_join_col(g_expr)
            if not ok:
                return JoinExtractorResult(False, reason=err)
            if qc not in group_cols_list:
                group_cols_list.append(qc)

    if group_cols_list and agg_fn is None:
        return JoinExtractorResult(False, reason="GroupWithoutAggregate")

    if agg_fn is not None and not (set(proj_cols) <= set(group_cols_list)):
        return JoinExtractorResult(False, reason="UngroupedProjectionInAggregate")

    pred_terms: list[tuple[str, str, str, Any]] = []
    where_node = stmt.args.get("where")
    if where_node is not None:

        def parse_join_where(w_expr: Any) -> tuple[bool, str]:
            while type(w_expr) is exp.Paren:
                w_expr = w_expr.this
            if type(w_expr) is exp.And:
                ok_l, err_l = parse_join_where(w_expr.left)
                if not ok_l:
                    return False, err_l
                return parse_join_where(w_expr.right)
            if isinstance(w_expr, COMPARISON_NODE_TYPES):
                left = w_expr.left
                right = w_expr.right
                while type(left) is exp.Paren:
                    left = left.this
                while type(right) is exp.Paren:
                    right = right.this
                op_sym = _comparison_op_symbol(w_expr)

                if type(left) is exp.Column and type(right) is not exp.Column:
                    ok_c, qc, err_c = resolve_join_col(left)
                    if not ok_c:
                        return False, err_c
                    ok_lit, lit_val, err_lit = _extract_literal_value(right)
                    if not ok_lit:
                        return False, err_lit
                    pred_terms.append((qc[0], qc[1], op_sym, lit_val))
                    return True, ""
                if type(right) is exp.Column and type(left) is not exp.Column:
                    ok_c, qc, err_c = resolve_join_col(right)
                    if not ok_c:
                        return False, err_c
                    ok_lit, lit_val, err_lit = _extract_literal_value(left)
                    if not ok_lit:
                        return False, err_lit
                    pred_terms.append((qc[0], qc[1], FLIP_OP[op_sym], lit_val))
                    return True, ""
                return False, "NonLiteralWhereComparison"
            return False, type(w_expr).__name__

        ok_w, err_w = parse_join_where(where_node.this)
        if not ok_w:
            return JoinExtractorResult(False, reason=err_w)

    norm_sec = frozenset(
        (qualifier_to_canonical.get(q.lower(), q.lower()), c.lower())
        for q, c in security_cols
    )

    join_shape = MultiTableJoinShape(
        base_relation=(
            f"{base_node.db.lower()}.{base_rel}" if base_node.db else base_rel
        ),
        base_alias=base_alias or base_rel,
        joins=tuple(join_clauses),
        agg_fn=agg_fn,
        agg_col=agg_col,
        group_cols=tuple(group_cols_list),
        predicate_terms=tuple(pred_terms),
        projection_cols=tuple(proj_cols),
        security_cols=norm_sec,
        name=name,
    )
    for column in stmt.find_all(exp.Column):
        if column.catalog:
            return JoinExtractorResult(False, reason="CatalogQualifier")
        if column.db:
            qualifier = qualifier_to_canonical.get(column.table.lower(), "")
            relation = join_shape.qualifier_to_relation(qualifier)
            if "." not in relation or column.db.lower() != relation.rsplit(".", 1)[0]:
                return JoinExtractorResult(False, reason="UnknownSchemaQualifier")
    return JoinExtractorResult(is_supported=True, reason="", shape=join_shape)
