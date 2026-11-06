# Benchmarks and Live Container Verification

`SmplCache` includes both deterministic synthetic benchmarks (`smplcache.benchmark`) and live containerized database verification (`tests/test_live_containers.py` and `tests/test_substrate_cold_rebuild.py`).

## Cold-Rebuild SQL Substrate (`smplcache.substrate`)

`ColdRebuildSubstrate` (`ColdRebuildAdapter`) connects `SmplCache` to embedded SQLite (`sqlite3`) and DuckDB (`duckdb`) engines for cold-miss recovery:

- Executes grouped SQL queries (`SELECT <group_cols>, COALESCE(SUM(...), 0), COUNT(...), COUNT(*) ... GROUP BY ...`) rather than pulling full base tables into Python when recovering a single-table or pre-aggregated `AggregateCache`.
- Registers an exact `Decimal` custom aggregate (`SMPL_DEC_SUM`) on SQLite connections so `SUM` and `AVG` over `Decimal` columns match Python `Decimal` arithmetic without IEEE-754 binary64 float drift.
- Seeds `AggregateCache.from_group_state(shape, group_stats)` with `_from_rows = True`, enabling subsequent CDC events with complete before/after images to execute `REPAIR` (`FIX IN PLACE`) immediately after a cold rebuild.

## Live Containerized Database Suite (`.github/workflows/test.yml`)

In GitHub Actions (`live-databases` job with `REQUIRE_LIVE_DB_TESTS=1`), `tests/test_live_containers.py` exercises three production database engines:

1. **PostgreSQL 16 (`postgres:16-alpine` via `psycopg`)**:
   - Synthesizes 3NF/BCNF tables and foreign-key indexes via `generate_sql_ddl(..., dialect="postgres")` and executes the DDL on the live PostgreSQL 16 server.
   - Streams `BEGIN` / `DATA` / `COMMIT` WAL records through `WalStreamProcessor` and `SharedJoinAuxPool`, verifying `boundary_clock` LSN watermarks and 100% agreement between `SmplCache` (`PRESERVE`, `REPAIR`, `INVALIDATE` -> cold rebuild) and live PostgreSQL `GROUP BY` queries.
2. **SQL Server 2022 (`mcr.microsoft.com/mssql/server:2022-latest` via `pymssql`)**:
   - Executes topologically sorted T-SQL 3NF DDL (`generate_sql_ddl(..., dialect="mssql")`) on live SQL Server 2022.
   - Verifies single-table `AggregateCache` (`SUM`/`AVG`) and `SortedMultisetExtremumCache` (`MIN`/`MAX`) maintenance and bounded-window exhaustion recovery against live T-SQL `GROUP BY` queries.
3. **MongoDB 7 (`mongo:7` via `pymongo`)**:
   - Exercises document-to-relational projection and oplog-style `INSERT`/`UPDATE`/`DELETE` mutations through `AggregateCache` and `SortedMultisetExtremumCache`, verifying agreement against live MongoDB `$match` + `$group` aggregation pipelines.

## Claim Hygiene Summary

1. **What this page supports:** End-to-end oracle agreement between `SmplCache` incremental maintenance / cold-rebuild recovery and live PostgreSQL 16, SQL Server 2022, MongoDB 7, SQLite, and DuckDB queries on the tested workloads.
2. **What this page does not support:** Distributed multi-region throughput projections or sub-millisecond tail latency claims under network partition faults.
3. **Narrowest defensible claim:** Across all tested single-table and multi-table join workloads in `tests/test_live_containers.py` and `tests/test_substrate_cold_rebuild.py`, `SmplCache` cached group values agree 100% with live database `GROUP BY` results after every `PRESERVE`, `REPAIR`, and `INVALIDATE` cold-rebuild step.
4. **Next test needed:** Sustained high-throughput Debezium Kafka Connect replication slot soak tests under concurrent DDL migrations.
