# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Relational normal-form synthesis, Tableau Chase losslessness verification,
topologically sorted PostgreSQL 16 / SQL Server 2022 DDL generation,
FD-aware GROUP BY reduction, and bounded incremental MIN/MAX extremum cache
for SmplCache.

Absorbed from Simplex Normal Forms (1NF -> 2NF -> 3NF -> E-KNF -> BCNF -> Tableau Chase).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence

from smplcache.smplcache import (
    NULL_GROUP_KEY,
    Certificate,
    Decision,
    DecisionKind,
    Predicate,
    QueryShape,
    WriteEvent,
    _checked_group_atom,
    _normalize_group_atom,
    _values_equal,
    normalize_relation,
)


def _to_frozenset(items: Iterable[str] | str) -> frozenset[str]:
    if isinstance(items, str):
        parts = [p.strip() for p in items.split(",") if p.strip()]
        return frozenset(parts)
    return frozenset(str(x).strip() for x in items if str(x).strip())


@dataclass(frozen=True)
class FunctionalDependency:
    """
    Represents a functional dependency X -> Y over attribute names.
    """

    determinant: frozenset[str]
    dependent: frozenset[str]

    def __init__(
        self,
        determinant: Iterable[str] | str,
        dependent: Iterable[str] | str,
    ) -> None:
        object.__setattr__(self, "determinant", _to_frozenset(determinant))
        object.__setattr__(self, "dependent", _to_frozenset(dependent))

    @classmethod
    def parse(cls, text: str) -> FunctionalDependency:
        """
        Parse a functional dependency string such as ``"customer_id -> customer_tier, region"``
        or ``"A, B -> C"``.
        """
        if "->" in text:
            lhs_raw, rhs_raw = text.split("->", 1)
        elif "→" in text:
            lhs_raw, rhs_raw = text.split("→", 1)
        else:
            raise ValueError(f"Functional dependency must contain '->': {text!r}")
        lhs = _to_frozenset(lhs_raw)
        rhs = _to_frozenset(rhs_raw)
        if not lhs or not rhs:
            raise ValueError(f"Determinant and dependent must be non-empty: {text!r}")
        return cls(lhs, rhs)

    def format(self) -> str:
        lhs = ", ".join(sorted(self.determinant))
        rhs = ", ".join(sorted(self.dependent))
        return f"{{{lhs}}} -> {{{rhs}}}"

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "determinant": sorted(self.determinant),
            "dependent": sorted(self.dependent),
        }


def coerce_fds(
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str],
) -> tuple[FunctionalDependency, ...]:
    """
    Normalize a sequence of FunctionalDependency instances, dicts
    (``{"determinant": [...], "dependent": [...]}`` or ``{"lhs": [...], "rhs": [...]}``),
    or ``"X -> Y"`` strings into a tuple of `FunctionalDependency`.
    """
    result: list[FunctionalDependency] = []
    for item in fds:
        if isinstance(item, FunctionalDependency):
            result.append(item)
        elif isinstance(item, str):
            result.append(FunctionalDependency.parse(item))
        elif isinstance(item, Mapping):
            lhs = item.get("determinant", item.get("lhs", ()))
            rhs = item.get("dependent", item.get("rhs", ()))
            result.append(FunctionalDependency(lhs, rhs))
        else:
            raise TypeError(
                f"Unsupported functional dependency specification: {item!r}"
            )
    return tuple(result)


@dataclass(frozen=True)
class RelationalSchema:
    """
    Represents a relational table schema R(U, F) with attributes U and functional
    dependencies F, plus optional sample tuples and column SQL types.
    """

    name: str
    attributes: frozenset[str]
    functional_dependencies: tuple[FunctionalDependency, ...] = ()
    tuples: tuple[dict[str, Any], ...] = ()
    column_types: dict[str, str] = field(default_factory=dict)

    def __init__(
        self,
        name: str,
        attributes: Iterable[str],
        functional_dependencies: Iterable[
            FunctionalDependency | Mapping[str, Any] | str
        ] = (),
        tuples: Sequence[Mapping[str, Any]] = (),
        column_types: Mapping[str, str] | None = None,
    ) -> None:
        object.__setattr__(self, "name", str(name).strip() or "Relation")
        object.__setattr__(self, "attributes", _to_frozenset(attributes))
        object.__setattr__(
            self, "functional_dependencies", coerce_fds(functional_dependencies)
        )
        object.__setattr__(self, "tuples", tuple(dict(t) for t in tuples))
        object.__setattr__(
            self,
            "column_types",
            {str(k).strip(): str(v).strip() for k, v in (column_types or {}).items()},
        )


def compute_attribute_closure(
    attributes: Iterable[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str],
) -> frozenset[str]:
    """
    Compute the attribute closure X^+ under functional dependencies F using
    Armstrong's axioms (reflexivity, augmentation, transitivity).
    """
    closure = set(_to_frozenset(attributes))
    norm_fds = coerce_fds(fds)
    changed = True
    while changed:
        changed = False
        for fd in norm_fds:
            if fd.determinant.issubset(closure) and not fd.dependent.issubset(closure):
                closure.update(fd.dependent)
                changed = True
    return frozenset(closure)


attribute_closure = compute_attribute_closure


def compute_minimal_cover(
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str],
) -> tuple[FunctionalDependency, ...]:
    """
    Compute a canonical minimal (irreducible) cover G for a set of functional
    dependencies F:
      1. Split right-hand sides into singleton attributes.
      2. Remove extraneous left-hand side attributes.
      3. Remove redundant dependencies.
    """
    singletons: list[FunctionalDependency] = []
    for fd in coerce_fds(fds):
        for attr in sorted(fd.dependent):
            if attr not in fd.determinant:
                singletons.append(
                    FunctionalDependency(fd.determinant, frozenset({attr}))
                )

    reduced_lhs: list[FunctionalDependency] = []
    for i, fd in enumerate(singletons):
        current_det = set(fd.determinant)
        for attr in sorted(fd.determinant):
            if len(current_det) <= 1:
                break
            candidate_det = frozenset(current_det - {attr})
            temp_fds = (
                reduced_lhs
                + [FunctionalDependency(frozenset(current_det), fd.dependent)]
                + singletons[i + 1 :]
            )
            if fd.dependent.issubset(
                compute_attribute_closure(candidate_det, temp_fds)
            ):
                current_det.remove(attr)
        reduced_lhs.append(FunctionalDependency(frozenset(current_det), fd.dependent))

    final_fds: list[FunctionalDependency] = []
    for i, fd in enumerate(reduced_lhs):
        remaining = final_fds + reduced_lhs[i + 1 :]
        if not fd.dependent.issubset(
            compute_attribute_closure(fd.determinant, remaining)
        ):
            if fd not in final_fds:
                final_fds.append(fd)

    final_fds.sort(key=lambda d: (sorted(d.determinant), sorted(d.dependent)))
    return tuple(final_fds)


minimal_cover = compute_minimal_cover


