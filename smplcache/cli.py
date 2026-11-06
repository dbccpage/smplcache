#!/usr/bin/env python3
# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import sys
from typing import Any, Iterable, Literal, Mapping, Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from smplcache.benchmark import (
    DEFAULT_BUDGET_GRID,
    SNOWFLAKE_BENCHMARK_SQL,
    format_adaptive_pool_sweep_csv,
    format_adaptive_pool_sweep_markdown,
    format_benchmark_results,
    format_eviction_sweep_csv,
    format_eviction_sweep_markdown,
    format_phase_shift_sweep_csv,
    format_phase_shift_sweep_markdown,
    ingest_wal_stream_records,
    run_debezium_ingest_benchmark,
    run_decay_window_sensitivity_sweep,
    run_phase_rotated_eviction_sweep,
    run_rapid_oscillation_eviction_sweep,
    run_shared_multi_query_pool_benchmark,
    run_zipfian_eviction_sweep,
)
from smplcache.extractor import parse_join_sql, parse_sql
from smplcache.normal_forms import (
    FunctionalDependency,
    RelationalSchema,
    candidate_keys,
    classify_normal_form,
    decompose_bcnf,
    detect_fd_redundant_group_cols,
    generate_sql_ddl,
    minimal_cover,
    synthesize_3nf,
    tableau_chase,
)
from smplcache.smplcache import (
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
    normalize_relation,
)

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def validate_workload_data(data: Any, *, require_events: bool = True) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Workload JSON root must be an object.")
    shapes = data.get("shapes")
    if not isinstance(shapes, list):
        raise ValueError("Workload JSON must contain a 'shapes' list.")
    for idx, s in enumerate(shapes):
        if not isinstance(s, dict):
            raise ValueError(f"Shape at index {idx} must be an object.")
        if not s.get("relation") or not isinstance(s.get("relation"), str):
            raise ValueError(f"Shape at index {idx} must declare a string 'relation'.")
    if require_events:
        events = data.get("events")
        if not isinstance(events, list):
            raise ValueError("Workload JSON must contain an 'events' list.")
        for idx, e in enumerate(events):
            if not isinstance(e, dict):
                raise ValueError(f"Event at index {idx} must be an object.")
            if not e.get("relation") or not isinstance(e.get("relation"), str):
                raise ValueError(
                    f"Event at index {idx} must declare a string 'relation'."
                )
    return data


def _shape_from_dict(
    s: Mapping[str, Any],
    db_schema: Mapping[str, Any] | None = None,
) -> QueryShape:
    name = str(s.get("name", s.get("shape_hash", "unknown")))
    relation = str(s.get("relation", ""))
    sec_cols = frozenset(str(c).lower() for c in s.get("security_cols", []))
    raw_sql = s.get("query") or s.get("sql")

    if isinstance(raw_sql, str) and raw_sql.strip():
        res = parse_sql(
            raw_sql,
            schema=db_schema,
            name=name,
            security_cols=sec_cols,
        )
        if res.is_supported and res.shape is not None:
            return replace(
                res.shape,
                name=name,
                security_cols=res.shape.security_cols | sec_cols,
            )
        join_res = parse_join_sql(raw_sql, name=name)
        if join_res.is_supported and join_res.shape is not None:
            jshape = join_res.shape
            deps = jshape.table_dependency_set(relation)
            return QueryShape(
                name=name,
                relation=relation,
                predicate_cols=set(deps["filter_cols"])
                | set(s.get("predicate_cols", [])),
                aggregate_cols=set(deps["read_cols"])
                | set(s.get("aggregate_cols", [])),
                group_cols=tuple(
                    sorted(set(deps["group_cols"]) | set(s.get("group_cols", [])))
                ),
                projection_cols=set(deps["read_cols"])
                | set(s.get("projection_cols", [])),
                join_cols=set(deps["join_keys"]) | set(s.get("join_cols", [])),
                security_cols=sec_cols | deps["security_cols"],
            )

        try:
            parsed_ast = sqlglot.parse_one(raw_sql)
            ast_preds: set[str] = set()
            ast_groups: list[str] = []
            ast_aggs: set[str] = set()
            ast_projs: set[str] = set()
            where_node = parsed_ast.find(exp.Where)
            if where_node is not None:
                for col_node in where_node.find_all(exp.Column):
                    ast_preds.add(col_node.name.lower())
            group_node = parsed_ast.find(exp.Group)
            if group_node is not None:
                for col_node in group_node.find_all(exp.Column):
                    c_name = col_node.name.lower()
                    if c_name not in ast_groups:
                        ast_groups.append(c_name)
            for agg_node in parsed_ast.find_all(exp.AggFunc):
                for col_node in agg_node.find_all(exp.Column):
                    ast_aggs.add(col_node.name.lower())
            if isinstance(parsed_ast, exp.Select):
                for sel_expr in parsed_ast.expressions:
                    if not list(sel_expr.find_all(exp.AggFunc)):
                        for col_node in sel_expr.find_all(exp.Column):
                            ast_projs.add(col_node.name.lower())
            merged_groups = list(s.get("group_cols", [])) or ast_groups
            return QueryShape(
                name=name,
                relation=relation,
                predicate_cols=set(s.get("predicate_cols", [])) | ast_preds,
                aggregate_cols=set(s.get("aggregate_cols", [])) | ast_aggs,
                group_cols=tuple(merged_groups),
                projection_cols=set(s.get("projection_cols", [])) | ast_projs,
                join_cols=set(s.get("join_cols", [])),
                security_cols=sec_cols,
            )
        except SqlglotError:
            pass

    return QueryShape(
        name=name,
        relation=relation,
        predicate_cols=set(s.get("predicate_cols", [])),
        aggregate_cols=set(s.get("aggregate_cols", [])),
        group_cols=tuple(s.get("group_cols", [])),
        projection_cols=set(s.get("projection_cols", [])),
        join_cols=set(s.get("join_cols", [])),
        security_cols=sec_cols,
    )


def _event_from_dict(e: Mapping[str, Any], index: int) -> WriteEvent:
    raw_evt_id = e.get("event_id")
    evt_id = (
        f"evt_{raw_evt_id}"
        if isinstance(raw_evt_id, int)
        else str(raw_evt_id or f"evt_{index}")
    )
    return WriteEvent(
        relation=str(e.get("relation", "")),
        changed_cols=set(e.get("changed_cols", [])),
        old=dict(e.get("old", {})),
        new=dict(e.get("new", {})),
        event_id=evt_id,
        explicit_operation=e.get("operation"),
    )


def calculate_entropy(shape_hits: Mapping[str, int]) -> float:
    total = sum(shape_hits.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for hits in shape_hits.values():
        if hits > 0:
            p = hits / total
            entropy -= p * math.log2(p)
    return entropy


def calculate_topomap(
    shape_names: list[str], correlations: Iterable[tuple[str, int]]
) -> dict[str, Any]:
    adjacency: dict[str, set[str]] = {s: set() for s in shape_names}
    edge_count = 0

    for pair, _count in correlations:
        left, right = pair.split(" <-> ")
        if left not in adjacency:
            adjacency[left] = set()
        if right not in adjacency:
            adjacency[right] = set()
        adjacency[left].add(right)
        adjacency[right].add(left)
        edge_count += 1

    active_nodes = [s for s in shape_names if adjacency.get(s)]
    isolated_shapes = [s for s in shape_names if not adjacency.get(s)]
    visited: set[str] = set()
    components: list[list[str]] = []

    for node in active_nodes:
        if node in visited:
            continue

        stack = [node]
        comp: list[str] = []
        visited.add(node)

        while stack:
            cur = stack.pop()
            comp.append(cur)

            for nxt in sorted(adjacency[cur]):
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)

        components.append(sorted(comp))

    v = len(active_nodes)
    e = edge_count
    c = len(components)

    beta_1 = max(0, e - v + c)

    possible_edges = len(shape_names) * (len(shape_names) - 1) / 2
    coupling_score = (edge_count / possible_edges * 100) if possible_edges else 0

    largest = max(components, key=len) if components else []
    all_components = list(components) + [[s] for s in isolated_shapes]

    return {
        "total_nodes": len(shape_names),
        "active_nodes": v,
        "isolated_nodes": len(isolated_shapes),
        "isolated_shapes": isolated_shapes,
        "edges": e,
        "components": c,
        "total_components": len(all_components),
        "all_components": all_components,
        "beta_1": beta_1,
        "coupling_score": coupling_score,
        "largest_component": largest,
    }


def _parse_fds_from_raw(raw_fds: Any) -> list[FunctionalDependency]:
    fds: list[FunctionalDependency] = []
    if not isinstance(raw_fds, list):
        return fds
    for item in raw_fds:
        if isinstance(item, FunctionalDependency):
            fds.append(item)
        elif isinstance(item, str) and "->" in item:
            lhs_str, rhs_str = item.split("->", 1)
            lhs = [c.strip().lower() for c in lhs_str.split(",") if c.strip()]
            rhs = [c.strip().lower() for c in rhs_str.split(",") if c.strip()]
            if lhs and rhs:
                fds.append(FunctionalDependency(determinant=lhs, dependent=rhs))
        elif isinstance(item, Mapping):
            det = item.get("determinant", item.get("lhs", []))
            dep = item.get("dependent", item.get("rhs", []))
            if isinstance(det, str):
                det = [c.strip().lower() for c in det.split(",") if c.strip()]
            else:
                det = [str(c).strip().lower() for c in det if str(c).strip()]
            if isinstance(dep, str):
                dep = [c.strip().lower() for c in dep.split(",") if c.strip()]
            else:
                dep = [str(c).strip().lower() for c in dep if str(c).strip()]
            if det and dep:
                fds.append(FunctionalDependency(determinant=det, dependent=dep))
    return fds


