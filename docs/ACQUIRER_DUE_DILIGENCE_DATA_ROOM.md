# SmplCache + Simplex Normal Forms: Technical Due Diligence Data Room

**Repository:** `smplcache`  
**License:** Apache-2.0  
**Runtime:** Python `>=3.11` (Core runtime dependency: `sqlglot>=25.0.0`)

---

## 1. Executive Technical Summary

`SmplCache` is a boundary-certified incremental query cache and relational schema normalization engine. It addresses two coupled problems in read-heavy transactional and analytical systems:

1. **Boundary-Certified Incremental Maintenance (`smplcache.smplcache`, `smplcache.join`, `smplcache.debezium`, `smplcache.substrate`)**:
   Evaluates CDC/WAL write streams (`INSERT`, `UPDATE`, `DELETE`, transactional `BEGIN`/`COMMIT`/`ROLLBACK`) against admitted SQL query shapes (`SELECT ... FROM ... [INNER JOIN] ... WHERE ... GROUP BY`). Every event produces an auditable `Certificate` classifying the transition into:
   - `PRESERVE` (`KEEP` — `0` DB queries),
   - `REPAIR` (`FIX IN PLACE` — `0` DB queries, exact additive or bounded-window group update), or
   - `INVALIDATE` (`REREAD` — fail-closed cache invalidation when required evidence is missing or evicted).
2. **Relational Normalization & Schema Lift Engine (`smplcache.normal_forms`, `smplcache.cli`)**:
   Computes exact functional dependency closures ($X^+$), canonical minimal covers, candidate keys, normal-form classifications (`1NF`/`2NF`/`3NF`/`EKNF`/`BCNF`), Bernstein 3NF synthesis, Tsou-Fischer BCNF decomposition, Tableau Chase losslessness verification (with concrete spurious-row counterexample generation), and topologically sorted PostgreSQL 16 / SQL Server 2022 migration DDL.

---

## 2. Module Inventory & Architecture Boundaries

| Module | Responsibility | External Dependencies |
| :--- | :--- | :--- |
| `smplcache/smplcache.py` | Single-table AST parser (`parse_sql`), `QueryShape`, `WriteEvent` (`boundary_clock`), `Certificate`, `certify_event`, `AggregateCache` (`from_rows`, `from_group_state`). | `sqlglot` |
| `smplcache/join.py` | Acyclic inner-join AST parser (`parse_join_sql`), `JoinAuxState` ($O(1)$ PK + secondary FK indices, W-TinyLFU eviction, bounded `max_tombstones`), `SharedJoinAuxPool`, `JoinCacheManager`. | `sqlglot` |
| `smplcache/normal_forms.py` | `FunctionalDependency`, `attribute_closure`, `minimal_cover`, `candidate_keys`, `classify_normal_form`, `synthesize_3nf`, `decompose_bcnf`, `tableau_chase`, `generate_sql_ddl`, `SortedMultisetExtremumCache`, `detect_fd_redundant_group_cols`. | Standard library only |
| `smplcache/debezium.py` | `parse_debezium_event` (PostgreSQL `pgoutput`, MySQL, SQL Server, MongoDB oplog/change-stream envelopes), `WalStreamProcessor` transactional staging buffer, LSN/txId watermark propagation. | Standard library only |
| `smplcache/substrate.py` | `ColdRebuildSubstrate` adapter for `sqlite3` and `duckdb` executing cold-miss `GROUP BY` queries and seeding `AggregateCache.from_group_state()` and `JoinCacheManager`. | `sqlite3` (stdlib), `duckdb` (optional) |
| `smplcache/cli.py` | CLI commands: `analyze`, `topomap`, `doctor` (SQL AST anti-pattern + FD-redundant `GROUP BY` inspection), `compare` (3NF/BCNF lift + Tableau Chase), and `normalize`. | `sqlglot` |
| `demo/api/app.py` & `demo/web/` | FastAPI + TypeScript interactive verification lab (`/`, `/lab/`, `/api/runs`, `/api/normalize`) with independent SQLite oracle checking. | `fastapi`, `pydantic`, `uvicorn` |