def find_candidate_keys(
    schema_or_attributes: RelationalSchema | Iterable[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str] | None = None,
) -> list[frozenset[str]]:
    """
    Find all minimal superkeys (candidate keys) of a relational schema R(U, F).
    """
    if isinstance(schema_or_attributes, RelationalSchema):
        all_attrs = schema_or_attributes.attributes
        eff_fds = (
            coerce_fds(fds)
            if fds is not None
            else schema_or_attributes.functional_dependencies
        )
    else:
        all_attrs = _to_frozenset(schema_or_attributes)
        eff_fds = coerce_fds(fds or ())

    if not all_attrs:
        return []

    rhs_attrs: set[str] = set()
    lhs_attrs: set[str] = set()
    for fd in eff_fds:
        rhs_attrs.update(fd.dependent - fd.determinant)
        lhs_attrs.update(fd.determinant)

    essential = frozenset(all_attrs - rhs_attrs)
    if compute_attribute_closure(essential, eff_fds) >= all_attrs:
        return [essential]

    middle = sorted((all_attrs & lhs_attrs) - essential)
    found_keys: list[frozenset[str]] = []

    from itertools import combinations

    for r in range(1, len(middle) + 1):
        for comb in combinations(middle, r):
            candidate = essential | frozenset(comb)
            if any(k.issubset(candidate) for k in found_keys):
                continue
            if compute_attribute_closure(candidate, eff_fds) >= all_attrs:
                found_keys.append(candidate)

    if not found_keys:
        return [all_attrs]

    found_keys.sort(key=lambda k: (len(k), sorted(k)))
    return found_keys


candidate_keys = find_candidate_keys


def is_superkey(
    attributes: frozenset[str],
    candidate_key_list: Iterable[frozenset[str]],
) -> bool:
    return any(ck.issubset(attributes) for ck in candidate_key_list)


def check_1nf(schema: RelationalSchema) -> tuple[bool, list[str]]:
    """
    Check First Normal Form (1NF): all attribute values in sample tuples are
    atomic (not nested lists, sets, or dicts) and schema has at least one attribute.
    """
    violations: list[str] = []
    if not schema.attributes:
        violations.append("Schema has no attributes.")
    for idx, row in enumerate(schema.tuples):
        for k, v in row.items():
            if isinstance(v, (list, dict, set, tuple, frozenset)):
                violations.append(
                    f"Tuple {idx} has non-atomic collection value in attribute '{k}': {v!r}"
                )
    return len(violations) == 0, violations


def check_2nf(schema: RelationalSchema) -> tuple[bool, list[str]]:
    """
    Check Second Normal Form (2NF): 1NF holds and no non-prime attribute is
    partially dependent on a proper subset of any candidate key.
    """
    ok_1nf, v_1nf = check_1nf(schema)
    if not ok_1nf:
        return False, v_1nf

    cks = find_candidate_keys(schema)
    prime_attrs = frozenset().union(*cks) if cks else frozenset()
    non_prime_attrs = schema.attributes - prime_attrs
    if not non_prime_attrs:
        return True, []

    violations: list[str] = []
    for ck in cks:
        if len(ck) <= 1:
            continue
        from itertools import combinations

        for r in range(1, len(ck)):
            for sub in combinations(sorted(ck), r):
                sub_set = frozenset(sub)
                sub_closure = compute_attribute_closure(
                    sub_set, schema.functional_dependencies
                )
                for np_attr in sorted(non_prime_attrs):
                    if np_attr in sub_closure:
                        msg = (
                            f"Partial dependency: proper key subset {{{', '.join(sorted(sub_set))}}} "
                            f"of key {{{', '.join(sorted(ck))}}} determines non-prime attribute '{np_attr}'."
                        )
                        if msg not in violations:
                            violations.append(msg)
    return len(violations) == 0, violations


def check_3nf(schema: RelationalSchema) -> tuple[bool, list[str]]:
    """
    Check Third Normal Form (3NF): 2NF holds and for every non-trivial FD X -> A,
    either X is a superkey or A is a prime attribute (part of some candidate key).
    """
    ok_2nf, v_2nf = check_2nf(schema)
    violations = list(v_2nf)

    cks = find_candidate_keys(schema)
    prime_attrs = frozenset().union(*cks) if cks else frozenset()
    cover = compute_minimal_cover(schema.functional_dependencies)

    for fd in cover:
        for attr in sorted(fd.dependent - fd.determinant):
            if not is_superkey(fd.determinant, cks) and attr not in prime_attrs:
                msg = (
                    f"Transitive dependency violation of 3NF: {{{', '.join(sorted(fd.determinant))}}} "
                    f"-> '{attr}' (determinant is not a superkey and '{attr}' is non-prime)."
                )
                if msg not in violations:
                    violations.append(msg)

    return len(violations) == 0 and ok_2nf, violations


def _is_elementary_fd(
    determinant: frozenset[str],
    attr: str,
    all_fds: Iterable[FunctionalDependency],
) -> bool:
    """
    An FD X -> A is an elementary functional dependency if X != empty,
    A not in X, and there is no proper subset X' subset X such that X' -> A
    holds in F+.
    """
    if not determinant or attr in determinant:
        return False
    if attr not in attribute_closure(determinant, all_fds):
        return False
    for a in determinant:
        reduced = determinant - {a}
        if attr in attribute_closure(reduced, all_fds):
            return False
    return True


def find_elementary_keys(schema: RelationalSchema) -> list[frozenset[str]]:
    """
    Find all elementary keys of a relation schema (Zaniolo 1982).
    X is an elementary key if there exists some attribute A such that X -> A
    is a full (elementary) FD AND X is a superkey of R.
    """
    cks = find_candidate_keys(schema)
    elementary_keys: set[frozenset[str]] = set(cks)
    cover = compute_minimal_cover(schema.functional_dependencies)
    for fd in cover:
        for attr in fd.dependent - fd.determinant:
            if is_superkey(fd.determinant, cks) and _is_elementary_fd(
                fd.determinant, attr, cover
            ):
                elementary_keys.add(fd.determinant)
    return sorted(elementary_keys, key=lambda k: (len(k), tuple(sorted(k))))


def check_eknf(schema: RelationalSchema) -> tuple[bool, list[str]]:
    """
    Check Elementary Key Normal Form (E-KNF, Zaniolo 1982):
    A schema R is in E-KNF iff for every non-trivial FD X -> A in F+:
      1. X is a superkey (or elementary key), OR
      2. A is an elementary key attribute (belongs to at least one elementary key).
    """
    ok_1nf, v_1nf = check_1nf(schema)
    if not ok_1nf:
        return False, v_1nf

    cks = find_candidate_keys(schema)
    elem_keys = find_elementary_keys(schema)
    elem_prime_attrs: frozenset[str] = (
        frozenset().union(*elem_keys) if elem_keys else frozenset()
    )
    cover = compute_minimal_cover(schema.functional_dependencies)
    violations: list[str] = []

    for fd in cover:
        for attr in sorted(fd.dependent - fd.determinant):
            if not is_superkey(fd.determinant, cks) and attr not in elem_prime_attrs:
                msg = (
                    f"E-KNF violation: {{{', '.join(sorted(fd.determinant))}}} -> '{attr}' "
                    f"(determinant is not a superkey and '{attr}' is not in any elementary key)."
                )
                if msg not in violations:
                    violations.append(msg)
    return len(violations) == 0, violations


def check_bcnf(schema: RelationalSchema) -> tuple[bool, list[str]]:
    """
    Check Boyce-Codd Normal Form (BCNF): 1NF holds and for every non-trivial FD
    X -> Y, X is a superkey of R.
    """
    ok_1nf, v_1nf = check_1nf(schema)
    if not ok_1nf:
        return False, v_1nf

    cks = find_candidate_keys(schema)
    cover = compute_minimal_cover(schema.functional_dependencies)
    violations: list[str] = []

    for fd in cover:
        non_trivial = fd.dependent - fd.determinant
        if non_trivial and not is_superkey(fd.determinant, cks):
            msg = (
                f"BCNF violation: {{{', '.join(sorted(fd.determinant))}}} -> "
                f"{{{', '.join(sorted(non_trivial))}}} (determinant is not a superkey)."
            )
            if msg not in violations:
                violations.append(msg)

    return len(violations) == 0, violations