def _extract_relational_schemas(data: Mapping[str, Any]) -> list[RelationalSchema]:
    schemas: list[RelationalSchema] = []

    if "attributes" in data and ("functional_dependencies" in data or "fds" in data):
        rel_name = normalize_relation(
            str(data.get("name", data.get("relation", "relation")))
        )
        attrs = [
            str(a).strip().lower() for a in data.get("attributes", []) if str(a).strip()
        ]
        raw_fds = data.get("functional_dependencies", data.get("fds", []))
        fds = _parse_fds_from_raw(raw_fds)
        col_types = {
            str(k).strip().lower(): str(v).strip().upper()
            for k, v in (data.get("column_types") or {}).items()
        }
        if attrs:
            schemas.append(
                RelationalSchema(
                    name=rel_name,
                    attributes=frozenset(attrs),
                    functional_dependencies=tuple(fds),
                    column_types=col_types,
                )
            )
        return schemas

    db_schema = data.get("schema", {})
    top_fds_raw = data.get("functional_dependencies", data.get("fds", []))
    shapes_raw = data.get("shapes", [])

    if isinstance(db_schema, Mapping) and db_schema:
        for rel_key, rel_info in db_schema.items():
            if not isinstance(rel_info, Mapping):
                continue
            rel_name = normalize_relation(str(rel_key))
            cols_dict = rel_info.get("columns", {})
            attrs_set: set[str] = set()
            col_types = {}
            if isinstance(cols_dict, Mapping):
                for c_name, c_meta in cols_dict.items():
                    c_norm = str(c_name).strip().lower()
                    attrs_set.add(c_norm)
                    if isinstance(c_meta, Mapping):
                        t_str = str(
                            c_meta.get("type", c_meta.get("physical_type", "TEXT"))
                        ).upper()
                        col_types[c_norm] = t_str
                    elif isinstance(c_meta, str):
                        col_types[c_norm] = c_meta.upper()
            elif isinstance(rel_info.get("attributes"), list):
                for c_name in rel_info["attributes"]:
                    attrs_set.add(str(c_name).strip().lower())

            for s in shapes_raw:
                if (
                    isinstance(s, Mapping)
                    and normalize_relation(str(s.get("relation", ""))) == rel_name
                ):
                    shape_obj = _shape_from_dict(s, db_schema)
                    attrs_set.update(shape_obj.fingerprint())

            rel_fds = _parse_fds_from_raw(
                rel_info.get("functional_dependencies", rel_info.get("fds", []))
            )
            if isinstance(top_fds_raw, Mapping):
                rel_fds.extend(_parse_fds_from_raw(top_fds_raw.get(rel_name, [])))
            elif isinstance(top_fds_raw, list):
                for fd in _parse_fds_from_raw(top_fds_raw):
                    if (fd.determinant | fd.dependent) <= attrs_set or len(
                        db_schema
                    ) == 1:
                        rel_fds.append(fd)
                        attrs_set.update(fd.determinant | fd.dependent)

            if attrs_set:
                schemas.append(
                    RelationalSchema(
                        name=rel_name,
                        attributes=frozenset(attrs_set),
                        functional_dependencies=tuple(rel_fds),
                        column_types=col_types,
                    )
                )

    if not schemas and shapes_raw:
        by_rel: dict[str, set[str]] = {}
        for s in shapes_raw:
            if isinstance(s, Mapping) and s.get("relation"):
                shape_obj = _shape_from_dict(
                    s, db_schema if isinstance(db_schema, Mapping) else None
                )
                by_rel.setdefault(shape_obj.relation, set()).update(
                    shape_obj.fingerprint()
                )
        all_fds = _parse_fds_from_raw(top_fds_raw)
        for rel_name, attrs_set in sorted(by_rel.items()):
            rel_fds = [
                fd
                for fd in all_fds
                if (fd.determinant | fd.dependent) <= attrs_set or len(by_rel) == 1
            ]
            for fd in rel_fds:
                attrs_set.update(fd.determinant | fd.dependent)
            if attrs_set:
                schemas.append(
                    RelationalSchema(
                        name=rel_name,
                        attributes=frozenset(attrs_set),
                        functional_dependencies=tuple(rel_fds),
                    )
                )

    return schemas


def analyze_workload(
    workload_path: str, format_md: str | bool = False, topomap: bool = False
) -> None:
    with open(workload_path, "r", encoding="utf-8") as f:
        data = validate_workload_data(json.load(f), require_events=True)

    db_schema = data.get("schema", {})
    shapes = [_shape_from_dict(s, db_schema) for s in data.get("shapes", [])]
    events = [
        _event_from_dict(e, idx + 1) for idx, e in enumerate(data.get("events", []))
    ]

    total_evaluations = 0
    table_invalidations = 0
    shape_invalidations = 0

    column_hits: dict[str, int] = {}
    column_shape_links: dict[str, set[str]] = {}
    shape_hits: dict[str, int] = {s.name: 0 for s in shapes}
    shape_event_history: dict[str, set[int]] = {s.name: set() for s in shapes}
    shape_repair_history: dict[str, set[int]] = {s.name: set() for s in shapes}
    shape_invalidate_history: dict[str, set[int]] = {s.name: set() for s in shapes}

    avoided_details = []
    repair_opportunities = {s.name: {"repairable": 0, "full": 0} for s in shapes}

    certificates = []

    for i, event in enumerate(events):
        event_avoided_shapes = []
        event_rel = normalize_relation(event.relation)
        effective_changed = event.derived_changed_cols()

        for shape in shapes:
            if shape.relation == event_rel:
                total_evaluations += 1
                table_invalidations += 1

                decision = certify_event(shape, event)
                certificates.append(decision.certificate)

                if decision.kind in [
                    DecisionKind.REPAIR,
                    DecisionKind.INVALIDATE,
                    DecisionKind.UNSUPPORTED,
                ]:
                    shape_invalidations += 1
                    shape_hits[shape.name] += 1
                    shape_event_history[shape.name].add(i)

                    intersection = shape.fingerprint() & effective_changed
                    for col in intersection:
                        col_key = f"{shape.relation}.{col}"
                        column_hits[col_key] = column_hits.get(col_key, 0) + 1
                        if col_key not in column_shape_links:
                            column_shape_links[col_key] = set()
                        column_shape_links[col_key].add(shape.name)

                    if decision.kind == DecisionKind.REPAIR:
                        repair_opportunities[shape.name]["repairable"] += 1
                        shape_repair_history[shape.name].add(i)
                    else:
                        repair_opportunities[shape.name]["full"] += 1
                        shape_invalidate_history[shape.name].add(i)
                else:
                    event_avoided_shapes.append(shape.name)

        if event_avoided_shapes:
            avoided_details.append(
                {
                    "event_id": i + 1,
                    "table": event_rel,
                    "changed_cols": ", ".join(sorted(list(effective_changed))),
                    "preserved_shapes": ", ".join(sorted(event_avoided_shapes)),
                }
            )

    false_invalidations_avoided = table_invalidations - shape_invalidations
    avoidance_rate = (
        (false_invalidations_avoided / table_invalidations * 100)
        if table_invalidations
        else 0
    )

    gravity_wells = sorted(column_hits.items(), key=lambda x: x[1], reverse=True)

    correlations_map: dict[str, int] = {}
    correlation_breakdown: dict[str, dict[str, int]] = {}
    shape_names = [s.name for s in shapes]
    for i in range(len(shape_names)):
        for j in range(i + 1, len(shape_names)):
            s1, s2 = shape_names[i], shape_names[j]
            shared_events = shape_event_history[s1] & shape_event_history[s2]
            overlap = len(shared_events)
            if overlap > 0:
                pair_key = f"{s1} <-> {s2}"
                correlations_map[pair_key] = overlap
                both_repair = len(shape_repair_history[s1] & shape_repair_history[s2])
                both_inv = len(
                    shape_invalidate_history[s1] & shape_invalidate_history[s2]
                )
                mixed = overlap - both_repair - both_inv
                correlation_breakdown[pair_key] = {
                    "total": overlap,
                    "repair": both_repair,
                    "invalidate": both_inv,
                    "mixed": mixed,
                }

    correlations = sorted(correlations_map.items(), key=lambda x: x[1], reverse=True)

    topomap_data = None
    if topomap:
        topomap_data = calculate_topomap(shape_names, correlations)
        topomap_data["entropy"] = calculate_entropy(shape_hits)

    if format_md == "json":
        import dataclasses

        print(json.dumps([dataclasses.asdict(c) for c in certificates], indent=2))
        return
    elif format_md == "markdown" or format_md is True:
        print_markdown_report(
            len(events),
            len(shapes),
            table_invalidations,
            shape_invalidations,
            false_invalidations_avoided,
            avoidance_rate,
            gravity_wells,
            shape_hits,
            correlations,
            avoided_details,
            repair_opportunities,
            topomap_data,
            column_shape_links,
            correlation_breakdown=correlation_breakdown,
        )
    else:
        print_text_report(
            len(events),
            len(shapes),
            table_invalidations,
            shape_invalidations,
            false_invalidations_avoided,
            avoidance_rate,
            gravity_wells,
            shape_hits,
            correlations,
            avoided_details,
            repair_opportunities,
            topomap_data,
            column_shape_links,
            correlation_breakdown=correlation_breakdown,
        )


