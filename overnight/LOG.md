## What this app does

1. SmplCache Live is an incremental SQL aggregate cache engine (`smplcache/`) and interactive web application (`/` and `/lab/`).
2. It parses single-table and multi-table `INNER`/`LEFT` join aggregate queries (`SUM`, `COUNT`, `AVG`) and rejects unsupported SQL shapes fail-closed.
3. From each admitted query AST, it derives each participating table's dependency set: read columns, join keys, filter columns, and `GROUP BY` columns.
4. When a row-level write (`INSERT`, `UPDATE`, `DELETE`) or Debezium/WAL transaction record arrives, it classifies the change into one of three moves: `KEEP`, `FIX IN PLACE`, or `REREAD`.
5. It chooses `KEEP` when the write touches only columns outside the query's dependency set, fails the `WHERE` filter before and after, rolls back, or touches a join dimension row with zero matching fact rows.
6. It chooses `FIX IN PLACE` when the change log and resident join lookup rows supply both old and new values needed to add or subtract the per-group difference in memory with zero database queries.
7. It chooses `REREAD` with an explicit reason code whenever required before-image values are missing, a needed join lookup row was evicted under capacity limits, or the cache is latched invalid, withholding stale answers until reread.
8. On every committed step in the demo and test suite, an independent SQLite database recomputes the grouped query from scratch and checks that the cached answer matches.
9. The front page (`/`) shows a single-pane interactive loop over `SELECT r.name, SUM(o.amount_cents) FROM orders o JOIN customers c ON o.customer_id = c.id JOIN regions r ON c.region_id = r.id WHERE o.status = 'paid' GROUP BY r.name`, while `/lab/` runs full seeded scenarios (`live_revenue`, `customers_and_regions`, `missing_evidence`) via FastAPI (`/api/runs`, `/api/scenarios`, `/api/version`, `/healthz`).
10. Code vs. README disagreement: `README.md` covers the single-table CLI and library, whereas the code also implements the multi-table join engine (`smplcache/join.py`), Debezium/WAL processor (`smplcache/debezium.py`), and FastAPI + TypeScript web app (`demo/api/app.py`, `demo/scenarios/runner.py`, `demo/web/`).

## Baseline

- [front_1280x800.png](baseline/front_1280x800.png)
- [front_390x844.png](baseline/front_390x844.png)
- [lab_1280x800.png](baseline/lab_1280x800.png)
- [lab_390x844.png](baseline/lab_390x844.png)
- [browser_console.txt](baseline/browser_console.txt)
- [test_suite_output.txt](baseline/test_suite_output.txt)
- [first_result_timing.txt](baseline/first_result_timing.txt)

## Changed

- Record baseline screenshots, console output, timings, and test output — proved by `overnight/baseline/test_suite_output.txt`, `overnight/baseline/front_1280x800.png`, `overnight/baseline/front_390x844.png`, `overnight/baseline/lab_1280x800.png`, `overnight/baseline/lab_390x844.png`
- Derive join classification from query dependency set and prevent KEEP on non-PK join keys — proved by `tests/regressions/test_join_key_boundary.py::test_non_pk_join_columns_customers_region_id_and_orders_customer_id_never_classify_keep`
- Add 1,000-iteration seeded join property test and preserve evicted aux rows on KEEP — proved by `tests/test_join_property_1000.py::test_property_1000_seeded_random_datasets_and_change_streams` and `tests/test_join_property_1000.py::test_disjoint_column_partial_update_on_evicted_row_does_not_corrupt_aux_state`
- Extract shared three-move module and expose step change SQL and arithmetic — proved by `demo/tests/test_demo_acceptance.py::test_shared_moves_module_and_copy_record_and_url_roundtrip_contracts`
- Pre-render front-page rows, add replayable record and URL round-trip, align `/lab/`, and fix 390px phone layout — proved by `demo/tests/test_demo_acceptance.py::test_front_page_prerendered_rows_and_no_change_yet_before_js`, `overnight/after_front_1280x800.png`, `overnight/after_front_390x844.png`, `overnight/after_lab_1280x800.png`, `overnight/after_lab_390x844.png`
- Fix URL round-trip seed/size state, step change SQL, fail-closed move classification, and mobile header nav — proved by `tests/regressions/test_join_key_boundary.py`, `demo/tests/test_demo_acceptance.py::test_shared_moves_module_and_copy_record_and_url_roundtrip_contracts`, `overnight/after_front_390x844.png`

## Not done

- None. All 7 items in Section 4.1 (SmplCache Live) are complete.

## Decisions for the owner

1. **Should `README.md` be updated to describe the multi-table join engine (`smplcache/join.py`) and web demo (`demo/`)?**
   - **Option A (Recommended):** Expand `README.md` to cover `parse_join_sql`, `JoinCacheManager`, `JoinAuxState`, and the `/` and `/lab/` web application so the README matches the code.
   - **Option B:** Keep `README.md` scoped to the core single-table CLI and document the multi-table join engine and web app in a separate `demo/README.md`.

## Found but out of scope

- `fastapi.testclient` emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` during test runs.
- The repository has no `.gitattributes` file normalizing line endings, causing Git on Windows to emit `LF will be replaced by CRLF` warnings when staging touched files.