@dataclass(frozen=True)
class NormalFormReport:
    """
    Classification summary for a relational schema across 1NF, 2NF, 3NF, E-KNF, and BCNF.
    """

    highest_normal_form: str
    is_1nf: bool
    is_2nf: bool
    is_3nf: bool
    is_eknf: bool
    is_bcnf: bool
    candidate_keys: tuple[tuple[str, ...], ...]
    prime_attributes: tuple[str, ...]
    non_prime_attributes: tuple[str, ...]
    minimal_cover: tuple[FunctionalDependency, ...]
    violations_1nf: tuple[str, ...]
    violations_2nf: tuple[str, ...]
    violations_3nf: tuple[str, ...]
    violations_bcnf: tuple[str, ...]

    @property
    def violations(self) -> tuple[str, ...]:
        seen: list[str] = []
        for v in (
            *self.violations_1nf,
            *self.violations_2nf,
            *self.violations_3nf,
            *self.violations_bcnf,
        ):
            if v not in seen:
                seen.append(v)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "highest_normal_form": self.highest_normal_form,
            "is_1nf": self.is_1nf,
            "is_2nf": self.is_2nf,
            "is_3nf": self.is_3nf,
            "is_eknf": self.is_eknf,
            "is_bcnf": self.is_bcnf,
            "candidate_keys": [list(k) for k in self.candidate_keys],
            "prime_attributes": list(self.prime_attributes),
            "non_prime_attributes": list(self.non_prime_attributes),
            "minimal_cover": [fd.to_dict() for fd in self.minimal_cover],
            "violations_1nf": list(self.violations_1nf),
            "violations_2nf": list(self.violations_2nf),
            "violations_3nf": list(self.violations_3nf),
            "violations_bcnf": list(self.violations_bcnf),
        }


def classify_normal_form(
    schema_or_attributes: RelationalSchema | Iterable[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str] | None = None,
    *,
    name: str = "Relation",
) -> NormalFormReport:
    """
    Classify a relational schema R(U, F) into its highest satisfied normal form
    (UNF, 1NF, 2NF, 3NF, E-KNF, or BCNF) with explicit violation diagnostics.
    """
    schema = (
        schema_or_attributes
        if isinstance(schema_or_attributes, RelationalSchema)
        else RelationalSchema(name, schema_or_attributes, fds or ())
    )
    ok_1, v1 = check_1nf(schema)
    ok_2, v2 = check_2nf(schema)
    ok_3, v3 = check_3nf(schema)
    ok_ek, _ = check_eknf(schema)
    ok_bc, vbc = check_bcnf(schema)

    if ok_bc:
        highest = "BCNF"
    elif ok_3:
        highest = "3NF"
    elif ok_2:
        highest = "2NF"
    elif ok_1:
        highest = "1NF"
    else:
        highest = "UNF"

    cks = find_candidate_keys(schema)
    prime = sorted(frozenset().union(*cks)) if cks else []
    non_prime = sorted(schema.attributes - frozenset(prime))
    cov = compute_minimal_cover(schema.functional_dependencies)

    return NormalFormReport(
        highest_normal_form=highest,
        is_1nf=ok_1,
        is_2nf=ok_2,
        is_3nf=ok_3,
        is_eknf=ok_ek,
        is_bcnf=ok_bc,
        candidate_keys=tuple(tuple(sorted(k)) for k in cks),
        prime_attributes=tuple(prime),
        non_prime_attributes=tuple(non_prime),
        minimal_cover=cov,
        violations_1nf=tuple(v1),
        violations_2nf=tuple(v2),
        violations_3nf=tuple(v3),
        violations_bcnf=tuple(vbc),
    )


def project_functional_dependencies(
    all_fds: Iterable[FunctionalDependency | Mapping[str, Any] | str],
    sub_attributes: Iterable[str],
) -> tuple[FunctionalDependency, ...]:
    """
    Project a set of functional dependencies F onto a subset of attributes Z
    by computing X^+ intersect Z for all non-empty subsets X of Z and returning
    the minimal cover of the projected dependencies.
    """
    sub_set = _to_frozenset(sub_attributes)
    norm_fds = coerce_fds(all_fds)
    attr_list = sorted(sub_set)
    projected: list[FunctionalDependency] = []

    from itertools import combinations

    for r in range(1, len(attr_list) + 1):
        for comb in combinations(attr_list, r):
            lhs = frozenset(comb)
            closure = compute_attribute_closure(lhs, norm_fds)
            rhs = (closure & sub_set) - lhs
            if rhs:
                projected.append(FunctionalDependency(lhs, rhs))

    return compute_minimal_cover(projected)


def synthesize_3nf_bernstein(
    schema_or_attributes: RelationalSchema | Iterable[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str] | None = None,
    *,
    name: str = "R",
) -> list[RelationalSchema]:
    """
    Synthesize a lossless-join, dependency-preserving 3NF decomposition using
    Bernstein's algorithm:
      1. Compute canonical minimal cover G of F.
      2. Group dependencies with identical determinants X_i -> Y_i.
      3. Merge equivalent keys if X_i <-> X_j.
      4. Remove schemas properly contained in another synthesized schema.
      5. Append a candidate key schema if no synthesized schema contains a superkey of R.
    """
    schema = (
        schema_or_attributes
        if isinstance(schema_or_attributes, RelationalSchema)
        else RelationalSchema(name, schema_or_attributes, fds or ())
    )
    cover = compute_minimal_cover(schema.functional_dependencies)
    cks = find_candidate_keys(schema)

    by_lhs: dict[frozenset[str], set[str]] = {}
    for fd in cover:
        by_lhs.setdefault(fd.determinant, set()).update(fd.dependent)

    groups: list[set[str]] = []
    for lhs, rhs in sorted(by_lhs.items(), key=lambda kv: sorted(kv[0])):
        attrs = set(lhs) | set(rhs)
        groups.append(attrs)

    # Remove subsets
    filtered: list[frozenset[str]] = []
    for i, g in enumerate(groups):
        g_set = frozenset(g)
        if any(
            i != j
            and (g_set < frozenset(other) or (g_set == frozenset(other) and j < i))
            for j, other in enumerate(groups)
        ):
            continue
        filtered.append(g_set)

    # Ensure lossless join by including a candidate key if none is present
    if cks and not any(is_superkey(rel_attrs, cks) for rel_attrs in filtered):
        filtered.append(cks[0])

    if not filtered and schema.attributes:
        filtered.append(schema.attributes)

    result: list[RelationalSchema] = []
    for idx, rel_attrs in enumerate(filtered, start=1):
        proj_fds = project_functional_dependencies(
            schema.functional_dependencies, rel_attrs
        )
        proj_types = {k: v for k, v in schema.column_types.items() if k in rel_attrs}
        result.append(
            RelationalSchema(
                name=f"{schema.name}_3nf_{idx}",
                attributes=rel_attrs,
                functional_dependencies=proj_fds,
                column_types=proj_types,
            )
        )
    return result


synthesize_3nf = synthesize_3nf_bernstein


