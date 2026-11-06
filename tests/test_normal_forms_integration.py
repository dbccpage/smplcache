# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Milestone 2 verification suite for smplcache/normal_forms.py:
- FunctionalDependency, attribute_closure, minimal_cover, candidate_keys
- classify_normal_form (UNF / 1NF / 2NF / 3NF / E-KNF / BCNF)
- synthesize_3nf (Bernstein) and decompose_bcnf (Tsou-Fischer)
- tableau_chase lossless-join verification and spurious-tuple counterexample generation
- generate_sql_ddl topological ordering and foreign-key enforcement
- detect_fd_redundant_group_cols (Class D group-key reduction)
- SortedMultisetExtremumCache exact MIN/MAX maintenance and bounded window fallback
- Hypothesis property-based tests for closure axioms, minimal cover equivalence, and lossless decompositions
"""

from __future__ import annotations

from decimal import Decimal
import sqlite3
from typing import Any

from hypothesis import given, settings, strategies as st
import pytest

from smplcache import (
    DecisionKind,
    FunctionalDependency,
    Predicate,
    QueryShape,
    RelationalSchema,
    SortedMultisetExtremumCache,
    WriteEvent,
    attribute_closure,
    candidate_keys,
    classify_normal_form,
    decompose_bcnf,
    detect_fd_redundant_group_cols,
    generate_sql_ddl,
    minimal_cover,
    parse_sql,
    synthesize_3nf,
    tableau_chase,
)


def test_normal_form_classification_hierarchy() -> None:
    # 1. UNF (non-atomic list value in tuple)
    unf_schema = RelationalSchema(
        "orders_unf",
        ["order_id", "tags"],
        ["order_id -> tags"],
        tuples=[{"order_id": 1, "tags": ["urgent", "vip"]}],
    )
    rep_unf = classify_normal_form(unf_schema)
    assert rep_unf.highest_normal_form == "UNF"
    assert rep_unf.is_1nf is False

    # 2. 1NF (violates 2NF via partial dependency on composite key)
    schema_1nf = RelationalSchema(
        "order_lines",
        ["order_id", "item_id", "customer_id", "qty"],
        [
            "order_id, item_id -> customer_id, qty",
            "order_id -> customer_id",
        ],
    )
    rep_1nf = classify_normal_form(schema_1nf)
    assert rep_1nf.is_1nf is True
    assert rep_1nf.is_2nf is False
    assert rep_1nf.highest_normal_form == "1NF"

    # 3. 2NF (violates 3NF via transitive dependency order_id -> customer_id -> customer_tier)
    schema_2nf = RelationalSchema(
        "orders_denorm",
        ["order_id", "customer_id", "customer_tier", "amount"],
        [
            "order_id -> customer_id, amount",
            "customer_id -> customer_tier",
        ],
    )
    rep_2nf = classify_normal_form(schema_2nf)
    assert rep_2nf.is_1nf is True
    assert rep_2nf.is_2nf is True
    assert rep_2nf.is_3nf is False
    assert rep_2nf.highest_normal_form == "2NF"

    # 4. 3NF that violates BCNF: (student, course -> instructor; instructor -> course)
    schema_3nf = RelationalSchema(
        "enrollments",
        ["student", "course", "instructor"],
        [
            "student, course -> instructor",
            "instructor -> course",
        ],
    )
    rep_3nf = classify_normal_form(schema_3nf)
    assert rep_3nf.is_3nf is True
    assert rep_3nf.is_bcnf is False
    assert rep_3nf.highest_normal_form == "3NF"

    # 5. BCNF
    schema_bcnf = RelationalSchema(
        "customers",
        ["customer_id", "customer_tier", "region"],
        ["customer_id -> customer_tier, region"],
    )
    rep_bcnf = classify_normal_form(schema_bcnf)
    assert rep_bcnf.is_bcnf is True
    assert rep_bcnf.highest_normal_form == "BCNF"


def test_bernstein_3nf_and_tsou_fischer_bcnf_with_tableau_chase() -> None:
    schema = RelationalSchema(
        "orders",
        [
            "order_id",
            "customer_id",
            "customer_tier",
            "warehouse_id",
            "warehouse_region",
            "amount",
        ],
        [
            "order_id -> customer_id, warehouse_id, amount",
            "customer_id -> customer_tier",
            "warehouse_id -> warehouse_region",
        ],
    )

    rels_3nf = synthesize_3nf(schema)
    assert len(rels_3nf) == 3
    for r in rels_3nf:
        rep = classify_normal_form(r)
        assert rep.is_3nf is True

    chase_3nf = tableau_chase(schema, rels_3nf)
    assert chase_3nf.is_lossless is True
    assert chase_3nf.winning_row_index is not None
    assert chase_3nf.spurious_tuple_counterexample is None

    bcnf_res = decompose_bcnf(schema)
    assert bcnf_res.dependency_preserving is True
    for r in bcnf_res.relations:
        rep = classify_normal_form(r)
        assert rep.is_bcnf is True

    chase_bcnf = tableau_chase(schema, bcnf_res.relations)
    assert chase_bcnf.is_lossless is True


def test_lossy_decomposition_generates_spurious_tuple_counterexample() -> None:
    """
    Decomposing R(A, B, C) with FD A -> B into R1(A, C) and R2(B, C) is lossy.
    Verify that tableau_chase detects is_lossless == False and returns a concrete
    2-tuple counterexample whose projection-join synthesizes the spurious tuple.
    """
    schema = RelationalSchema("R", ["A", "B", "C"], ["A -> B"])
    chase = tableau_chase(schema, [["A", "C"], ["B", "C"]])
    assert chase.is_lossless is False
    assert chase.winning_row_index is None
    ce = chase.spurious_tuple_counterexample
    assert ce is not None
    orig_rows = ce["original_instance"]
    spurious = ce["spurious_joined_tuple"]
    assert len(orig_rows) == 2
    assert spurious not in orig_rows

    # Verify natural join of projections Pi_{A,C}(orig) |><| Pi_{B,C}(orig) contains `spurious`
    proj_ac = {(r["A"], r["C"]) for r in orig_rows}
    proj_bc = {(r["B"], r["C"]) for r in orig_rows}
    joined = [
        {"A": a, "B": b, "C": c1}
        for (a, c1) in proj_ac
        for (b, c2) in proj_bc
        if c1 == c2
    ]
    assert spurious in joined


def test_generate_sql_ddl_topological_order_and_sqlite_execution() -> None:
    schema = RelationalSchema(
        "orders",
        [
            "order_id",
            "customer_id",
            "customer_tier",
            "warehouse_id",
            "warehouse_region",
            "amount",
        ],
        [
            "order_id -> customer_id, warehouse_id, amount",
            "customer_id -> customer_tier",
            "warehouse_id -> warehouse_region",
        ],
    )
    bcnf_res = decompose_bcnf(schema)
    pg_ddl = generate_sql_ddl(bcnf_res.relations, dialect="postgres")
    ms_ddl = generate_sql_ddl(bcnf_res.relations, dialect="mssql")

    assert len(pg_ddl.foreign_keys) >= 2
    assert "NUMERIC(12, 2)" in pg_ddl.ddl_script
    assert "DECIMAL(12, 2)" in ms_ddl.ddl_script
    assert "CREATE INDEX" in pg_ddl.ddl_script

    # Execute generated DDL in SQLite with foreign keys enabled to verify
    # topological creation order (parents created before referencing children)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    for stmt in pg_ddl.statements:
        conn.execute(stmt)
    conn.close()


def test_detect_fd_redundant_group_cols() -> None:
    parsed = parse_sql(
        "SELECT customer_id, customer_tier, region, SUM(amount) "
        "FROM orders WHERE status = 'PAID' "
        "GROUP BY customer_id, customer_tier, region"
    )
    assert parsed.is_supported and parsed.shape is not None
    report = detect_fd_redundant_group_cols(
        parsed.shape,
        [
            "customer_id -> customer_tier",
            "customer_tier -> region",
        ],
    )
    assert report.original_group_cols == (
        "customer_id",
        "customer_tier",
        "region",
    )
    assert report.minimal_group_cols == ("customer_id",)
    assert set(report.redundant_group_cols) == {"customer_tier", "region"}
    assert report.reduced_shape is not None
    assert report.reduced_shape.group_cols == ("customer_id",)


def test_sorted_multiset_extremum_cache_min_max_and_window_exhaustion() -> None:
    shape = QueryShape(
        relation="orders",
        agg_fn="min",
        agg_col="amount",
        group_cols=("region",),
        predicate=Predicate.from_eq("status", "PAID"),
        name="min_amount_by_region",
    )
    initial_rows = [
        {"id": 1, "region": "us", "status": "PAID", "amount": Decimal("20.00")},
        {"id": 2, "region": "us", "status": "PAID", "amount": Decimal("10.00")},
        {"id": 3, "region": "us", "status": "PAID", "amount": Decimal("30.00")},
    ]
    # Bounded window of size 2 per group: retains [10.00, 20.00], truncates 30.00
    cache = SortedMultisetExtremumCache.from_rows(
        shape, initial_rows, agg_fn="min", max_window_per_group=2
    )
    assert cache.values["us"] == Decimal("10.00")

    # Delete current minimum (10.00): window still has 20.00 -> REPAIR (FIX IN PLACE)!
    d1 = cache.process(
        WriteEvent(
            relation="orders",
            old={
                "id": 2,
                "region": "us",
                "status": "PAID",
                "amount": Decimal("10.00"),
            },
            event_id="del_10",
        )
    )
    assert d1.kind == DecisionKind.REPAIR
    assert cache.values["us"] == Decimal("20.00")

    # Delete second minimum (20.00): retained window is now empty while 1 truncated row (30.00)
    # still exists in group "us" -> must safely latch to INVALIDATE ("extremum_window_exhausted")!
    d2 = cache.process(
        WriteEvent(
            relation="orders",
            old={
                "id": 1,
                "region": "us",
                "status": "PAID",
                "amount": Decimal("20.00"),
            },
            event_id="del_20",
        )
    )
    assert d2.kind == DecisionKind.INVALIDATE
    assert d2.reason == "extremum_window_exhausted"


_ATTR_POOL = ("a", "b", "c", "d", "e")


@st.composite
def _fd_set_strategy(
    draw: st.DrawFn,
) -> tuple[frozenset[str], list[FunctionalDependency]]:
    n_attrs = draw(st.integers(min_value=2, max_value=5))
    attrs = frozenset(_ATTR_POOL[:n_attrs])
    attr_list = sorted(attrs)
    n_fds = draw(st.integers(min_value=1, max_value=5))
    fds: list[FunctionalDependency] = []
    for _ in range(n_fds):
        lhs = draw(
            st.sets(
                st.sampled_from(attr_list), min_size=1, max_size=max(1, n_attrs - 1)
            )
        )
        rhs = draw(
            st.sets(
                st.sampled_from(attr_list), min_size=1, max_size=max(1, n_attrs - 1)
            )
        )
        fds.append(FunctionalDependency(lhs, rhs))
    return attrs, fds


@given(_fd_set_strategy())
@settings(max_examples=50, deadline=None)
def test_property_minimal_cover_equivalence_and_lossless_synthesis(
    data: tuple[frozenset[str], list[FunctionalDependency]],
) -> None:
    attrs, fds = data
    cover = minimal_cover(fds)

    # 1. Minimal cover preserves attribute closure for every subset of attributes
    from itertools import combinations

    attr_list = sorted(attrs)
    for r in range(1, len(attr_list) + 1):
        for comb in combinations(attr_list, r):
            assert attribute_closure(comb, fds) == attribute_closure(comb, cover)

    # 2. Bernstein 3NF synthesis is lossless and every synthesized table satisfies 3NF
    schema = RelationalSchema("prop_rel", attrs, fds)
    rels_3nf = synthesize_3nf(schema)
    assert tableau_chase(schema, rels_3nf).is_lossless is True
    for r_3nf in rels_3nf:
        assert classify_normal_form(r_3nf).is_3nf is True

    # 3. Tsou-Fischer BCNF decomposition is lossless and every decomposed table satisfies BCNF
    bcnf_decomp = decompose_bcnf(schema)
    assert tableau_chase(schema, bcnf_decomp.relations).is_lossless is True
    for r_bcnf in bcnf_decomp.relations:
        assert classify_normal_form(r_bcnf).is_bcnf is True