**One-Way Dependency Rule:** Core `smplcache/*.py` modules never import from `demo`, `fastapi`, `starlette`, `pydantic`, or `uvicorn` (enforced by `demo/tests/test_demo_acceptance.py::test_one_way_dependency_boundary_core_never_imports_demo_or_web`).

---

## 3. Verification Matrix & Reproducibility

All verification suites run deterministically via `pytest` and GitHub Actions (`.github/workflows/test.yml`):

```bash
# 1. Code formatting & static type checking
python -m black --check .
python -m mypy smplcache demo

# 2. Full unit, regression, property-based (Hypothesis), CLI, substrate, and demo acceptance suite
python -m pytest -q

# 3. Live containerized database verification (PostgreSQL 16, SQL Server 2022, MongoDB 7)
REQUIRE_LIVE_DB_TESTS=1 python -m pytest tests/test_live_containers.py -v
```

### Key Test Suites

- **`tests/test_smplcache.py` & `tests/test_hypothesis_fuzz.py`**: Single-table certification, `AggregateCache.from_group_state`, `boundary_clock` propagation, and stateful Hypothesis oracle comparison.
- **`tests/test_join.py` & `tests/regressions/test_pool_evicted_partial_update.py`**: Multi-table acyclic inner joins, $O(1)$ PK and secondary FK lookups, W-TinyLFU eviction, evicted PK partial-update safety, and `max_tombstones` overflow latching.
- **`tests/test_normal_forms_integration.py`**: Functional dependency closure, minimal cover, candidate keys, 1NF/2NF/3NF/BCNF classification, Bernstein 3NF synthesis, Tsou-Fischer BCNF decomposition, Tableau Chase losslessness and lossy counterexample generation, `SortedMultisetExtremumCache`, and `detect_fd_redundant_group_cols`.
- **`tests/test_cli_doctor_and_normalize.py`**: CLI `analyze`, `topomap`, `doctor`, `compare`, and `normalize` across text, Markdown, and JSON outputs.
- **`tests/test_substrate_cold_rebuild.py`**: End-to-end `KEEP` -> `FIX IN PLACE` -> `REREAD` cold rebuild via `ColdRebuildSubstrate` (`sqlite3` and `duckdb`) -> `FIX IN PLACE`.
- **`tests/test_live_containers.py`**: Live execution of generated 3NF/BCNF DDL and incremental CDC maintenance against containerized PostgreSQL 16, SQL Server 2022, and MongoDB 7 in GitHub Actions.

---

## 4. Claim Hygiene Summary

1. **What this artifact supports:**
   - Deterministic `PRESERVE` / `REPAIR` / `INVALIDATE` classification and exact integer/tuple group maintenance (`SUM`, `COUNT`, `AVG`) plus bounded-window `MIN`/`MAX` maintenance (`SortedMultisetExtremumCache`) for the admitted single-block SQL fragment (`SELECT ... FROM ... [INNER JOIN] ... WHERE ... GROUP BY`).
   - Exact functional dependency closure, minimal cover, candidate key enumeration, 3NF synthesis, BCNF decomposition, and Tableau Chase losslessness checking on finite declared schemas.
   - End-to-end verification against SQLite, DuckDB, PostgreSQL 16, SQL Server 2022, and MongoDB 7 in automated CI.
2. **What this artifact does not support:**
   - Arbitrary SQL queries outside the admitted allowlist (`LEFT/RIGHT/FULL OUTER JOIN`, `HAVING`, window functions, subqueries, recursive CTEs, or cyclic join hypergraphs).
   - Automatic discovery of functional dependencies from unvalidated raw data without declared schema rules.
   - Sub-millisecond network latency claims under arbitrary production WAN topologies.
3. **Narrowest defensible claim:**
   - Under the declared relational schema, unique primary keys, and complete CDC/WAL before/after row images, `SmplCache` matches native SQL group aggregation on every `PRESERVE` and `REPAIR` step checked by the test oracle, and fails closed to `INVALIDATE` (`REREAD`) whenever required column evidence or bounded join partner state is missing or evicted.
4. **Next test needed:**
   - Multi-process CDC consumer failover and LSN watermark deduplication benchmarks under chaos network partitions.
