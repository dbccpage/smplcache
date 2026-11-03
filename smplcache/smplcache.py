# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
smplcache: Shape-driven, default-deny cache repair and invalidation certifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
import re
from typing import Any, Iterable, Literal, Mapping, Sequence


class _NullGroupKey:
    """Sentinel representing a SQL NULL group key."""

    _instance: _NullGroupKey | None = None

    def __new__(cls) -> _NullGroupKey:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NULL_GROUP_KEY"

    def __str__(self) -> str:
        return "NULL"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NullGroupKey)

    def __hash__(self) -> int:
        return hash("__smplcache_null_group_key__")


NULL_GROUP_KEY = _NullGroupKey()


class FingerprintSet(frozenset):  # type: ignore[type-arg]
    """
    A frozenset of column names that is also callable (`shape.fingerprint()`),
    satisfying both `shape.fingerprint()` (method) and `shape.fingerprint` (property).
    """

    def __call__(self) -> frozenset[str]:
        return frozenset(self)


def normalize_relation(relation: str) -> str:
    """Normalize an unquoted relation identifier without erasing its namespace."""
    if not relation or not isinstance(relation, str):
        return ""
    cleaned = relation.strip()
    if not cleaned:
        return ""
    parts = [p.strip() for p in cleaned.split(".") if p.strip()]
    return ".".join(parts).lower() if parts else ""


def _ambiguous_relation(left: str, right: str) -> bool:
    """An unqualified name cannot identify a qualified table without a binding."""
    return (
        left != right
        and left.rsplit(".", 1)[-1] == right.rsplit(".", 1)[-1]
        and ("." not in left or "." not in right)
    )


def _is_numeric_non_bool(val: Any) -> bool:
    return isinstance(val, (int, float, Decimal)) and not isinstance(val, bool)


def _to_decimal(val: Any) -> Decimal:
    if isinstance(val, bool):
        raise TypeError("Boolean is not a valid numeric aggregate value")
    if isinstance(val, Decimal):
        if val.is_nan() or val.is_infinite():
            raise ValueError("NaN or infinite Decimal is not supported")
        return val
    if isinstance(val, int):
        return Decimal(val)
    if isinstance(val, float):
        return _to_decimal(Decimal(str(val)))
    raise TypeError(f"Expected numeric value, got {type(val).__name__}")


def _decimal_add(left: Decimal, right: Decimal) -> Decimal:
    """Add finite decimal sums without rounding away low-order contributions."""
    left, right = _to_decimal(left), _to_decimal(right)
    if not left:
        return right
    if not right:
        return left
    exponent = min(int(left.as_tuple().exponent), int(right.as_tuple().exponent))
    precision = max(left.adjusted(), right.adjusted()) - exponent + 2
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, precision)
        return left + right


def _normalize_group_atom(val: Any) -> Any:
    if val is None:
        return NULL_GROUP_KEY
    if isinstance(val, float):
        d = _to_decimal(val)
        if d == d.to_integral_value():
            return int(d)
        return d
    if isinstance(val, Decimal) and val == val.to_integral_value():
        return int(val)
    return val


def _values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if _is_numeric_non_bool(a) and _is_numeric_non_bool(b):
        try:
            return _to_decimal(a) == _to_decimal(b)
        except (TypeError, ValueError, InvalidOperation):
            return False
    if type(a) is not type(b):
        return False
    return bool(a == b)


class DecisionKind(str, Enum):
    PRESERVE = "preserve"
    REPAIR = "repair"
    INVALIDATE = "invalidate"
    UNSUPPORTED = "unsupported"