def is_lossless_binary_split(
    r1_attrs: Iterable[str],
    r2_attrs: Iterable[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str],
) -> bool:
    """
    Check Heath's theorem condition for a binary decomposition (R1, R2):
    (R1 intersect R2) -> R1 or (R1 intersect R2) -> R2 under F^+.
    """
    s1 = _to_frozenset(r1_attrs)
    s2 = _to_frozenset(r2_attrs)
    common = s1 & s2
    if not common:
        return False
    common_closure = compute_attribute_closure(common, fds)
    return s1.issubset(common_closure) or s2.issubset(common_closure)


@dataclass(frozen=True)
class BCNFStep:
    """
    Records a single Heath binary split during Tsou-Fischer BCNF decomposition.
    """

    input_relation: str
    input_attributes: frozenset[str]
    violating_fd: FunctionalDependency
    left_name: str
    left_attributes: frozenset[str]
    right_name: str
    right_attributes: frozenset[str]


@dataclass(frozen=True)
class BCNFDecomposition:
    """
    Result of Tsou-Fischer BCNF decomposition, including the final BCNF relations,
    the step-by-step split trace, and whether all original functional dependencies
    remain preserved across the decomposed relations.
    """

    relations: tuple[RelationalSchema, ...]
    steps: tuple[BCNFStep, ...]
    dependency_preserving: bool
    lost_dependencies: tuple[FunctionalDependency, ...]

    @property
    def tables(self) -> tuple[RelationalSchema, ...]:
        return self.relations

    @property
    def is_dependency_preserving(self) -> bool:
        return self.dependency_preserving


def decompose_bcnf_tree(
    schema_or_attributes: RelationalSchema | Iterable[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str] | None = None,
    *,
    name: str = "R",
) -> BCNFDecomposition:
    """
    Decompose a relational schema R(U, F) into Boyce-Codd Normal Form (BCNF)
    using Tsou-Fischer / Heath lossless binary splits.
    """
    schema = (
        schema_or_attributes
        if isinstance(schema_or_attributes, RelationalSchema)
        else RelationalSchema(name, schema_or_attributes, fds or ())
    )
    steps: list[BCNFStep] = []
    worklist: list[RelationalSchema] = [schema]
    final_relations: list[RelationalSchema] = []
    split_counter = 0

    while worklist:
        curr = worklist.pop(0)
        ok_bcnf, _ = check_bcnf(curr)
        if ok_bcnf:
            final_relations.append(curr)
            continue

        cks = find_candidate_keys(curr)
        cover = compute_minimal_cover(curr.functional_dependencies)
        violating_fd: FunctionalDependency | None = None
        for fd in cover:
            if (fd.dependent - fd.determinant) and not is_superkey(fd.determinant, cks):
                violating_fd = fd
                break

        if violating_fd is None:
            final_relations.append(curr)
            continue

        split_counter += 1
        x_closure = (
            compute_attribute_closure(
                violating_fd.determinant, curr.functional_dependencies
            )
            & curr.attributes
        )
        r1_attrs = x_closure
        r2_attrs = violating_fd.determinant | (curr.attributes - x_closure)

        r1_name = f"{schema.name}_bcnf_{split_counter}a"
        r2_name = f"{schema.name}_bcnf_{split_counter}b"

        r1_schema = RelationalSchema(
            name=r1_name,
            attributes=r1_attrs,
            functional_dependencies=project_functional_dependencies(
                schema.functional_dependencies, r1_attrs
            ),
            column_types={
                k: v for k, v in schema.column_types.items() if k in r1_attrs
            },
        )
        r2_schema = RelationalSchema(
            name=r2_name,
            attributes=r2_attrs,
            functional_dependencies=project_functional_dependencies(
                schema.functional_dependencies, r2_attrs
            ),
            column_types={
                k: v for k, v in schema.column_types.items() if k in r2_attrs
            },
        )
        steps.append(
            BCNFStep(
                input_relation=curr.name,
                input_attributes=curr.attributes,
                violating_fd=violating_fd,
                left_name=r1_name,
                left_attributes=r1_attrs,
                right_name=r2_name,
                right_attributes=r2_attrs,
            )
        )
        worklist.extend([r1_schema, r2_schema])

    # Rename final relations cleanly in order
    clean_relations: list[RelationalSchema] = []
    for idx, rel in enumerate(final_relations, start=1):
        rel_name = (
            rel.name
            if len(final_relations) == 1 and rel.name == schema.name
            else f"{schema.name}_bcnf_{idx}"
        )
        clean_relations.append(
            RelationalSchema(
                name=rel_name,
                attributes=rel.attributes,
                functional_dependencies=rel.functional_dependencies,
                column_types=rel.column_types,
            )
        )

    union_projected_fds: list[FunctionalDependency] = []
    for rel in clean_relations:
        union_projected_fds.extend(rel.functional_dependencies)

    lost: list[FunctionalDependency] = []
    for orig_fd in compute_minimal_cover(schema.functional_dependencies):
        closure_under_proj = compute_attribute_closure(
            orig_fd.determinant, union_projected_fds
        )
        if not orig_fd.dependent.issubset(closure_under_proj):
            lost.append(orig_fd)

    return BCNFDecomposition(
        relations=tuple(clean_relations),
        steps=tuple(steps),
        dependency_preserving=(len(lost) == 0),
        lost_dependencies=tuple(lost),
    )


decompose_bcnf = decompose_bcnf_tree


@dataclass(frozen=True)
class ChaseStep:
    """
    A single step of the Tableau Chase algorithm applying a functional dependency
    X -> Y to equate symbols in the tableau.
    """

    fd: FunctionalDependency
    equated_from: str
    equated_to: str
    tableau_snapshot: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ChaseResult:
    """
    Outcome of the Tableau Chase algorithm for lossless-join verification.
    When `is_lossless` is False, `spurious_tuple_counterexample` provides a
    concrete 2-row instance over the original relation whose project-join produces
    a spurious row not present in the original relation.
    """

    attributes: tuple[str, ...]
    initial_tableau: tuple[tuple[str, ...], ...]
    final_tableau: tuple[tuple[str, ...], ...]
    steps: tuple[ChaseStep, ...]
    is_lossless: bool
    winning_row_index: int | None = None
    spurious_tuple_counterexample: dict[str, Any] | None = None

    @property
    def counterexample(self) -> dict[str, Any] | None:
        return self.spurious_tuple_counterexample

    def to_dict(self) -> dict[str, Any]:
        return {
            "attributes": list(self.attributes),
            "initial_tableau": [list(r) for r in self.initial_tableau],
            "final_tableau": [list(r) for r in self.final_tableau],
            "steps": [
                {
                    "fd": s.fd.format(),
                    "equated_from": s.equated_from,
                    "equated_to": s.equated_to,
                }
                for s in self.steps
            ],
            "is_lossless": self.is_lossless,
            "winning_row_index": self.winning_row_index,
            "spurious_tuple_counterexample": self.spurious_tuple_counterexample,
        }