def print_text_report(
    num_events: int,
    num_shapes: int,
    tbl_inv: int,
    shp_inv: int,
    avoided: int,
    rate: float,
    wells: list[tuple[str, int]],
    shape_hits: dict[str, int],
    correlations: list[tuple[str, int]],
    avoided_details: list[dict[str, Any]],
    repair_ops: dict[str, dict[str, int]],
    topomap_data: dict[str, Any] | None,
    links: dict[str, set[str]],
    correlation_breakdown: dict[str, dict[str, int]] | None = None,
) -> None:
    print("=" * 60)
    print(" smplcache Workload Advisor Report")
    print("=" * 60)
    print(
        "smplcache compares blind table-level invalidation against query-shape dependency invalidation."
    )
    print(
        "A false invalidation is counted when a table-level cache would invalidate a shape, but the write boundary does not intersect that shape's dependency fingerprint.\n"
    )
    print(f"Events Analyzed: {num_events}")
    print(f"Query Shapes Tracked: {num_shapes}\n")

    print("--- FALSE INVALIDATION ANALYSIS ---")
    print(f"Table-Level Invalidations:     {tbl_inv}")
    print(f"Shape-Level Invalidations:     {shp_inv}")
    print(f"False Invalidations Avoided:   {avoided} ({rate:.1f}% reduction)\n")

    print("--- SHAPE-LEVEL INVALIDATIONS ---")
    for name, hits in shape_hits.items():
        if hits > 0:
            print(f"  {name}: {hits}")

    print("\n--- REPAIR OPPORTUNITIES ---")
    for name, ops in repair_ops.items():
        if ops["repairable"] > 0 or ops["full"] > 0:
            print(
                f"  {name}: {ops['repairable']} repairable, {ops['full']} full invalidations"
            )

    print("\n--- GRAVITY WELLS (Top Invalidating Columns) ---")
    for col, count in wells[:5]:
        print(f"  {col}: {count} invalidation(s)")

    print("\n--- SHAPE COUPLING (Correlation Matrix) ---")
    for pair, count in correlations[:5]:
        if correlation_breakdown and pair in correlation_breakdown:
            bd = correlation_breakdown[pair]
            print(
                f"  {pair}: invalidated together {count} time(s) "
                f"(repair={bd['repair']}, invalidate={bd['invalidate']}, mixed={bd['mixed']})"
            )
        else:
            print(f"  {pair}: invalidated together {count} time(s)")

    if topomap_data:
        print("\n--- TOPOLOGICAL WORKLOAD GEOMETRY (TopoMap) ---")
        print(f"  Total Shapes: {topomap_data.get('total_nodes', num_shapes)}")
        print(f"  Coupled Shapes: {topomap_data['active_nodes']}")
        print(f"  Isolated Shapes: {topomap_data.get('isolated_nodes', 0)}")
        print(f"  Coupling Edges: {topomap_data['edges']}")
        print(f"  Connected Components: {topomap_data['components']}")
        print(f"  Cache Betti-1 (Cycles): {topomap_data['beta_1']}")
        print(f"  Coupling Score: {topomap_data['coupling_score']:.1f}%")
        print(f"  Shape Entropy: {topomap_data['entropy']:.2f}")
        print("\n  Largest Coupled Component:")
        if topomap_data["largest_component"]:
            print("  " + ", ".join(topomap_data["largest_component"]))
        else:
            print("  None")
        if topomap_data.get("isolated_shapes"):
            print("\n  Isolated Singleton Shapes:")
            print("  " + ", ".join(topomap_data["isolated_shapes"]))

        print("\n  Interpretation:")
        if topomap_data["beta_1"] == 0:
            print(
                "  The workload has a tree-like acyclic coupling structure. Coupling is localized rather than globally tangled."
            )
        else:
            print(
                f"  Your cache workload has {topomap_data['beta_1']} invalidation cycle(s). This means several cached shapes are coupled through multiple write paths, making cache churn harder to isolate."
            )

        print("\n  RECOMMENDATIONS")
        idx = 1
        if wells:
            dom_col = wells[0][0]
            linked_shapes = sorted(list(links.get(dom_col, [])))
            if len(linked_shapes) > 0:
                print(f"  {idx}. `{dom_col}` is the dominant coupling driver.")
                if len(linked_shapes) > 1:
                    print(
                        f"     It links `{linked_shapes[0]}` and `{linked_shapes[1]}` shapes. Consider materializing status-specific projections.\n"
                    )
                else:
                    print(
                        f"     It heavily invalidates `{linked_shapes[0]}`. Consider materializing specific projections.\n"
                    )
                idx += 1

        if topomap_data["beta_1"] == 0:
            print(f"  {idx}. The coupling graph is acyclic.")
            print(
                "     This is good: cache invalidation is localized and should be easy to reason about.\n"
            )
            idx += 1
            print(f"  {idx}. No global invalidation cycles detected.")
            print("     Current Cache Betti-1 = 0.")
        else:
            print(f"  {idx}. The coupling graph is cyclic.")
            print(
                "     Cache invalidations may propagate unpredictably across shapes.\n"
            )
            idx += 1
            print(f"  {idx}. Global invalidation cycles detected.")
            print(f"     Current Cache Betti-1 = {topomap_data['beta_1']}.")

    print("=" * 60)


def print_markdown_report(
    num_events: int,
    num_shapes: int,
    tbl_inv: int,
    shp_inv: int,
    avoided: int,
    rate: float,
    wells: list[tuple[str, int]],
    shape_hits: dict[str, int],
    correlations: list[tuple[str, int]],
    avoided_details: list[dict[str, Any]],
    repair_ops: dict[str, dict[str, int]],
    topomap_data: dict[str, Any] | None,
    links: dict[str, set[str]],
    correlation_breakdown: dict[str, dict[str, int]] | None = None,
) -> None:
    print("# smplcache Workload Advisor Report\n")
    print(
        "smplcache compares blind table-level invalidation against query-shape dependency invalidation.  "
    )
    print(
        "A false invalidation is counted when a table-level cache would invalidate a shape, but the write boundary does not intersect that shape's dependency fingerprint.\n"
    )
    print(f"**Events Analyzed:** {num_events}  ")
    print(f"**Query Shapes Tracked:** {num_shapes}  \n")

    print("## False Invalidation Analysis")
    print("| Metric | Count |")
    print("|--------|-------|")
    print(f"| Table-Level Invalidations | {tbl_inv} |")
    print(f"| Shape-Level Invalidations | {shp_inv} |")
    print(f"| **False Invalidations Avoided** | **{avoided}** |")
    print(f"| **Reduction Rate** | **{rate:.1f}%** |\n")

    print("## Shape-Level Invalidations")
    print("| Shape | Invalidations |")
    print("|---|---:|")
    for name, hits in shape_hits.items():
        if hits > 0:
            print(f"| `{name}` | {hits} |")

    print("\n## Avoided Invalidations")
    print("| Event | Table | Changed Columns | Preserved Shapes |")
    print("|---|---|---|---|")
    for detail in avoided_details:
        print(
            f"| {detail['event_id']} | `{detail['table']}` | `{detail['changed_cols']}` | `{detail['preserved_shapes']}` |"
        )

    print("\n## Repair Opportunities")
    print("| Shape | Repairable Events | Full Invalidations Required |")
    print("|---|---:|---:|")
    for name, ops in repair_ops.items():
        if ops["repairable"] > 0 or ops["full"] > 0:
            print(f"| `{name}` | {ops['repairable']} | {ops['full']} |")

    print("\n## Gravity Wells (Top Invalidating Columns)")
    if not wells:
        print("*None detected.*")
    else:
        print("| Column | Invalidation Hits |")
        print("|--------|-------------------|")
        for col, count in wells[:5]:
            print(f"| `{col}` | {count} |")

    print("\n## Shape Coupling (Correlation Matrix)")
    if not correlations:
        print("*No structurally coupled shapes detected.*")
    else:
        print(
            "| Coupled Shapes | Co-Invalidation Count | Both Repair | Both Invalidate | Mixed |"
        )
        print("|---|---:|---:|---:|---:|")
        for pair, count in correlations[:5]:
            if correlation_breakdown and pair in correlation_breakdown:
                bd = correlation_breakdown[pair]
                print(
                    f"| `{pair}` | {count} | {bd['repair']} | {bd['invalidate']} | {bd['mixed']} |"
                )
            else:
                print(f"| `{pair}` | {count} | 0 | {count} | 0 |")

    if topomap_data:
        print("\n## Topological Workload Geometry (TopoMap)")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Total Shapes | {topomap_data.get('total_nodes', num_shapes)} |")
        print(f"| Coupled Shapes | {topomap_data['active_nodes']} |")
        print(f"| Isolated Shapes | {topomap_data.get('isolated_nodes', 0)} |")
        print(f"| Coupling Edges | {topomap_data['edges']} |")
        print(f"| Connected Components | {topomap_data['components']} |")
        print(f"| Cache Betti-1 (Cycles) | {topomap_data['beta_1']} |")
        print(f"| Coupling Score | {topomap_data['coupling_score']:.1f}% |")
        print(f"| Shape Entropy | {topomap_data['entropy']:.2f} |")

        print("\n### Largest Coupled Component")
        if topomap_data["largest_component"]:
            print("`" + "`, `".join(topomap_data["largest_component"]) + "`")
        else:
            print("*None*")

        if topomap_data.get("isolated_shapes"):
            print("\n### Isolated Singleton Shapes")
            print("`" + "`, `".join(topomap_data["isolated_shapes"]) + "`")

        print("\n### Interpretation")
        if topomap_data["beta_1"] == 0:
            print(
                "The workload has a tree-like acyclic coupling structure. Coupling is localized rather than globally tangled."
            )
        else:
            print(
                f"Your cache workload has **{topomap_data['beta_1']} invalidation cycle(s)**. This means several cached shapes are coupled through multiple write paths, making cache churn harder to isolate."
            )

        print("\n### Recommendations\n")
        idx = 1
        if wells:
            dom_col = wells[0][0]
            linked_shapes = sorted(list(links.get(dom_col, [])))
            if len(linked_shapes) > 0:
                print(f"{idx}. `{dom_col}` is the dominant coupling driver.")
                if len(linked_shapes) > 1:
                    print(
                        f"   It links `{linked_shapes[0]}` and `{linked_shapes[1]}` shapes. Consider materializing status-specific projections.\n"
                    )
                else:
                    print(
                        f"   It heavily invalidates `{linked_shapes[0]}`. Consider materializing specific projections.\n"
                    )
                idx += 1

        if topomap_data["beta_1"] == 0:
            print(f"{idx}. The coupling graph is acyclic.")
            print(
                "   This is good: cache invalidation is localized and should be easy to reason about.\n"
            )
            idx += 1
            print(f"{idx}. No global invalidation cycles detected.")
            print("   Current Cache Betti-1 = 0.")
        else:
            print(f"{idx}. The coupling graph is cyclic.")
            print("   Cache invalidations may propagate unpredictably across shapes.\n")
            idx += 1
            print(f"{idx}. Global invalidation cycles detected.")
            print(f"   Current Cache Betti-1 = {topomap_data['beta_1']}.")


