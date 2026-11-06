# SmplCache: Cache Boundary Diagnostic Engine

SmplCache is an open-source diagnostic library and incremental cache-maintenance engine that tests the repairability of cached database workloads under streaming CDC (Change Data Capture) updates.

Unlike traditional heuristic caches that flush everything or rely on logical replication guessing, SmplCache executes explicit boundary testing. The model is analyzed on paper for a finite relational fragment; single-relation `SUM`, `COUNT`, `COUNT(*)`, `AVG`, and projection shapes are implemented and checked against a brute-force SQLite oracle.

---

## Quick Navigation

- [Technical Wiki Home](docs/wiki/Home.md)
  - [Theorem 7 & Boundary Certificates](docs/wiki/Theorem-7-and-Boundary-Certificates.md)
  - [Simplex Normal Forms & Theorem 9 Lifts](docs/wiki/Simplex-Normal-Forms-and-Theorem-9-Lifts.md)
  - [Bounded Join Maintenance & W-TinyLFU](docs/wiki/Bounded-Join-Maintenance-and-W-TinyLFU.md)
  - [Benchmarks & Live Container Verification](docs/wiki/Benchmarks-and-Live-Container-Verification.md)
- [Acquirer Technical Due Diligence Data Room](docs/ACQUIRER_DUE_DILIGENCE_DATA_ROOM.md)
- [15-Minute Adoption Quickstart](docs/quickstart_15_minute_adoption.md)
- [8-Level Pedagogical Curriculum](docs/pedagogical_8_level_curriculum.md)
- [SmplCache Live Architecture & Operations](docs/demo/architecture_and_operations.md)

---

## Core Philosophy

SmplCache explores whether data systems over-invalidate because they route state changes through machinery that does not check repair evidence.

Relational theory normalizes data at rest (`smplcache.normal_forms`). SmplCache evaluates the repairability of data in motion within its declared SQL fragment (`smplcache.smplcache`, `smplcache.join`).

### The Professional Bar
1. **No silent under-invalidation.**
2. **No repair claim without evidence.**
3. **No unsupported SQL feature quietly accepted.**

---

## 15-Minute Adoption Quickstart

Get started with SmplCache in under 15 minutes:

```bash
# 1. Install package in editable mode
pip install -e ".[dev,demo]"

# 2. Run the workload advisor on the example schema
python -m smplcache.cli report examples/workload.common.json

# 3. Run SQL AST & FD-redundant GROUP BY doctor diagnostics
python -m smplcache.cli doctor examples/workload.common.json

# 4. Run 3NF/BCNF schema normalization, Tableau Chase check, and PostgreSQL/MSSQL DDL generation
python -m smplcache.cli normalize examples/ecommerce_before.json --dialect postgres --format markdown

# 5. Execute streaming in-place delta repair demo
python examples/01_python_orders_revenue_demo.py

# 6. Run multi-table join benchmark under Zipfian foreign-key skew
python -m smplcache.cli ingest --benchmark --transactions 10 --zipf-s 1.2

# 7. Run unit, property, CLI, substrate, and demo acceptance tests
python -m pytest

# 8. Launch SmplCache Live interactive web demo locally
uvicorn demo.api.app:app --host 0.0.0.0 --port 8000
```

For the complete walk-through and terminal output logs, refer to the [15-Minute Adoption Guide](docs/quickstart_15_minute_adoption.md).

---

## Integration contract

Use the same explicit relation identity in the SQL, seed tables, and change events.
`tenant_a.orders` and `tenant_b.orders` are distinct; a qualified name cannot be
matched to bare `orders` without an upstream binding. The library does not infer a
SQL search path. Scope each engine instance to one source database.

Seed aggregates with `AggregateCache.from_rows(shape, rows)` or
`AggregateCache.from_group_state(shape, group_stats)` (via `ColdRebuildSubstrate` for
`sqlite3`/`duckdb`), and joins with `rebuild_join_cache` plus complete `JoinAuxState`
tables. A totals-only `AggregateCache(values=...)` is readable but cannot authorize
repair: totals alone omit the row/non-null counts needed for deletion and SQL NULL
semantics.