@dataclass
class Certificate:
    shape: str
    event_id: str
    relation: str
    decision_kind: DecisionKind
    reason_code: str
    required_evidence: list[str] = field(default_factory=list)
    available_evidence: list[str] = field(default_factory=list)
    repair_program: str | None = None
    boundary_clock: str | None = None


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    certificate: Certificate

    @classmethod
    def preserve(cls, reason: str, cert: Certificate) -> Decision:
        cert.decision_kind = DecisionKind.PRESERVE
        cert.reason_code = reason
        return cls(DecisionKind.PRESERVE, reason, cert)

    @classmethod
    def repair(cls, reason: str, cert: Certificate) -> Decision:
        cert.decision_kind = DecisionKind.REPAIR
        cert.reason_code = reason
        return cls(DecisionKind.REPAIR, reason, cert)

    @classmethod
    def invalidate(cls, reason: str, cert: Certificate) -> Decision:
        cert.decision_kind = DecisionKind.INVALIDATE
        cert.reason_code = reason
        cert.repair_program = None
        return cls(DecisionKind.INVALIDATE, reason, cert)

    @classmethod
    def unsupported(cls, reason: str, cert: Certificate) -> Decision:
        cert.decision_kind = DecisionKind.UNSUPPORTED
        cert.reason_code = reason
        cert.repair_program = None
        return cls(DecisionKind.UNSUPPORTED, reason, cert)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True)
class Predicate:
    """Conjunction of (column, op, literal) terms for the v1 SQL fragment."""

    terms: tuple[tuple[str, str, Any], ...] = ()

    def __post_init__(self) -> None:
        normalized: list[tuple[str, str, Any]] = []
        for col, op, lit in self.terms:
            normalized.append((str(col).lower(), str(op), lit))
        object.__setattr__(self, "terms", tuple(normalized))

    @property
    def columns(self) -> frozenset[str]:
        return frozenset(col for col, _, _ in self.terms)

    def matches(self, row: Mapping[str, Any]) -> bool:
        if not self.terms:
            return True
        lower_row = {str(k).lower(): v for k, v in row.items()}
        for col, op, lit in self.terms:
            val = lower_row.get(col)
            # SQL three-valued logic: NULL compared with any literal in WHERE is UNKNOWN (falsy)
            if val is None or lit is None:
                return False
            if isinstance(lit, bool) or isinstance(val, bool):
                if type(val) is not type(lit):
                    raise TypeError(
                        f"Incompatible predicate types for {col}: {type(val).__name__} vs {type(lit).__name__}"
                    )
                lhs: Any = val
                rhs: Any = lit
            elif _is_numeric_non_bool(lit):
                if not _is_numeric_non_bool(val):
                    raise TypeError(
                        f"Incompatible predicate types for {col}: {type(val).__name__} vs numeric"
                    )
                lhs = _to_decimal(val)
                rhs = _to_decimal(lit)
            elif isinstance(lit, str):
                if not isinstance(val, str):
                    raise TypeError(
                        f"Incompatible predicate types for {col}: {type(val).__name__} vs str"
                    )
                lhs = val
                rhs = lit
            else:
                raise TypeError(f"Unsupported literal type: {type(lit).__name__}")

            if op in ("=", "=="):
                ok = lhs == rhs
            elif op in ("!=", "<>"):
                ok = lhs != rhs
            elif op == ">":
                ok = lhs > rhs
            elif op == ">=":
                ok = lhs >= rhs
            elif op == "<":
                ok = lhs < rhs
            elif op == "<=":
                ok = lhs <= rhs
            else:
                raise ValueError(f"Unsupported predicate operator: {op}")

            if not ok:
                return False
        return True


AggFn = Literal["sum", "count", "count_star", "avg"]