def _build_spurious_counterexample(
    ordered_attrs: Sequence[str],
    final_tableau: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """
    Construct a concrete counterexample instance from a converged Tableau Chase
    matrix that failed to produce an all-distinguished (`a_j`) row.
    Mapping each distinct symbol in `final_tableau` to a concrete value produces
    a relation I that satisfies all FDs in F, yet whose projection-join onto
    the decomposed schemas yields the all-distinguished tuple (a_1, ..., a_n)
    as a spurious tuple not present in I.
    """
    original_tuples: list[dict[str, str]] = []
    for row in final_tableau:
        t = {attr: sym for attr, sym in zip(ordered_attrs, row)}
        if t not in original_tuples:
            original_tuples.append(t)

    spurious_tuple = {attr: f"a_{j + 1}" for j, attr in enumerate(ordered_attrs)}
    return {
        "original_instance": original_tuples,
        "spurious_joined_tuple": spurious_tuple,
        "explanation": (
            "The converged tableau rows satisfy all declared functional dependencies, "
            "and their projections cover every decomposed component of the all-distinguished "
            "tuple, so the natural join of the projections synthesizes the spurious tuple "
            "which does not exist in the original instance."
        ),
    }


def chase_join_dependency(
    all_attributes: Iterable[str] | RelationalSchema,
    decomposed_relations: Sequence[Iterable[str] | RelationalSchema],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str] | None = None,
) -> ChaseResult:
    """
    Execute the Tableau Chase algorithm to test whether decomposing R(U, F) into
    {R_1, ..., R_k} is a lossless-join decomposition under F.

    Constructs the initial k x |U| tableau with distinguished symbols `a_j` where
    attribute A_j in R_i and non-distinguished symbols `b_{i,j}` otherwise, then
    repeatedly equates symbols under F until convergence or until a row becomes
    all-distinguished (`a_1, ..., a_n`).
    """
    if isinstance(all_attributes, RelationalSchema):
        u_set = all_attributes.attributes
        eff_fds = (
            coerce_fds(fds)
            if fds is not None
            else all_attributes.functional_dependencies
        )
    else:
        u_set = _to_frozenset(all_attributes)
        eff_fds = coerce_fds(fds or ())

    ordered_attrs = tuple(sorted(u_set))
    attr_idx = {a: j for j, a in enumerate(ordered_attrs)}

    sub_sets: list[frozenset[str]] = []
    for rel in decomposed_relations:
        if isinstance(rel, RelationalSchema):
            sub_sets.append(rel.attributes)
        else:
            sub_sets.append(_to_frozenset(rel))

    tableau: list[list[str]] = []
    for i, r_set in enumerate(sub_sets, start=1):
        row: list[str] = []
        for j, attr in enumerate(ordered_attrs, start=1):
            if attr in r_set:
                row.append(f"a_{j}")
            else:
                row.append(f"b_{i}_{j}")
        tableau.append(row)

    initial_snapshot = tuple(tuple(r) for r in tableau)
    steps: list[ChaseStep] = []
    cover = compute_minimal_cover(eff_fds)

    def _has_winning_row() -> int | None:
        for r_i, row in enumerate(tableau):
            if all(cell.startswith("a_") for cell in row):
                return r_i
        return None

    changed = True
    while changed and _has_winning_row() is None:
        changed = False
        for fd in cover:
            if not (fd.determinant <= u_set and fd.dependent <= u_set):
                continue
            lhs_indices = [attr_idx[a] for a in sorted(fd.determinant)]
            rhs_indices = [attr_idx[a] for a in sorted(fd.dependent)]

            groups: dict[tuple[str, ...], list[int]] = {}
            for r_i, row in enumerate(tableau):
                key = tuple(row[idx] for idx in lhs_indices)
                groups.setdefault(key, []).append(r_i)

            for matching_rows in groups.values():
                if len(matching_rows) < 2:
                    continue
                for col_idx in rhs_indices:
                    symbols = {tableau[r_i][col_idx] for r_i in matching_rows}
                    if len(symbols) <= 1:
                        continue
                    distinguished = sorted(s for s in symbols if s.startswith("a_"))
                    target_sym = (
                        distinguished[0] if distinguished else sorted(symbols)[0]
                    )
                    for sym in sorted(symbols):
                        if sym == target_sym:
                            continue
                        for r_row in tableau:
                            for c_i in range(len(ordered_attrs)):
                                if r_row[c_i] == sym:
                                    r_row[c_i] = target_sym
                        changed = True
                        steps.append(
                            ChaseStep(
                                fd=fd,
                                equated_from=sym,
                                equated_to=target_sym,
                                tableau_snapshot=tuple(tuple(r) for r in tableau),
                            )
                        )
                    if _has_winning_row() is not None:
                        break
                if _has_winning_row() is not None:
                    break
            if _has_winning_row() is not None:
                break

    winner = _has_winning_row()
    is_lossless = winner is not None
    final_snapshot = tuple(tuple(r) for r in tableau)
    counterexample = (
        None
        if is_lossless
        else _build_spurious_counterexample(ordered_attrs, final_snapshot)
    )

    return ChaseResult(
        attributes=ordered_attrs,
        initial_tableau=initial_snapshot,
        final_tableau=final_snapshot,
        steps=tuple(steps),
        is_lossless=is_lossless,
        winning_row_index=winner,
        spurious_tuple_counterexample=counterexample,
    )


tableau_chase = chase_join_dependency


def is_lossless_decomposition(
    all_attributes: Iterable[str] | RelationalSchema,
    decomposed_relations: Sequence[Iterable[str] | RelationalSchema],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str] | None = None,
) -> bool:
    return chase_join_dependency(all_attributes, decomposed_relations, fds).is_lossless


def _sanitize_sql_ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())
    if not cleaned:
        return "col"
    if cleaned[0].isdigit():
        return f"t_{cleaned}"
    return cleaned.lower()


def _infer_sql_column_type(
    col_name: str,
    dialect: Literal["postgres", "mssql"],
    explicit_types: Mapping[str, str] | None = None,
) -> str:
    if explicit_types and col_name in explicit_types:
        raw_t = explicit_types[col_name].upper()
        if dialect == "mssql" and "TEXT" in raw_t:
            return "NVARCHAR(128)"
        return explicit_types[col_name]
    lower = col_name.lower()
    if lower == "id" or lower.endswith("_id"):
        return "INTEGER" if dialect == "postgres" else "INT"
    if any(
        tok in lower
        for tok in ("amount", "price", "total", "balance", "cost", "rate", "tax")
    ):
        return "NUMERIC(12, 2)" if dialect == "postgres" else "DECIMAL(12, 2)"
    if any(tok in lower for tok in ("count", "qty", "quantity", "num", "age")):
        return "INTEGER" if dialect == "postgres" else "INT"
    return "VARCHAR(128)" if dialect == "postgres" else "NVARCHAR(128)"


@dataclass(frozen=True)
class ForeignKeySpec:
    child_table: str
    parent_table: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class SqlDdlBundle:
    """
    Topologically sorted SQL DDL bundle (parent dimension tables before child
    referencing tables) with PRIMARY KEY, UNIQUE candidate keys, FOREIGN KEY
    constraints, and automatic foreign-key indexes for join maintenance.
    """

    dialect: Literal["postgres", "mssql"]
    table_order: tuple[str, ...]
    statements: tuple[str, ...]
    foreign_keys: tuple[ForeignKeySpec, ...]
    ddl_script: str

    def __str__(self) -> str:
        return self.ddl_script


