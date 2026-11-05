## What this app does

1. SmplCache Live is an incremental query cache maintenance engine and interactive web application (`/` and `/lab/`).
2. It parses SQL aggregate queries (`SUM`, `COUNT`, `MIN`, `MAX`, `AVG`) over single tables or multi-table `INNER`/`LEFT` joins (`orders`, `customers`, `regions`).
3. From each parsed query, it extracts the query's dependency set: aggregate columns, filter predicates, join keys, and `GROUP BY` columns.
4. As row-level write events (`INSERT`, `UPDATE`, `DELETE` or Debezium/WAL change records) arrive, it classifies each change into one of three moves: `KEEP`, `FIX IN PLACE`, or `REREAD`.
5. It chooses `KEEP` (`PRESERVE`) when the write touches only columns outside the query's dependency set, fails the `WHERE` filter before and after, or touches a dimension row with zero matching join rows.
6. It chooses `FIX IN PLACE` (`REPAIR`) for additive aggregates (`SUM`, `COUNT`) when the change log and bounded join lookup rows supply the old and new values needed to add or subtract the exact delta per group.
7. It chooses `REREAD` (`INVALIDATE`) with an explicit reason code whenever old row values are missing from the log, a bounded join lookup row was evicted, a `MIN`/`MAX` extremum may have shifted, or a schema change occurs.
8. On every step in the demo and test suite, it runs an independent SQLite ground-truth query (`SQLiteOracle` / `SQLiteJoinOracle`) and checks that the cached answer after the move matches the database answer.
9. The front page (`/`) presents a single-pane interactive loop over `SELECT r.name, SUM(o.amount_cents) FROM orders o JOIN customers c ON o.customer_id = c.id JOIN regions r ON c.region_id = r.id WHERE o.status = 'paid' GROUP BY r.name` with four representative writes and a query-count comparison against Redis TTL caching and PostgreSQL `REFRESH MATERIALIZED VIEW`.
10. The `/lab/` page runs multi-step seeded scenarios (`live_revenue`, `customers_and_regions`, `missing_evidence_wal`) via a FastAPI backend (`/api/v1/run`, `/api/v1/sql/check`) with capacity eviction and fault-injection controls.

Code vs. README disagreement: `README.md` describes only the initial single-table `smplcache` CLI package (`QueryShape`, `WriteEvent`, `process_event`, and `smplcache replay/check/matrix`), whereas the codebase also implements a multi-table join maintenance engine (`smplcache/join.py`) with bounded auxiliary lookup state (`JoinAuxState` with `fifo`, `lru`, `lfu`, `w_lfu`, and `wtinylfu` eviction), a Debezium/WAL stream processor (`smplcache/debezium.py`), and the FastAPI + TypeScript web application (`demo/app.py`, `demo/scenarios/runner.py`, `demo/web/`).

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

## Not done

- Section 4.1 (SmplCache Live): All 7 items are complete.
- Section 4.2 (Audit), Section 4.3 (Simplex-NF Studio), and Section 4.4 (Owed): Not started (0%). Blocked because the local checkout paths in Section 1 of the task prompt were left as `<path>` placeholders and only the `smplcache` repository exists in the local workspace (`c:\Users\a343o\source\repos\smplcache\smplcache`).

## Decisions for the owner

1. **Where are the local checkouts for Audit (`anomalon`), Simplex-NF Studio (`simplex-normal-forms`), and Owed (`owed_ai`)?**
   - **Option A (Recommended):** Fill in the three local checkout paths in Section 1 (or clone those repositories into the workspace) and re-run the overnight agent for Sections 4.2–4.4.
   - **Option B:** Run the overnight agent separately inside each of the other three repository workspaces.
2. **Should `README.md` be updated to describe the multi-table join engine (`smplcache/join.py`) and web demo (`demo/`)?**
   - **Option A (Recommended):** Expand `README.md` to cover `parse_join_sql`, `JoinCacheManager`, `JoinAuxState`, and the `/` and `/lab/` web application so the README matches the code.
   - **Option B:** Keep `README.md` scoped to the core single-table CLI and document the multi-table join engine and web app in a separate `demo/README.md`.

## Found but out of scope

- `fastapi.testclient` emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` during test runs.
- The repository has no `.gitattributes` file normalizing line endings, causing Git on Windows to emit `LF will be replaced by CRLF` warnings when staging touched files.
