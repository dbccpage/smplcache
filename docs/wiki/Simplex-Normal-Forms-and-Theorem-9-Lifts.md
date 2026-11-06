# Simplex Normal Forms and Theorem 9 Lifts

`smplcache.normal_forms` integrates exact relational normalization and incremental extremum maintenance directly into `SmplCache`.

## Why Schema Normalization Matters for Incremental Caching

When a denormalized table stores transitive or partial functional dependencies (for example, `orders_denorm` storing `customer_id -> customer_tier, region_id` and `region_id -> region_name` alongside high-velocity `status` and `amount_cents` columns), two failure modes arise:

1. **Co-Invalidation Gravity Wells:** High-velocity writes to `orders_denorm` intersect dimension queries that only care about `customer_tier` or `region_name`.
2. **Redundant `GROUP BY` Columns:** Queries written as `GROUP BY customer_id, customer_tier` force CDC payloads to carry `customer_tier` on every update even though `customer_id -> customer_tier` functionally determines it.

## Core Algorithms in `smplcache.normal_forms`

- **`attribute_closure(attrs, fds)`**: Linear-time fixed-point closure $X^+$ under a set of `FunctionalDependency` rules.
- **`minimal_cover(fds)`**: Canonical cover via singleton right-hand decomposition, extraneous left-hand attribute elimination, and transitive redundancy removal.
- **`candidate_keys(schema)`**: Exact minimal superkey enumeration partitioned by essential (source-only), middle, and dependent-only attributes.
- **`classify_normal_form(schema)`**: Evaluates `1NF`, `2NF` (no partial dependencies on proper subsets of candidate keys), `3NF`, `EKNF`, and `BCNF` (every non-trivial determinant is a superkey), returning human-readable violation diagnostics.
- **`synthesize_3nf(schema)`**: Bernstein's 3NF synthesis algorithm guaranteeing under the declared functional dependencies both dependency preservation and lossless join (by ensuring at least one synthesized relation contains a candidate key of the original relation).
- **`decompose_bcnf(schema)`**: Tsou-Fischer BCNF decomposition with explicit tracking of `is_dependency_preserving` and `lost_dependencies`.
- **`tableau_chase(schema, sub_relations)`**: Classical Tableau Chase algorithm over distinguished ($a_j$) and non-distinguished ($b_{i,j}$) variables. If a decomposition is lossy, `tableau_chase` constructs a concrete counterexample relation instance $I$ where the natural join of projections produces a spurious tuple not in $I$.
- **`generate_sql_ddl(tables, dialect)`**: Emits topologically sorted `CREATE TABLE` and foreign-key covering index DDL for PostgreSQL 16 (`postgres`) and SQL Server 2022 (`mssql`).
- **`detect_fd_redundant_group_cols(shape, fds)`**: Identifies `GROUP BY` columns functionally determined by other `GROUP BY` or equality-bound `WHERE` columns so query shapes can shrink their required CDC column footprint.
- **`SortedMultisetExtremumCache`**: Maintains `MIN` and `MAX` group aggregates incrementally in $O(\log W)$ time per group using a bounded sorted multiset window (`max_window_per_group`), falling back to `INVALIDATE` (`extremum_window_exhausted`) only when deletions exhaust the retained top-$W$ window while truncated elements remain in that group.

## Claim Hygiene Summary

1. **What this page supports:** Exact functional dependency closure, Bernstein 3NF synthesis, Tsou-Fischer BCNF decomposition, Tableau Chase losslessness checks, and bounded-window `MIN`/`MAX` maintenance on finite relational schemas.
2. **What this page does not support:** Automatic discovery of functional dependencies from dirty data without declared schema rules, or unbounded $O(1)$ deletion maintenance of `MIN`/`MAX` without retaining auxiliary order statistics.
3. **Narrowest defensible claim:** For any finite `RelationalSchema`, `synthesize_3nf` produces 3NF sub-relations whose lossless-join property is checked by `tableau_chase`, and `SortedMultisetExtremumCache` matches native SQL `MIN`/`MAX` on all `REPAIR` steps while failing closed to `INVALIDATE` when the bounded window is exhausted.
4. **Next test needed:** Evaluation of multi-valued dependencies (4NF) and join dependencies (5NF) on wide analytical star schemas.
