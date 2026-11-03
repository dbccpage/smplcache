# SmplCache Live: M4 Adversarial Verification & Review Log

Reviewed revision: `feature/upgrades-1-3-join-wtinylfu-shared-pool` (base engine commit `bbc5d703a29751a23ad1782a31e92abc250ea04c`).

## 1. Adversarial Findings Fixed During Implementation

| ID | Category | Finding / Reproduction | Resolution | Verification |
| --- | --- | --- | --- | --- |
| D-1 | Schema Binding | In `missing_evidence` Step 4, the query initially used unqualified `FROM orders` while the cross-schema event wrote to `tenant_archive.orders`. Because `bbc5d70` rejects ambiguous qualified-vs-unqualified table bindings fail-closed (`ambiguous_relation`), Step 4 invalidated rather than demonstrating cross-schema isolation. | Bound the `missing_evidence` query and its primary events explicitly to `main.orders`, so writes to `tenant_archive.orders` are certified `unrelated_relation` (`Preserve`). Added a separate regression (`test_ambiguous_unqualified_binding_invalidates_rather_than_guessing`) to verify that unqualified `orders` against `main.orders` invalidates. | `test_missing_evidence_wal_scenario_and_cross_schema_isolation` and `test_ambiguous_unqualified_binding_invalidates_rather_than_guessing` |
| D-2 | Join Reason Code Mapping | `_build_customers_and_regions_steps` initially mapped `join_dimension_update` in `PLAIN_LANGUAGE_REASONS`, whereas `process_join_event` emits `dimension_update` and `fk_reparent`. | Aligned `PLAIN_LANGUAGE_REASONS` and acceptance assertions with the exact reason codes emitted by `smplcache/join.py` (`dimension_update`, `fk_reparent`). | `test_customers_and_regions_join_fanout_and_transactions` |
| D-3 | Rebuild Latency Under Tight Eviction Cap | Running `customers_and_regions` with `dataset_size=1000` and `aux_max_total_rows=60` under `eviction_policy="wtinylfu"` recomputed graph multiplicity across resident rows on every single insert during 3-table rebuilds, taking ~20s per run. | Switched the interactive demo's `JoinAuxState` eviction policy to `"lru"`, reducing the 1,000-row `cap=60` run with 4 multi-table rebuilds from ~20s to `<150ms` while preserving deterministic eviction tombstones (`evicted_join_auxiliary_key`). | `test_customers_and_regions_lowered_capacity_eviction_and_honest_counters` |
| D-4 | UI Sample Row Region Column | In `demo/web/src/main.ts`, `renderSampleOrders` initially displayed `row.joined_region_name || row.region` for all scenarios, causing Order #105 in `live_revenue` to display its customer's joined region (`Latin America`) instead of `orders.region` (`Pilot Sandbox`). | Scoped `row.joined_region_name` to `customers_and_regions` and displayed `row.region` for single-table scenarios (`live_revenue`, `missing_evidence`). | Verified in live DOM snapshot via Chrome DevTools MCP |
| D-5 | Windows Optional Native Rollup Dependency | `vite build` failed on Windows Node 25 due to npm's optional dependency bug with `@rollup/rollup-win32-x64-msvc`. | Replaced Rollup with a zero-native-binary `demo/web/build.mjs` builder using `tsc` (`--noEmit false`) + SHA-256 content-hashed asset bundling. | `npm run build` and `test_spa_root_and_static_assets_served` |

## 2. Independent Oracle and Counter Audit

1. **Oracle independence:** `_IndependentSqliteVerifier` in [`demo/scenarios/runner.py`](../../demo/scenarios/runner.py) executes the scenario's SQL string directly against an in-memory `sqlite3` connection using SQLite's native integer `SUM` and `COUNT`. It never calls `AggregateCache`, `rebuild_join_cache`, or `_row_contribution` to compute expected answers.
2. **Snapshot alignment:** Every rebuild callback (`_rebuild_at_current_boundary`) reads `SELECT * FROM <table> ORDER BY id ASC` from the SQLite connection *at the exact committed boundary being processed*, never from a preloaded future state.
3. **Honest source-query accounting:** In `customers_and_regions` with `aux_max_total_rows=60`, each 3-table rebuild charges `3` queries to `smplcache_source_queries` while the baseline charges `1` joined query to `baseline_source_queries`, resulting in `source_queries_avoided = -5` at the end of the run (displayed without clamping in both the API and UI).
4. **Fault injection:** `test_fault_injection_corrupt_value_triggers_mismatch_and_halts_run` injects a corrupted value (`Decimal(-999999)`) at boundary 2 and verifies that the run halts immediately (`len(boundaries) == 2`), sets `summary.status == "mismatch"`, and surfaces `first_failing_boundary == 2`.

## 3. Claim Hygiene Summary

1. **What it supports:** Supports end-to-end verification of the SmplCache Live web API, UI bundle, and scenario runner against the repaired `bbc5d70` engine and native SQLite oracle across 20 acceptance and adversarial tests.
2. **What it does not support:** Does not replace live external database replication testing or prove soundness outside the tested SQL fragment and synthetic fixtures.
3. **Narrowest defensible claim:** All 20 acceptance tests in `demo/tests/test_demo_acceptance.py` and all 129 engine tests in `tests/` pass (`149 passed`), with `mypy` clean across all 15 source files and `black --check` clean.
4. **Next test needed:** Live Render deployment verification once Render credentials/Blueprint connection and the `smplcache.anomalon.com` DNS CNAME record are applied.