@dataclass(frozen=True, init=False)
class QueryShape:
    relation: str
    agg_fn: AggFn | None
    agg_col: str | None
    group_cols: tuple[str, ...]
    predicate: Predicate
    projection_cols: frozenset[str]
    security_cols: frozenset[str]
    name: str
    is_supported: bool = True
    unsupported_reason: str = ""
    _extra_predicate_cols: frozenset[str] = field(
        default_factory=frozenset, init=False, repr=False
    )
    _has_unparsed_predicate: bool = field(default=False, init=False, repr=False)

    def __init__(
        self,
        relation: str = "",
        agg_fn: AggFn | str | None = None,
        agg_col: str | None = None,
        group_cols: Iterable[str] = (),
        predicate: Predicate | None = None,
        projection_cols: Iterable[str] = frozenset(),
        security_cols: Iterable[str] = frozenset(),
        *,
        name: str = "unnamed_shape",
        predicate_cols: Iterable[str] | None = None,
        aggregate_cols: Iterable[str] | None = None,
        join_cols: Iterable[str] | None = None,
        is_supported: bool = True,
        unsupported_reason: str = "",
    ) -> None:
        norm_rel = normalize_relation(relation)
        norm_groups = tuple(str(c).lower() for c in group_cols)
        norm_projs = frozenset(str(c).lower() for c in projection_cols)
        norm_secs = frozenset(str(c).lower() for c in security_cols)

        supported = is_supported
        reason = unsupported_reason

        if join_cols:
            norm_joins = frozenset(str(c).lower() for c in join_cols)
            if norm_joins:
                supported = False
                reason = reason or "join_not_supported"
                norm_projs = norm_projs | norm_joins

        resolved_agg_fn: str | None = (
            str(agg_fn).lower() if agg_fn is not None else None
        )
        resolved_agg_col: str | None = (
            str(agg_col).lower() if agg_col is not None else None
        )

        if aggregate_cols is not None:
            agg_list = [str(a).lower() for a in aggregate_cols]
            for a in agg_list:
                if re.match(r"^\s*(?:min|max)\s*\(", a):
                    supported = False
                    reason = reason or "min_max_requires_auxiliary_state"
            if len(agg_list) == 1 and resolved_agg_col is None:
                resolved_agg_col = agg_list[0]
                if resolved_agg_fn is None and supported:
                    resolved_agg_fn = "sum"
            elif len(agg_list) > 1:
                supported = False
                reason = reason or "multiple_aggregates"

        has_unparsed_pred = False
        extra_pred_cols: frozenset[str] = frozenset()
        if predicate is not None:
            resolved_pred = predicate
        elif predicate_cols is not None:
            p_cols = frozenset(str(c).lower() for c in predicate_cols)
            extra_pred_cols = p_cols
            resolved_pred = Predicate(())
            if p_cols:
                has_unparsed_pred = True
        else:
            resolved_pred = Predicate(())

        object.__setattr__(self, "relation", norm_rel)
        object.__setattr__(self, "agg_fn", resolved_agg_fn)
        object.__setattr__(self, "agg_col", resolved_agg_col)
        object.__setattr__(self, "group_cols", norm_groups)
        object.__setattr__(self, "predicate", resolved_pred)
        object.__setattr__(self, "projection_cols", norm_projs)
        object.__setattr__(self, "security_cols", norm_secs)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "is_supported", supported)
        object.__setattr__(self, "unsupported_reason", reason)
        object.__setattr__(self, "_extra_predicate_cols", extra_pred_cols)
        object.__setattr__(self, "_has_unparsed_predicate", has_unparsed_pred)

    @property
    def predicate_cols(self) -> frozenset[str]:
        return self.predicate.columns | self._extra_predicate_cols

    @property
    def aggregate_cols(self) -> frozenset[str]:
        return frozenset({self.agg_col}) if self.agg_col else frozenset()

    @property
    def fingerprint(self) -> FingerprintSet:
        cols: set[str] = set()
        if self.agg_col is not None:
            cols.add(self.agg_col)
        cols.update(self.group_cols)
        cols.update(self.predicate.columns)
        cols.update(self._extra_predicate_cols)
        cols.update(self.projection_cols)
        cols.update(self.security_cols)
        return FingerprintSet(cols)

    def required_evidence(self) -> frozenset[str]:
        cols: set[str] = set()
        if self.agg_col is not None:
            cols.add(self.agg_col)
        cols.update(self.group_cols)
        cols.update(self.predicate.columns)
        cols.update(self._extra_predicate_cols)
        return frozenset(cols)

    def extract_group_key(self, row: Mapping[str, Any]) -> Any:
        lower_row = {str(k).lower(): v for k, v in row.items()}
        if not self.group_cols:
            return ()
        if len(self.group_cols) == 1:
            return _normalize_group_atom(lower_row.get(self.group_cols[0]))
        return tuple(
            _normalize_group_atom(lower_row.get(col)) for col in self.group_cols
        )


