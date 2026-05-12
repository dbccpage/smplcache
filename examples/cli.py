#!/usr/bin/env python3
# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jeremy Carroll

import json
import argparse
from smplcache import QueryShape, WriteEvent

def analyze_workload(workload_path: str, format_md: bool = False):
    with open(workload_path, "r") as f:
        data = json.load(f)

    # Reconstruct shapes
    shapes = []
    for s in data.get("shapes", []):
        shapes.append(QueryShape(
            name=s["name"],
            relation=s["relation"],
            predicate_cols=set(s.get("predicate_cols", [])),
            aggregate_cols=set(s.get("aggregate_cols", [])),
            group_cols=set(s.get("group_cols", []))
        ))

    # Reconstruct events
    events = []
    for e in data.get("events", []):
        events.append(WriteEvent(
            relation=e["relation"],
            changed_cols=set(e["changed_cols"]),
            old={}, # Not required for intersection math
            new={}
        ))

    total_evaluations = 0
    table_invalidations = 0
    shape_invalidations = 0
    
    column_hits: dict[str, int] = {}
    shape_hits: dict[str, int] = {s.name: 0 for s in shapes}
    
    # Track which event indices invalidated which shapes for correlation
    shape_event_history: dict[str, set[int]] = {s.name: set() for s in shapes}

    for i, event in enumerate(events):
        for shape in shapes:
            if shape.relation == event.relation:
                total_evaluations += 1
                table_invalidations += 1  # Table-level caching invalidates blindly
                
                intersection = shape.fingerprint & event.changed_cols
                if intersection:
                    shape_invalidations += 1
                    shape_hits[shape.name] += 1
                    shape_event_history[shape.name].add(i)
                    
                    for col in intersection:
                        col_key = f"{shape.relation}.{col}"
                        column_hits[col_key] = column_hits.get(col_key, 0) + 1

    false_invalidations_avoided = table_invalidations - shape_invalidations
    avoidance_rate = (false_invalidations_avoided / table_invalidations * 100) if table_invalidations else 0

    # Sort gravity wells (columns causing most invalidations)
    gravity_wells = sorted(column_hits.items(), key=lambda x: x[1], reverse=True)
    
    # Calculate pairwise correlation
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
            gravity_wells, shape_hits, correlations
        )
    else:
        print_text_report(
            len(events), len(shapes),
            table_invalidations, shape_invalidations, false_invalidations_avoided, avoidance_rate,
            gravity_wells, shape_hits, correlations
        )

def print_text_report(num_events, num_shapes, tbl_inv, shp_inv, avoided, rate, wells, shape_hits, correlations):
    print("=" * 60)
    print(" smplcache Workload Advisor Report")
    print("=" * 60)
    print(f"Events Analyzed: {num_events}")
    print(f"Query Shapes Tracked: {num_shapes}\n")
    
    print("--- FALSE INVALIDATION ANALYSIS ---")
    print(f"Table-Level Invalidations:     {tbl_inv}")
    print(f"Shape-Level Invalidations:     {shp_inv}")
    print(f"False Invalidations Avoided:   {avoided} ({rate:.1f}% reduction)\n")
    
    print("--- GRAVITY WELLS (Top Invalidating Columns) ---")
    if not wells:
        print("  None detected.")
    for col, count in wells[:5]:
        print(f"  {col}: {count} invalidation(s)")
        
    print("\n--- SHAPE COUPLING (Correlation Matrix) ---")
    if not correlations:
        print("  No structurally coupled shapes detected.")
    for pair, count in correlations[:5]:
        print(f"  {pair}: invalidated together {count} time(s)")
    print("=" * 60)


def print_markdown_report(num_events, num_shapes, tbl_inv, shp_inv, avoided, rate, wells, shape_hits, correlations):
    print("# smplcache Workload Advisor Report\n")
    print(f"**Events Analyzed:** {num_events}  ")
    print(f"**Query Shapes Tracked:** {num_shapes}  \n")
    
    print("## False Invalidation Analysis")
    print("| Metric | Count |")
    print("|--------|-------|")
    print(f"| Table-Level Invalidations | {tbl_inv} |")
    print(f"| Shape-Level Invalidations | {shp_inv} |")
    print(f"| **False Invalidations Avoided** | **{avoided}** |")
    print(f"| **Reduction Rate** | **{rate:.1f}%** |\n")
    
    print("## Gravity Wells (Top Invalidating Columns)")
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

def main():
    parser = argparse.ArgumentParser(description="smplcache Workload Advisor CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    report_parser = subparsers.add_parser("report", help="Generate an advisor report from a workload JSON")
    report_parser.add_argument("workload_file", help="Path to the JSON workload file")
    report_parser.add_argument("--format", choices=["text", "markdown"], default="text", help="Output format")

    args = parser.parse_args()

    if args.command == "report":
        analyze_workload(args.workload_file, format_md=(args.format == "markdown"))

if __name__ == "__main__":
    main()
