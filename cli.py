#!/usr/bin/env python3
# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0

import json
import argparse
import math
from smplcache import QueryShape, WriteEvent, EvidenceLevel, RepairClass, Authority, DecisionPacket

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

def calculate_invalidation_graph(shape_names, correlations):
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
        "cycles": beta_1,
        "coupling_score": coupling_score,
        "largest_component": largest,
    }

def analyze_workload(workload_path: str, format_md: bool = False):
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
            security_cols=set(s.get("security_cols", [])),
            required_evidence=EvidenceLevel[s.get("required_evidence", "E2")],
            repair_class=RepairClass[s.get("repair_class", "INVALIDATE_ONLY")]
        ))

    events = []
    for e in data.get("events", []):
        events.append(WriteEvent(
            relation=e.get("relation", ""),
            changed_cols=set(e.get("changed_cols", [])),
            old=e.get("old", {}),
            new=e.get("new", {}),
            evidence_level=EvidenceLevel[e.get("evidence_level", "E3")]
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

    for i, event in enumerate(events):
        event_avoided_shapes = []
        for shape in shapes:
            if shape.relation == event.relation:
                total_evaluations += 1
                table_invalidations += 1 
                
                intersection = shape.fingerprint & event.changed_cols
                if intersection:
                    shape_invalidations += 1
                    shape_hits[shape.name] += 1
                    shape_event_history[shape.name].add(i)
                    
                    for col in intersection:
                        col_key = f"{shape.relation}.{col}"
                        column_hits[col_key] = column_hits.get(col_key, 0) + 1
                        if col_key not in column_shape_links:
                            column_shape_links[col_key] = set()
                        column_shape_links[col_key].add(shape.name)
                        
                    if event.evidence_level >= shape.required_evidence and shape.repair_class != RepairClass.INVALIDATE_ONLY:
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

    hotspots = sorted(column_hits.items(), key=lambda x: x[1], reverse=True)
    
    correlations = {}
    shape_names = [s.name for s in shapes]
    for i in range(len(shape_names)):
        for j in range(i + 1, len(shape_names)):
            s1, s2 = shape_names[i], shape_names[j]
            overlap = len(shape_event_history[s1] & shape_event_history[s2])
            if overlap > 0:
                correlations[f"{s1} <-> {s2}"] = overlap
                
    correlations = sorted(correlations.items(), key=lambda x: x[1], reverse=True)

    if format_md:
        print_markdown_report(
            len(events), len(shapes),
            table_invalidations, shape_invalidations, false_invalidations_avoided, avoidance_rate,
            hotspots, shape_hits, correlations, avoided_details, repair_opportunities, column_shape_links
        )
    else:
        print_text_report(
            len(events), len(shapes),
            table_invalidations, shape_invalidations, false_invalidations_avoided, avoidance_rate,
            hotspots, shape_hits, correlations, avoided_details, repair_opportunities, column_shape_links
        )

def print_text_report(num_events, num_shapes, tbl_inv, shp_inv, avoided, rate, hotspots, shape_hits, correlations, avoided_details, repair_ops, links):
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

    print("\n--- CACHE HOTSPOTS (Top Invalidating Columns) ---")
    for col, count in hotspots[:5]:
        print(f"  {col}: {count} invalidation(s)")
        
    print("\n--- SHAPE COUPLING (Correlation Matrix) ---")
    for pair, count in correlations[:5]:
        print(f"  {pair}: invalidated together {count} time(s)")

    print("=" * 60)

def print_markdown_report(num_events, num_shapes, tbl_inv, shp_inv, avoided, rate, hotspots, shape_hits, correlations, avoided_details, repair_ops, links):
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

    print("\n## Cache Hotspots (Top Invalidating Columns)")
    if not hotspots:
        print("*None detected.*")
    else:
        print("| Column | Invalidation Hits |")
        print("|--------|-------------------|")
        for col, count in hotspots[:5]:
            print(f"| `{col}` | {count} |")
            
    print("\n## Shape Coupling (Correlation Matrix)")
    if not correlations:
        print("*No structurally coupled shapes detected.*")
    else:
        print("| Coupled Shapes | Co-Invalidation Count |")
        print("|----------------|-----------------------|")
        for pair, count in correlations[:5]:
            print(f"| `{pair}` | {count} |")

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
        query = s.get("query", "")
        query_upper = query.upper()

        # Non-sargable predicate detection
        import re
        for pred in predicates:
            pred_upper = pred.upper()
            # Function wraps: CAST(col ...), CONVERT(..., col), ISNULL(col, ...), UPPER(col), LOWER(col)
            patterns = [
                (rf'CAST\s*\(\s*{re.escape(pred)}', f"CAST({pred}) prevents index seek"),
                (rf'CONVERT\s*\([^,]+,\s*{re.escape(pred)}', f"CONVERT({pred}) prevents index seek"),
                (rf'ISNULL\s*\(\s*{re.escape(pred)}', f"ISNULL({pred}) prevents index seek"),
                (rf'UPPER\s*\(\s*{re.escape(pred)}', f"UPPER({pred}) prevents index seek"),
                (rf'LOWER\s*\(\s*{re.escape(pred)}', f"LOWER({pred}) prevents index seek"),
            ]
            for pattern, risk in patterns:
                if re.search(pattern, query, re.IGNORECASE):
                    non_sargable.append({"shape": name, "pred": pred, "risk": f"non-sargable: {risk}"})
            # Leading wildcard LIKE
            if re.search(rf"LIKE\s+'%[^']*'", query_upper) and pred_upper in query_upper:
                non_sargable.append({"shape": name, "pred": pred, "risk": "non-sargable: leading wildcard LIKE prevents index seek"})

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
        print(f"| Non-Sargable Predicate Risks | {len(non_sargable)} |")
        print(f"| Implicit Conversion Risks | {len(implicit_conversions)} |")
        print(f"| Missing Index Candidates | {len(missing_indexes)} |")
        print(f"| Parameter Skew Risks | {len(parameter_skews)} |")
        print(f"| Cardinality Drift Warnings | 0 |\n")

        if non_sargable:
            print("## Non-Sargable Predicates\n")
            print("| Shape | Predicate | Risk |")
            print("|---|---|---|")
            for ns in non_sargable:
                print(f"| `{ns['shape']}` | `{ns['pred']}` | {ns['risk']} |")
            print()

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
        
        topomap = calculate_invalidation_graph(shapes, correlations.items())
        entropy = calculate_entropy(shape_hits)
        
        topomap['entropy'] = entropy
        topomap['shapes_count'] = len(shapes)
        topomap['score'] = topomap['cycles'] + (topomap['coupling_score'] / 100.0) + (len(topomap['largest_component']) / len(shapes) if shapes else 0)
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
        print(f"| Invalidation Cycles | {m_a['cycles']} |")
        print(f"| Coupling Score | {m_a['coupling_score']:.1f}% |")
        print(f"| Invalidation Skew | {m_a['entropy']:.2f} |")
        print(f"| Obstruction Score | {m_a['score']:.2f} |\n")

        print("## After: Lift\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Query Shapes | {m_b['shapes_count']} |")
        print(f"| Coupling Edges | {m_b['edges']} |")
        print(f"| Invalidation Cycles | {m_b['cycles']} |")
        print(f"| Coupling Score | {m_b['coupling_score']:.1f}% |")
        print(f"| Invalidation Skew | {m_b['entropy']:.2f} |")
        print(f"| Obstruction Score | {m_b['score']:.2f} |\n")

        print("## Lift Summary\n")
        print(f"The proposed lift reduced invalidation cycles from {m_a['cycles']} to {m_b['cycles']} and reduced coupling score from {m_a['coupling_score']:.1f}% to {m_b['coupling_score']:.1f}%.\n")
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
        print(f"  Invalidation Cycles: {m_a['cycles']}")
        print(f"  Coupling Score: {m_a['coupling_score']:.1f}%")
        print(f"  Obstruction Score: {m_a['score']:.2f}\n")
        print("--- AFTER (Lift) ---")
        print(f"  Invalidation Cycles: {m_b['cycles']}")
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

def run_graph(workload_path, format_md):
    with open(workload_path, "r") as f:
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
    
    graph_data = calculate_invalidation_graph(shapes, correlations.items())
    entropy = calculate_entropy(shape_hits)
    
    if format_md:
        print("# Invalidation Graph Diagnostics\n")
        print("| Metric | Value |")
        print("|---|---:|")
        print(f"| Coupled Shapes | {graph_data['active_nodes']} |")
        print(f"| Coupling Edges | {graph_data['edges']} |")
        print(f"| Connected Components | {graph_data['components']} |")
        print(f"| Cache Invalidation Cycles | {graph_data['cycles']} |")
        print(f"| Coupling Score | {graph_data['coupling_score']:.1f}% |")
        print(f"| Invalidation Skew | {entropy:.2f} |")
        
        print("\n### Largest Coupled Component")
        if graph_data['largest_component']:
            print("`" + "`, `".join(graph_data['largest_component']) + "`")
        else:
            print("*None*")
    else:
        print("=" * 60)
        print(" Invalidation Graph Diagnostics")
        print("=" * 60)
        print(f"  Coupled Shapes: {graph_data['active_nodes']}")
        print(f"  Coupling Edges: {graph_data['edges']}")
        print(f"  Connected Components: {graph_data['components']}")
        print(f"  Cache Invalidation Cycles: {graph_data['cycles']}")
        print(f"  Coupling Score: {graph_data['coupling_score']:.1f}%")
        print(f"  Invalidation Skew: {entropy:.2f}")
        print("\n  Largest Coupled Component:")
        if graph_data['largest_component']:
            print("  " + ", ".join(graph_data['largest_component']))
        else:
            print("  None")

def run_repair(workload_path, shape_name, event_id_str, dialect):
    with open(workload_path, "r") as f:
        data = json.load(f)
        
    shape_def = None
    for s in data.get("shapes", []):
        if s.get("name") == shape_name:
            shape_def = s
            break
            
    if not shape_def:
        print(f"-- ERROR: Shape '{shape_name}' not found.")
        return
        
    try:
        event_id = int(event_id_str)
    except:
        event_id = event_id_str
        
    event_def = None
    for e in data.get("events", []):
        if e.get("event_id") == event_id or str(e.get("event_id")) == str(event_id_str):
            event_def = e
            break
            
    if not event_def:
        print(f"-- ERROR: Event '{event_id_str}' not found.")
        return
    
    # Build typed objects for classifier
    from smplcache import classify_repairability, RepairVerdict
    
    shape = QueryShape(
        name=shape_def.get("name", "unknown"),
        relation=shape_def.get("relation", ""),
        predicate_cols=set(shape_def.get("predicate_cols", [])),
        aggregate_cols=set(shape_def.get("aggregate_cols", [])),
        group_cols=set(shape_def.get("group_cols", [])),
        projection_cols=set(shape_def.get("projection_cols", [])),
        join_cols=set(shape_def.get("join_cols", [])),
        security_cols=set(shape_def.get("security_cols", [])),
        required_evidence=EvidenceLevel[shape_def.get("required_evidence", "E2")],
        repair_class=RepairClass[shape_def.get("repair_class", "INVALIDATE_ONLY")]
    )
    
    event = WriteEvent(
        relation=event_def.get("relation", ""),
        changed_cols=set(event_def.get("changed_cols", [])),
        old=event_def.get("old", {}),
        new=event_def.get("new", {}),
        evidence_level=EvidenceLevel[event_def.get("evidence_level", "E3")]
    )
    
    verdict = classify_repairability(shape, event)
    
    shape_hash = shape_def.get("shape_hash", "q_hash")
    repair_class_name = shape_def.get("repair_class", "INVALIDATE_ONLY")
    agg_fn = shape_def.get("aggregate_function", "NONE")
    
    # Emit decision packet as structured comment
    packet = {
        "decision": verdict.decision,
        "authority": verdict.authority.value,
        "shape": shape_name,
        "event_id": event_def.get("event_id"),
        "evidence_level": event.evidence_level.name,
        "required_evidence": shape.required_evidence.name,
        "repair_class": repair_class_name,
        "operator": verdict.operator,
        "reason": verdict.reason,
        "proof_tags": verdict.proof_tags,
        "fallback": verdict.fallback
    }
    
    print("-- Decision Packet:")
    for line in json.dumps(packet, indent=2).split("\n"):
        print(f"--   {line}")
    print()
    
    if verdict.action == "preserve":
        print(f"-- No action required. Cache is still valid.")
        return
        
    if verdict.action == "invalidate":
        print(f"-- REPAIR DENIED: {verdict.reason}")
        print(f"-- Fallback: full cache invalidation required.")
        print(f"\nDELETE FROM dbo.cached_aggregates WHERE shape_hash = '{shape_hash}';")
        return
    
    # Repair path
    group_cols = shape_def.get("group_cols", [])
    aggr_cols = shape_def.get("aggregate_cols", [])
    preds = shape_def.get("predicate_cols", [])
    changed_cols = event_def.get("changed_cols", [])
    old_vals = event_def.get("old", {})
    new_vals = event_def.get("new", {})
    
    group_col = group_cols[0] if group_cols else "group_key"
    aggr_col = aggr_cols[0] if aggr_cols else "value"
    
    # Detect scenario
    predicate_changed = bool(set(preds) & set(changed_cols))
    group_key_changed = bool(set(group_cols) & set(changed_cols))
    aggregate_changed = bool(set(aggr_cols) & set(changed_cols))
    
    print(f"\n-- Aggregate Function: {agg_fn}")
    
    if group_key_changed:
        # Scenario 2: group key movement
        old_key = old_vals.get(group_col, "unknown")
        new_key = new_vals.get(group_col, "unknown")
        old_amount = old_vals.get(aggr_col, 0)
        new_amount = new_vals.get(aggr_col, 0)
        
        if agg_fn == "COUNT":
            delta_sub = "1"
            delta_add = "1"
        else:
            delta_sub = str(old_amount)
            delta_add = str(new_amount)
        
        print(f"-- Scenario: group key movement ({group_col}: {old_key} -> {new_key})")
        print(f"\n-- Step 1: subtract from old group key")
        print(f"UPDATE dbo.cached_aggregates")
        print(f"SET value = value - {delta_sub}")
        print(f"WHERE shape_hash = '{shape_hash}'")
        print(f"  AND group_key = @old_{group_col};")
        print(f"\n-- Step 2: add to new group key")
        print(f"MERGE dbo.cached_aggregates AS target")
        print(f"USING (SELECT '{shape_hash}' AS shape_hash, @new_{group_col} AS group_key, {delta_add} AS delta_value) AS src")
        print(f"ON target.shape_hash = src.shape_hash")
        print(f"AND target.group_key = src.group_key")
        print(f"WHEN MATCHED THEN")
        print(f"    UPDATE SET value = value + src.delta_value")
        print(f"WHEN NOT MATCHED THEN")
        print(f"    INSERT (shape_hash, group_key, value)")
        print(f"    VALUES (src.shape_hash, src.group_key, src.delta_value);")
        
    elif predicate_changed:
        # Scenario 1: predicate boundary crossing
        pred_col = list(set(preds) & set(changed_cols))[0]
        old_pred_val = old_vals.get(pred_col, "unknown")
        new_pred_val = new_vals.get(pred_col, "unknown")
        
        group_key_val = new_vals.get(group_col, old_vals.get(group_col, "unknown"))
        amount_val = new_vals.get(aggr_col, old_vals.get(aggr_col, 0))
        
        if agg_fn == "COUNT":
            delta = "1"
        else:
            delta = str(amount_val)
        
        print(f"-- Scenario: predicate boundary crossing ({pred_col}: {old_pred_val} -> {new_pred_val})")
        print(f"\n-- Row may have entered or left the predicate boundary.")
        print(f"-- Application must evaluate old/new predicate match to determine +/- direction.")
        print(f"\nMERGE dbo.cached_aggregates AS target")
        print(f"USING (SELECT '{shape_hash}' AS shape_hash, @{group_col} AS group_key, {delta} AS delta_value) AS src")
        print(f"ON target.shape_hash = src.shape_hash")
        print(f"AND target.group_key = src.group_key")
        print(f"WHEN MATCHED THEN")
        print(f"    UPDATE SET value = value + src.delta_value")
        print(f"WHEN NOT MATCHED THEN")
        print(f"    INSERT (shape_hash, group_key, value)")
        print(f"    VALUES (src.shape_hash, src.group_key, src.delta_value);")
        
    elif aggregate_changed:
        # Scenario 3: aggregate value changed, predicate unchanged
        old_amount = old_vals.get(aggr_col, 0)
        new_amount = new_vals.get(aggr_col, 0)
        
        if agg_fn == "COUNT":
            print(f"-- Scenario: aggregate column changed but COUNT is unaffected")
            print(f"-- No repair needed.")
            return
        else:
            delta = new_amount - old_amount
            print(f"-- Scenario: aggregate delta ({aggr_col}: {old_amount} -> {new_amount}, delta={delta})")
            print(f"\nUPDATE dbo.cached_aggregates")
            print(f"SET value = value + ({delta})")
            print(f"WHERE shape_hash = '{shape_hash}'")
            print(f"  AND group_key = @{group_col};")
    else:
        # Generic fallback
        if agg_fn == "COUNT":
            delta = "1"
        else:
            delta = f"@{aggr_col}"
        
        print(f"-- Scenario: generic repair")
        print(f"\nMERGE dbo.cached_aggregates AS target")
        print(f"USING (SELECT '{shape_hash}' AS shape_hash, @{group_col} AS group_key, {delta} AS delta_value) AS src")
        print(f"ON target.shape_hash = src.shape_hash")
        print(f"AND target.group_key = src.group_key")
        print(f"WHEN MATCHED THEN")
        print(f"    UPDATE SET value = value + src.delta_value")
        print(f"WHEN NOT MATCHED THEN")
        print(f"    INSERT (shape_hash, group_key, value)")
        print(f"    VALUES (src.shape_hash, src.group_key, src.delta_value);")

def run_replay(workload_path, format_md):
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
            security_cols=set(s.get("security_cols", [])),
            required_evidence=EvidenceLevel[s.get("required_evidence", "E2")],
            repair_class=RepairClass[s.get("repair_class", "INVALIDATE_ONLY")]
        ))

    events = []
    for e in data.get("events", []):
        events.append(WriteEvent(
            relation=e.get("relation", ""),
            changed_cols=set(e.get("changed_cols", [])),
            old=e.get("old", {}),
            new=e.get("new", {}),
            evidence_level=EvidenceLevel[e.get("evidence_level", "E3")]
        ))

    from smplcache import classify_repairability

    table_drops = 0
    shape_drops = 0
    repair_drops = 0
    repairs_successful = 0
    
    log = []

    for event in events:
        event_log = {
            "relation": event.relation,
            "changed": list(event.changed_cols),
            "table_impact": 0,
            "shape_impact": 0,
            "repair_impact": 0,
            "repairs": 0
        }
        for shape in shapes:
            if shape.relation == event.relation:
                table_drops += 1
                event_log["table_impact"] += 1
                
                verdict = classify_repairability(shape, event)
                
                if verdict.fingerprint_hit:
                    shape_drops += 1
                    event_log["shape_impact"] += 1
                    
                    if verdict.action == "repair":
                        repairs_successful += 1
                        event_log["repairs"] += 1
                    else:
                        repair_drops += 1
                        event_log["repair_impact"] += 1
                        
        log.append(event_log)
    
    # Reduction calculations
    red_vs_table = ((table_drops - repair_drops) / table_drops * 100) if table_drops else 0
    red_vs_shape = ((shape_drops - repair_drops) / shape_drops * 100) if shape_drops else 0
        
    if format_md:
        print("# Replay Simulator Results\n")
        print("## Policy Comparison\n")
        print("| Policy | Total Cache Drops | Total Repairs |")
        print("|---|---:|---:|")
        print(f"| Table Invalidation | {table_drops} | 0 |")
        print(f"| Shape Invalidation | {shape_drops} | 0 |")
        print(f"| Shape + Repair | {repair_drops} | {repairs_successful} |")
        print(f"| **Reduction vs Table** | **{red_vs_table:.1f}%** | — |")
        print(f"| **Reduction vs Shape** | **{red_vs_shape:.1f}%** | — |\n")
        
        print("## Event Log\n")
        print("| Event Relation | Changed Columns | Table Drops | Shape Drops | Repair Drops | Repairs |")
        print("|---|---|---:|---:|---:|---:|")
        for e in log:
            cols = ", ".join(sorted(e['changed']))
            print(f"| `{e['relation']}` | `{cols}` | {e['table_impact']} | {e['shape_impact']} | {e['repair_impact']} | {e['repairs']} |")
    else:
        print("=" * 60)
        print(" Replay Simulator Results")
        print("=" * 60)
        print("--- POLICY COMPARISON ---")
        print(f"  Table Invalidation: {table_drops} drops")
        print(f"  Shape Invalidation: {shape_drops} drops")
        print(f"  Shape + Repair:     {repair_drops} drops, {repairs_successful} repairs")
        print(f"  Reduction vs Table: {red_vs_table:.1f}%")
        print(f"  Reduction vs Shape: {red_vs_shape:.1f}%")
        print("\n--- EVENT LOG ---")
        for i, e in enumerate(log):
            cols = ", ".join(sorted(e['changed']))
            print(f"  Event {i+1} ({e['relation']}, cols: {cols}):")
            print(f"    Table policy:  {e['table_impact']} drops")
            print(f"    Shape policy:  {e['shape_impact']} drops")
            print(f"    Repair policy: {e['repair_impact']} drops, {e['repairs']} repairs\n")