@dataclass
class WriteEvent:
    relation: str
    changed_cols: set[str] = field(default_factory=set)
    old: dict[str, Any] = field(default_factory=dict)
    new: dict[str, Any] = field(default_factory=dict)
    event_id: str = "unknown"
    explicit_operation: str | None = None
    unavailable_cols: set[str] = field(default_factory=set)
    schema_change: bool = False

    @property
    def operation(self) -> str:
        if self.explicit_operation:
            return self.explicit_operation.upper()
        if self.schema_change:
            return "SCHEMA_CHANGE"
        if not self.old and self.new:
            return "INSERT"
        if self.old and not self.new:
            return "DELETE"
        return "UPDATE"

    def derived_changed_cols(self) -> frozenset[str]:
        old_map = {str(k).lower(): v for k, v in (self.old or {}).items()}
        new_map = {str(k).lower(): v for k, v in (self.new or {}).items()}
        unavail = frozenset(str(c).lower() for c in (self.unavailable_cols or set()))
        if not old_map and new_map:
            return frozenset(new_map.keys()) | unavail
        if old_map and not new_map:
            return frozenset(old_map.keys()) | unavail
        if old_map and new_map:
            return (
                frozenset(
                    k
                    for k in (old_map.keys() | new_map.keys())
                    if k not in old_map
                    or k not in new_map
                    or not _values_equal(old_map[k], new_map[k])
                )
                | unavail
            )
        return frozenset(str(c).lower() for c in (self.changed_cols or set())) | unavail