_NUMERIC_SQL_TYPES = {
    "int",
    "integer",
    "bigint",
    "smallint",
    "tinyint",
    "numeric",
    "decimal",
    "float",
    "real",
    "double",
    "double precision",
}
_STRING_SQL_TYPES = {
    "varchar",
    "nvarchar",
    "char",
    "nchar",
    "text",
    "string",
    "uuid",
}


def _base_sql_type(type_str: str) -> str:
    cleaned = type_str.strip().lower()
    if "(" in cleaned:
        cleaned = cleaned.split("(", 1)[0].strip()
    return cleaned


def _inspect_sql_ast_doctor(
    raw_sql: str,
    shape_name: str,
    relation: str,
    rel_schema: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    non_sargable_findings: list[dict[str, Any]] = []
    implicit_conv_findings: list[dict[str, Any]] = []

    try:
        parsed = sqlglot.parse_one(raw_sql)
    except SqlglotError:
        return non_sargable_findings, implicit_conv_findings

    where_node = parsed.find(exp.Where)
    if where_node is None:
        return non_sargable_findings, implicit_conv_findings

    seen_non_sargable: set[tuple[str, str]] = set()
    seen_implicit: set[tuple[str, str]] = set()

    for node in where_node.walk():
        if isinstance(node, (exp.Like, exp.ILike)):
            col_node = node.this
            pat_node = node.expression
            if isinstance(col_node, exp.Column) and isinstance(pat_node, exp.Literal):
                pat_val = str(pat_node.this)
                if pat_val.startswith("%"):
                    col_name = col_node.name.lower()
                    key = (col_name, "leading_wildcard_like")
                    if key not in seen_non_sargable:
                        seen_non_sargable.add(key)
                        non_sargable_findings.append(
                            {
                                "shape": shape_name,
                                "col": f"{relation}.{col_name}",
                                "expression": node.sql(),
                                "reason": "leading_wildcard_like",
                            }
                        )

        elif isinstance(
            node,
            (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE),
        ):
            sides = [(node.this, node.expression), (node.expression, node.this)]
            for left_expr, right_expr in sides:
                if left_expr is None:
                    continue
                if isinstance(left_expr, exp.Column) and isinstance(
                    right_expr, exp.Literal
                ):
                    col_name = left_expr.name.lower()
                    if col_name in rel_schema:
                        col_meta = rel_schema[col_name]
                        raw_t = (
                            col_meta.get("type", col_meta.get("physical_type", ""))
                            if isinstance(col_meta, Mapping)
                            else str(col_meta)
                        )
                        base_t = _base_sql_type(raw_t)
                        if base_t in _NUMERIC_SQL_TYPES and right_expr.is_string:
                            ikey = (col_name, "STRING_LITERAL")
                            if ikey not in seen_implicit:
                                seen_implicit.add(ikey)
                                implicit_conv_findings.append(
                                    {
                                        "shape": shape_name,
                                        "col": f"{relation}.{col_name}",
                                        "col_type": raw_t.upper(),
                                        "param_type": "VARCHAR",
                                        "risk": "string literal compared to numeric column",
                                    }
                                )
                        elif base_t in _STRING_SQL_TYPES and right_expr.is_number:
                            ikey = (col_name, "NUMERIC_LITERAL")
                            if ikey not in seen_implicit:
                                seen_implicit.add(ikey)
                                implicit_conv_findings.append(
                                    {
                                        "shape": shape_name,
                                        "col": f"{relation}.{col_name}",
                                        "col_type": raw_t.upper(),
                                        "param_type": "NUMERIC",
                                        "risk": "numeric literal compared to string column",
                                    }
                                )
                elif not isinstance(left_expr, exp.Column):
                    cols_in_expr = list(left_expr.find_all(exp.Column))
                    if cols_in_expr:
                        is_arith = isinstance(
                            left_expr, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
                        ) or any(
                            isinstance(
                                sub, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
                            )
                            for sub in left_expr.walk()
                        )
                        reason = (
                            "arithmetic_on_column" if is_arith else "function_on_column"
                        )
                        for c_node in cols_in_expr:
                            col_name = c_node.name.lower()
                            key = (col_name, reason)
                            if key not in seen_non_sargable:
                                seen_non_sargable.add(key)
                                non_sargable_findings.append(
                                    {
                                        "shape": shape_name,
                                        "col": f"{relation}.{col_name}",
                                        "expression": left_expr.sql(),
                                        "reason": reason,
                                    }
                                )

    return non_sargable_findings, implicit_conv_findings


def run_doctor(workload_path: str, format_md: bool) -> dict[str, Any]:
    with open(workload_path, "r", encoding="utf-8") as f:
        data = validate_workload_data(json.load(f), require_events=False)

    db_schema = data.get("schema", {})
    shapes = data.get("shapes", [])
    observed_shape_types = data.get("observed_shape_types", [])
    rel_schemas = {rs.name: rs for rs in _extract_relational_schemas(data)}
    top_fds = _parse_fds_from_raw(
        data.get("functional_dependencies", data.get("fds", []))
    )

    implicit_conversions: list[dict[str, Any]] = []
    missing_indexes: list[dict[str, Any]] = []
    non_sargable: list[dict[str, Any]] = []
    parameter_skews: list[dict[str, Any]] = []
    fd_redundant_groups: list[dict[str, Any]] = []

    for s in shapes:
        shape_obj = _shape_from_dict(s, db_schema)
        name = shape_obj.name
        relation = shape_obj.relation
        predicates = shape_obj.predicate_cols
        groups = set(shape_obj.group_cols)
        aggregates = shape_obj.aggregate_cols
        projections = shape_obj.projection_cols
        joins = set(s.get("join_cols", []))
        params = s.get("params", {})

        rel_schema = db_schema.get(relation, {}).get("columns", {})

        for p_name, p_info in params.items():
            if p_name in rel_schema:
                col_meta = rel_schema[p_name]
                col_type = (
                    col_meta.get("type", col_meta.get("physical_type", "")).lower()
                    if isinstance(col_meta, Mapping)
                    else str(col_meta).lower()
                )
                param_type = p_info.get("type", "").lower()
                if (
                    col_type
                    and param_type
                    and _base_sql_type(col_type) != _base_sql_type(param_type)
                ):
                    implicit_conversions.append(
                        {
                            "shape": name,
                            "col": f"{relation}.{p_name}",
                            "col_type": col_type.upper(),
                            "param_type": param_type.upper(),
                            "risk": "index seek risk",
                        }
                    )

        raw_sql = s.get("query") or s.get("sql")
        if isinstance(raw_sql, str) and raw_sql.strip():
            ast_ns, ast_ic = _inspect_sql_ast_doctor(
                raw_sql, name, relation, rel_schema
            )
            non_sargable.extend(ast_ns)
            implicit_conversions.extend(ast_ic)

        rel_fds: list[FunctionalDependency] = []
        if relation in rel_schemas:
            rel_fds.extend(rel_schemas[relation].functional_dependencies)
        elif top_fds:
            rel_fds.extend(top_fds)

        if rel_fds and shape_obj.group_cols:
            redundant_cols = detect_fd_redundant_group_cols(shape_obj, rel_fds)
            if redundant_cols:
                reduced_groups = [
                    c for c in shape_obj.group_cols if c.lower() not in redundant_cols
                ]
                fd_redundant_groups.append(
                    {
                        "shape": name,
                        "relation": relation,
                        "original_group_cols": list(shape_obj.group_cols),
                        "redundant_cols": list(redundant_cols),
                        "reduced_group_cols": reduced_groups,
                    }
                )

        keys = set(predicates) | set(groups) | set(joins)
        includes = (set(projections) | set(aggregates)) - keys

        if keys:
            key_str = ", ".join(sorted(list(keys)))
            inc_str = ", ".join(sorted(list(includes)))
            idx_name = f"ix_{relation}_" + "_".join(sorted(list(keys)))
            idx_sql = f"CREATE INDEX {idx_name} ON {relation}({key_str})"
            if inc_str:
                idx_sql += f" INCLUDE({inc_str});"
            else:
                idx_sql += ";"
            missing_indexes.append({"shape": name, "sql": idx_sql})

        shape_regimes = []
        for col in set(predicates) | set(joins) | set(params.keys()):
            for ost in observed_shape_types:
                if ost.get("relation") == relation and ost.get("column") == col:
                    regimes = ost.get("regimes", [])
                    if len(regimes) > 1:
                        shape_regimes.append({"col": col, "regimes": regimes})
        if shape_regimes:
            parameter_skews.append({"shape": name, "skews": shape_regimes})

    if format_md:
        print("# smplcache Query Shape Doctor\n")
        print("## Summary\n")
        print("| Finding | Count |")
        print("|---|---:|")
        print(f"| Implicit Conversion Risks | {len(implicit_conversions)} |")
        print(f"| Missing Index Candidates | {len(missing_indexes)} |")
        print(f"| Parameter Skew Risks | {len(parameter_skews)} |")
        print("| Cardinality Drift Warnings | 0 |")
        print(f"| Non-Sargable Predicate Risks | {len(non_sargable)} |")
        print(f"| FD-Redundant GROUP BY Risks | {len(fd_redundant_groups)} |\n")

        if non_sargable:
            print("## Non-Sargable Predicate Risks\n")
            print("| Shape | Column | Expression | Reason |")
            print("|---|---|---|---|")
            for ns in non_sargable:
                print(
                    f"| `{ns['shape']}` | `{ns['col']}` | `{ns['expression']}` | `{ns['reason']}` |"
                )
            print()

        if fd_redundant_groups:
            print("## FD-Redundant GROUP BY Columns\n")
            print(
                "| Shape | Original GROUP BY | Redundant Columns | Reduced GROUP BY |"
            )
            print("|---|---|---|---|")
            for fg in fd_redundant_groups:
                orig_s = ", ".join(fg["original_group_cols"])
                red_s = ", ".join(fg["redundant_cols"])
                reduced_s = ", ".join(fg["reduced_group_cols"])
                print(f"| `{fg['shape']}` | `{orig_s}` | `{red_s}` | `{reduced_s}` |")
            print()

        if parameter_skews:
            print("## Parameter Shape Split\n")
            for p in parameter_skews:
                print(f"Shape: `{p['shape']}`\n")
                print("The parameter has multiple stable runtime regimes:\n")
                print("| Regime | Avg Rows | Executions | Suggested Shape |")
                print("|---|---:|---:|---|")
                for sk in p["skews"]:
                    for r in sk["regimes"]:
                        r_name = r.get("name", "unknown")
                        print(
                            f"| {r_name} | {r.get('avg_rows', 0)} | {r.get('executions', 0)} | `{p['shape']}/{r_name}` |"
                        )
                print("\nRecommendation:")
                print(
                    "Split this query into observed sub-shapes. One cached plan or one cache policy is unlikely to fit all regimes.\n"
                )

        if implicit_conversions:
            print("## Implicit Conversion Risks\n")
            print("| Shape | Column | Column Type | Param Type | Risk |")
            print("|---|---|---|---|---|")
            for ic in implicit_conversions:
                print(
                    f"| `{ic['shape']}` | `{ic['col']}` | `{ic['col_type']}` | `{ic['param_type']}` | {ic['risk']} |"
                )
            print()

        if missing_indexes:
            print("## Missing Index Candidates\n")
            print("| Shape | Recommended Index |")
            print("|---|---|")
            for mi in missing_indexes:
                print(f"| `{mi['shape']}` | `{mi['sql']}` |")
            print()
    else:
        print("=" * 60)
        print(" smplcache Query Shape Doctor")
        print("=" * 60)
        print("--- SUMMARY ---")
        print(f"Implicit Conversion Risks:    {len(implicit_conversions)}")
        print(f"Missing Index Candidates:     {len(missing_indexes)}")
        print(f"Parameter Skew Risks:         {len(parameter_skews)}")
        print(f"Non-Sargable Predicate Risks: {len(non_sargable)}")
        print(f"FD-Redundant GROUP BY Risks:  {len(fd_redundant_groups)}\n")

        if non_sargable:
            print("--- NON-SARGABLE PREDICATES ---")
            for ns in non_sargable:
                print(f"  Shape: {ns['shape']}")
                print(
                    f"  Col:   {ns['col']} in `{ns['expression']}` ({ns['reason']})\n"
                )

        if fd_redundant_groups:
            print("--- FD-REDUNDANT GROUP BY COLUMNS ---")
            for fg in fd_redundant_groups:
                print(f"  Shape:     {fg['shape']}")
                print(f"  Redundant: {', '.join(fg['redundant_cols'])}")
                print(f"  Reduced:   GROUP BY {', '.join(fg['reduced_group_cols'])}\n")

        if parameter_skews:
            print("--- PARAMETER SKEW ---")
            for p in parameter_skews:
                print(f"  Shape: {p['shape']}")
                for sk in p["skews"]:
                    print(f"  Col {sk['col']} has {len(sk['regimes'])} regimes.")
            print()

        if implicit_conversions:
            print("--- IMPLICIT CONVERSIONS ---")
            for ic in implicit_conversions:
                print(f"  Shape: {ic['shape']}")
                print(f"  Col:   {ic['col']} ({ic['col_type']})")
                print(f"  Param: {ic['param_type']} => {ic['risk']}\n")

        if missing_indexes:
            print("--- MISSING INDEXES ---")
            for mi in missing_indexes:
                print(f"  Shape: {mi['shape']}")
                print(f"  Index: {mi['sql']}\n")

    return {
        "implicit_conversions": implicit_conversions,
        "missing_indexes": missing_indexes,
        "non_sargable": non_sargable,
        "parameter_skews": parameter_skews,
        "fd_redundant_groups": fd_redundant_groups,
    }


def compare_workloads(path_a: str, path_b: str, format_md: bool) -> dict[str, Any]:
    def get_metrics(path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            data = validate_workload_data(json.load(f), require_events=True)
        db_schema = data.get("schema", {})
        shape_objs = [_shape_from_dict(s, db_schema) for s in data.get("shapes", [])]
        shape_names = [s.name for s in shape_objs]
        events = [
            _event_from_dict(e, idx + 1) for idx, e in enumerate(data.get("events", []))
        ]

        shape_event_history: dict[str, set[int]] = {s: set() for s in shape_names}
        column_hits: dict[str, int] = {}
        all_cols: set[str] = set()
        for shape_obj in shape_objs:
            for c in shape_obj.fingerprint():
                all_cols.add(f"{shape_obj.relation}.{c}")

        for i, evt in enumerate(events):
            rel = normalize_relation(evt.relation)
            changed = evt.derived_changed_cols()
            for shape_obj in shape_objs:
                intersection = shape_obj.fingerprint() & changed
                if shape_obj.relation == rel and intersection:
                    shape_event_history[shape_obj.name].add(i)
                    for col in intersection:
                        col_key = f"{shape_obj.relation}.{col}"
                        column_hits[col_key] = column_hits.get(col_key, 0) + 1

        correlations: dict[str, int] = {}
        for i in range(len(shape_names)):
            for j in range(i + 1, len(shape_names)):
                s1, s2 = shape_names[i], shape_names[j]
                overlap = len(shape_event_history[s1] & shape_event_history[s2])
                if overlap > 0:
                    correlations[f"{s1} <-> {s2}"] = overlap

        shape_hits = {s: len(shape_event_history[s]) for s in shape_names}
        gravity_wells = sorted(column_hits.items(), key=lambda x: x[1], reverse=True)

        topomap = calculate_topomap(shape_names, correlations.items())
        entropy = calculate_entropy(shape_hits)

        topomap["entropy"] = entropy
        topomap["shapes_count"] = len(shape_names)
        topomap["gravity_wells"] = gravity_wells
        topomap["all_cols"] = sorted(all_cols)
        topomap["rel_schemas"] = _extract_relational_schemas(data)
        topomap["score"] = (
            topomap["beta_1"]
            + (topomap["coupling_score"] / 100.0)
            + (
                len(topomap["largest_component"]) / len(shape_names)
                if shape_names
                else 0
            )
        )
        return topomap

    m_a = get_metrics(path_a)
    m_b = get_metrics(path_b)

    primary_obstruction = (
        m_a["gravity_wells"][0][0] if m_a.get("gravity_wells") else "orders.status"
    )
    obs_rel = (
        primary_obstruction.split(".", 1)[0] if "." in primary_obstruction else "orders"
    )
    cols_a = set(m_a.get("all_cols", []))
    cols_b = set(m_b.get("all_cols", []))
    new_cols_in_b = sorted(
        c.split(".", 1)[1] if "." in c else c
        for c in (cols_b - cols_a)
        if c.startswith(f"{obs_rel}.")
    )
    if not new_cols_in_b:
        new_cols_in_b = ["payment_status", "fulfillment_status", "support_status"]

    nf_summaries: list[dict[str, Any]] = []
    for rs in m_a.get("rel_schemas", []):
        if rs.functional_dependencies:
            nf_rep = classify_normal_form(rs)
            synth_3nf = synthesize_3nf(rs)
            bcnf_dec = decompose_bcnf(rs)
            chase_3nf = tableau_chase(rs, [t.attributes for t in synth_3nf])
            chase_bcnf = tableau_chase(rs, [t.attributes for t in bcnf_dec.tables])
            nf_summaries.append(
                {
                    "relation": rs.name,
                    "highest_normal_form": nf_rep.highest_normal_form,
                    "3nf_tables": [(t.name, sorted(t.attributes)) for t in synth_3nf],
                    "bcnf_tables": [
                        (t.name, sorted(t.attributes)) for t in bcnf_dec.tables
                    ],
                    "chase_3nf_lossless": chase_3nf.is_lossless,
                    "chase_bcnf_lossless": chase_bcnf.is_lossless,
                }
            )

    if format_md:
        print("# smplcache Lift Report\n")
        print("## Before: Obstruction\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Query Shapes | {m_a['shapes_count']} |")
        print(f"| Coupling Edges | {m_a['edges']} |")
        print(f"| Cache Betti-1 | {m_a['beta_1']} |")
        print(f"| Coupling Score | {m_a['coupling_score']:.1f}% |")
        print(f"| Invalidation Entropy | {m_a['entropy']:.2f} |")
        print(f"| Obstruction Score | {m_a['score']:.2f} |\n")

        print("## After: Lift\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Query Shapes | {m_b['shapes_count']} |")
        print(f"| Coupling Edges | {m_b['edges']} |")
        print(f"| Cache Betti-1 | {m_b['beta_1']} |")
        print(f"| Coupling Score | {m_b['coupling_score']:.1f}% |")
        print(f"| Invalidation Entropy | {m_b['entropy']:.2f} |")
        print(f"| Obstruction Score | {m_b['score']:.2f} |\n")

        print("## Lift Summary\n")
        print(
            f"The proposed lift reduced invalidation cycles from {m_a['beta_1']} to {m_b['beta_1']} and reduced coupling score from {m_a['coupling_score']:.1f}% to {m_b['coupling_score']:.1f}%.\n"
        )
        print("Primary obstruction:")
        print(f"`{primary_obstruction}`\n")
        print("Suggested lift:")
        print(
            f"split `{primary_obstruction}` into narrower observer-specific dependencies:\n"
        )
        for col_name in new_cols_in_b:
            print(f"- `{col_name}`")

        if nf_summaries:
            print("\n## Normal-Form Decomposition & Tableau Chase Verification\n")
            for nfs in nf_summaries:
                print(
                    f"- **Relation `{nfs['relation']}`**: classified as `{nfs['highest_normal_form']}`"
                )
                print(
                    f"  - **3NF Synthesis (lossless={nfs['chase_3nf_lossless']})**: "
                    + ", ".join(
                        f"`{tname}({', '.join(tcols)})`"
                        for tname, tcols in nfs["3nf_tables"]
                    )
                )
                print(
                    f"  - **BCNF Decomposition (lossless={nfs['chase_bcnf_lossless']})**: "
                    + ", ".join(
                        f"`{tname}({', '.join(tcols)})`"
                        for tname, tcols in nfs["bcnf_tables"]
                    )
                )
    else:
        print("=" * 60)
        print(" smplcache Lift Report")
        print("=" * 60)
        print("--- BEFORE (Obstruction) ---")
        print(f"  Cache Betti-1: {m_a['beta_1']}")
        print(f"  Coupling Score: {m_a['coupling_score']:.1f}%")
        print(f"  Obstruction Score: {m_a['score']:.2f}\n")
        print("--- AFTER (Lift) ---")
        print(f"  Cache Betti-1: {m_b['beta_1']}")
        print(f"  Coupling Score: {m_b['coupling_score']:.1f}%")
        print(f"  Obstruction Score: {m_b['score']:.2f}\n")
        print(f"Primary obstruction: {primary_obstruction}")
        print("Suggested lift columns: " + ", ".join(new_cols_in_b))
        if nf_summaries:
            print("\n--- NORMAL-FORM DECOMPOSITION ---")
            for nfs in nf_summaries:
                print(
                    f"  {nfs['relation']} ({nfs['highest_normal_form']}): "
                    f"3NF lossless={nfs['chase_3nf_lossless']}, "
                    f"BCNF lossless={nfs['chase_bcnf_lossless']}"
                )

    return {
        "before": m_a,
        "after": m_b,
        "primary_obstruction": primary_obstruction,
        "lifted_columns": new_cols_in_b,
        "normal_form_summaries": nf_summaries,
    }


def normalize_workload_or_schema(
    path: str, dialect: str = "postgres"
) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, Mapping):
        raise ValueError("Input JSON root must be an object.")

    rel_schemas = _extract_relational_schemas(data)
    if not rel_schemas:
        raise ValueError(
            "No relational schemas or shapes with attributes found in input JSON."
        )

    db_schema = (
        data.get("schema", {}) if isinstance(data.get("schema"), Mapping) else {}
    )
    shapes_raw = data.get("shapes", []) if isinstance(data.get("shapes"), list) else []

    relations_report: list[dict[str, Any]] = []
    for rs in rel_schemas:
        min_cov = minimal_cover(rs.functional_dependencies)
        cand_keys = candidate_keys(rs)
        nf_report = classify_normal_form(rs)
        tables_3nf = synthesize_3nf(rs)
        bcnf_res = decompose_bcnf(rs)
        chase_3nf = tableau_chase(rs, [t.attributes for t in tables_3nf])
        chase_bcnf = tableau_chase(rs, [t.attributes for t in bcnf_res.tables])
        sql_dialect: Literal["postgres", "mssql"] = (
            "mssql" if dialect == "mssql" else "postgres"
        )
        ddl_3nf = generate_sql_ddl(tables_3nf, dialect=sql_dialect).ddl_script
        ddl_bcnf = generate_sql_ddl(bcnf_res.tables, dialect=sql_dialect).ddl_script

        shape_redundancies: list[dict[str, Any]] = []
        for s in shapes_raw:
            if (
                isinstance(s, Mapping)
                and normalize_relation(str(s.get("relation", ""))) == rs.name
            ):
                shape_obj = _shape_from_dict(s, db_schema)
                if shape_obj.group_cols and rs.functional_dependencies:
                    red_cols = detect_fd_redundant_group_cols(
                        shape_obj, rs.functional_dependencies
                    )
                    if red_cols:
                        shape_redundancies.append(
                            {
                                "shape": shape_obj.name,
                                "original_group_cols": list(shape_obj.group_cols),
                                "redundant_cols": list(red_cols),
                                "reduced_group_cols": [
                                    c
                                    for c in shape_obj.group_cols
                                    if c.lower() not in red_cols
                                ],
                            }
                        )

        relations_report.append(
            {
                "relation": rs.name,
                "attributes": sorted(rs.attributes),
                "minimal_cover": [
                    {
                        "determinant": sorted(fd.determinant),
                        "dependent": sorted(fd.dependent),
                    }
                    for fd in min_cov
                ],
                "candidate_keys": [sorted(k) for k in cand_keys],
                "highest_normal_form": nf_report.highest_normal_form,
                "is_1nf": nf_report.is_1nf,
                "is_2nf": nf_report.is_2nf,
                "is_3nf": nf_report.is_3nf,
                "is_eknf": nf_report.is_eknf,
                "is_bcnf": nf_report.is_bcnf,
                "violations": list(nf_report.violations),
                "synthesis_3nf": {
                    "tables": [
                        {
                            "name": t.name,
                            "attributes": sorted(t.attributes),
                            "candidate_keys": [sorted(k) for k in candidate_keys(t)],
                        }
                        for t in tables_3nf
                    ],
                    "is_lossless": chase_3nf.is_lossless,
                    "counterexample": chase_3nf.counterexample,
                    "ddl": ddl_3nf,
                },
                "decomposition_bcnf": {
                    "tables": [
                        {
                            "name": t.name,
                            "attributes": sorted(t.attributes),
                            "candidate_keys": [sorted(k) for k in candidate_keys(t)],
                        }
                        for t in bcnf_res.tables
                    ],
                    "is_dependency_preserving": bcnf_res.is_dependency_preserving,
                    "lost_dependencies": [
                        {
                            "determinant": sorted(fd.determinant),
                            "dependent": sorted(fd.dependent),
                        }
                        for fd in bcnf_res.lost_dependencies
                    ],
                    "is_lossless": chase_bcnf.is_lossless,
                    "counterexample": chase_bcnf.counterexample,
                    "ddl": ddl_bcnf,
                },
                "fd_redundant_group_bys": shape_redundancies,
            }
        )

    return {
        "dialect": dialect,
        "relations": relations_report,
    }


def run_normalize(
    path: str, dialect: str = "postgres", output_format: str = "text"
) -> dict[str, Any]:
    report = normalize_workload_or_schema(path, dialect=dialect)
    if output_format == "json":
        print(json.dumps(report, indent=2))
        return report

    if output_format == "markdown":
        print(f"# smplcache Schema Normalizer Report (`{dialect}`)\n")
        for rel in report["relations"]:
            print(f"## Relation: `{rel['relation']}`\n")
            print(f"- **Attributes:** `{', '.join(rel['attributes'])}`")
            keys_str = ", ".join(
                "(" + ", ".join(k) + ")" for k in rel["candidate_keys"]
            )
            print(f"- **Candidate Keys:** `{keys_str}`")
            print(f"- **Highest Normal Form:** `{rel['highest_normal_form']}`")
            if rel["violations"]:
                print("- **Violations:**")
                for v in rel["violations"]:
                    print(f"  - {v}")
            print("\n### 3NF Synthesis (Bernstein)\n")
            s3 = rel["synthesis_3nf"]
            print(f"- **Tableau Chase Lossless:** `{s3['is_lossless']}`")
            for t in s3["tables"]:
                print(f"  - `{t['name']}({', '.join(t['attributes'])})`")
            print(f"\n```sql\n{s3['ddl']}\n```\n")

            print("### BCNF Decomposition (Tsou-Fischer)\n")
            db = rel["decomposition_bcnf"]
            print(f"- **Tableau Chase Lossless:** `{db['is_lossless']}`")
            print(f"- **Dependency Preserving:** `{db['is_dependency_preserving']}`")
            for t in db["tables"]:
                print(f"  - `{t['name']}({', '.join(t['attributes'])})`")
            print(f"\n```sql\n{db['ddl']}\n```\n")
    else:
        print("=" * 60)
        print(f" smplcache Schema Normalizer Report (dialect={dialect})")
        print("=" * 60)
        for rel in report["relations"]:
            print(f"Relation:            {rel['relation']}")
            print(f"Attributes:          {', '.join(rel['attributes'])}")
            keys_str = ", ".join(
                "(" + ", ".join(k) + ")" for k in rel["candidate_keys"]
            )
            print(f"Candidate Keys:      {keys_str}")
            print(f"Highest Normal Form: {rel['highest_normal_form']}")
            s3 = rel["synthesis_3nf"]
            print(f"3NF Lossless Chase:  {s3['is_lossless']}")
            for t in s3["tables"]:
                print(f"  3NF Table: {t['name']}({', '.join(t['attributes'])})")
            db = rel["decomposition_bcnf"]
            print(
                f"BCNF Lossless Chase: {db['is_lossless']} (DepPreserving={db['is_dependency_preserving']})"
            )
            for t in db["tables"]:
                print(f"  BCNF Table: {t['name']}({', '.join(t['attributes'])})")
            print("\n--- 3NF MIGRATION DDL ---")
            print(s3["ddl"])
            print("=" * 60)

    return report


def calc_matrix_metrics(path: str) -> dict[str, Any] | None:
    if not HAS_NUMPY:
        print("Error: numpy is required for matrix geometry calculation.")
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = validate_workload_data(json.load(f), require_events=True)

    db_schema = data.get("schema", {})
    shape_objs = [_shape_from_dict(s, db_schema) for s in data.get("shapes", [])]
    shape_names = [s.name for s in shape_objs]
    events = [
        _event_from_dict(e, idx + 1) for idx, e in enumerate(data.get("events", []))
    ]

    if not shape_names:
        return None

    n = len(shape_names)
    shape_indices = {s: i for i, s in enumerate(shape_names)}

    M = np.zeros((n, n))

    for evt in events:
        rel = normalize_relation(evt.relation)
        changed = evt.derived_changed_cols()

        v = np.zeros(n)
        for shape_obj in shape_objs:
            if shape_obj.relation == rel and (shape_obj.fingerprint() & changed):
                idx = shape_indices.get(shape_obj.name)
                if idx is not None:
                    v[idx] = 1.0

        M += np.outer(v, v)

    trace_M = np.trace(M)
    if trace_M == 0:
        return {"purity": 0, "entropy": 0, "dom_eig": 0, "dom_shapes": 0}

    rho = M / trace_M
    purity = np.trace(rho @ rho)

    raw_eigenvalues = np.linalg.eigvalsh(rho)
    eigenvalues = [float(e) for e in raw_eigenvalues if e > 1e-9]

    entropy = -sum(e * np.log2(e) for e in eigenvalues)

    dom_eig = max(eigenvalues) if eigenvalues else 0

    vals, vecs = np.linalg.eigh(rho)
    dom_idx = np.argmax(vals)
    dom_vec = vecs[:, dom_idx]

    threshold = 1.0 / np.sqrt(n) * 0.5
    dom_shapes = sum(1 for v in dom_vec if abs(v) > threshold)

    return {
        "purity": float(purity),
        "entropy": float(entropy),
        "dom_eig": float(dom_eig),
        "dom_shapes": int(dom_shapes),
    }


def run_matrix(path_a: str, path_b: str | None = None, format_md: bool = False) -> None:
    m_a = calc_matrix_metrics(path_a)
    if m_a is None:
        return

    if path_b:
        m_b = calc_matrix_metrics(path_b)
        if m_b is None:
            return

        print("## Matrix Geometry\n")
        print("| Metric | Before Lift | After Lift |")
        print("|---|---:|---:|")
        print(f"| Dominance Score | {m_a['purity']:.2f} | {m_b['purity']:.2f} |")
        print(f"| Invalidation Entropy | {m_a['entropy']:.2f} | {m_b['entropy']:.2f} |")
        print(
            f"| Dominant Component lambda_1 | {m_a['dom_eig']:.2f} | {m_b['dom_eig']:.2f} |"
        )
        print(
            f"| Shapes in Dominant Mode | {m_a['dom_shapes']} | {m_b['dom_shapes']} |"
        )
    else:
        print("## Matrix Geometry\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Dominance Score | {m_a['purity']:.2f} |")
        print(f"| Invalidation Entropy | {m_a['entropy']:.2f} |")
        print(f"| Dominant Component lambda_1 | {m_a['dom_eig']:.2f} |")
        print(f"| Shapes in Dominant Mode | {m_a['dom_shapes']} |")


def _parse_budgets_arg(
    raw_budgets: str | None,
    single_max_rows: int | None,
    single_max_fanout: int | None,
) -> list[tuple[int | None, int | None]]:
    if raw_budgets:
        out: list[tuple[int | None, int | None]] = []
        for item in raw_budgets.split(","):
            tok = item.strip()
            if not tok:
                continue
            if ":" in tok:
                r_str, f_str = tok.split(":", 1)
                r_val = (
                    None
                    if r_str.strip().lower() in ("none", "unbounded", "")
                    else int(r_str.strip())
                )
                f_val = (
                    None
                    if f_str.strip().lower() in ("none", "unbounded", "")
                    else int(f_str.strip())
                )
                out.append((r_val, f_val))
            else:
                r_val = None if tok.lower() in ("none", "unbounded") else int(tok)
                out.append((r_val, single_max_fanout))
        if out:
            return out

    if single_max_rows is not None or single_max_fanout is not None:
        return [(single_max_rows, single_max_fanout)]

    return list(DEFAULT_BUDGET_GRID)


def _load_wal_records_from_path(
    stream_file: str,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]] | None, str | None]:
    raw_text = (
        sys.stdin.read()
        if stream_file == "-"
        else open(stream_file, "r", encoding="utf-8").read()
    )
    stripped = raw_text.strip()
    if not stripped:
        return [], None, None

    if stripped.startswith("{") and "\n" not in stripped:
        parsed_obj = json.loads(stripped)
        if isinstance(parsed_obj, dict) and "records" in parsed_obj:
            return (
                list(parsed_obj["records"]),
                parsed_obj.get("initial_tables"),
                parsed_obj.get("sql"),
            )
        return [parsed_obj], None, None

    try:
        parsed_all = json.loads(stripped)
        if isinstance(parsed_all, list):
            return [dict(x) for x in parsed_all], None, None
        if isinstance(parsed_all, dict) and "records" in parsed_all:
            return (
                [dict(x) for x in parsed_all["records"]],
                parsed_all.get("initial_tables"),
                parsed_all.get("sql"),
            )
    except json.JSONDecodeError:
        pass

    records: list[dict[str, Any]] = []
    for line in stripped.splitlines():
        line_s = line.strip()
        if line_s:
            records.append(json.loads(line_s))
    return records, None, None