After `invalidate` or `unsupported`, callers of the low-level processing functions
must discard/rebuild the cached result before serving it or processing further
changes. `WalStreamProcessor` remembers invalidation until its rebuild callback
succeeds; join rebuilds must replace both the cache and auxiliary state. Callbacks
must rebuild at the processed committed boundary, not an arbitrary later snapshot.

## How It Works

SmplCache decomposes a cached query shape into its dependency boundary (predicates, aggregates, groups, projections, security columns).

When a CDC `WriteEvent` occurs, SmplCache:
1. Checks whether the event intersects the shape boundary.
2. Checks whether the event contains sufficient old/new row evidence to repair the cache (Theorem 7).
3. Emits a `Decision` (`preserve`, `repair`, `invalidate`, `unsupported`) backed by a machine-readable `Certificate` (including `boundary_clock` LSN/sequence watermarks).

### Example Outputs

**Certificate for an Invalidated Cache (Missing Evidence):**
```json
{
  "shape": "inventory_stock_count",
  "event_id": "evt_6",
  "relation": "inventory",
  "decision_kind": "invalidate",
  "reason_code": "missing_evidence_for_repair",
  "required_evidence": ["item_id", "quantity"],
  "available_evidence": ["item_id", "quantity"]
}
```

**Certificate for a Repaired Cache:**
```json
{
  "shape": "revenue_by_customer_paid",
  "event_id": "evt_5",
  "relation": "orders",
  "decision_kind": "repair",
  "reason_code": "group_move",
  "required_evidence": ["amount", "customer_id", "status"],
  "available_evidence": ["amount", "customer_id", "status"],
  "repair_program": "sum_by_group"
}
```

---

## The 8-Level Pedagogical Curriculum

SmplCache organizes developer onboarding into 8 progressive cognitive levels:
- **Level 0 (The Pattern Break):** Exposing the failure modes of naive cache-aside.
- **Level 1 (Coordinate Anchoring):** Anchoring queries as geometric coordinate regions in $C^1$.
- **Level 2 (Refusal Assurance):** Enforcing Theorem 7: fail-closed refusal when CDC evidence is incomplete.
- **Level 3 (In-Place Delta Repair):** Applying incremental aggregate updates with sufficient group state.
- **Level 4 (Auxiliary Semi-Join Tracking):** Tracking foreign-key fanout in `JoinAuxState` with $O(1)$ primary and secondary key indices.
- **Level 5 (Capacity Bounds & Skew Eviction):** Enforcing hard row and tombstone limits with W-TinyLFU retention under Zipfian skew.
- **Level 6 (Multi-Tenant Isolation):** Exploring tenant-aware boundaries; production isolation is not established by these synthetic tests.
- **Level 7 (Systemic Workload Diagnosis & Schema Lift):** Workload pathology analysis and 3NF/BCNF synthesis with `smplcache doctor` and `smplcache normalize`.

See [8-Level Curriculum](docs/pedagogical_8_level_curriculum.md) for full details.

---

## Implemented capabilities and limits