@dataclass
class AggregateCache:
    values: dict[Any, Any] = field(default_factory=dict)
    _group_state: dict[Any, tuple[Decimal, int, int]] = field(
        default_factory=dict, init=False, repr=False
    )
    _from_rows: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        normalized_values: dict[Any, Any] = {}
        for k, v in list(self.values.items()):
            norm_k = k if isinstance(k, tuple) else _normalize_group_atom(k)
            if isinstance(v, tuple) and len(v) == 2:
                s_dec = _to_decimal(v[0])
                n_int = int(v[1])
                self._group_state[norm_k] = (s_dec, n_int, max(1, n_int))
                normalized_values[norm_k] = (
                    (s_dec / Decimal(n_int)) if n_int > 0 else None
                )
            elif v is None:
                self._group_state[norm_k] = (Decimal(0), 0, 1)
                normalized_values[norm_k] = None
            else:
                d_val = _to_decimal(v)
                self._group_state[norm_k] = (d_val, 1, 1)
                normalized_values[norm_k] = d_val
        self.values = normalized_values

    @classmethod
    def from_rows(
        cls,
        shape: QueryShape,
        rows: Sequence[Mapping[str, Any]],
    ) -> AggregateCache:
        cache = cls()
        cache._from_rows = True
        if shape.agg_fn is None:
            return cache

        is_grouped = len(shape.group_cols) > 0
        if not is_grouped:
            cache._group_state[()] = (Decimal(0), 0, 0)

        for r in rows:
            lower_row = {str(k).lower(): v for k, v in r.items()}
            if not shape.required_evidence() <= lower_row.keys():
                raise ValueError("Incomplete aggregate seed evidence")
            if not shape.predicate.matches(lower_row):
                continue
            g_key = shape.extract_group_key(lower_row)
            s_cur, n_cur, r_cur = cache._group_state.get(g_key, (Decimal(0), 0, 0))
            r_cur += 1
            if shape.agg_fn == "count_star":
                n_cur += 1
            else:
                raw_val = (
                    lower_row.get(shape.agg_col) if shape.agg_col is not None else None
                )
                if raw_val is not None:
                    n_cur += 1
                    if shape.agg_fn in ("sum", "avg"):
                        s_cur = _decimal_add(s_cur, _to_decimal(raw_val))
            cache._group_state[g_key] = (s_cur, n_cur, r_cur)

        for g_key in list(cache._group_state.keys()):
            cache._refresh_group_value(g_key, shape.agg_fn, is_grouped)
        return cache

    def _refresh_group_value(
        self, g_key: Any, agg_fn: AggFn | None, is_grouped: bool
    ) -> None:
        s_val, n_cnt, r_cnt = self._group_state.get(g_key, (Decimal(0), 0, 0))
        if is_grouped and r_cnt <= 0:
            self._group_state.pop(g_key, None)
            self.values.pop(g_key, None)
            return

        if not is_grouped and r_cnt <= 0:
            if agg_fn in ("count", "count_star"):
                self.values[g_key] = Decimal(0)
            else:
                self.values[g_key] = None
            return

        if agg_fn == "count_star":
            self.values[g_key] = Decimal(r_cnt)
        elif agg_fn == "count":
            self.values[g_key] = Decimal(n_cnt)
        elif agg_fn == "sum":
            self.values[g_key] = s_val if n_cnt > 0 else None
        elif agg_fn == "avg":
            self.values[g_key] = (s_val / Decimal(n_cnt)) if n_cnt > 0 else None

    def add(self, key: Any, amount: int | float | Decimal) -> None:
        norm_k = key if isinstance(key, tuple) else _normalize_group_atom(key)
        d_amt = _to_decimal(amount)
        cur = _to_decimal(self.values.get(norm_k, Decimal(0)) or Decimal(0))
        new_val = cur + d_amt
        self.values[norm_k] = new_val
        s_cur, n_cur, r_cur = self._group_state.get(norm_k, (Decimal(0), 0, 0))
        self._group_state[norm_k] = (s_cur + d_amt, n_cur + 1, r_cur + 1)

    def sub(self, key: Any, amount: int | float | Decimal) -> None:
        norm_k = key if isinstance(key, tuple) else _normalize_group_atom(key)
        d_amt = _to_decimal(amount)
        cur = _to_decimal(self.values.get(norm_k, Decimal(0)) or Decimal(0))
        new_val = cur - d_amt
        self.values[norm_k] = new_val
        s_cur, n_cur, r_cur = self._group_state.get(norm_k, (cur, 1, 1))
        self._group_state[norm_k] = (
            s_cur - d_amt,
            max(0, n_cur - 1),
            max(0, r_cur - 1),
        )


def row_matches_paid(row: Mapping[str, Any]) -> bool:
    """Legacy helper retained for backward-compatible imports."""
    return row.get("status") == "paid"


def _row_contribution(
    shape: QueryShape, row: Mapping[str, Any]
) -> tuple[bool, Decimal, int]:
    """
    Returns (matches_predicate, sum_delta, non_null_delta) for `row`.
    Raises TypeError/ValueError/InvalidOperation if types are invalid.
    """
    matches = shape.predicate.matches(row)
    if not matches:
        return False, Decimal(0), 0

    if shape.agg_fn == "count_star":
        return True, Decimal(0), 1

    raw_val = row.get(shape.agg_col) if shape.agg_col is not None else None
    if raw_val is None:
        return True, Decimal(0), 0

    if shape.agg_fn == "count":
        return True, Decimal(0), 1

    if shape.agg_fn in ("sum", "avg"):
        return True, _to_decimal(raw_val), 1

    raise ValueError(f"Unsupported aggregate function: {shape.agg_fn}")


