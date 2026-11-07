# CDC Evidence, Store Application, and Kernel Contracts

`AuthorityEffect: None`

This document consolidates and compresses the CDC evidence, aggregate admission, store adapter application, and kernel verification contracts integrated into `smplcache` from its earlier prototype.

---

## 1. CDC Evidence Completeness and Value Domain Guards

### 1.1 Decoder Evidence Boundaries (`PostgresLogicalStreamDecoder`)
`PostgresLogicalStreamDecoder` ([`smplcache/debezium.py`](../smplcache/debezium.py)) decodes PostgreSQL `wal2json` v1 row-array payloads (`columnnames`, `columnvalues`, `oldvalues`, `oldkeys`) and decoded `pgoutput` tuple dictionaries:
- **No fabricated before-images:** The decoder observes row fragments. It does not infer complete old rows from new rows, replica identity keys, or the union of before/after fragments.
- **Conservative completeness defaults:** Decoded events retain explicit `INSERT`, `UPDATE`, or `DELETE` operations and default `old_image_complete=False`, `new_image_complete=False`, and `changed_cols_complete=False`. Observed tuples do not establish a replica-identity or schema completeness witness on their own.
- **Batch LSN propagation (`CACHE-02`):** Top-level `wal2json` v1 batch watermarks (`nextlsn`, `lsn`, `xid`, `txId`) propagate into each decoded `WriteEvent.event_id` (`wal_{lsn}_{idx}`) and `boundary_clock`, preventing cross-batch receipt key collisions on `"wal_None_0"`.
- **Malformed payload refusal:** Non-array column/value lists, mismatched column/value lengths, duplicate or empty column names, contradictory `kind`/`action` labels, contradictory `oldkeys`/`oldvalues`, `INSERT` carrying an old image, or `DELETE` carrying a new image raise `ValueError`.

### 1.2 Explicit Completeness Declarations on `WriteEvent`
[`WriteEvent`](../smplcache/smplcache.py) accepts optional keyword flags `operation`, `old_image_complete`, `new_image_complete`, and `changed_cols_complete`:
- **Exact boolean typing:** All completeness flags (`old_image_complete`, `new_image_complete`, `changed_cols_complete`, `schema_change`) require exact `bool` values (`True` or `False` when provided); non-booleans such as strings or integers raise `TypeError`.
- **Per-side completeness checks:** When completeness flags are supplied on a same-relation event:
  - `UPDATE` requires `old_image_complete=True`, `new_image_complete=True`, `changed_cols_complete=True`, and all shape fingerprint columns present in both `old` and `new` maps independently.
  - `INSERT` requires `new_image_complete=True`, an empty `old` map, and all shape fingerprint columns present in `new`.
  - `DELETE` requires `old_image_complete=True`, an empty `new` map, and all shape fingerprint columns present in `old`.
  - `INSERT` and `DELETE` change row membership and never take the `UPDATE` disjoint-column preservation path.

### 1.3 Recursive JSON Type and Depth Guards
Row image comparisons in [`_values_equal`](../smplcache/smplcache.py) and [`_supported_row_value`](../smplcache/smplcache.py) enforce strict type and structural checks:
- **Type-sensitive equality:** `1` vs `True`, `0` vs `False`, and `1` vs `1.0`—whether at the top level or nested inside dicts/lists—are treated as distinct values and never disappear behind Python's `1 == True` or `1 == 1.0` equality.
- **Supported value domain and depth bound:** Row values must be built from `None`, `bool`, `int`, `str`, finite `Decimal`, finite `float`, `list`/`tuple`, and `str`-keyed `dict` up to a maximum nesting depth of `64`. Nonfinite floats (`NaN`, `inf`, `-inf`), sets, arbitrary objects, reference cycles, or nesting deeper than `64` levels fail closed to `DecisionKind.INVALIDATE` (`unsupported_row_value`).

---

## 2. Aggregate Shape Admission and Group-Presence Semantics

### 2.1 Admitted Single-Block and Multi-Table Join Shapes
- **Single-table shapes (`QueryShape`):** Extracted via the `sqlglot` AST allowlist parser [`parse_sql`](../smplcache/extractor.py) or constructed directly with explicit `relation`, `agg_fn` (`sum`, `count`, `count_star`, `avg`), `agg_col`, `group_cols`, and `predicate` (`Predicate`). Unaggregated multi-table `JOIN` queries passed to single-table `check_cdc_evidence` are refused fail-closed with `DecisionKind.UNSUPPORTED` (`unsupported_sql: Join`), avoiding false `unrelated_relation` preservation when rows in a joined table are modified or deleted (`CACHE-01`).
- **Multi-table equijoin shapes (`MultiTableJoinShape`):** Extracted via [`parse_join_sql`](../smplcache/extractor.py) and certified via [`certify_join_event`](../smplcache/join.py), which tracks all participating base and joined relations so updates or deletes on any joined table are evaluated against the join boundary.

### 2.2 Seeded Group State vs. Totals-Only Caches
- **Sufficient statistics per group:** [`AggregateCache.from_rows`](../smplcache/smplcache.py) and [`AggregateCache.from_group_state`](../smplcache/smplcache.py) track `(sum_val, non_null_count, row_count)` per group. When a row exits a predicate, moves groups, or is deleted and the group's `row_count` reaches `0`, the empty group is evicted cleanly while non-empty zero-sum groups (e.g., `2` and `-2`) remain present.
- **Totals-only refusal on membership removal:** A cache initialized from raw totals alone (`AggregateCache(values={...})`) cannot distinguish an empty group from a non-empty group whose values cancel to zero. `process_event` therefore refuses membership-removal repairs on unseeded caches and invalidates with `missing_aggregate_state`, clearing `cache.values`.

