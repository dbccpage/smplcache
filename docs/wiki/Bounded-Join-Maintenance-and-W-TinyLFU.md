# Bounded Join Maintenance and W-TinyLFU

Maintaining acyclic `INNER` and `LEFT` equi-joins (`SELECT ... FROM orders JOIN customers ON ... LEFT JOIN regions ON ...`) incrementally requires looking up partner rows in the joined tables when a fact or dimension row changes. `smplcache.join` bounds the memory footprint of this auxiliary state while preserving fail-closed correctness.

## $O(1)$ Primary-Key and Secondary Foreign-Key Indexing

`JoinAuxState` maintains two complementary lookup structures:

1. **Primary-Key Table Store (`table_rows`)**: Maps `relation -> {pk_val -> projected_row_dict}`, storing only the minimum required column projection (`table_required_evidence`) for each relation in the join tree.
2. **Secondary Join-Key Index (`secondary_join_index`)**: For every non-PK join key `(relation, col)` used in an equi-join condition, `JoinAuxState` maintains `{(rel, col): {col_val: set[pk_val]}}` alongside `fk_index`.

During `evaluate_fact_row_contributions`, joining on a table's primary key uses an $O(1)$ dictionary lookup (`table_rows[rel].get(pk)`), even when evaluating a candidate override row (`override_rel == r_rel`). Joining on a non-PK column uses the $O(1)$ secondary index instead of scanning the table.

## W-TinyLFU Bounded Eviction and Tombstone Safety

When `max_total_rows` is configured on `JoinAuxState` or `SharedJoinAuxPool`:

- **Segmented Admission (Window + Probation + Protected):** Newly inserted primary keys enter the Window segment, promote on reuse, and compete via Count-Min Sketch frequency estimation (4-bit counters with periodic halving) against Probation victims.
- **Tombstone Tracking (`evicted_pks`):** When a primary key is evicted from `table_rows`, its `(relation, pk)` pair is recorded in `evicted_pks`.
- **Fail-Closed Eviction Checks:**
  - If a subsequent `UPDATE` or `DELETE` arrives for an evicted PK, `JoinAuxState` refuses to resurrect a partial CDC payload into `table_rows` (including in `SharedJoinAuxPool.process_event` when `DecisionKind.PRESERVE` is emitted for disjoint columns) and emits `INVALIDATE` (`evicted_pk_in_join_aux`) for any shape that requires that row's before-image.
  - If an incoming row's foreign key references an evicted partner PK in `evicted_pks`, `certify_join_event` emits `INVALIDATE` (`evicted_fk_partner_in_join_aux`) rather than silently dropping the join contribution.
- **Bounded Tombstone Cap (`max_tombstones`):** If the number of evicted PK tombstones exceeds `max_tombstones`, `JoinAuxState` sets `tombstone_overflowed = True` and latches all dependent join caches to `INVALIDATE` (`tombstone_capacity_exceeded`) until the next `rebuild()`.

## Claim Hygiene Summary

1. **What this page supports:** Bounded-memory incremental maintenance for acyclic `INNER` and `LEFT` equi-joins with $O(1)$ PK/FK lookups, W-TinyLFU eviction, and fail-closed tombstone tracking.
2. **What this page does not support:** Cyclic hypergraph joins (triangles), `RIGHT`/`FULL OUTER` joins, or inequality join conditions (`ON a.x < b.y`).
3. **Narrowest defensible claim:** Under unique primary keys and foreign-key integrity, `JoinAuxState` and `SharedJoinAuxPool` either compute the exact join group delta in place or emit `INVALIDATE` (`REREAD`) whenever a required join row or foreign-key partner has been evicted or tombstone capacity has overflowed.
4. **Next test needed:** High-skew Zipfian foreign-key fan-out stress tests comparing W-TinyLFU hit rates against plain FIFO and LRU under tight memory caps.