def certify_event(shape: QueryShape, event: WriteEvent) -> Decision:
    shape_rel = normalize_relation(shape.relation)
    event_rel = normalize_relation(event.relation)

    cert = Certificate(
        shape=shape.name,
        event_id=str(event.event_id),
        relation=event_rel or event.relation,
        decision_kind=DecisionKind.PRESERVE,
        reason_code="",
    )

    if not shape.is_supported:
        if (
            "min" in shape.unsupported_reason.lower()
            or "max" in shape.unsupported_reason.lower()
        ):
            cert.required_evidence = ["auxiliary_extremum_state"]
            return Decision.unsupported("min_max_requires_auxiliary_state", cert)
        return Decision.unsupported(
            shape.unsupported_reason or "unsupported_shape", cert
        )

    if shape.agg_fn is not None and shape.agg_fn not in (
        "sum",
        "count",
        "count_star",
        "avg",
    ):
        if "min" in str(shape.agg_fn).lower() or "max" in str(shape.agg_fn).lower():
            cert.required_evidence = ["auxiliary_extremum_state"]
            return Decision.unsupported("min_max_requires_auxiliary_state", cert)
        return Decision.unsupported(f"unsupported_agg_fn: {shape.agg_fn}", cert)

    # Schema evolution or transaction boundary violations on wildcard/target relation fail closed
    explicit_op = event.explicit_operation.upper() if event.explicit_operation else None
    if event.schema_change or explicit_op in ("SCHEMA_CHANGE", "DDL"):
        if _ambiguous_relation(shape_rel, event_rel):
            return Decision.invalidate("ambiguous_relation", cert)
        if event.relation.strip() in ("*", "") or event_rel == shape_rel:
            return Decision.invalidate("schema_evolution", cert)
        return Decision.preserve("unrelated_relation", cert)

    if explicit_op == "TX_VIOLATION":
        if _ambiguous_relation(shape_rel, event_rel):
            return Decision.invalidate("ambiguous_relation", cert)
        if event.relation.strip() in ("*", "") or event_rel == shape_rel:
            return Decision.invalidate("transaction_boundary_violation", cert)
        return Decision.preserve("unrelated_relation", cert)

    # Rule 1: Relation mismatch after normalization -> preserve unrelated_relation
    if event_rel in ("", "*") or _ambiguous_relation(shape_rel, event_rel):
        return Decision.invalidate("ambiguous_relation", cert)
    if event_rel != shape_rel:
        return Decision.preserve("unrelated_relation", cert)

    if explicit_op == "TRUNCATE":
        return Decision.invalidate("truncate_event", cert)

    if explicit_op and explicit_op not in ("INSERT", "UPDATE", "DELETE"):
        return Decision.invalidate("unsupported_event_operation", cert)

    old_map = {str(k).lower(): v for k, v in (event.old or {}).items()}
    new_map = {str(k).lower(): v for k, v in (event.new or {}).items()}
    caller_cols = frozenset(str(c).lower() for c in (event.changed_cols or set()))
    unavail_cols = frozenset(str(c).lower() for c in (event.unavailable_cols or set()))

    if unavail_cols & shape.security_cols:
        return Decision.invalidate("security_column_changed", cert)

    if unavail_cols & shape.fingerprint():
        cert.required_evidence = sorted(
            shape.required_evidence() or shape.fingerprint()
        )
        cert.available_evidence = sorted(
            (old_map.keys() & new_map.keys())
            if (old_map and new_map)
            else (new_map.keys() or old_map.keys())
        )
        return Decision.invalidate("missing_evidence_for_repair", cert)

    if explicit_op:
        op = explicit_op
        if op == "INSERT" and (old_map or not new_map):
            return Decision.invalidate("invalid_event_payload", cert)
        if op == "DELETE" and (new_map or not old_map):
            return Decision.invalidate("invalid_event_payload", cert)
        if op == "UPDATE" and (not old_map or not new_map):
            cert.required_evidence = sorted(shape.required_evidence())
            cert.available_evidence = sorted(old_map.keys() & new_map.keys())
            return Decision.invalidate("missing_evidence_for_repair", cert)
    else:
        if not old_map and new_map:
            op = "INSERT"
        elif old_map and not new_map:
            op = "DELETE"
        elif old_map and new_map:
            op = "UPDATE"
        else:
            op = "EMPTY"

    # Rule 2: Derive changed_cols from old/new; if caller supplies it and they disagree -> invalidate
    if op == "INSERT":
        derived_cols = frozenset(new_map.keys()) | unavail_cols
        if caller_cols and (caller_cols - unavail_cols) != frozenset(new_map.keys()):
            return Decision.invalidate("changed_cols_mismatch", cert)
    elif op == "DELETE":
        derived_cols = frozenset(old_map.keys()) | unavail_cols
        if caller_cols and (caller_cols - unavail_cols) != frozenset(old_map.keys()):
            return Decision.invalidate("changed_cols_mismatch", cert)
    elif op == "UPDATE":
        common_keys = old_map.keys() & new_map.keys()
        known_diff = frozenset(
            k for k in common_keys if not _values_equal(old_map[k], new_map[k])
        )
        known_same = frozenset(
            k for k in common_keys if _values_equal(old_map[k], new_map[k])
        )
        sym_diff = frozenset(old_map.keys() ^ new_map.keys())
        derived_cols = known_diff | sym_diff | unavail_cols | caller_cols
        if caller_cols:
            non_unavail_caller = caller_cols - unavail_cols
            if not (known_diff <= non_unavail_caller) or (
                non_unavail_caller & known_same
            ):
                return Decision.invalidate("changed_cols_mismatch", cert)
            if not sym_diff and non_unavail_caller != known_diff:
                return Decision.invalidate("changed_cols_mismatch", cert)
    else:
        derived_cols = unavail_cols

    if op == "EMPTY":
        if not caller_cols and not unavail_cols:
            return Decision.invalidate("missing_evidence_for_repair", cert)
        effective_changed = caller_cols | unavail_cols
    else:
        effective_changed = derived_cols

    # Rule 3: INSERT or DELETE on the relation is never disjoint; go to the repair check.
    # Rule 4: Changed columns disjoint from fingerprint() -> preserve disjoint_columns.
    if op not in ("INSERT", "DELETE"):
        if not (effective_changed & shape.fingerprint()):
            return Decision.preserve("disjoint_columns", cert)

    # Rule 5: Any changed column in security_cols or projection_cols (non-aggregate shape) -> invalidate
    if (effective_changed & shape.security_cols) or (
        op in ("INSERT", "DELETE") and shape.security_cols
    ):
        return Decision.invalidate("security_column_changed", cert)

    if shape.agg_fn is None:
        return Decision.invalidate("shape_intersected", cert)

    if shape._has_unparsed_predicate:
        cert.required_evidence = sorted(shape.required_evidence())
        cert.available_evidence = sorted(
            (old_map.keys() & new_map.keys())
            if op == "UPDATE"
            else (new_map.keys() if op == "INSERT" else old_map.keys())
        )
        return Decision.invalidate("missing_evidence_for_repair", cert)

    # Rule 6: Repair only if agg_fn has a repair program and required_evidence() is covered
    # on old and new separately (not on their union).
    req = shape.required_evidence()
    cert.required_evidence = sorted(req)

    if op == "UPDATE":
        avail_old = set(old_map.keys())
        avail_new = set(new_map.keys())
        cert.available_evidence = sorted(avail_old & avail_new)
        if not (req <= avail_old and req <= avail_new):
            return Decision.invalidate("missing_evidence_for_repair", cert)
    elif op == "INSERT":
        avail_new = set(new_map.keys())
        cert.available_evidence = sorted(avail_new)
        if not (req <= avail_new):
            return Decision.invalidate("missing_evidence_for_repair", cert)
    elif op == "DELETE":
        avail_old = set(old_map.keys())
        cert.available_evidence = sorted(avail_old)
        if not (req <= avail_old):
            return Decision.invalidate("missing_evidence_for_repair", cert)
    else:
        cert.available_evidence = []
        return Decision.invalidate("missing_evidence_for_repair", cert)

    try:
        old_in, old_sum, old_n = (
            _row_contribution(shape, old_map)
            if op in ("UPDATE", "DELETE")
            else (False, Decimal(0), 0)
        )
        new_in, new_sum, new_n = (
            _row_contribution(shape, new_map)
            if op in ("UPDATE", "INSERT")
            else (False, Decimal(0), 0)
        )
        for row in (old_map, new_map):
            if row:
                hash(shape.extract_group_key(row))
    except (TypeError, ValueError, InvalidOperation):
        return Decision.invalidate("invalid_evidence_type", cert)

    cert.repair_program = f"{shape.agg_fn}_by_group"

    if op == "UPDATE":
        old_group = shape.extract_group_key(old_map)
        new_group = shape.extract_group_key(new_map)

        if not old_in and not new_in:
            cert.repair_program = None
            return Decision.preserve("noop_update", cert)

        if (
            old_in
            and new_in
            and _values_equal(old_group, new_group)
            and old_sum == new_sum
            and old_n == new_n
        ):
            cert.repair_program = None
            return Decision.preserve("noop_update", cert)

        if not old_in and new_in:
            return Decision.repair("predicate_entry", cert)
        if old_in and not new_in:
            return Decision.repair("predicate_exit", cert)
        if not _values_equal(old_group, new_group):
            return Decision.repair("group_move", cert)
        return Decision.repair("value_change", cert)

    if op == "INSERT":
        return Decision.repair("insert", cert)
    return Decision.repair("delete", cert)