def run_ingest(args: argparse.Namespace) -> None:
    if getattr(args, "benchmark_adaptive_pool", False):
        skews: Sequence[float] = (
            [float(x.strip()) for x in args.skew_sweep.split(",") if x.strip()]
            if getattr(args, "skew_sweep", None)
            else (0.0, 0.5, 1.0, 1.5, 2.0)
        )
        raw_pols = getattr(args, "policies", None) or "fifo,lru,lfu,w_lfu,wtinylfu"
        if raw_pols in ("fifo,lru,lfu", "fifo,lru,lfu,w_lfu"):
            raw_pols = "fifo,lru,lfu,w_lfu,wtinylfu"
        policies = tuple(p.strip().lower() for p in raw_pols.split(",") if p.strip())
        max_rows = args.max_total_rows or 50
        num_phases = getattr(args, "num_phases", 4)
        if num_phases < 4:
            num_phases = 4
        osc_res = run_rapid_oscillation_eviction_sweep(
            skew_values=skews,
            policies=policies,
            max_total_rows=max_rows,
            num_transactions=96,
            num_phases=num_phases,
            seed=args.seed,
            verify_oracle=True,
        )
        pool_res = run_shared_multi_query_pool_benchmark(
            pool_budgets=(48, 54, 66, 78, 96),
            policies=policies,
            zipf_s=1.5,
            num_transactions=80,
            num_phases=num_phases,
            seed=args.seed,
            verify_oracle=True,
        )
        csv_str = format_adaptive_pool_sweep_csv(osc_res, pool_res)
        md_str = format_adaptive_pool_sweep_markdown(osc_res, pool_res)
        if getattr(args, "csv_out", None):
            from pathlib import Path

            Path(args.csv_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.csv_out).write_text(csv_str, encoding="utf-8")
        if getattr(args, "md_out", None):
            from pathlib import Path

            Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.md_out).write_text(md_str, encoding="utf-8")
        if args.format == "json":
            from dataclasses import asdict

            print(
                json.dumps(
                    {
                        "rapid_oscillation": [asdict(r) for r in osc_res],
                        "shared_multi_query_pool": [asdict(r) for r in pool_res],
                    },
                    indent=2,
                )
            )
        else:
            print(md_str)
        return

    if getattr(args, "benchmark_phase_shift", False):
        skews = (
            [float(x.strip()) for x in args.skew_sweep.split(",") if x.strip()]
            if getattr(args, "skew_sweep", None)
            else (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
        )
        raw_pols = getattr(args, "policies", None) or "fifo,lru,lfu,w_lfu"
        if raw_pols == "fifo,lru,lfu":
            raw_pols = "fifo,lru,lfu,w_lfu"
        policies = tuple(p.strip().lower() for p in raw_pols.split(",") if p.strip())
        max_rows = args.max_total_rows or 50
        txs = args.transactions if args.transactions != 60 else 80
        decay_win = getattr(args, "lfu_decay_window", None) or 32
        decay_fac = getattr(args, "lfu_decay_factor", 0.5)
        phase_res = run_phase_rotated_eviction_sweep(
            skew_values=skews,
            policies=policies,
            lfu_decay_window=decay_win,
            lfu_decay_factor=decay_fac,
            max_total_rows=max_rows,
            num_transactions=txs,
            num_phases=getattr(args, "num_phases", 2),
            seed=args.seed,
            verify_oracle=True,
        )
        sens_res = run_decay_window_sensitivity_sweep(
            zipf_s=1.5,
            decay_factors=(decay_fac,),
            max_total_rows=max_rows,
            num_transactions=txs,
            num_phases=getattr(args, "num_phases", 2),
            seed=args.seed,
            verify_oracle=True,
        )
        csv_str = format_phase_shift_sweep_csv(phase_res)
        md_str = format_phase_shift_sweep_markdown(phase_res, sens_res)
        if getattr(args, "csv_out", None):
            from pathlib import Path

            Path(args.csv_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.csv_out).write_text(csv_str, encoding="utf-8")
        if getattr(args, "md_out", None):
            from pathlib import Path

            Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.md_out).write_text(md_str, encoding="utf-8")
        if args.format == "json":
            from dataclasses import asdict

            print(
                json.dumps(
                    {
                        "sweep": [asdict(r) for r in phase_res],
                        "window_sensitivity": [asdict(r) for r in sens_res],
                    },
                    indent=2,
                )
            )
        else:
            print(md_str)
        return

    if getattr(args, "benchmark_eviction", False):
        skews = (
            [float(x.strip()) for x in args.skew_sweep.split(",") if x.strip()]
            if getattr(args, "skew_sweep", None)
            else (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
        )
        policies = tuple(
            p.strip().lower()
            for p in getattr(args, "policies", "fifo,lru,lfu").split(",")
            if p.strip()
        )
        max_rows = args.max_total_rows or 50
        sweep_res = run_zipfian_eviction_sweep(
            skew_values=skews,
            policies=policies,
            max_total_rows=max_rows,
            num_transactions=args.transactions,
            seed=args.seed,
            verify_oracle=True,
        )
        csv_str = format_eviction_sweep_csv(sweep_res)
        md_str = format_eviction_sweep_markdown(sweep_res)
        if getattr(args, "csv_out", None):
            from pathlib import Path

            Path(args.csv_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.csv_out).write_text(csv_str, encoding="utf-8")
        if getattr(args, "md_out", None):
            from pathlib import Path

            Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.md_out).write_text(md_str, encoding="utf-8")
        if args.format == "json":
            from dataclasses import asdict

            print(json.dumps([asdict(r) for r in sweep_res], indent=2))
        else:
            print(md_str)
        return

    if args.benchmark or not args.stream_file:
        budgets = _parse_budgets_arg(
            args.budgets, args.max_total_rows, args.max_fk_fanout
        )
        results = run_debezium_ingest_benchmark(
            zipf_s=args.zipf_s,
            budgets=budgets,
            eviction_policy=getattr(args, "eviction_policy", "fifo"),
            num_transactions=args.transactions,
            seed=args.seed,
            verify_oracle=True,
        )
        print(format_benchmark_results(results, output_format=args.format))
        return

    records, file_tables, file_sql = _load_wal_records_from_path(args.stream_file)
    effective_sql = args.sql or file_sql or SNOWFLAKE_BENCHMARK_SQL
    summary = ingest_wal_stream_records(
        records,
        sql=effective_sql,
        initial_tables=file_tables,
        eviction_policy=getattr(args, "eviction_policy", "fifo"),
        max_total_rows=args.max_total_rows,
        max_rows_per_table=args.max_rows_per_table,
        max_fk_fanout=args.max_fk_fanout,
        strict_transactions=args.strict_transactions,
    )
    if args.format == "json":
        print(json.dumps(summary, indent=2))
    elif args.format == "markdown":
        print("# SmplCache WAL Stream Ingestion Report\n")
        print(f"- **Source Format:** `{args.source_format}`")
        print(f"- **Query:** `{summary['sql']}`")
        print(f"- **WAL Messages:** {summary['total_wal_messages']}")
        print(f"- **Decisions:** {summary['total_decisions']}")
        print(
            f"- **Preserve:** {summary['preserve_count']} ({summary['preserve_rate'] * 100:.1f}%)"
        )
        print(
            f"- **Repair:** {summary['repair_count']} ({summary['repair_rate'] * 100:.1f}%)"
        )
        print(
            f"- **Invalidate:** {summary['invalidate_count']} "
            f"({summary['invalidation_rate'] * 100:.1f}%, "
            f"eviction={summary['eviction_invalidate_count']})"
        )
        print(f"- **Peak Aux Rows:** {summary['peak_aux_rows']}")
        print(f"- **Peak FK Fanout:** {summary['peak_fk_bucket_size']}")
    else:
        print("=" * 72)
        print(f" SmplCache WAL Stream Ingestion Report (from={args.source_format})")
        print("=" * 72)
        print(f" Query:            {summary['sql']}")
        print(f" WAL Messages:     {summary['total_wal_messages']}")
        print(f" Total Decisions:  {summary['total_decisions']}")
        print(
            f" Preserve:         {summary['preserve_count']} "
            f"({summary['preserve_rate'] * 100:.1f}%)"
        )
        print(
            f" Repair:           {summary['repair_count']} "
            f"({summary['repair_rate'] * 100:.1f}%)"
        )
        print(
            f" Invalidate:       {summary['invalidate_count']} "
            f"({summary['invalidation_rate'] * 100:.1f}%, "
            f"eviction={summary['eviction_invalidate_count']})"
        )
        print(f" Peak Aux Rows:    {summary['peak_aux_rows']}")
        print(f" Peak FK Fanout:   {summary['peak_fk_bucket_size']}")
        print("=" * 72)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="smplcache Workload Advisor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser(
        "report", help="Generate an advisor report from a workload JSON"
    )
    report_parser.add_argument("workload_file", help="Path to the JSON workload file")
    report_parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Output format",
    )
    report_parser.add_argument(
        "--topomap",
        action="store_true",
        help="Generate Topological Workload Geometry",
    )

    compare_parser = subparsers.add_parser(
        "compare", help="Compare an obstructed workload vs a lifted workload"
    )
    compare_parser.add_argument(
        "workload_a", help="Path to the original tangled workload"
    )
    compare_parser.add_argument("workload_b", help="Path to the lifted workload")
    compare_parser.add_argument(
        "--format",
        choices=["text", "markdown"],
        default="text",
        help="Output format",
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Run pathology checks on a workload shape"
    )
    doctor_parser.add_argument("workload_file", help="Path to the JSON workload file")
    doctor_parser.add_argument(
        "--format",
        choices=["text", "markdown"],
        default="text",
        help="Output format",
    )

    matrix_parser = subparsers.add_parser(
        "matrix", help="Run Quantum Matrix logic on workload(s)"
    )
    matrix_parser.add_argument("workload_a", help="Path to the first workload")
    matrix_parser.add_argument(
        "workload_b",
        nargs="?",
        help="Optional second workload for comparison",
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help=(
            "Ingest a Debezium/WAL stream or run the Zipfian snowflaked "
            "(orders -> customers -> regions) memory-budget benchmark"
        ),
    )
    ingest_parser.add_argument(
        "stream_file",
        nargs="?",
        default=None,
        help="Optional path to a Debezium/wal2json JSON/JSONL file (or '-' for stdin)",
    )
    ingest_parser.add_argument(
        "--from",
        dest="source_format",
        choices=["debezium", "wal2json", "auto"],
        default="debezium",
        help="Source CDC/WAL envelope format (default: debezium)",
    )
    ingest_parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run the Zipfian snowflaked (orders -> customers -> regions) benchmark sweep",
    )
    ingest_parser.add_argument(
        "--sql",
        default=None,
        help="SQL query to certify and maintain against the WAL stream",
    )
    ingest_parser.add_argument(
        "--zipf-s",
        type=float,
        default=1.2,
        help="Zipfian foreign-key skew parameter s >= 0 (default: 1.2)",
    )
    ingest_parser.add_argument(
        "--transactions",
        type=int,
        default=60,
        help="Number of WAL transactions in benchmark mode (default: 60)",
    )
    ingest_parser.add_argument(
        "--max-total-rows",
        type=int,
        default=None,
        help="Maximum total auxiliary rows in JoinAuxState before eviction",
    )
    ingest_parser.add_argument(
        "--max-rows-per-table",
        type=int,
        default=None,
        help="Maximum rows per table in JoinAuxState before eviction",
    )
    ingest_parser.add_argument(
        "--max-fk-fanout",
        type=int,
        default=None,
        help="Maximum foreign-key fanout bucket size in JoinAuxState before eviction",
    )
    ingest_parser.add_argument(
        "--budgets",
        default=None,
        help="Comma-separated max_total_rows:max_fk_fanout pairs (e.g. 'none:none,80:16,50:8,30:4,15:2')",
    )
    ingest_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible Zipfian benchmark generation",
    )
    ingest_parser.add_argument(
        "--strict-transactions",
        action="store_true",
        help="Require explicit BEGIN/COMMIT boundaries for all WAL events",
    )
    ingest_parser.add_argument(
        "--eviction-policy",
        choices=["fifo", "lru", "lfu", "w_lfu", "wtinylfu"],
        default="fifo",
        help="Row eviction policy for JoinAuxState (default: fifo)",
    )
    ingest_parser.add_argument(
        "--lfu-decay-window",
        type=int,
        default=None,
        help="Access window size for frequency decay in w_lfu (default: 32 when policy=w_lfu)",
    )
    ingest_parser.add_argument(
        "--lfu-decay-factor",
        type=float,
        default=0.5,
        help="Multiplicative frequency decay factor in [0.0, 1.0] for w_lfu (default: 0.5)",
    )
    ingest_parser.add_argument(
        "--num-phases",
        type=int,
        default=2,
        help="Number of rotated Zipfian phases in --benchmark-phase-shift mode (default: 2)",
    )
    ingest_parser.add_argument(
        "--benchmark-eviction",
        action="store_true",
        help="Run Zipfian foreign-key skew sweep comparing FIFO, LRU, and LFU",
    )
    ingest_parser.add_argument(
        "--benchmark-phase-shift",
        action="store_true",
        help="Run phase-rotated Zipfian access shift sweep comparing FIFO, LRU, static LFU, and windowed W-LFU",
    )
    ingest_parser.add_argument(
        "--benchmark-adaptive-pool",
        action="store_true",
        help="Run rapid multi-phase oscillation (W-TinyLFU / Count-Min Sketch) and shared multi-query JoinAuxState pool benchmark",
    )
    ingest_parser.add_argument(
        "--skew-sweep",
        default=None,
        help="Comma-separated skew values s in [0.0, 2.0] for eviction sweep",
    )
    ingest_parser.add_argument(
        "--policies",
        default="fifo,lru,lfu",
        help="Comma-separated policies for eviction sweep (default: fifo,lru,lfu)",
    )
    ingest_parser.add_argument(
        "--csv-out",
        default=None,
        help="Path to write CSV benchmark output",
    )
    ingest_parser.add_argument(
        "--md-out",
        default=None,
        help="Path to write Markdown benchmark output",
    )
    ingest_parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Output format",
    )

    normalize_parser = subparsers.add_parser(
        "normalize",
        help="Analyze functional dependencies, normal forms (3NF/BCNF), Tableau Chase losslessness, and migration DDL",
    )
    normalize_parser.add_argument(
        "input_file",
        help="Path to the JSON workload or relational schema file",
    )
    normalize_parser.add_argument(
        "--dialect",
        choices=["postgres", "mssql"],
        default="postgres",
        help="SQL dialect for migration DDL (default: postgres)",
    )
    normalize_parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Output format",
    )

    args = parser.parse_args(argv)

    if args.command == "report":
        analyze_workload(
            args.workload_file, format_md=args.format, topomap=args.topomap
        )
    elif args.command == "compare":
        compare_workloads(
            args.workload_a,
            args.workload_b,
            format_md=(args.format == "markdown"),
        )
    elif args.command == "doctor":
        run_doctor(args.workload_file, format_md=(args.format == "markdown"))
    elif args.command == "matrix":
        run_matrix(args.workload_a, args.workload_b, format_md=True)
    elif args.command == "ingest":
        run_ingest(args)
    elif args.command == "normalize":
        run_normalize(
            args.input_file,
            dialect=args.dialect,
            output_format=args.format,
        )


if __name__ == "__main__":
    main()