1. Shape-driven preserve, repair, invalidate, and unsupported decisions for the admitted SQL fragment.
2. Incremental `SUM`, `COUNT`, `COUNT(*)`, and `AVG` state (plus bounded-window `MIN`/`MAX` via `SortedMultisetExtremumCache`), including group and predicate changes and external group-state seeding via `AggregateCache.from_group_state` and `ColdRebuildSubstrate`.
3. Foreign-key join maintenance with $O(1)$ primary-key and secondary foreign-key indices, W-TinyLFU auxiliary row limits (`max_total_rows`), bounded tombstone tracking (`max_tombstones`), and explicit fallback after evidence eviction.
4. Relational normalization engine (`smplcache.normal_forms`): functional dependency closure, minimal cover, candidate keys, 1NF/2NF/3NF/EKNF/BCNF classification, Bernstein 3NF synthesis, Tsou-Fischer BCNF decomposition, Tableau Chase losslessness verification with counterexample tuple generation, FD-redundant `GROUP BY` reduction, and PostgreSQL 16 / SQL Server 2022 DDL generation.
5. Decoded Debezium/wal2json event replay, transaction buffering, LSN/txId `boundary_clock` propagation, and live containerized verification against PostgreSQL 16, SQL Server 2022, and MongoDB 7.
6. Interactive **SmplCache Live** web demonstration (`demo/`) with side-by-side table-invalidation baseline comparison, interactive Schema Normalizer & Doctor tab (`/api/normalize`), independent native SQLite verification, and reproducible decision traces.

The adapter
consumes decoded JSON fixtures; it is not a native PostgreSQL `pgoutput` binary
protocol client. Live replication, durable offsets, duplicate suppression, and
production concurrency remain integration work.

`max_total_rows` bounds resident auxiliary rows, not total process memory. Indexes,
eviction tombstones (bounded by `max_tombstones`), frequency history, output groups,
and transient work also use memory. Decimal sums retain finite input digits; displayed
`AVG` division follows the active Decimal context and is not an exact rational or a
cross-database rounding assurance under all dialects.

Join maintenance requires each right-side join key to be that table's primary
key. The default key name is `id`; provide the same accurate `primary_keys`
mapping to SQL extraction and auxiliary-state seeding when it differs. The
extractor does not inspect the database schema, and seeding rejects duplicate
or null primary keys.

Boolean and float group and join keys, float predicate values, and float
SUM/AVG evidence are rejected rather than being interpreted using Python
equality or Decimal arithmetic. Use integer
or exact Decimal aggregate inputs; JSON float representations of database
numeric values are not exact evidence. Database collation, numeric precision,
and AVG rounding must still match the integration's declared semantics.

See the [PR #7 review](docs/reviews/pr7_review.md) and the
[SmplCache Live architecture & operations runbook](docs/demo/architecture_and_operations.md).

---

## Claim Discipline & Epistemic Audit

In accordance with the project Claim Hygiene Rule:
1. **What It Supports:** Supports boundary-quotient certification, certified in-place arithmetic repair for admitted SPJA single-block aggregates, bounded-window `MIN`/`MAX` maintenance, capacity-bounded join tracking under Zipfian skew, and functional dependency 3NF/BCNF schema normalization checked by Tableau Chase.
2. **What It Does Not Support:** Does not claim support for arbitrary unconstrained SQL (e.g., recursive CTEs, window functions, non-deterministic expressions), does not eliminate physical memory bounds for unbounded cardinality, and confers no operational execution authority (`AuthorityEffect: None`).
3. **Narrowest Defensible Claim:** Within its declared single-block SPJA equijoin SQL fragment and given complete before-and-after CDC row images, SmplCache identifies repairable state transitions, maintains aggregate sufficient statistics and, for joins, auxiliary row/index state, and deterministically falls back to certified invalidation whenever evidence is absent or capacity bounds are exceeded.
4. **Next Test Needed:** Multi-node CDC lease handoff and partition failover benchmarks under concurrent transactional workloads.

---

## Documentation

- [Technical Wiki Home](docs/wiki/Home.md)
- [Acquirer Due Diligence Data Room](docs/ACQUIRER_DUE_DILIGENCE_DATA_ROOM.md)
- Mathematical foundations: `docs/smplcache_theory_quotient_repairability.md`
- Dependency fingerprinting: `docs/dependency_fingerprinting.md`
- Invalidation rules: `docs/invalidation_rules.md`
- [CDC Evidence, Store Application, and Kernel Contracts](docs/cdc_evidence_and_store_contracts.md)

## License
Apache 2.0 License. See [LICENSE](LICENSE).