def process_event(
    shape: QueryShape, cache: AggregateCache, event: WriteEvent
) -> Decision:
    decision = certify_event(shape, event)
    if decision.kind != DecisionKind.REPAIR:
        return decision

    if not cache._from_rows:
        return Decision.invalidate("missing_aggregate_state", decision.certificate)

    try:
        old_map = {str(k).lower(): v for k, v in (event.old or {}).items()}
        new_map = {str(k).lower(): v for k, v in (event.new or {}).items()}
        op = event.operation

        old_in, old_sum, old_n = (
            _row_contribution(shape, old_map)
            if op in ("UPDATE", "DELETE")
            else (False, Decimal(0), 0)
        )
        new_in, new_sum, new_n = (
            _row_contribution(shape, new_map)
            if op in ("UPDATE", "INSERT")
            else (False, Decimal(0), 0)
        )

        is_grouped = len(shape.group_cols) > 0
        touched_groups: list[Any] = []

        if old_in:
            old_group = shape.extract_group_key(old_map)
            if old_group not in cache._group_state and old_group in cache.values:
                init_v = cache.values[old_group]
                if shape.agg_fn in ("count", "count_star"):
                    cnt_v = int(_to_decimal(init_v))
                    cache._group_state[old_group] = (Decimal(0), cnt_v, cnt_v)
                else:
                    cache._group_state[old_group] = (
                        _to_decimal(init_v),
                        1,
                        1,
                    )
            s_cur, n_cur, r_cur = cache._group_state.get(old_group, (Decimal(0), 0, 0))
            cache._group_state[old_group] = (
                _decimal_add(s_cur, old_sum.copy_negate()),
                n_cur - old_n,
                r_cur - 1,
            )
            touched_groups.append(old_group)

        if new_in:
            new_group = shape.extract_group_key(new_map)
            if new_group not in cache._group_state and new_group in cache.values:
                init_v = cache.values[new_group]
                if shape.agg_fn in ("count", "count_star"):
                    cnt_v = int(_to_decimal(init_v))
                    cache._group_state[new_group] = (Decimal(0), cnt_v, cnt_v)
                else:
                    cache._group_state[new_group] = (
                        _to_decimal(init_v),
                        1,
                        1,
                    )
            s_cur, n_cur, r_cur = cache._group_state.get(new_group, (Decimal(0), 0, 0))
            cache._group_state[new_group] = (
                _decimal_add(s_cur, new_sum),
                n_cur + new_n,
                r_cur + 1,
            )
            if new_group not in touched_groups:
                touched_groups.append(new_group)

        for g_key in touched_groups:
            cache._refresh_group_value(g_key, shape.agg_fn, is_grouped)

    except (TypeError, ValueError, InvalidOperation, ZeroDivisionError):
        return Decision.invalidate("repair_type_error", decision.certificate)

    return decision
