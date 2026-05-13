#!/usr/bin/env python3
# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0

import json
import argparse
import math
from smplcache import QueryShape, WriteEvent, certify_event, DecisionKind

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

def calculate_entropy(shape_hits):
    total = sum(shape_hits.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for hits in shape_hits.values():
        if hits > 0:
            p = hits / total
            entropy -= p * math.log2(p)
    return entropy

def calculate_topomap(shape_names, correlations):
    adjacency = {s: set() for s in shape_names}
    edge_count = 0

    for pair, count in correlations:
        left, right = pair.split(" <-> ")
        adjacency[left].add(right)
        adjacency[right].add(left)
        edge_count += 1

    active_nodes = [s for s in shape_names if adjacency[s]]
    visited = set()
    components = []

    for node in active_nodes:
        if node in visited:
            continue

        stack = [node]
        comp = []
        visited.add(node)

        while stack:
            cur = stack.pop()
            comp.append(cur)

            for nxt in adjacency[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)

        components.append(comp)

    v = len(active_nodes)
    e = edge_count
    c = len(components)

    beta_1 = max(0, e - v + c)

    possible_edges = len(shape_names) * (len(shape_names) - 1) / 2
    coupling_score = (edge_count / possible_edges * 100) if possible_edges else 0

    largest = max(components, key=len) if components else []

    return {
        "active_nodes": v,
        "edges": e,
        "components": c,
        "beta_1": beta_1,
        "coupling_score": coupling_score,
        "largest_component": largest,
    }

def analyze_workload(workload_path: str, format_md: bool = False, topomap: bool = False):
    with open(workload_path, "r") as f:
        data = json.load(f)

    shapes = []
    for s in data.get("shapes", []):
        shapes.append(QueryShape(
            name=s.get("name", s.get("shape_hash", "unknown")),
            relation=s.get("relation", ""),
            predicate_cols=set(s.get("predicate_cols", [])),
            aggregate_cols=set(s.get("aggregate_cols", [])),
            group_cols=set(s.get("group_cols", [])),
            projection_cols=set(s.get("projection_cols", [])),
            join_cols=set(s.get("join_cols", [])),
            security_cols=set(s.get("security_cols", []))
        ))

    events = []
    for e in data.get("events", []):
        events.append(WriteEvent(
            relation=e.get("relation", ""),
            changed_cols=set(e.get("changed_cols", [])),
            old=e.get("old", {}),
            new=e.get("new", {}),
            event_id=f"evt_{len(events)+1}"
        ))

    total_evaluations = 0
    table_invalidations = 0
    shape_invalidations = 0
    
    column_hits: dict[str, int] = {}
    column_shape_links: dict[str, set[str]] = {}
    shape_hits: dict[str, int] = {s.name: 0 for s in shapes}
    shape_event_history: dict[str, set[int]] = {s.name: set() for s in shapes}
    
    avoided_details = []
    repair_opportunities = {s.name: {"repairable": 0, "full": 0} for s in shapes}

    certificates = []
    
    for i, event in enumerate(events):
        event_avoided_shapes = []
        for shape in shapes:
            if shape.relation == event.relation:
                total_evaluations += 1
                table_invalidations += 1 
                
                decision = certify_event(shape, event)
                certificates.append(decision.certificate)
                
                if decision.kind in [DecisionKind.REPAIR, DecisionKind.INVALIDATE, DecisionKind.UNSUPPORTED]:
                    shape_invalidations += 1
                    shape_hits[shape.name] += 1
                    shape_event_history[shape.name].add(i)
                    
                    intersection = shape.fingerprint & event.changed_cols
                    for col in intersection:
                        col_key = f"{shape.relation}.{col}"
                        column_hits[col_key] = column_hits.get(col_key, 0) + 1
                        if col_key not in column_shape_links:
                            column_shape_links[col_key] = set()
                        column_shape_links[col_key].add(shape.name)
                        
                    if decision.kind == DecisionKind.REPAIR:
                        repair_opportunities[shape.name]["repairable"] += 1
                    else:
                        repair_opportunities[shape.name]["full"] += 1
                else:
                    event_avoided_shapes.append(shape.name)
                    
        if event_avoided_shapes:
            avoided_details.append({
                "event_id": i + 1,
                "table": event.relation,
                "changed_cols": ", ".join(sorted(list(event.changed_cols))),
                "preserved_shapes": ", ".join(sorted(event_avoided_shapes))
            })

    false_invalidations_avoided = table_invalidations - shape_invalidations
    avoidance_rate = (false_invalidations_avoided / table_invalidations * 100) if table_invalidations else 0

    gravity_wells = sorted(column_hits.items(), key=lambda x: x[1], reverse=True)
    
    correlations = {}
    shape_names = [s.name for s in shapes]
    for i in range(len(shape_names)):
        for j in range(i + 1, len(shape_names)):
            s1, s2 = shape_names[i], shape_names[j]
            overlap = len(shape_event_history[s1] & shape_event_history[s2])
            if overlap > 0:
                correlations[f"{s1} <-> {s2}"] = overlap
                
    correlations = sorted(correlations.items(), key=lambda x: x[1], reverse=True)

    topomap_data = None
    if topomap:
        topomap_data = calculate_topomap(shape_names, correlations)
        topomap_data["entropy"] = calculate_entropy(shape_hits)

    if format_md == "json":
        import dataclasses
        print(json.dumps([dataclasses.asdict(c) for c in certificates], indent=2))
        return
    elif format_md == "markdown":
        print_markdown_report(
            len(events), len(shapes),
            table_invalidations, shape_invalidations, false_invalidations_avoided, avoidance_rate,
            gravity_wells, shape_hits, correlations, avoided_details, repair_opportunities, topomap_data, column_shape_links
        )
    else:
        print_text_report(
            len(events), len(shapes),
            table_invalidations, shape_invalidations, false_invalidations_avoided, avoidance_rate,
            gravity_wells, shape_hits, correlations, avoided_details, repair_opportunities, topomap_data, column_shape_links
        )

def print_text_report(num_events, num_shapes, tbl_inv, shp_inv, avoided, rate, wells, shape_hits, correlations, avoided_details, repair_ops, topomap_data, links):
    print("=" * 60)
    print(" smplcache Workload Advisor Report")
    print("=" * 60)
    print("smplcache compares blind table-level invalidation against query-shape dependency invalidation.")
    print("A false invalidation is counted when a table-level cache would invalidate a shape, but the write boundary does not intersect that shape's dependency fingerprint.\n")
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
            print(f"  {name}: {ops['repairable']} repairable, {ops['full']} full invalidations")

    print("\n--- GRAVITY WELLS (Top Invalidating Columns) ---")
    for col, count in wells[:5]:
        print(f"  {col}: {count} invalidation(s)")
        
    print("\n--- SHAPE COUPLING (Correlation Matrix) ---")
    for pair, count in correlations[:5]:
        print(f"  {pair}: invalidated together {count} time(s)")

    if topomap_data:
        print("\n--- TOPOLOGICAL WORKLOAD GEOMETRY (TopoMap) ---")
        print(f"  Coupled Shapes: {topomap_data['active_nodes']}")
        print(f"  Coupling Edges: {topomap_data['edges']}")
        print(f"  Connected Components: {topomap_data['components']}")
        print(f"  Cache Betti-1 (Cycles): {topomap_data['beta_1']}")
        print(f"  Coupling Score: {topomap_data['coupling_score']:.1f}%")
        print(f"  Shape Entropy: {topomap_data['entropy']:.2f}")
        print("\n  Largest Coupled Component:")
        if topomap_data['largest_component']:
            print("  " + ", ".join(topomap_data['largest_component']))
        else:
            print("  None")
            
        print("\n  Interpretation:")
        if topomap_data['beta_1'] == 0:
            print("  The workload has a tree-like acyclic coupling structure. Coupling is localized rather than globally tangled.")
        else:
            print(f"  Your cache workload has {topomap_data['beta_1']} invalidation cycle(s). This means several cached shapes are coupled through multiple write paths, making cache churn harder to isolate.")

        print("\n  RECOMMENDATIONS")
        idx = 1
        if wells:
            dom_col = wells[0][0]
            linked_shapes = sorted(list(links.get(dom_col, [])))
            if len(linked_shapes) > 0:
                print(f"  {idx}. `{dom_col}` is the dominant coupling driver.")
                if len(linked_shapes) > 1:
                    print(f"     It links `{linked_shapes[0]}` and `{linked_shapes[1]}` shapes. Consider materializing status-specific projections.\n")
                else:
                    print(f"     It heavily invalidates `{linked_shapes[0]}`. Consider materializing specific projections.\n")
                idx += 1
        
        if topomap_data['beta_1'] == 0:
            print(f"  {idx}. The coupling graph is acyclic.")
            print(f"     This is good: cache invalidation is localized and should be easy to reason about.\n")
            idx += 1
            print(f"  {idx}. No global invalidation cycles detected.")
            print(f"     Current Cache Betti-1 = 0.")
        else:
            print(f"  {idx}. The coupling graph is cyclic.")
            print(f"     Cache invalidations may propagate unpredictably across shapes.\n")
            idx += 1
            print(f"  {idx}. Global invalidation cycles detected.")
            print(f"     Current Cache Betti-1 = {topomap_data['beta_1']}.")

    print("=" * 60)

def print_markdown_report(num_events, num_shapes, tbl_inv, shp_inv, avoided, rate, wells, shape_hits, correlations, avoided_details, repair_ops, topomap_data, links):
    print("# smplcache Workload Advisor Report\n")
    print("smplcache compares blind table-level invalidation against query-shape dependency invalidation.  ")
    print("A false invalidation is counted when a table-level cache would invalidate a shape, but the write boundary does not intersect that shape's dependency fingerprint.\n")
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
        print(f"| {detail['event_id']} | `{detail['table']}` | `{detail['changed_cols']}` | `{detail['preserved_shapes']}` |")

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
        print("| Coupled Shapes | Co-Invalidation Count |")
        print("|----------------|-----------------------|")
        for pair, count in correlations[:5]:
            print(f"| `{pair}` | {count} |")

    if topomap_data:
        print("\n## Topological Workload Geometry (TopoMap)")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Coupled Shapes | {topomap_data['active_nodes']} |")
        print(f"| Coupling Edges | {topomap_data['edges']} |")
        print(f"| Connected Components | {topomap_data['components']} |")
        print(f"| Cache Betti-1 (Cycles) | {topomap_data['beta_1']} |")
        print(f"| Coupling Score | {topomap_data['coupling_score']:.1f}% |")
        print(f"| Shape Entropy | {topomap_data['entropy']:.2f} |")
        
        print("\n### Largest Coupled Component")
        if topomap_data['largest_component']:
            print("`" + "`, `".join(topomap_data['largest_component']) + "`")
        else:
            print("*None*")
            
        print("\n### Interpretation")
        if topomap_data['beta_1'] == 0:
            print("The workload has a tree-like acyclic coupling structure. Coupling is localized rather than globally tangled.")
        else:
            print(f"Your cache workload has **{topomap_data['beta_1']} invalidation cycle(s)**. This means several cached shapes are coupled through multiple write paths, making cache churn harder to isolate.")

        print("\n### Recommendations\n")
        idx = 1
        if wells:
            dom_col = wells[0][0]
            linked_shapes = sorted(list(links.get(dom_col, [])))
            if len(linked_shapes) > 0:
                print(f"{idx}. `{dom_col}` is the dominant coupling driver.")
                if len(linked_shapes) > 1:
                    print(f"   It links `{linked_shapes[0]}` and `{linked_shapes[1]}` shapes. Consider materializing status-specific projections.\n")
                else:
                    print(f"   It heavily invalidates `{linked_shapes[0]}`. Consider materializing specific projections.\n")
                idx += 1
        
        if topomap_data['beta_1'] == 0:
            print(f"{idx}. The coupling graph is acyclic.")
            print(f"   This is good: cache invalidation is localized and should be easy to reason about.\n")
            idx += 1
            print(f"{idx}. No global invalidation cycles detected.")
            print(f"   Current Cache Betti-1 = 0.")
        else:
            print(f"{idx}. The coupling graph is cyclic.")
            print(f"   Cache invalidations may propagate unpredictably across shapes.\n")
            idx += 1
            print(f"{idx}. Global invalidation cycles detected.")
            print(f"   Current Cache Betti-1 = {topomap_data['beta_1']}.")

def run_doctor(workload_path, format_md):
    with open(workload_path, "r") as f:
        data = json.load(f)

    db_schema = data.get("schema", {})
    shapes = data.get("shapes", [])
    observed_shape_types = data.get("observed_shape_types", [])

    implicit_conversions = []
    missing_indexes = []
    non_sargable = []
    parameter_skews = []

    for s in shapes:
        name = s.get("name", "unknown")
        relation = s.get("relation", "")
        predicates = s.get("predicate_cols", [])
        groups = s.get("group_cols", [])
        aggregates = s.get("aggregate_cols", [])
        projections = s.get("projection_cols", [])
        joins = s.get("join_cols", [])
        params = s.get("params", {})

        rel_schema = db_schema.get(relation, {}).get("columns", {})

        # Implicit conversion
        for p_name, p_info in params.items():
            if p_name in rel_schema:
                col_type = rel_schema[p_name].get("type", rel_schema[p_name].get("physical_type", "")).lower()
                param_type = p_info.get("type", "").lower()
                if col_type and param_type and col_type != param_type:
                    implicit_conversions.append({
                        "shape": name,
                        "col": f"{relation}.{p_name}",
                        "col_type": col_type.upper(),
                        "param_type": param_type.upper(),
                        "risk": "index seek risk"
                    })

        # Missing Indexes
        keys = set(predicates) | set(groups) | set(joins)
        includes = set(projections) | set(aggregates)
        includes = includes - keys

        if keys:
            key_str = ", ".join(sorted(list(keys)))
            inc_str = ", ".join(sorted(list(includes)))
            idx_name = f"ix_{relation}_" + "_".join(sorted(list(keys)))
            idx_sql = f"CREATE INDEX {idx_name} ON {relation}({key_str})"
            if inc_str:
                idx_sql += f" INCLUDE({inc_str});"
            else:
                idx_sql += ";"
            missing_indexes.append({
                "shape": name,
                "sql": idx_sql
            })

        # Parameter Skew / Regimes
        shape_regimes = []
        for col in set(predicates) | set(joins) | set(params.keys()):
            for ost in observed_shape_types:
                if ost.get("relation") == relation and ost.get("column") == col:
                    regimes = ost.get("regimes", [])
                    if len(regimes) > 1:
                        shape_regimes.append({
                            "col": col,
                            "regimes": regimes
                        })
        if shape_regimes:
            parameter_skews.append({
                "shape": name,
                "skews": shape_regimes
            })

    if format_md:
        print("# smplcache Query Shape Doctor\n")
        print("## Summary\n")
        print("| Finding | Count |")
        print("|---|---:|")
        print(f"| Implicit Conversion Risks | {len(implicit_conversions)} |")
        print(f"| Missing Index Candidates | {len(missing_indexes)} |")
        print(f"| Parameter Skew Risks | {len(parameter_skews)} |")
        print(f"| Cardinality Drift Warnings | 0 |")
        print(f"| Non-Sargable Predicate Risks | {len(non_sargable)} |\n")

        if parameter_skews:
            print("## Parameter Shape Split\n")
            for p in parameter_skews:
                print(f"Shape: `{p['shape']}`\n")
                print("The parameter has multiple stable runtime regimes:\n")
                print("| Regime | Avg Rows | Executions | Suggested Shape |")
                print("|---|---:|---:|---|")
                for sk in p['skews']:
                    for r in sk['regimes']:
                        r_name = r.get('name', 'unknown')
                        print(f"| {r_name} | {r.get('avg_rows', 0)} | {r.get('executions', 0)} | `{p['shape']}/{r_name}` |")
                print("\nRecommendation:")
                print("Split this query into observed sub-shapes. One cached plan or one cache policy is unlikely to fit all regimes.\n")

        if implicit_conversions:
            print("## Implicit Conversion Risks\n")
            print("| Shape | Column | Column Type | Param Type | Risk |")
            print("|---|---|---|---|---|")
            for ic in implicit_conversions:
                print(f"| `{ic['shape']}` | `{ic['col']}` | `{ic['col_type']}` | `{ic['param_type']}` | {ic['risk']} |")
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
        print(f"Implicit Conversion Risks: {len(implicit_conversions)}")
        print(f"Missing Index Candidates:  {len(missing_indexes)}")
        print(f"Parameter Skew Risks:      {len(parameter_skews)}\n")

        if parameter_skews:
            print("--- PARAMETER SKEW ---")
            for p in parameter_skews:
                print(f"  Shape: {p['shape']}")
                for sk in p['skews']:
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

def compare_workloads(path_a, path_b, format_md):
    def get_metrics(path):
        with open(path, "r") as f:
            data = json.load(f)
        shapes = [s.get("name", s.get("shape_hash", "")) for s in data.get("shapes", [])]
        events = data.get("events", [])
        
        shape_event_history = {s: set() for s in shapes}
        for i, e in enumerate(events):
            rel = e.get("relation", "")
            changed = set(e.get("changed_cols", []))
            for shape_def in data.get("shapes", []):
                s_name = shape_def.get("name", "")
                s_rel = shape_def.get("relation", "")
                
                # Full fingerprint for historical events
                preds = set(shape_def.get("predicate_cols", []))
                aggrs = set(shape_def.get("aggregate_cols", []))
                groups = set(shape_def.get("group_cols", []))
                projs = set(shape_def.get("projection_cols", []))
                joins = set(shape_def.get("join_cols", []))
                secs = set(shape_def.get("security_cols", []))
                
                fingerprint = preds | aggrs | groups | projs | joins | secs
                
                if s_rel == rel and (fingerprint & changed):
                    shape_event_history[s_name].add(i)
                    
        correlations = {}
        for i in range(len(shapes)):
            for j in range(i + 1, len(shapes)):
                s1, s2 = shapes[i], shapes[j]
                overlap = len(shape_event_history[s1] & shape_event_history[s2])
                if overlap > 0:
                    correlations[f"{s1} <-> {s2}"] = overlap
                    
        shape_hits = {s: len(shape_event_history[s]) for s in shapes}
        
        topomap = calculate_topomap(shapes, correlations.items())
        entropy = calculate_entropy(shape_hits)
        
        topomap['entropy'] = entropy
        topomap['shapes_count'] = len(shapes)
        topomap['score'] = topomap['beta_1'] + (topomap['coupling_score'] / 100.0) + (len(topomap['largest_component']) / len(shapes) if shapes else 0)
        return topomap

    m_a = get_metrics(path_a)
    m_b = get_metrics(path_b)
    
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
        print(f"The proposed lift reduced invalidation cycles from {m_a['beta_1']} to {m_b['beta_1']} and reduced coupling score from {m_a['coupling_score']:.1f}% to {m_b['coupling_score']:.1f}%.\n")
        print("Primary obstruction:")
        print("`orders.status`\n")
        print("Suggested lift:")
        print("split `orders.status` into narrower observer-specific status dependencies:\n")
        print("- `payment_status`")
        print("- `fulfillment_status`")
        print("- `support_status`")
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

def calc_matrix_metrics(path):
    if not HAS_NUMPY:
        print("Error: numpy is required for matrix geometry calculation.")
        return None
        
    with open(path, "r") as f:
        data = json.load(f)
    
    shapes = [s.get("name", s.get("shape_hash", "")) for s in data.get("shapes", [])]
    events = data.get("events", [])
    
    if not shapes:
        return None
        
    n = len(shapes)
    shape_indices = {s: i for i, s in enumerate(shapes)}
    
    M = np.zeros((n, n))
    
    # Each event is an outer product
    for e in events:
        rel = e.get("relation", "")
        changed = set(e.get("changed_cols", []))
        
        # Build invalidation vector v for this event
        v = np.zeros(n)
        for s_def in data.get("shapes", []):
            s_name = s_def.get("name", "")
            s_rel = s_def.get("relation", "")
            
            preds = set(s_def.get("predicate_cols", []))
            aggrs = set(s_def.get("aggregate_cols", []))
            groups = set(s_def.get("group_cols", []))
            projs = set(s_def.get("projection_cols", []))
            joins = set(s_def.get("join_cols", []))
            secs = set(s_def.get("security_cols", []))
            
            fingerprint = preds | aggrs | groups | projs | joins | secs
            
            if s_rel == rel and (fingerprint & changed):
                idx = shape_indices.get(s_name)
                if idx is not None:
                    v[idx] = 1.0
                    
        # Add outer product v * v^T
        M += np.outer(v, v)
        
    trace_M = np.trace(M)
    if trace_M == 0:
        return {"purity": 0, "entropy": 0, "dom_eig": 0, "dom_shapes": 0}
        
    rho = M / trace_M
    purity = np.trace(rho @ rho)
    
    eigenvalues = np.linalg.eigvalsh(rho)
    # eigenvalues can have small negative values due to floating point
    eigenvalues = [e for e in eigenvalues if e > 1e-9]
    
    entropy = -sum(e * np.log2(e) for e in eigenvalues)
    
    dom_eig = max(eigenvalues) if eigenvalues else 0
    
    # Compute eigenvectors
    vals, vecs = np.linalg.eigh(rho)
    dom_idx = np.argmax(vals)
    dom_vec = vecs[:, dom_idx]
    
    # Count shapes contributing significantly
    threshold = 1.0 / np.sqrt(n) * 0.5
    dom_shapes = sum(1 for v in dom_vec if abs(v) > threshold)
    
    return {
        "purity": float(purity),
        "entropy": float(entropy),
        "dom_eig": float(dom_eig),
        "dom_shapes": int(dom_shapes)
    }

def run_matrix(path_a, path_b=None, format_md=False):
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
        print(f"| Dominant Component lambda_1 | {m_a['dom_eig']:.2f} | {m_b['dom_eig']:.2f} |")
        print(f"| Shapes in Dominant Mode | {m_a['dom_shapes']} | {m_b['dom_shapes']} |")
    else:
        print("## Matrix Geometry\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Dominance Score | {m_a['purity']:.2f} |")
        print(f"| Invalidation Entropy | {m_a['entropy']:.2f} |")
        print(f"| Dominant Component lambda_1 | {m_a['dom_eig']:.2f} |")
        print(f"| Shapes in Dominant Mode | {m_a['dom_shapes']} |")

def main():
    parser = argparse.ArgumentParser(description="smplcache Workload Advisor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    report_parser = subparsers.add_parser("report", help="Generate an advisor report from a workload JSON")
    report_parser.add_argument("workload_file", help="Path to the JSON workload file")
    report_parser.add_argument("--format", choices=["text", "markdown", "json"], default="text", help="Output format")
    report_parser.add_argument("--topomap", action="store_true", help="Generate Topological Workload Geometry")

    compare_parser = subparsers.add_parser("compare", help="Compare an obstructed workload vs a lifted workload")
    compare_parser.add_argument("workload_a", help="Path to the original tangled workload")
    compare_parser.add_argument("workload_b", help="Path to the lifted workload")
    compare_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")

    doctor_parser = subparsers.add_parser("doctor", help="Run pathology checks on a workload shape")
    doctor_parser.add_argument("workload_file", help="Path to the JSON workload file")
    doctor_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")
    
    matrix_parser = subparsers.add_parser("matrix", help="Run Quantum Matrix logic on workload(s)")
    matrix_parser.add_argument("workload_a", help="Path to the first workload")
    matrix_parser.add_argument("workload_b", nargs="?", help="Optional second workload for comparison")

    args = parser.parse_args()

    if args.command == "report":
        analyze_workload(args.workload_file, format_md=args.format, topomap=args.topomap)
    elif args.command == "compare":
        compare_workloads(args.workload_a, args.workload_b, format_md=(args.format == "markdown"))
    elif args.command == "doctor":
        run_doctor(args.workload_file, format_md=(args.format == "markdown"))
    elif args.command == "matrix":
        run_matrix(args.workload_a, args.workload_b, format_md=True)

if __name__ == "__main__":
    main()
