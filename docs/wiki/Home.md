# SmplCache + Simplex Normal Forms Wiki

Welcome to the technical documentation for **SmplCache**, a boundary-certified incremental query cache and relational normalization engine for Python (`3.11+`).

## Core Architecture Pages

1. **[Theorem 7 and Boundary Certificates](Theorem-7-and-Boundary-Certificates.md)**
   How `certify_event` and `certify_join_event` classify every write event into `PRESERVE` (`KEEP`), `REPAIR` (`FIX IN PLACE`), or `INVALIDATE` (`REREAD`) with machine-readable `Certificate` records and WAL LSN / sequence `boundary_clock` watermarks.
2. **[Simplex Normal Forms and Theorem 9 Lifts](Simplex-Normal-Forms-and-Theorem-9-Lifts.md)**
   How `smplcache.normal_forms` computes functional dependency closures, minimal covers, candidate keys, Bernstein 3NF synthesis, Tsou-Fischer BCNF decomposition, Tableau Chase losslessness checks, and Class D group-key reductions.
3. **[Bounded Join Maintenance and W-TinyLFU](Bounded-Join-Maintenance-and-W-TinyLFU.md)**
   How `JoinAuxState` and `SharedJoinAuxPool` maintain $O(1)$ primary-key and secondary foreign-key indices under a hard memory budget (`max_total_rows` and `max_tombstones`) using segmented W-TinyLFU admission and fail-closed tombstone tracking.

## Quick CLI Reference

```bash
# 1. Inspect workload co-invalidation and repair vs. invalidate ratios
smplcache analyze examples/ecommerce_before.json

# 2. Compute simplicial nerve topology and isolated singleton shapes
smplcache topomap examples/ecommerce_before.json

# 3. Inspect SQL ASTs for non-SARGable predicates, implicit conversions, and FD-redundant GROUP BY columns
smplcache doctor examples/ecommerce_before.json

# 4. Synthesize 3NF/BCNF schema lifts, run Tableau Chase verification, and emit PostgreSQL 16 / SQL Server 2022 DDL
smplcache normalize examples/ecommerce_before.json --dialect postgres --format markdown
```

## Claim Hygiene Summary

1. **What this documentation supports:** Scoped engineering reference for the admitted SQL fragment (`SELECT ... FROM ... [INNER JOIN] ... WHERE ... GROUP BY`), exact integer/tuple group aggregation (`SUM`, `COUNT`, `AVG`), and `SortedMultisetExtremumCache` (`MIN`, `MAX`).
2. **What this documentation does not support:** Arbitrary SQL outside the admitted allowlist (such as `HAVING`, window functions, recursive CTEs, or `OUTER JOIN`), or unverified float arithmetic.
3. **Narrowest defensible claim:** Under the declared schema, primary-key uniqueness, and complete CDC/WAL before/after row images, `SmplCache` either updates cached group aggregates in place or emits a fail-closed `INVALIDATE` (`REREAD`) certificate when required evidence is missing or evicted.
4. **Next test needed:** Multi-node distributed lease coordination benchmarks under concurrent CDC partition rebalancing.
