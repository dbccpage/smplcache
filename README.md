# smplcache

**Stop invalidating caches. Start repairing them.**

`smplcache` is a workload-shape advisor for CDC-backed caches.

Most caches die from over-invalidation: a table changes, so every cached query over that table gets dropped, even when the changed column could not affect the result.

`smplcache` analyzes query shapes and CDC-style write events to show:

- which invalidations are false;
- which cached aggregates can be repaired instead of dropped;
- which columns are cache hotspots;
- which queries have implicit conversion, missing-index, non-sargable, or parameter-skew risks;
- which schema split or projection would reduce cache churn.

It is not a cache server yet. It is a cache survival kit.

## CLI Tools

`smplcache` exposes the following workload-shape tools:

### `report`

Analyze cache invalidation behavior from a workload JSON file.

```bash
python cli.py report examples/workload.common.json --format markdown
```

Reports false invalidations avoided, shape-level invalidations, repairable aggregate updates, top cache hotspots, and coupled query shapes.

### `doctor`

Detect query-shape pathologies.

```bash
python cli.py doctor examples/workload.common.json --format markdown
```

Finds non-sargable predicates, implicit conversion risks, missing index candidates, parameter skew, and stale stats risks.

### `repair`

Generate a SQL delta repair plan for a specific shape and CDC event.

```bash
python cli.py repair examples/workload.common.json --shape revenue_by_customer_paid --event 3 --format sqlserver
```

Outputs the exact SQL (e.g. `MERGE` statement) needed to incrementally repair a cached aggregate instead of dropping it.

### `compare`

Compare an obstructed workload against a lifted workload.

```bash
python cli.py compare examples/obstruction_mess.json examples/lifted_clean.json --format markdown
```

Shows coupling reduction, invalidation cycles reduction, obstruction score before/after, and suggested structural lift.

### `graph`

Analyze the workload's invalidation graph diagnostics.

```bash
python cli.py graph examples/workload.common.json --format markdown
```

Outputs the invalidation graph, cache invalidation cycles, invalidation skew, and coupling recommendations.

### `replay`

Run the replay simulator to compare invalidation policies across historical CDC events.

```bash
python cli.py replay examples/workload.common.json --format markdown
```

Shows event-by-event comparisons of Table Invalidation vs Shape Invalidation vs Repair policies, illustrating exactly where repairs reduce cache drops.

### `matrix` (experimental)

Analyze the workload as an invalidation correlation matrix.

```bash
python cli.py matrix examples/obstruction_mess.json examples/lifted_clean.json
```

Shows dominance score, dominant invalidation component, and the number of shapes controlled by the dominant mode.

## Roadmap

1. Add `evidence_level` support in workload JSON
2. Add `repair_class` field per shape
3. Build repairability classifier with strict invalidate fallback
4. Add repair SQL generator for single-table SUM/COUNT GROUP BY
5. Add non-sargable predicate detector
6. Add implicit conversion detector
7. Add replay simulator: table invalidation vs shape invalidation vs repair
8. Add join-aware sensitivity model
9. Add oracle-based fuzz tests for safe repair classes