def generate_sql_ddl(
    relations: Sequence[RelationalSchema],
    *,
    dialect: Literal["postgres", "mssql"] = "postgres",
    include_fk_indexes: bool = True,
) -> SqlDdlBundle:
    """
    Generate topologically sorted PostgreSQL 16 (`dialect="postgres"`) or
    SQL Server 2022 T-SQL (`dialect="mssql"`) DDL statements for a decomposed
    family of `RelationalSchema` tables.
    """
    if dialect not in ("postgres", "mssql"):
        raise ValueError(f"Unsupported SQL dialect: {dialect!r}")

    table_meta: list[dict[str, Any]] = []
    for rel in relations:
        t_name = _sanitize_sql_ident(rel.name)
        cks = find_candidate_keys(rel)
        pk = tuple(sorted(cks[0])) if cks else tuple(sorted(rel.attributes))
        alt_keys = [tuple(sorted(k)) for k in cks[1:]]
        ordered_cols = list(pk) + sorted(rel.attributes - set(pk))
        table_meta.append(
            {
                "schema": rel,
                "table_name": t_name,
                "pk": pk,
                "pk_set": frozenset(pk),
                "alt_keys": alt_keys,
                "ordered_cols": ordered_cols,
                "attr_set": rel.attributes,
            }
        )

    # Discover foreign-key references: if table A contains all PK attributes of table B
    # (and A's PK is not identical to B's PK unless A has strictly more attributes)
    fks_by_child: dict[str, list[ForeignKeySpec]] = {
        m["table_name"]: [] for m in table_meta
    }
    deps_by_table: dict[str, set[str]] = {m["table_name"]: set() for m in table_meta}

    for child in table_meta:
        c_name = child["table_name"]
        for parent in table_meta:
            p_name = parent["table_name"]
            if c_name == p_name:
                continue
            p_pk_set = parent["pk_set"]
            if not p_pk_set or not p_pk_set.issubset(child["attr_set"]):
                continue
            # Avoid bidirectional cycles when two relations share identical primary keys
            if child["pk_set"] == p_pk_set and (
                len(child["attr_set"]) < len(parent["attr_set"])
                or (
                    len(child["attr_set"]) == len(parent["attr_set"])
                    and c_name < p_name
                )
            ):
                continue
            fk_spec = ForeignKeySpec(
                child_table=c_name,
                parent_table=p_name,
                columns=parent["pk"],
            )
            fks_by_child[c_name].append(fk_spec)
            deps_by_table[c_name].add(p_name)

    # Topological sort (parents before children)
    meta_by_name = {m["table_name"]: m for m in table_meta}
    emitted: list[str] = []
    remaining = [m["table_name"] for m in table_meta]
    while remaining:
        ready = [
            t
            for t in remaining
            if all(parent in emitted for parent in deps_by_table[t])
        ]
        if not ready:
            # Break cycle deterministically by dropping back-edge dependencies
            chosen = remaining[0]
            fks_by_child[chosen] = [
                fk for fk in fks_by_child[chosen] if fk.parent_table in emitted
            ]
            emitted.append(chosen)
            remaining.remove(chosen)
        else:
            for t in ready:
                emitted.append(t)
                remaining.remove(t)

    statements: list[str] = []
    all_fks: list[ForeignKeySpec] = []

    for t_name in emitted:
        meta = meta_by_name[t_name]
        rel_obj: RelationalSchema = meta["schema"]
        col_lines: list[str] = []
        for col in meta["ordered_cols"]:
            c_ident = _sanitize_sql_ident(col)
            sql_t = _infer_sql_column_type(col, dialect, rel_obj.column_types)
            col_lines.append(f"    {c_ident} {sql_t} NOT NULL")

        pk_cols_sql = ", ".join(_sanitize_sql_ident(c) for c in meta["pk"])
        col_lines.append(f"    CONSTRAINT pk_{t_name} PRIMARY KEY ({pk_cols_sql})")

        for ak_idx, ak in enumerate(meta["alt_keys"], start=1):
            ak_cols_sql = ", ".join(_sanitize_sql_ident(c) for c in ak)
            col_lines.append(
                f"    CONSTRAINT uq_{t_name}_{ak_idx} UNIQUE ({ak_cols_sql})"
            )

        for fk_idx, fk in enumerate(fks_by_child[t_name], start=1):
            all_fks.append(fk)
            fk_cols_sql = ", ".join(_sanitize_sql_ident(c) for c in fk.columns)
            col_lines.append(
                f"    CONSTRAINT fk_{t_name}_{fk.parent_table}_{fk_idx} "
                f"FOREIGN KEY ({fk_cols_sql}) REFERENCES {fk.parent_table} ({fk_cols_sql})"
            )

        body_sql = ",\n".join(col_lines)
        create_stmt = f"CREATE TABLE {t_name} (\n{body_sql}\n);"
        statements.append(create_stmt)

        if include_fk_indexes:
            for fk_idx, fk in enumerate(fks_by_child[t_name], start=1):
                if tuple(fk.columns) == tuple(meta["pk"]):
                    continue
                fk_cols_sql = ", ".join(_sanitize_sql_ident(c) for c in fk.columns)
                idx_name = f"ix_{t_name}_fk_{fk.parent_table}_{fk_idx}"
                statements.append(
                    f"CREATE INDEX {idx_name} ON {t_name} ({fk_cols_sql});"
                )

    return SqlDdlBundle(
        dialect=dialect,
        table_order=tuple(emitted),
        statements=tuple(statements),
        foreign_keys=tuple(all_fks),
        ddl_script="\n\n".join(statements),
    )


@dataclass(frozen=True)
class RedundantGroupColReport:
    """
    Reports FD-redundant GROUP BY columns (Class D normal-form reduction):
    when a query groups by (X, Y) and X -> Y holds in F^+, Y is functionally
    determined by X within every group, so Y can be dropped from the minimal
    grouping key without changing group partitioning.
    """

    original_group_cols: tuple[str, ...]
    minimal_group_cols: tuple[str, ...]
    redundant_group_cols: tuple[str, ...]
    determining_fds: tuple[FunctionalDependency, ...]
    reduced_shape: QueryShape | None = None

    def __bool__(self) -> bool:
        return bool(self.redundant_group_cols)

    def __len__(self) -> int:
        return len(self.redundant_group_cols)

    def __iter__(self) -> Iterator[str]:
        return iter(self.redundant_group_cols)

    def __contains__(self, item: object) -> bool:
        return item in self.redundant_group_cols

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_group_cols": list(self.original_group_cols),
            "minimal_group_cols": list(self.minimal_group_cols),
            "redundant_group_cols": list(self.redundant_group_cols),
            "determining_fds": [fd.to_dict() for fd in self.determining_fds],
        }


def detect_fd_redundant_group_cols(
    shape_or_group_cols: QueryShape | Sequence[str],
    fds: Iterable[FunctionalDependency | Mapping[str, Any] | str],
) -> RedundantGroupColReport:
    """
    Given a `QueryShape` (or sequence of `GROUP BY` column names) and functional
    dependencies `fds`, identify any grouping columns Y that lie in the attribute
    closure of the remaining grouping columns X (i.e. Y in X^+ \\ X).
    """
    if isinstance(shape_or_group_cols, QueryShape):
        orig_cols = tuple(c.lower() for c in shape_or_group_cols.group_cols)
        source_shape: QueryShape | None = shape_or_group_cols
    else:
        orig_cols = tuple(str(c).strip().lower() for c in shape_or_group_cols)
        source_shape = None

    norm_fds = tuple(
        FunctionalDependency(
            frozenset(a.lower() for a in fd.determinant),
            frozenset(a.lower() for a in fd.dependent),
        )
        for fd in coerce_fds(fds)
    )

    kept = list(orig_cols)
    redundant: list[str] = []
    active_fds: list[FunctionalDependency] = []

    # Check in reverse order so leading primary/determinant grouping columns are kept
    for col in reversed(orig_cols):
        if len(kept) <= 1:
            break
        others = [c for c in kept if c != col]
        closure = compute_attribute_closure(others, norm_fds)
        if col in closure:
            kept.remove(col)
            redundant.append(col)
            for fd in compute_minimal_cover(norm_fds):
                if col in fd.dependent and fd.determinant.issubset(
                    compute_attribute_closure(others, norm_fds)
                ):
                    if fd not in active_fds:
                        active_fds.append(fd)

    redundant.reverse()
    reduced_shape: QueryShape | None = None
    if source_shape is not None:
        reduced_shape = QueryShape(
            relation=source_shape.relation,
            agg_fn=source_shape.agg_fn,
            agg_col=source_shape.agg_col,
            group_cols=tuple(kept),
            predicate=source_shape.predicate,
            security_cols=source_shape.security_cols,
            projection_cols=source_shape.projection_cols,
            name=source_shape.name,
            is_supported=source_shape.is_supported,
            unsupported_reason=source_shape.unsupported_reason,
        )

    return RedundantGroupColReport(
        original_group_cols=orig_cols,
        minimal_group_cols=tuple(kept),
        redundant_group_cols=tuple(redundant),
        determining_fds=tuple(active_fds),
        reduced_shape=reduced_shape,
    )