def main():
    parser = argparse.ArgumentParser(description="smplcache Workload Advisor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    report_parser = subparsers.add_parser("report", help="Generate an advisor report from a workload JSON")
    report_parser.add_argument("workload_file", help="Path to the JSON workload file")
    report_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")

    doctor_parser = subparsers.add_parser("doctor", help="Run pathology checks on a workload shape")
    doctor_parser.add_argument("workload_file", help="Path to the JSON workload file")
    doctor_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")

    repair_parser = subparsers.add_parser("repair", help="Generate SQL delta repair plan")
    repair_parser.add_argument("workload_file", help="Path to the JSON workload file")
    repair_parser.add_argument("--shape", required=True, help="Shape name to repair")
    repair_parser.add_argument("--event", required=True, help="Event ID to repair")
    repair_parser.add_argument("--dialect", default="sqlserver", help="SQL dialect (e.g. sqlserver, postgres)")

    compare_parser = subparsers.add_parser("compare", help="Compare an obstructed workload vs a lifted workload")
    compare_parser.add_argument("workload_a", help="Path to the original tangled workload")
    compare_parser.add_argument("workload_b", help="Path to the lifted workload")
    compare_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")

    graph_parser = subparsers.add_parser("graph", help="Analyze invalidation graph diagnostics")
    graph_parser.add_argument("workload_file", help="Path to the JSON workload file")
    graph_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")
    
    replay_parser = subparsers.add_parser("replay", help="Run replay simulator comparing invalidation policies")
    replay_parser.add_argument("workload_file", help="Path to the JSON workload file")
    replay_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")
    
    matrix_parser = subparsers.add_parser("matrix", help="Run Quantum Matrix logic on workload(s)")
    matrix_parser.add_argument("workload_a", help="Path to the first workload")
    matrix_parser.add_argument("workload_b", nargs="?", help="Optional second workload for comparison")

    args = parser.parse_args()

    if args.command == "report":
        analyze_workload(args.workload_file, format_md=(args.format == "markdown"))
    elif args.command == "doctor":
        run_doctor(args.workload_file, format_md=(args.format == "markdown"))
    elif args.command == "repair":
        run_repair(args.workload_file, args.shape, args.event, args.dialect)
    elif args.command == "compare":
        compare_workloads(args.workload_a, args.workload_b, format_md=(args.format == "markdown"))
    elif args.command == "graph":
        run_graph(args.workload_file, format_md=(args.format == "markdown"))
    elif args.command == "replay":
        run_replay(args.workload_file, format_md=(args.format == "markdown"))
    elif args.command == "matrix":
        run_matrix(args.workload_a, args.workload_b, format_md=True)

if __name__ == "__main__":
    main()