---

## 3. Store Adapters and Shape Registry Application (`store_adapter.py`)

### 3.1 Effective Eviction and Receipt Custody
[`InMemoryStoreAdapter`](../smplcache/store_adapter.py) and [`RedisStoreAdapter`](../smplcache/store_adapter.py) implement [`SmplCacheStore`](../smplcache/store_adapter.py):
- **Repair downgrade without an executor:** Store adapters receive a proposed `Decision` without row values or a delta repair executor. A proposed `DecisionKind.REPAIR` is therefore applied as an effective `DecisionKind.INVALIDATE` with `reason_code="repair_execution_unavailable"` and `repair_program=None`.
- **Evict-then-verify postcondition:** On `INVALIDATE` or `UNSUPPORTED`, the adapter deletes the key, verifies via readback that the key is absent, and only then records the receipt. Delete exceptions, readback exceptions, a retained value after delete, or an unacknowledged Redis receipt write raise [`StoreApplicationError`](../smplcache/store_adapter.py) without recording a success certificate.
- **Pre-mutation receipt cap (`A-45`):** `InMemoryStoreAdapter` enforces `max_certificates` (default `DEFAULT_MAX_CERTIFICATES = 100_000`). Once full, `apply_certified_decision` raises `StoreApplicationError` *before* mutating the store so no applied decision is left without a receipt.
- **Detached receipt immutability (`CACHE-03`):** Both stored and returned `Certificate` instances are deep-copied so caller mutations to `Decision.certificate` or its evidence lists cannot alter retained receipts.
- **Conflicting duplicate receipt refusal (`CACHE-02`):** Re-applying an identical `(key, event_id)` decision is idempotent, but attempting to record a conflicting `(decision_kind, reason_code)` under an existing `(key, event_id)` raises `StoreApplicationError` before mutating the store.

### 3.2 `RegisteredShapesRegistry`
[`RegisteredShapesRegistry`](../smplcache/store_adapter.py) binds registered `QueryShape` and `MultiTableJoinShape` instances to an `InMemoryStoreAdapter`:
- Counts effective applied decisions (`preserve`, `repair`, `invalidate`, `unsupported`) while preserving `proposed_decision` in each emitted result dictionary.
- Failed store applications raise `StoreApplicationError` without incrementing `decision_counts`.

---

## 4. Condensed Kernel Verification Contracts

The following architectural contracts from the verification kernel govern how `smplcache` structures certificates, boundaries, obstructions, lifts, and deterministic replay:

| Kernel Contract | Core Invariant in `smplcache` |
| :--- | :--- |
| **Certificate Kernel** (`Certificate`) | A certificate binds a decision to checkable evidence (`shape`, `event_id`, `relation`, `decision_kind`, `reason_code`, `required_evidence`, `available_evidence`, `repair_program`, `boundary_clock`, `operation`, completeness flags). A certificate is checkable structure, not operational authority. |
| **Boundary Kernel** (`QueryShape`, `MultiTableJoinShape`) | A shape boundary declares the carrier relation(s), predicate columns, group keys, aggregate column/function, projection columns, and security columns. Any event outside the declared boundary preserves (`unrelated_relation` or `disjoint_columns`); any uncertified boundary crossing invalidates. |
| **Obstruction Kernel** (`DecisionKind.INVALIDATE` / `UNSUPPORTED`) | Missing before/after row evidence, unparsed predicates, `MIN`/`MAX` without auxiliary state, or capacity eviction tombstones are recorded as explicit, machine-readable `reason_code` obstructions rather than silent heuristic fallbacks. |
| **Lift Kernel** (`JoinAuxState`, `SortedMultisetExtremumCache`, `normal_forms`) | Restoring repairability when a base shape is obstructed requires an explicit carrier extension (e.g., maintaining `JoinAuxState` primary/foreign-key indices for joins, `SortedMultisetExtremumCache` for bounded `MIN`/`MAX`, or 3NF/BCNF schema normalization in `smplcache.normal_forms`). |
| **Replay Kernel** (`WalStreamProcessor`, `boundary_clock`) | Given an identical initial cache seed and ordered CDC/WAL event sequence, certification and state transitions are deterministic and traceable via `event_id` and `boundary_clock`. |

---

## 5. Claim Discipline Summary

1. **What It Supports:** Supports fail-closed CDC completeness checking, recursive JSON type/depth validation (`<= 64`), `wal2json` v1 and `pgoutput` tuple decoding with batch LSN propagation, bounded-receipt store eviction adapters (`InMemoryStoreAdapter`, `RedisStoreAdapter`), and deterministic decision auditing checked by unit and SQLite oracle tests.
2. **What It Does Not Support:** Does not authenticate upstream PostgreSQL publishers, does not implement the binary `pgoutput` wire protocol, does not provide distributed cross-process transactions across external Redis instances, and confers no execution authority (`AuthorityEffect: None`).
3. **Narrowest Defensible Claim:** Within the declared `QueryShape` and `MultiTableJoinShape` fragments and in-process store adapter model, incomplete CDC row images, nested JSON type mismatches, and unexecutable store repairs fail closed to `INVALIDATE` or raise `StoreApplicationError` when eviction or receipt custody fails.
4. **Next Test Needed:** End-to-end Redis cluster failover and concurrent writer race tests under network partition injection.