def _comparable_extremum_val(raw_val: Any) -> Decimal | str | int:
    if isinstance(raw_val, bool) or isinstance(raw_val, float):
        raise TypeError("Boolean and float extremum values are unsupported")
    if isinstance(raw_val, Decimal):
        if not raw_val.is_finite():
            raise ValueError("Non-finite Decimal extremum value")
        return raw_val
    if isinstance(raw_val, int):
        return Decimal(raw_val)
    if isinstance(raw_val, str):
        try:
            d = Decimal(raw_val)
            if d.is_finite():
                return d
        except InvalidOperation:
            pass
        return raw_val
    raise TypeError(f"Unsupported extremum value type: {type(raw_val).__name__}")


@dataclass
class SortedMultisetExtremumCache:
    """
    Incremental MIN/MAX extremum cache per group using a bounded sorted multiset
    window (`max_window_per_group`).

    Supports exact incremental maintenance under `INSERT`, `UPDATE`, and `DELETE`:
    - When `max_window_per_group` is `None` (unbounded) or the group's row count
      never exceeded `max_window_per_group`, all `INSERT`/`UPDATE`/`DELETE` mutations
      repair in-place (`DecisionKind.REPAIR`, `FIX IN PLACE`) in $O(\\log W)$ time.
    - When `max_window_per_group` is bounded and a group's population exceeds `W`,
      only the best `W` extremal values (`W` smallest for `MIN`, `W` largest for `MAX`)
      are retained. If deletions exhaust the retained `W` window while truncated rows
      still exist in that group, `process()` safely latches to
      `Decision.invalidate("extremum_window_exhausted", cert)` (`REREAD`).
    """

    shape: QueryShape
    agg_fn: Literal["min", "max"] = "min"
    max_window_per_group: int | None = None
    values: dict[Any, Any] = field(default_factory=dict)
    is_invalidated: bool = False
    _windows: dict[Any, list[Any]] = field(default_factory=dict, repr=False)
    _group_counts: dict[Any, int] = field(default_factory=dict, repr=False)
    _truncated_groups: set[Any] = field(default_factory=set, repr=False)
    _truncation_bound: dict[Any, Any] = field(default_factory=dict, repr=False)
    _from_rows: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        fn = (self.shape.agg_fn or self.agg_fn or "min").lower()
        if fn not in ("min", "max"):
            raise ValueError(
                f"SortedMultisetExtremumCache requires 'min' or 'max', got {fn!r}"
            )
        self.agg_fn = fn  # type: ignore[assignment]
        if self.max_window_per_group is not None and self.max_window_per_group <= 0:
            raise ValueError(
                f"max_window_per_group must be >= 1, got {self.max_window_per_group}"
            )

    @classmethod
    def from_rows(
        cls,
        shape: QueryShape,
        rows: Sequence[Mapping[str, Any]],
        *,
        agg_fn: Literal["min", "max"] | None = None,
        max_window_per_group: int | None = None,
    ) -> SortedMultisetExtremumCache:
        eff_fn: Literal["min", "max"] = agg_fn or (
            shape.agg_fn if shape.agg_fn in ("min", "max") else "min"
        )  # type: ignore[assignment]
        cache = cls(
            shape=shape,
            agg_fn=eff_fn,
            max_window_per_group=max_window_per_group,
        )
        cache.rebuild(rows)
        return cache

    def rebuild(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.values.clear()
        self._windows.clear()
        self._group_counts.clear()
        self._truncated_groups.clear()
        self._truncation_bound.clear()
        self.is_invalidated = False
        self._from_rows = True

        agg_col = (self.shape.agg_col or "").lower()
        if not agg_col:
            raise ValueError("SortedMultisetExtremumCache requires shape.agg_col")

        for row in rows:
            lower_row = {str(k).lower(): v for k, v in row.items()}
            if not self.shape.predicate.matches(lower_row):
                continue
            raw_v = lower_row.get(agg_col)
            if raw_v is None:
                continue
            comp_v = _comparable_extremum_val(raw_v)
            g_key = self.shape.extract_group_key(lower_row)
            self._insert_into_group(g_key, comp_v)

        self._refresh_all_values()

    def _insert_into_group(self, g_key: Any, comp_v: Any) -> bool:
        """
        Insert `comp_v` into group `g_key`'s sorted window.
        When `g_key` is truncated (`g_key in self._truncation_bound`), any incoming
        value that is worse than or equal to the truncation frontier `tau` belongs
        in the truncated multiset rather than the retained window, preventing an
        inserted non-extremal value from jumping ahead of previously truncated values.
        """
        win = self._windows.setdefault(g_key, [])
        self._group_counts[g_key] = self._group_counts.get(g_key, 0) + 1

        if g_key in self._truncated_groups and g_key in self._truncation_bound:
            tau = self._truncation_bound[g_key]
            if self.agg_fn == "min" and comp_v >= tau:
                return True
            if self.agg_fn == "max" and comp_v <= tau:
                return True

        bisect.insort(win, comp_v)
        if (
            self.max_window_per_group is not None
            and len(win) > self.max_window_per_group
        ):
            self._truncated_groups.add(g_key)
            if self.agg_fn == "min":
                evicted = win.pop()  # drop largest, keep smallest W
                if g_key in self._truncation_bound:
                    if evicted < self._truncation_bound[g_key]:
                        self._truncation_bound[g_key] = evicted
                else:
                    self._truncation_bound[g_key] = evicted
            else:
                evicted = win.pop(0)  # drop smallest, keep largest W
                if g_key in self._truncation_bound:
                    if evicted > self._truncation_bound[g_key]:
                        self._truncation_bound[g_key] = evicted
                else:
                    self._truncation_bound[g_key] = evicted
        return True

    def _remove_from_group(
        self,
        g_key: Any,
        comp_v: Any,
        *,
        allow_empty_window: bool = False,
    ) -> bool:
        """
        Remove one occurrence of `comp_v` from `g_key`.
        Returns False if the bounded window was exhausted (`extremum_window_exhausted`)
        or if `comp_v` is inconsistent with the current window state.
        """
        total_cnt = self._group_counts.get(g_key, 0)
        if total_cnt <= 0:
            return False
        win = self._windows.get(g_key, [])
        idx = bisect.bisect_left(win, comp_v)
        if idx < len(win) and win[idx] == comp_v:
            win.pop(idx)
            self._group_counts[g_key] = total_cnt - 1
            if self._group_counts[g_key] == 0:
                self._windows.pop(g_key, None)
                self._group_counts.pop(g_key, None)
                self._truncated_groups.discard(g_key)
                self._truncation_bound.pop(g_key, None)
                return True
            if not win and self._group_counts[g_key] > 0 and not allow_empty_window:
                # All W retained extremal elements were deleted while truncated rows remain!
                return False
            return True

        # Value was not in the retained window: only valid if group was truncated
        # and comp_v lies at or beyond the truncation frontier.
        if g_key in self._truncated_groups and g_key in self._truncation_bound:
            tau = self._truncation_bound[g_key]
            if self.agg_fn == "min" and comp_v >= tau:
                self._group_counts[g_key] = total_cnt - 1
                if self._group_counts[g_key] <= len(win):
                    self._truncated_groups.discard(g_key)
                    self._truncation_bound.pop(g_key, None)
                if (
                    not win
                    and self._group_counts.get(g_key, 0) > 0
                    and not allow_empty_window
                ):
                    return False
                return True
            if self.agg_fn == "max" and comp_v <= tau:
                self._group_counts[g_key] = total_cnt - 1
                if self._group_counts[g_key] <= len(win):
                    self._truncated_groups.discard(g_key)
                    self._truncation_bound.pop(g_key, None)
                if (
                    not win
                    and self._group_counts.get(g_key, 0) > 0
                    and not allow_empty_window
                ):
                    return False
                return True
        return False

    def _refresh_group_value(self, g_key: Any) -> None:
        win = self._windows.get(g_key)
        is_grouped = len(self.shape.group_cols) > 0
        out_key = g_key if is_grouped else "extremum"
        if not win:
            self.values.pop(out_key, None)
            return
        self.values[out_key] = win[0] if self.agg_fn == "min" else win[-1]

    def _refresh_all_values(self) -> None:
        self.values.clear()
        for g_key in list(self._windows.keys()):
            self._refresh_group_value(g_key)

    def process(self, event: WriteEvent) -> Decision:
        """
        Certify and incrementally apply `event` to the sorted multiset extremum
        cache, returning `PRESERVE` (`KEEP`), `REPAIR` (`FIX IN PLACE`), or
        `INVALIDATE` (`REREAD` when `extremum_window_exhausted` or evidence is missing).
        """
        shape_rel = normalize_relation(self.shape.relation)
        event_rel = normalize_relation(event.relation)
        b_clock = event.boundary_clock or (
            str(event.event_id) if event.event_id is not None else None
        )
        cert = Certificate(
            shape=self.shape.name,
            event_id=str(event.event_id),
            relation=event_rel or event.relation,
            decision_kind=DecisionKind.PRESERVE,
            reason_code="",
            boundary_clock=b_clock,
        )

        if event.schema_change or (
            event.explicit_operation
            and event.explicit_operation.upper() in ("SCHEMA_CHANGE", "DDL", "TRUNCATE")
        ):
            self.is_invalidated = True
            return Decision.invalidate("schema_evolution", cert)

        if event_rel != shape_rel:
            return Decision.preserve("unrelated_relation", cert)

        if not self._from_rows or self.is_invalidated:
            self.is_invalidated = True
            return Decision.invalidate("missing_aggregate_state", cert)

        old_map = {str(k).lower(): v for k, v in (event.old or {}).items()}
        new_map = {str(k).lower(): v for k, v in (event.new or {}).items()}
        op = event.operation

        agg_col = (self.shape.agg_col or "").lower()
        req = self.shape.required_evidence()
        cert.required_evidence = sorted(req)

        if op == "UPDATE":
            common = old_map.keys() & new_map.keys()
            diff_cols = {
                k for k in common if not _values_equal(old_map[k], new_map[k])
            } | (old_map.keys() ^ new_map.keys())
            if not (diff_cols & self.shape.fingerprint()):
                return Decision.preserve("disjoint_columns", cert)
            if not (req <= old_map.keys() and req <= new_map.keys()):
                self.is_invalidated = True
                return Decision.invalidate("missing_evidence_for_repair", cert)
        elif op == "INSERT":
            if not (req <= new_map.keys()):
                self.is_invalidated = True
                return Decision.invalidate("missing_evidence_for_repair", cert)
        elif op == "DELETE":
            if not (req <= old_map.keys()):
                self.is_invalidated = True
                return Decision.invalidate("missing_evidence_for_repair", cert)
        else:
            self.is_invalidated = True
            return Decision.invalidate("missing_evidence_for_repair", cert)

        old_in = op in ("UPDATE", "DELETE") and self.shape.predicate.matches(old_map)
        new_in = op in ("UPDATE", "INSERT") and self.shape.predicate.matches(new_map)

        if not old_in and not new_in:
            return Decision.preserve("predicate_miss", cert)

        try:
            old_g = self.shape.extract_group_key(old_map) if old_in else None
            new_g = self.shape.extract_group_key(new_map) if new_in else None
            old_v = (
                _comparable_extremum_val(old_map.get(agg_col))
                if (old_in and old_map.get(agg_col) is not None)
                else None
            )
            new_v = (
                _comparable_extremum_val(new_map.get(agg_col))
                if (new_in and new_map.get(agg_col) is not None)
                else None
            )
        except (TypeError, ValueError, InvalidOperation):
            self.is_invalidated = True
            return Decision.invalidate("invalid_evidence_type", cert)

        if old_in and new_in and old_g == new_g and old_v == new_v:
            return Decision.preserve("noop_update", cert)

        same_group_replace = (
            old_in
            and new_in
            and old_g == new_g
            and old_v is not None
            and new_v is not None
        )

        if old_in and old_v is not None:
            ok_rem = self._remove_from_group(
                old_g, old_v, allow_empty_window=same_group_replace
            )
            if not ok_rem:
                self.is_invalidated = True
                return Decision.invalidate("extremum_window_exhausted", cert)
            if not same_group_replace:
                self._refresh_group_value(old_g)

        if new_in and new_v is not None:
            self._insert_into_group(new_g, new_v)
            win_after = self._windows.get(new_g, [])
            if not win_after and self._group_counts.get(new_g, 0) > 0:
                self.is_invalidated = True
                return Decision.invalidate("extremum_window_exhausted", cert)
            self._refresh_group_value(new_g)

        cert.repair_program = f"{self.agg_fn}_sorted_multiset_by_group"
        if op == "INSERT":
            return Decision.repair("insert", cert)
        if op == "DELETE":
            return Decision.repair("delete", cert)
        return Decision.repair("value_change", cert)


__all__ = [
    "BCNFDecomposition",
    "BCNFStep",
    "ChaseResult",
    "ChaseStep",
    "ForeignKeySpec",
    "FunctionalDependency",
    "NormalFormReport",
    "RedundantGroupColReport",
    "RelationalSchema",
    "SortedMultisetExtremumCache",
    "SqlDdlBundle",
    "attribute_closure",
    "candidate_keys",
    "chase_join_dependency",
    "check_1nf",
    "check_2nf",
    "check_3nf",
    "check_bcnf",
    "check_eknf",
    "classify_normal_form",
    "coerce_fds",
    "compute_attribute_closure",
    "compute_minimal_cover",
    "decompose_bcnf",
    "decompose_bcnf_tree",
    "detect_fd_redundant_group_cols",
    "find_candidate_keys",
    "find_elementary_keys",
    "generate_sql_ddl",
    "is_lossless_binary_split",
    "is_lossless_decomposition",
    "is_superkey",
    "minimal_cover",
    "project_functional_dependencies",
    "synthesize_3nf",
    "synthesize_3nf_bernstein",
    "tableau_chase",
]
