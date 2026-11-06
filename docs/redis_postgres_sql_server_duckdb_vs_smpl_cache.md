# Redis, PostgreSQL, SQL Server, Caching Proxies, and DuckDB vs. SmplCache: Certified Boundary Repair, Topological Auditing, and the 30–180 Day Roadmap to $350K ARR (2027)

> **Status:** Architecture Comparison, Codebase Audit, and 30–180 Day Commercial Execution Plan
> **Reference Baseline:** [`sql_server_vs_smpl_cache.md`](file:///C:/Users/a343o/source/repos/anomalon/_ooutwire/product_backlog/simplex_cosmos/smplx_000_simplex_cosmos/sql_server_vs_smpl_cache.md)
> **Active Repository:** [`c:/Users/a343o/source/repos/smplcache/smplcache`](file:///c:/Users/a343o/source/repos/smplcache/smplcache) (`v0.1.0`, Apache-2.0)

---

## Executive Thesis: Better Than All of Them, Without Building a Monolithic RDBMS

Modern data stacks force engineering teams into a false dichotomy when serving high-concurrency analytical dashboards, customer-facing aggregates, and operational views:

1. **Key-Value Caches (Redis, Valkey, Memcached):** Fast ($<1\text{ ms}$) key lookups, but zero relational semantics. They store opaque byte blobs, cannot track foreign-key join fanout, suffer from cache-aside race conditions, and force developers to choose between coarse table-level cache flushes or fragile hand-rolled `HINCRBY` mutation code.
2. **Heavyweight RDBMS Materialized Views & CDC (SQL Server, PostgreSQL):** Full relational semantics, but either batch-refresh from scratch (`REFRESH MATERIALIZED VIEW CONCURRENTLY`), impose synchronous transactional lock contention on the primary OLTP write path (SQL Server Indexed Views, `pg_ivm`), or emit raw Change Data Capture (CDC) streams without checking whether the captured columns are sufficient to repair a downstream view.
3. **Full Streaming Dataflow Clusters (Materialize, Feldera, RisingWave, Readyset):** Maintain continuous differential dataflow graphs, but operate as heavy, stateful secondary database clusters whose internal join state can grow unboundedly under high-cardinality skew.
4. **Embedded Vectorized OLAP Engines (DuckDB):** Exceptional single-node columnar scan speed, but no incremental boundary maintenance. Every read after a write re-scans millions of unchanged rows across columnar row groups rather than applying a 1-row arithmetic delta.

**The SmplCache Wedge ("Not So Damn Big as a Full RDBMS"):**
Building a superior caching and view-maintenance layer does **not** require writing a 5-million-line RDBMS with a B-tree page manager, MVCC vacuum daemon, distributed lock manager, and full SQL:2023 cost-based optimizer.

Instead, **SmplCache** implements a compact, verifiable **Boundary-Certified Repair Kernel** (~5,000–15,000 lines of code) that sits between the CDC/WAL write stream and an embedded vectorized cold-rebuild substrate (**DuckDB / SQLite**):

$$
\boxed{
\text{Relational theory normalizes data at rest. SmplCache certifies repairability of data in motion.}
}
$$

When a write event $e$ arrives on the CDC stream, SmplCache evaluates the induced defect $h(e) \in C^1$ against the query shape's declared repair space $\operatorname{im}(d_0)$ in the quotient $Q^1 = C^1 / \operatorname{im}(d_0)$ and emits one of three operational moves backed by a machine-readable [`Certificate`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L159-L170):

- **`KEEP` (`preserve`):** The write touches columns disjoint from the query shape's boundary, misses the `WHERE` filter before and after, rolls back, or touches a join dimension row with zero matching fact rows. Zero database queries executed.
- **`FIX IN PLACE` (`repair`):** Complete before-and-after row evidence and resident join lookup state exist ($[h(e)] = 0 \in Q^1$). SmplCache updates the cached `SUM`, `COUNT`, `COUNT(*)`, or `AVG` group state in memory in microseconds. Zero database queries executed.
- **`REREAD` (`invalidate` / `unsupported`):** Required before-image evidence is absent (Theorem 7), a needed foreign-key lookup row was evicted under the configured memory cap (`evicted_join_auxiliary_key`), or a schema DDL change occurred ($[h(e)] \neq 0 \in Q^1$). SmplCache refuses to guess, withholds the stale cache, latches invalidation until rebuilt at the committed snapshot boundary, and delegates the cold recomputation to an embedded **DuckDB** or **SQLite** engine.

---

## Part I: The Certified Repair Demo — Side-by-Side Across 6 Systems

### 1. The Setup

Consider two standard production dashboard queries implemented in [`examples/01_python_orders_revenue_demo.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/examples/01_python_orders_revenue_demo.py#L12-L26) and [`demo/scenarios/runner.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/demo/scenarios/runner.py#L163-L235):

**Query Shape 1 (Single-Table Filtered Aggregate — `revenue_by_customer_paid`):**
```sql
SELECT customer_id, SUM(amount) AS total_revenue
FROM orders
WHERE status = 'paid'
GROUP BY customer_id;
```

**Query Shape 2 (Three-Table Foreign-Key Join Aggregate — `regional_revenue_paid`):**
```sql
SELECT r.name, SUM(o.amount_cents) AS regional_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.id
JOIN regions r ON c.region_id = r.id
WHERE o.status = 'paid'
GROUP BY r.name;
```

Now consider four realistic production write events arriving in sequence:

- **Event 0 (Disjoint Column Update):**
  ```sql
  UPDATE orders SET delivery_note = 'Leave at loading dock' WHERE order_id = 100;
  ```
- **Event 1 (Partial CDC Payload — Missing Before-Image Evidence):**
  ```sql
  UPDATE orders SET status = 'paid' WHERE order_id = 100;
  -- CDC stream supplies: {order_id: 100, status_old: 'pending', status_new: 'paid'}
  -- CDC stream OMITS:    amount, customer_id (e.g., PostgreSQL REPLICA IDENTITY DEFAULT or partial projection)
  ```
- **Event 2 (Complete CDC Payload — Full Before/After Image):**
  ```sql
  UPDATE orders SET status = 'paid' WHERE order_id = 200;
  -- OLD: {order_id: 200, customer_id: 12, amount: 75, status: 'pending'}
  -- NEW: {order_id: 200, customer_id: 12, amount: 75, status: 'paid'}
  ```
- **Event 3 (Dimension Table Move with 1-to-$k$ Foreign-Key Fanout):**
  ```sql
  UPDATE customers SET region_id = 2 WHERE id = 12;
  -- Customer 12 (who has 18 paid orders totaling 425,000 cents / $4,250) moves from Region 1 (North America) to Region 2 (Europe)
  ```

---

### 2. How Each System Handles Events 0–3

#### A. Redis / Valkey / Memcached (Application Cache-Aside & Imperative `HINCRBY`)

1. **Event 0 (`delivery_note` update):**
   - If the application uses table-level cache invalidation (`DEL cache:revenue_by_customer_paid` on any `orders` write), Event 0 causes a **100% false invalidation** even though `delivery_note` does not appear in the query.
   - If the application developer manually hardcodes column lists in application middleware, the logic drifts as soon as a query changes.
2. **Event 1 (Partial CDC payload):**
   - A developer trying to maintain a Redis hash (`HINCRBY cache:revenue 7 50`) from a partial event stream does not have `customer_id` or `amount`. Worse, under concurrent writes or network retries, imperative `HINCRBY` calls lack idempotent boundary clocks and double-count or drift from PostgreSQL/SQL Server ground truth.
3. **Event 2 (Complete payload):**
   - Even with full row data, application-side Redis mutation code rarely checks all four state transitions required for conditional aggregates: (i) predicate entry (`pending` $\to$ `paid`), (ii) predicate exit (`paid` $\to$ `refunded`), (iii) group key move (`customer_id` reassigned), and (iv) last-row deletion vs. `NULL` sum semantics.
4. **Event 3 (Dimension move across joined tables):**
   - Redis has no foreign-key index between `customers` and `orders`. When `customers.region_id` changes, Redis cannot determine which orders belong to Customer 12 without querying the primary database or flushing the entire regional dashboard cache.

#### B. SQL Server (CDC + Indexed Views)

1. **SQL Server CDC (`sys.sp_cdc_enable_table`):**
   - Captures table row changes into `cdc.<capture_instance>_CT`. However, SQL Server CDC is an uncertified transport layer: if the capture instance column list omits a group or aggregate column, or if a downstream consumer reads a partial projection on Event 1, SQL Server provides no boundary check indicating whether the view can be repaired.
2. **SQL Server Indexed Views (`WITH SCHEMABINDING`):**
   - Maintains the view synchronously inside the OLTP transaction that modifies `orders` or `customers`. Every `INSERT`/`UPDATE`/`DELETE` on `orders` must acquire locks on the indexed view's clustered index partitions, contributing to `LOCK_HASH` spinlock contention and deadlocks under high write concurrency.
   - Furthermore, SQL Server Indexed Views reject `LEFT JOIN`, `AVG` (requiring manual `SUM`/`COUNT_BIG` decomposition), `MIN`/`MAX`, `UNION`, and outer references.

#### C. PostgreSQL (`MATERIALIZED VIEW`, Logical Replication `pgoutput` / `wal2json`, `pg_ivm`)

1. **Native `MATERIALIZED VIEW` (`REFRESH MATERIALIZED VIEW CONCURRENTLY`):**
   - Cannot apply Event 2 or Event 3 incrementally. Refreshing the view re-executes the full multi-table join and `GROUP BY` across all millions of rows in `orders`, `customers`, and `regions`.
2. **Logical Replication (`pgoutput` / `wal2json` / Debezium):**
   - By default, PostgreSQL tables use `REPLICA IDENTITY DEFAULT` (recording only the primary key in the `before` image of an `UPDATE` or `DELETE`).
   - Consequently, on **Event 1**, PostgreSQL WAL natively emits `before: {"order_id": 100}` and omits the old `status`, `customer_id`, and `amount`! Even with `REPLICA IDENTITY FULL`, unchanged `TOAST`ed columns are emitted as sentinel placeholders (`__debezium_unavailable_value` or `__toast_unchanged__`). Any naive WAL consumer that attempts incremental subtraction on default Postgres WAL silently corrupts the cached sum.
3. **`pg_ivm` (Incremental View Maintenance extension):**
   - Like SQL Server Indexed Views, `pg_ivm` executes maintenance synchronously via statement-level `AFTER` triggers inside the user's OLTP write transaction, multiplying write latency on hot tables.

#### D. Heuristic Caching Proxies (PolyScale, Heimdall, Pgpool-II) vs. Streaming Dataflow Clusters (Readyset, Materialize, Feldera)

1. **Heuristic Query Caches (PolyScale, Heimdall, Pgpool-II):**
   - Operate on coarse table-level invalidation or TTL. On Event 0 (`delivery_note`), they flush every cached query referencing `orders`. On Events 1–3, they cannot repair in place; they drop the cache entry and force the primary database to re-run the heavy aggregation on the next read.
2. **Streaming Dataflow Engines (Materialize, Feldera, RisingWave, Readyset):**
   - Maintain full dataflow operator graphs. While they can incrementally update joins on Event 2 and Event 3, they require maintaining complete intermediate relational state in memory/storage across the dataflow graph. Under skewed high-cardinality foreign keys, memory footprint scales with total historical join state rather than a bounded auxiliary cache with explicit eviction fallback.

#### E. DuckDB (Embedded Columnar OLAP Engine)

1. **How DuckDB Executes:**
   - DuckDB is an in-process vectorized columnar execution engine. It does not maintain incremental deltas from CDC events.
2. **What Happens on Events 0–3:**
   - Whether 1 row changed or 0 relevant columns changed, serving a fresh answer from DuckDB requires either re-scanning the local DuckDB table/Parquet files or pulling fresh pages over `postgres_scanner`.
   - While DuckDB scans 10M rows in ~20–50 ms on a modern CPU, running 200 concurrent dashboard requests per second across 50 tenants saturates CPU cores by repeatedly re-aggregating 99.999% unchanged data.
3. **Why DuckDB Is SmplCache's Ideal Partner, Not Replacement:**
   - DuckDB is fast at **cold vectorized recomputation** (`REREAD`), whereas SmplCache is fast at **hot boundary-certified delta maintenance** (`KEEP` and `FIX IN PLACE`). Combining them yields O(1) memory-resident updates on the hot path and millisecond vectorized rebuilds on the cold fallback path.

#### F. What SmplCache Does (`smplcache` Engine)

SmplCache intercepts each event via [`certify_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L631-L877) / [`process_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L880-L958), [`certify_join_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L1567-L1904) / [`process_join_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L1934-L2001), and [`WalStreamProcessor`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/debezium.py#L561-L887):

- **On Event 0 (`delivery_note` changed):**
  - `delivery_note` is disjoint from `revenue_by_customer_paid`'s dependency boundary (`{customer_id, amount, status}`).
  - SmplCache emits **`KEEP` (`preserve`, `reason_code="disjoint_columns"`)** with zero arithmetic and zero database reads:
    ```json
    {
      "shape": "revenue_by_customer_paid",
      "event_id": "evt_0",
      "relation": "orders",
      "decision_kind": "preserve",
      "reason_code": "disjoint_columns",
      "required_evidence": [],
      "available_evidence": [],
      "repair_program": null,
      "boundary_clock": null
    }
    ```
- **On Event 1 (Incomplete CDC payload — missing `amount` and `customer_id`):**
  - SmplCache checks **Theorem 7 (CDC Evidence Sufficiency)**:
    ```text
    Required evidence for repair:  ["amount", "customer_id", "status"]
    Available evidence in payload: ["order_id", "status"]
    ```
  - Because $[h(e)] \neq 0 \in Q^1$, SmplCache refuses to guess and emits **`REREAD` (`invalidate`, `reason_code="missing_evidence_for_repair"`)**:
    ```json
    {
      "shape": "revenue_by_customer_paid",
      "event_id": "evt_100",
      "relation": "orders",
      "decision_kind": "invalidate",
      "reason_code": "missing_evidence_for_repair",
      "required_evidence": ["amount", "customer_id", "status"],
      "available_evidence": ["order_id", "status"],
      "repair_program": null,
      "boundary_clock": null
    }
    ```
  - Crucially, `WalStreamProcessor` **latches** the cache into an invalid state (`cache_requires_rebuild`) until the rebuild callback refreshes from the committed snapshot boundary.
- **On Event 2 (Complete CDC payload):**
  - Available evidence covers required evidence on both `old` and `new` row images. The row transitions into `status = 'paid'` (`predicate_entry`).
  - SmplCache emits **`FIX IN PLACE` (`repair`, `reason_code="predicate_entry"`, `repair_program="sum_by_group"`)**:
    ```json
    {
      "shape": "revenue_by_customer_paid",
      "event_id": "evt_200",
      "relation": "orders",
      "decision_kind": "repair",
      "reason_code": "predicate_entry",
      "required_evidence": ["amount", "customer_id", "status"],
      "available_evidence": ["amount", "customer_id", "order_id", "status"],
      "repair_program": "sum_by_group",
      "boundary_clock": null
    }
    ```
  - Customer 12's cached sum is incremented by `75` in place, and its non-null/row counts `(s_val, n_cnt, r_cnt)` are updated in `_group_state` so subsequent `AVG` or `DELETE` operations remain exact.
- **On Event 3 (Customer 12 moves from Region 1 to Region 2):**
  - If Customer 12 and its 18 child orders are resident in [`JoinAuxState`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L331-L1436) / [`SharedJoinAuxPool`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L2096-L2325), SmplCache resolves the 1-to-18 foreign-key fanout in memory, subtracts `425000` cents (`$4,250`) and `18` rows from `North America` (pruning the `'North America'` group key from `cache.values` via [`_refresh_group_value`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L551-L558) when its group row count `r_cnt` reaches `0`, matching SQL `GROUP BY` semantics), adds `425000` cents to `'Europe'`, and emits **`FIX IN PLACE` (`repair`, `reason_code="dimension_update"`, `repair_program="join_sum_by_group"`)**:
    ```json
    {
      "shape": "regional_revenue_paid",
      "event_id": "evt_300",
      "relation": "customers",
      "decision_kind": "repair",
      "reason_code": "dimension_update",
      "required_evidence": ["id", "region_id"],
      "available_evidence": ["id", "region_id"],
      "repair_program": "join_sum_by_group",
      "boundary_clock": null
    }
    ```
  - If `max_total_rows` was capped (e.g., `max_total_rows=5`) and any of Customer 12's lookup rows had been evicted into the tombstone set (`evicted_pks` / `evicted_fk_buckets`), [`_EvictedJoinKeyError`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L158-L160) triggers a fail-closed **`REREAD` (`invalidate`, `reason_code="evicted_join_auxiliary_key"`)**:
    ```json
    {
      "shape": "regional_revenue_paid",
      "event_id": "evt_300",
      "relation": "customers",
      "decision_kind": "invalidate",
      "reason_code": "evicted_join_auxiliary_key",
      "required_evidence": ["id", "region_id"],
      "available_evidence": ["id", "region_id"],
      "repair_program": null,
      "boundary_clock": null
    }
    ```


---

### 3. Master Architectural Comparison Matrix

| Dimension | Redis / Valkey (Cache-Aside) | SQL Server (CDC + Indexed Views) | PostgreSQL (`MAT VIEW` + WAL) | Heuristic Cache Proxies (PolyScale / Heimdall) | Streaming IVM Clusters (Materialize / Readyset) | DuckDB (Standalone OLAP) | **SmplCache (+ Embedded DuckDB/SQLite)** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Primary Abstraction** | Key-value byte/JSON store | Monolithic RDBMS + sync views / raw CDC tables | Monolithic RDBMS + batch MV / logical WAL | Transparent SQL query cache proxy | Distributed / dataflow view cluster | In-process vectorized columnar OLAP | **Boundary-certified delta cache + cold vectorized rebuild** |
| **Irrelevant Column Write (Event 0)** | Flushes table keys or requires manual code | Indexed view updates index; CDC logs row | Batch MV ignores until full refresh | Flushes all cached queries on table | Preserves down dataflow graph | Re-scans columns on next query | **`KEEP` (`preserve: disjoint_columns`) in $O(1)$** |
| **Partial CDC / Missing Old Value (Event 1)** | Silent drift or manual crash | Sync view locks base table; external CDC uncertified | `REPLICA IDENTITY DEFAULT` omits old values | Flushes whole table cache | Requires full replica identity or fails | Full scan on read | **`REREAD` (`invalidate: missing_evidence_for_repair`) + latch** |
| **Complete Aggregate Update (Event 2)** | Imperative `HINCRBY` (race-prone, no predicate check) | Sync transactional maintenance on OLTP write | Full table scan on `REFRESH MATERIALIZED VIEW` | Flushes cache; re-runs SQL on primary DB | Incremental dataflow update | Re-scans columnar row groups | **`FIX IN PLACE` (`repair`) with exact Decimal/int state** |
| **Multi-Table FK Join Fanout (Event 3)** | Cannot resolve FK fanout without DB query | Sync view maintenance (no `LEFT JOIN` allowed) | Full multi-table join scan on refresh | Flushes all joined table caches | Full dataflow join state (unbounded RAM) | Fast vectorized hash join scan | **`FIX IN PLACE` via bounded `SharedJoinAuxPool` (`wtinylfu`)** |
| **Behavior When Memory Cap Exceeded** | Evicts random/LRU keys silently | Spills to `tempdb` or blocks | Spills to disk (`work_mem`) | Evicts whole query results | OOM risk or heavy disk spill | Spills to local disk | **Tombstone tracking $\to$ certified `REREAD` (`evicted_join_auxiliary_key`)** |
| **OLTP Write-Path Contention** | None (async), but consistency races | **High** (sync locks + `LOCK_HASH` spinlocks) | None for batch MV; **High** for `pg_ivm` triggers | None | None (logical replication) | None | **Zero** (asynchronous CDC/WAL boundary consumer) |
| **Unsupported SQL Handling** | Developer's problem | Hard error on `CREATE INDEX` | Allowed in batch MV (slow) | Often cached with heuristic TTL/flush | Partial support or unindexed passthrough | Full OLAP SQL scan | **Explicit AST allowlist $\to$ fail-closed `unsupported` certificate** |
| **Multi-Tenant Schema Isolation** | Manual key prefixing (`tenant:a:...`) | Schema/DB bound | Schema/RLS bound | Often over-invalidates across tenants | Complex view per tenant | File/schema per tenant | **First-class namespace & `security_cols` boundary verification** |
| **Workload Pathology Diagnostics** | `INFO memory` / slowlog only | Raw XML plans, DMV counters, XML deadlock graphs | `EXPLAIN`, `pg_stat_statements` | Hit/miss ratio dashboard | Dataflow operator metrics | `EXPLAIN ANALYZE` | **`smplcache doctor` + `report --topomap` ($\beta_1$ cycles, gravity wells, SQL fixes)** |
| **Operational Footprint** | Separate cluster (`redis-server`) | Full RDBMS ($>2\text{ GB}$ RAM base) | Full RDBMS + replication infra | Proxy tier | Heavy stateful cluster | Embedded library ($<50\text{ MB}$) | **Lightweight sidecar / library ($<50\text{ MB}$) + embedded DuckDB/SQLite** |

---

## Part II: "Not So Damn Big as a Full RDBMS" — The SmplCache + DuckDB Hybrid Architecture

### Why Full RDBMSs and Full Streaming Clusters Are Too Big

A traditional RDBMS (SQL Server, PostgreSQL, Oracle) or a full streaming database (Materialize, RisingWave) is massive because it tries to solve **general-purpose durable storage, arbitrary ad-hoc SQL optimization, transactional locking, and crash recovery** inside one giant runtime.

For the 80% of production read bottlenecks—customer revenue dashboards, multi-tenant SaaS analytics widgets, inventory counters, billing usage rollups, and operational queues—you do **not** need a second RDBMS. You need two tightly coupled, small primitives:

```text
                       ┌────────────────────────────────────────────────────────┐
                       │                 Primary OLTP Database                  │
                       │         (PostgreSQL / SQL Server / MySQL)              │
                       └───────────────────────────┬────────────────────────────┘
                                                   │
                              WAL / CDC Stream     │ (Debezium / pgoutput /
                              (BEGIN/DATA/COMMIT)  │  wal2json / MSSQL CDC)
                                                   ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│                            SmplCache Sidecar / Embedded Engine (<50 MB)                           │
│                                                                                                   │
│  1. AST Allowlist Extractor (`sqlglot`) ──► Decomposes SQL into Predicate, Group, Agg, Join, RLS  │
│  2. Boundary Certifier (Theorem 7)      ──► Evaluates [h(e)] in Q^1 = C^1 / im(d_0)               │
│                                                                                                   │
│     ├──► [Move 1: KEEP (preserve)]      ──► Disjoint column / false filter / rollback (0 I/O)     │
│     │                                                                                             │
│     ├──► [Move 2: FIX IN PLACE (repair)]──► Updates `AggregateCache` + `SharedJoinAuxPool`        │
│     │                                       (CountMinSketch + W-TinyLFU bounded memory, 0 I/O)    │
│     │                                                                                             │
│     └──► [Move 3: REREAD (invalidate)]  ──► Missing WAL evidence / evicted join key / DDL         │
│                    │                        Latches invalid state (`cache_requires_rebuild`)      │
│                    ▼                                                                              │
│     ┌──────────────────────────────────────────────────────────────────────────────────────┐      │
│     │        Embedded Cold-Rebuild Substrate (DuckDB Columnar Replica / SQLite)            │      │
│     │  Recomputes invalidated shape at committed LSN boundary in ~5–30 ms without          │      │
│     │  hammering the primary OLTP database, then unlatches SmplCache for delta repair.     │      │
│     └──────────────────────────────────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                   │
                                                   ▼
                     Sub-millisecond Read API (HTTP / gRPC / Redis RESP3)
                     Returns: Cached Result + Replayable Certificate + LSN Watermark
```

### Why This Hybrid Architecture Wins

1. **Beats Redis on Correctness and Joins:** Every served result is backed by a replayable `Certificate` tied to a committed WAL transaction boundary, and 3-table foreign-key joins (`orders -> customers -> regions`) update in place when a customer changes regions.
2. **Beats PostgreSQL & SQL Server Materialized Views on Freshness and Write Overhead:** Zero triggers or synchronous locks on the primary OLTP write path, and no full-table `REFRESH MATERIALIZED VIEW` scans on every write.
3. **Beats Standalone DuckDB on Read Concurrency & CPU Efficiency:** Instead of running 200 vectorized table scans per second when only `orders.delivery_note` or a single `orders.amount_cents` changed, SmplCache absorbs 85%–95% of writes via $O(1)$ `KEEP` or `FIX IN PLACE` operations and invokes DuckDB **only** when a nonzero quotient obstruction (`REREAD`) requires a snapshot rebuild.
4. **Beats Heavy Streaming Clusters on Memory Predictability:** `SharedJoinAuxPool` enforces a strict resident row budget (`max_total_rows`, `max_fk_fanout`) using `CountMinSketch` frequency aging and `wtinylfu` adaptive window sizing. Executing [`run_shared_multi_query_pool_benchmark`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/benchmark.py#L1905-L2103) on the current engine revision (`v0.1.0`, post-`d8467e7` read-only certification telemetry; cf. historical pre-`d8467e7` tables in [`docs/benchmarks/adaptive_wtinylfu_shared_pool_sweep.md`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/docs/benchmarks/adaptive_wtinylfu_shared_pool_sweep.md)), consolidating three snowflake join queries (`q_region_rev`, `q_tier_cnt`, `q_region_avg`) into `SharedJoinAuxPool` with `wtinylfu` reduces eviction invalidations across 420 decisions from `237` (isolated at `total_pool_rows=48`, `108` repairs) to `66` (`273` repairs, 72.2% reduction), and from `207` (isolated at `total_pool_rows=54`, `138` repairs; `208` pre-`d8467e7`) to `0` (`330` repairs, 100% reduction in that synthetic benchmark, checked against `rebuild_join_cache`, which shares engine code).

---

## Part III: Topological Plan Cache, WAL, and Contention Auditing Across SQL Server, PostgreSQL, Redis, and DuckDB

In [`sql_server_vs_smpl_cache.md`](file:///C:/Users/a343o/source/repos/anomalon/_ooutwire/product_backlog/simplex_cosmos/smplx_000_simplex_cosmos/sql_server_vs_smpl_cache.md), we introduced topological plan cache auditing for SQL Server DBAs. Here we generalize that diagnostic engine—already implemented in [`smplcache/cli.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/cli.py) via `smplcache doctor`, `smplcache report --topomap`, `smplcache compare`, and `smplcache matrix`—across **SQL Server, PostgreSQL, Redis, and DuckDB**.

### 1. Live Output from the Existing `smplcache` Diagnostic CLI

Running the four diagnostic CLI commands in the repository today produces:

- **Topological Workload Geometry (`python -m smplcache.cli report examples/workload.common.json --topomap --format markdown`):**
  - Identifies **Gravity Wells** (top invalidating columns: `orders.status` with 6 hits, `orders.customer_id` with 3 hits, `orders.amount` with 2 hits).
  - Computes the co-invalidation adjacency graph across cached shapes (`Coupled Shapes = 3`, `Coupling Edges = 3`, `Connected Components = 1`, **`Cache Betti-1 (Cycles) = 1`**, `Coupling Score = 50.0%`, `Shape Entropy = 1.85`).
- **Topological Lift Verification (`python -m smplcache.cli compare examples/obstruction_mess.json examples/lifted_clean.json --format markdown`):**
  - Evaluates splitting `orders.status` into narrower observer-specific columns (`payment_status`, `fulfillment_status`, `support_status`) across 6 query shapes: reduces `Coupling Edges` from `15` to `4`, **`Cache Betti-1` from `10` to `1`**, `Coupling Score` from `100.0%` to `26.7%`, and `Obstruction Score` from `12.00` to `1.77`.
- **Spectral Matrix Geometry (`python -m smplcache.cli matrix examples/workload.common.json`):**
  - Computes `Dominance Score = 0.69`, `Invalidation Entropy = 0.85`, `Dominant Component lambda_1 = 0.82`, and `Shapes in Dominant Mode = 3`.
- **Query Shape Doctor (`python -m smplcache.cli doctor examples/workload.common.json --format markdown`):**
  - Detects **Implicit Conversion Risks**: `orders.customer_id` in `order_detail_view` (`VARCHAR(50)` column vs. `NVARCHAR(4000)` parameter $\to$ index seek risk).
  - Detects **Parameter Skew Regimes**: `order_detail_view` has three distinct cardinality regimes (`small`: avg 3 rows, 8,000 executions; `medium`: avg 800 rows, 500 executions; `whale`: avg 900,000 rows, 20 executions) and recommends splitting the shape into `order_detail_view/{small,medium,whale}`.
  - Emits **4 Exact Covering Index DDL Statements**, including:
    ```sql
    CREATE INDEX ix_orders_customer_id_status ON orders(customer_id, status) INCLUDE(amount);
    ```

### 2. The Seven Cross-Engine Obstruction Classes & Certified Fix Templates

| Obstruction Class | SQL Server Signature | PostgreSQL Signature | Redis / DuckDB Signature | Concrete Fix Emitted by `smplcache doctor` |
| :--- | :--- | :--- | :--- | :--- |
| **Class A: Missing Predicate Boundary (Non-Sargable / Implicit Cast)** | `Filter` above `Table Scan` / `Index Scan` with `CONVERT_IMPLICIT` on column side in `sys.dm_exec_query_plan` | `Seq Scan` with `Filter: ((customer_id)::text = ...)` in `EXPLAIN (FORMAT JSON)` | DuckDB full row-group scan (`SEQ_SCAN` without zonemap pushdown) | Match parameter type (`VARCHAR` vs `NVARCHAR`), add functional index, or rewrite `UPPER(col) = @p` to explicit collation |
| **Class B: Late Filter / Predicate Placement Collapse** | `Filter` with high selectivity placed above `Hash Match (Inner Join)` or `Sort` | CTE materialized (`WITH cte AS MATERIALIZED`) or subquery blocking predicate pushdown | Application fetches full joined dataset into Redis/Pandas before filtering | Push `WHERE status = 'paid'` into inner derived table / CTE or register filtered shape in SmplCache |
| **Class C: Join Topology Inversion & Parameter Sniffing Skew** | `Hash Join` build input rows $>$ probe input rows; `Nested Loops` with 900k outer rows from sniffed `@p` | Generic plan chosen after 5 executions in `pg_prepared_statements` despite skewed tenant IDs | Single Redis key (`whale` tenant) holding 900k serialized JSON items blocking event loop | Split query shape by cardinality regime (`small`/`medium`/`whale`), `OPTION(RECOMPILE)`, or route `whale` to DuckDB |
| **Class D: Group Key Over-Specificity** | `GROUP BY CustomerID, OrderID, CustomerName` forcing `Sort` spill to `tempdb` | `HashAggregate` / `GroupAggregate` spilling batches to `pgsql_tmp` due to redundant PK/FD columns | High-cardinality Redis key explosion (`cache:cust:id:name:order_id`) | Project functional dependents (`MAX(CustomerName)`) or join dimension attributes after grouping by `CustomerID` |
| **Class E: CDC Replica Identity & TOAST Evidence Obstruction** | CDC capture instance configured with partial `@captured_column_list` missing group/agg column | `pg_class.relreplident = 'd'` (`DEFAULT`) on table with `UPDATE`/`DELETE` in cached aggregate view, or `TOAST`ed column | Kafka/Debezium consumer silently dropping `__debezium_unavailable_value` | Emit exact DDL: `ALTER TABLE orders REPLICA IDENTITY FULL;` or include required columns in capture instance |
| **Class F: Workload Coupling Cycle ($\beta_1 > 0$ Gravity Well)** | One hot `status` column invalidating 5+ indexed views / cached procs simultaneously | Hot updates on wide table causing MV refresh storms and `HOT` (Heap-Only Tuple) update failure | Coarse `DEL orders:*` invalidating unrelated fulfillment, billing, and support caches | Decompose wide hot column (`status` $\to$ `payment_status`, `fulfillment_status`) to reduce $\beta_1$ |
| **Class G: Bipartite Lock Cycle & Spinlock / `LWLock` Thrash** | Cycle in deadlock XML $G=(P,R,E)$; high `LOCK_HASH` or `SOS_SCHEDULER` spins per collision in `sys.dm_os_spinlock_stats` | `LockManager` or `BufferMapping` `LWLock` contention in `pg_stat_activity` from repetitive dashboard scans | Redis single-threaded command queue saturation from Lua script storms | Offload repetitive aggregate reads to SmplCache sidecar; enable `READ_COMMITTED_SNAPSHOT` (MSSQL); standardize lock order |

---

## Part IV: Technical Review of the Existing `SmplCache` Product (`v0.1.0`)

Our audit of [`c:\Users\a343o\source\repos\smplcache\smplcache`](file:///c:/Users/a343o/source/repos/smplcache/smplcache) confirms a high-integrity foundation with **177 passing automated tests** (`73` targeted regression tests in `tests/regressions/`, `78` unit and property-based Hypothesis tests in `tests/`, and `26` API/UI acceptance and rate-limit tests in `demo/tests/`).

### 1. What Exists and Works Today

1. **Core Certification & Exact Aggregate Kernel ([`smplcache/smplcache.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py)):**
   - Enforces Theorem 7 ([`certify_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L631-L877), [`process_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L880-L958)) across `SUM`, `COUNT`, `COUNT(*)`, and `AVG`.
   - Uses exact exponent-preserving `Decimal` addition (`_decimal_add`) so `10^28 + 1` never loses low-order digits, rejects non-finite floats/booleans as aggregate or group evidence, tracks per-group sufficient statistics `(sum_val, non_null_count, row_count)` in `_group_state` for SQL `NULL` and empty-group deletion semantics, and preserves schema namespace qualifiers (`tenant_a.orders` $\neq$ `tenant_b.orders`).
2. **AST Allowlist SQL Extractor ([`smplcache/extractor.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/extractor.py)):**
   - Uses `sqlglot` (`postgres`, `tsql`, `sqlite` dialects) with an explicit AST node allowlist (`ALLOWED_AST_NODE_TYPES`, `ALLOWED_JOIN_AST_NODE_TYPES`). Rejects comments, subqueries, window functions, `LIMIT/OFFSET`, quoted-identifier ambiguities, and non-deterministic expressions fail-closed with typed `unsupported` reasons.
3. **Bounded Foreign-Key Join Engine ([`smplcache/join.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py)):**
   - Implements `MultiTableJoinShape`, [`JoinAuxState`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L331-L1436), [`SharedJoinAuxPool`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L2096-L2325), and `MultiQueryJoinCacheManager` for `INNER` and `LEFT` foreign-key equijoins, deriving join key classification directly from `table_dependency_set["join_keys"]` so non-PK join keys never misclassify as `KEEP`.
   - Implements `CountMinSketch` with periodic multiplicative aging and `wtinylfu` adaptive window hill-climbing eviction.
   - Tracks evicted primary keys and foreign-key fanout buckets via tombstones (`evicted_pks`, `evicted_fk_buckets`) so any event touching an evicted key raises `_EvictedJoinKeyError` and emits a certified `invalidate` (`evicted_join_auxiliary_key`).
   - Certification ([`certify_join_event`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L1567-L1904)) is strictly read-only (`record_access=False`), preventing inspection from mutating eviction telemetry.
4. **WAL / Debezium / `wal2json` Stream Harness ([`smplcache/debezium.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/debezium.py)):**
   - Parses Debezium and `wal2json` envelopes, detects `DEBEZIUM_UNAVAILABLE_VALUE` and `WAL2JSON_UNAVAILABLE_VALUE` (`__toast_unchanged__`), buffers transactions (`BEGIN`/`COMMIT`/`ROLLBACK`), detects `SCHEMA_CHANGE` DDL, and latches [`WalStreamProcessor`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/debezium.py#L561-L887) invalidation (`cache_requires_rebuild`) until the rebuild callback succeeds.
5. **Interactive Web Application & CLI ([`demo/`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/demo), [`smplcache/cli.py`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/cli.py)):**
   - FastAPI backend (`demo/api/app.py`) + TypeScript frontend (`demo/web/src/onepane.ts`, `main.ts`, shared `moves.ts`) with pre-rendered single-pane front page (`/`), multi-scenario lab (`/lab/`), URL round-trip state sharing, rate limiting, 16 KB body guards, and step-by-step native SQLite oracle verification.

### 2. Current Product Gaps Blocking Commercial Scale ($350K ARR)

To transition SmplCache from a verified Python library and interactive demo into a commercial product capable of generating **$350,000 in yearly revenue or seed investment in 2027**, six concrete engineering gaps must be closed over the next 30–180 days:

| Gap ID | Current Limitation in `v0.1.0` | Required Production Capability |
| :--- | :--- | :--- |
| **GAP-1: Live Network CDC Ingestion** | `smplcache/debezium.py` ingests in-memory JSON dicts or static JSONL files (`smplcache ingest`). | Live streaming connectors for **PostgreSQL Logical Replication (`pgoutput` / `wal2json`)**, **Kafka / Redpanda Debezium topics**, and **SQL Server CDC (`cdc.*_CT` LSN polling)** with durable offset/LSN watermarks. |
| **GAP-2: Single-Aggregate SQL Fragment** | `extractor.py` admits at most 1 aggregate function (`SUM`, `COUNT`, or `AVG`) per `SELECT` and only conjunctive (`AND`) binary comparisons (`=`, `!=`, `<`, `<=`, `>`, `>=`). | Multi-aggregate queries (`SELECT region, COUNT(*), SUM(amount), AVG(amount)` in one shape), `IN (...)`, `BETWEEN`, `IS NULL` / `IS NOT NULL`, `HAVING` post-filters, and linear arithmetic expressions (`price * qty`). |
| **GAP-3: Cold-Rebuild Engine Coupling & `_from_rows` Sufficient-Statistics Seeding** | Rebuild callbacks in `demo/` use an in-memory `sqlite3` database, and [`AggregateCache.from_rows`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/smplcache.py#L512-L550) / [`rebuild_join_cache`](file:///c:/Users/a343o/source/repos/smplcache/smplcache/smplcache/join.py#L1438-L1505) iterate over all raw base-table rows in Python because seeding via `AggregateCache(values=...)` leaves `_from_rows = False` and fails on the next repair with `missing_aggregate_state` (`smplcache.py#L887-L888`). | **Embedded DuckDB Cold-Rebuild Substrate + `AggregateCache.from_group_state()`**: add `AggregateCache.from_group_state(shape, group_stats)` accepting pre-aggregated `(group_key, sum_val, non_null_count, row_count)` tuples computed directly by DuckDB's vectorized `SELECT grp, SUM(col), COUNT(col), COUNT(*) ... GROUP BY grp` query (returning $G$ groups instead of 10M raw rows), plus top-$K$ working-set seeding for `JoinAuxState`. |
| **GAP-4: Python Memory Ceiling & Unbounded Eviction Tombstone Sets** | `JoinAuxState` stores rows as Python `dict[str, Any]` objects (~600–1,000 bytes/row overhead in CPython) and records evicted keys in unbounded Python sets (`evicted_pks: dict[str, set[Any]]`, `evicted_fk_buckets` at `join.py#L891-L923`), which grow proportionally to total historical evictions between rebuilds. | **Rust Core Kernel (`smplcache-rs` via `PyO3` + `smplcached` binary)** using compact columnar/slot-map arenas, byte-accurate budgets (`max_total_bytes`), and a **bounded Bloom / Cuckoo filter (or RoaringBitmap) for eviction tombstones** (zero false negatives preserve fail-closed safety; false positives only trigger conservative `REREAD`), reaching 250k+ events/sec/core. |
| **GAP-5: Drop-In Client Read Protocol** | Callers must import Python classes (`AggregateCache`, `WalStreamProcessor`) or call demo endpoints (`/api/runs`). | **Drop-in `smplcached` Sidecar** exposing (a) a **Redis `RESP3`-compatible read port** (`GET` / `HGETALL smpl:<shape>:<group>`) and (b) an **HTTP/gRPC Read-Through API** returning JSON/Arrow results + `X-SmplCache-Certificate` headers. |
| **GAP-6: Live DB Introspection for `doctor`** | `smplcache doctor` reads hand-authored `examples/workload.common.json` files. | **Zero-Config DB Inspector (`smplcache doctor --pg-dsn ...` / `--mssql-dsn ...`)** that queries `pg_stat_statements` + `pg_class.relreplident` + `EXPLAIN` or SQL Server `sys.dm_exec_cached_plans` + `sys.dm_os_spinlock_stats` directly and outputs a board-ready Markdown/HTML audit report. |

---

## Part V: The 30–180 Day Execution Plan to $350,000 Yearly Revenue or Seed Investment (2027)

### Strategic Positioning: Two Monetizable Wedges from One Engine

To reach **$350,000 ARR (or raise a $1.5M–$3.0M Seed Round) in 2027**, we do not sell "a new database" that requires a 12-month enterprise rip-and-replace cycle. We monetize two immediate, high-ROI wedges powered by the exact same topological boundary engine:

1. **Wedge 1 — `SmplCache Doctor & CI Gate` (Fast Entry / Lead Magnet $\to$ $500–$1,000/mo):**
   - Connects read-only to a prospect's PostgreSQL or SQL Server metadata (`pg_stat_statements`, `pg_class.relreplident`, `sys.dm_exec_query_plan`, `sys.dm_os_spinlock_stats`) or runs in GitHub Actions against SQL migrations.
   - Outputs a **Topological Workload & Cacheability Audit** in 60 seconds: identifies which slow queries are algebraically repairable via SmplCache, which tables lack `REPLICA IDENTITY FULL` (Class E), which plans suffer from implicit conversion or parameter skew (Classes A–C), and which hot columns form invalidation gravity wells (Class F, $\beta_1 > 0$).
2. **Wedge 2 — `SmplCache Pro Sidecar` (`smplcached` = SmplCache Hot Boundary + Embedded DuckDB Cold Rebuild $\to$ $2,500–$5,000/mo):**
   - Deployed as a single Docker container / Kubernetes sidecar next to the customer's existing PostgreSQL or SQL Server database.
   - Subscribes to the WAL/CDC stream, maintains registered multi-tenant dashboard shapes in memory (`KEEP` / `FIX IN PLACE`), uses embedded DuckDB for fast fallback `REREAD`s, and serves results over Redis `RESP3` or HTTP with zero stale-cache bugs and 70%–95% reduction in repetitive analytical SQL load on the primary database.

---

### Phase-by-Phase 30–180 Day Engineering & GTM Roadmap

#### Phase 1: Days 1–30 — Live DB Inspector, SQL Fragment Expansion, and Public Launch

- **Engineering Milestones:**
  1. **Deploy `SmplCache Live` (`https://smplcache.anomalon.com`):** Publish the existing FastAPI + TypeScript app (`demo/`) via `Dockerfile` / `render.yaml` with shareable URL round-trip state (`?scenario=customers_and_regions&seed=42&size=1000&aux=60`).
  2. **Expand Admitted SQL Fragment (`smplcache/extractor.py` & `smplcache/smplcache.py`):**
     - Support multi-aggregate projections in a single `QueryShape` (e.g., `COUNT(*)`, `SUM(a)`, `AVG(a)` maintained simultaneously in one group dictionary).
     - Support `IN (lit1, lit2, ...)`, `IS NULL`, `IS NOT NULL`, and `HAVING` clauses over maintained aggregates.
  3. **Ship Live DB Audit Connectors (`smplcache doctor --connect`):**
     - Add PostgreSQL inspector (`pg_stat_statements` top queries + `pg_class.relreplident` check + `EXPLAIN (FORMAT JSON)` operator parser).
     - Add SQL Server XML plan parser (`sys.dm_exec_query_plan` showplan XML ingester detecting Classes A–F).
  4. **Ship Live Streaming WAL / CDC Adapter (`smplcache/connectors/`):**
     - Add async PostgreSQL `wal2json` / Debezium JSONL stream tailer and SQL Server `cdc.<capture_instance>_CT` LSN poller with SQLite-backed durable LSN/offset checkpointing.
- **Commercial / GTM Milestones (Days 1–30):**
  - Launch open-source `smplcache` + live interactive demo on Hacker News, r/postgresql, r/SQLServer, and LinkedIn with the side-by-side "SQL Server / Postgres CDC vs. SmplCache" terminal recording.
  - Offer a free **"10-Minute Postgres & SQL Server Plan/CDC Topological Audit"** using `smplcache doctor`. Target: **50 CLI downloads/runs, 15 engineering discovery calls, 3 design partners signed.**

#### Phase 2: Days 31–60 — `smplcached` Sidecar + Embedded DuckDB Cold Rebuild + Shadow Mode

- **Engineering Milestones:**
  1. **Embedded DuckDB Cold-Rebuild Engine (`smplcache/rebuild_duckdb.py` + `AggregateCache.from_group_state`):**
     - Implement `AggregateCache.from_group_state(shape, group_stats)` so `AggregateCache` can be initialized with `_from_rows = True` directly from DuckDB's vectorized sufficient-statistics query (`SELECT grp, SUM(col), COUNT(col), COUNT(*) ... GROUP BY grp`) without iterating over millions of raw rows in Python.
     - Wire DuckDB as the default snapshot and `REREAD` recomputation engine: when `WalStreamProcessor` triggers `on_invalidate`, DuckDB executes the vectorized aggregate/join query over local Parquet/Arrow tables or `postgres_scanner` read-replica connections, re-seeds `AggregateCache.from_group_state` and `SharedJoinAuxPool` at the committed LSN watermark, and clears the invalidation latch.
  2. **Drop-In `smplcached` Daemon (HTTP + Redis `RESP3` Protocol):**
     - Expose a lightweight Redis-compatible read server (`GET smpl:v1:<shape_name>:<group_key>`) and REST endpoint (`GET /v1/shapes/{shape_name}`) so backend teams can swap `redis.get(...)` for `smplcache` in 10 lines of code while receiving `{data, certificate, boundary_clock_lsn, freshness_status}`.
  3. **Zero-Risk "Shadow Mode" Verifier:**
     - Allow `smplcached` to run in read-only shadow mode alongside a prospect's existing Redis cache or Postgres read replica, sampling reads to compare SmplCache's incremental state against live DB queries and generating a daily **ROI & Correctness Receipt** (queries avoided, latency saved, and any stale reads caught in the customer's legacy Redis layer).
- **Commercial / GTM Milestones (Days 31–60):**
  - Deploy `smplcached` in Shadow Mode at 3–5 design partner companies (B2B SaaS teams with slow customer-facing analytics dashboards or high AWS RDS / Azure SQL read-replica bills).
  - Convert first **2 paying pilot customers** at **$2,000–$2,500/month** ($48,000–$60,000 annualized run-rate) + **5 `SmplCache Doctor` subscribers** at **$499/month** ($30,000 annualized run-rate).

#### Phase 3: Days 61–90 — Rust Core Acceleration (`smplcache-rs`) & Multi-Tenant Shield

- **Engineering Milestones:**
  1. **Rust Core Kernel (`smplcache-rs` + `PyO3`):**
     - Port `AggregateCache`, `JoinAuxState`, `SharedJoinAuxPool`, `CountMinSketch`, and `wtinylfu` into a Rust crate (`smplcache-core`) with zero-copy Arrow/compact struct storage, true byte-level memory accounting (`max_total_bytes`), and a fixed-size fail-closed Bloom/Cuckoo/RoaringBitmap filter replacing unbounded `evicted_pks` / `evicted_fk_buckets` tombstone sets.
     - Target benchmark: **$>250,000\text{ CDC events/sec}$ per core** and **$5\times\text{–}8\times$ lower RAM per 100k auxiliary join rows** compared to CPython `dict` objects, checked against the existing 177-test parity suite.
  2. **Multi-Tenant Observer Quotient (`SmplCache Shield`):**
     - Enforce per-tenant `SharedJoinAuxPool` quotas and cryptographic tenant-key binding (`security_cols` / `tenant_id` partitioning) so a single high-volume tenant burst cannot evict other tenants' dimension rows or leak cross-tenant aggregates.
  3. **Prometheus / OpenTelemetry Observability Pack:**
     - Export real-time counters for `smplcache_keep_total`, `smplcache_fix_in_place_total`, `smplcache_reread_total{reason}`, `smplcache_duckdb_rebuild_duration_ms`, and `smplcache_oracle_mismatches_total`.
- **Commercial / GTM Milestones (Days 61–90):**
  - Publish reproducible **Rust + DuckDB vs. Redis / Postgres MV / Readyset Benchmark Report** showing throughput, tail latency ($p_{99} < 1\text{ ms}$ on `KEEP`/`FIX IN PLACE`, $<25\text{ ms}$ on DuckDB `REREAD`), and bounded memory under Zipfian skew ($s = 1.5$).
  - Expand to **5 paying Pro Sidecar customers** ($150,000 ARR) + **10 Doctor/CI customers** ($60,000 ARR) = **$210,000 ARR run-rate**.

#### Phase 4: Days 91–180 — Enterprise Scale, Fast Recovery, and $350,000 ARR / Seed Close

- **Engineering Milestones:**
  1. **Sub-Second Warm Restart (Boundary Checkpoint Snapshots):**
     - Periodically serialize `AggregateCache` + `SharedJoinAuxPool` + `CountMinSketch` + committed WAL LSN watermark to local NVMe or S3/MinIO in Arrow IPC / Parquet format so container restarts resume from the last LSN in $<1\text{ second}$ without a cold rebuild.
  2. **Automated SQL Fix PR Bot (`smplcache doctor --github-pr`):**
     - Automatically opens pull requests with exact T-SQL / PostgreSQL migrations when a new query shape or schema change introduces a Class A–F topological obstruction (e.g., missing covering index, implicit conversion, or missing `REPLICA IDENTITY FULL`).
  3. **Bounded `MIN` / `MAX` and Windowed Time-Bucket Rollups:**
     - Add bounded $k$-extremum heap state for `MIN`/`MAX` (falling back to DuckDB `REREAD` only when all $k$ tracked extrema are deleted) and tumbling time-window group keys (`DATE_TRUNC('hour', created_at)`).
- **Commercial / GTM Milestones (Days 91–180):**
  - Reach **$350,000+ ARR** across self-serve Doctor/CI packs, mid-market Pro Sidecar subscriptions, and enterprise DBA/architecture deployments—or close a **$1.5M–$3.0M Seed Round** backed by live production Shadow-Mode ROI receipts.

---

### Financial & Revenue Model: Three Concrete Paths to $350,000 in 2027

We do not rely on a single low-probability enterprise whale. Because SmplCache has near-zero COGS (runs as a lightweight sidecar or CLI inside the customer's own VPC/cloud), gross margins exceed 90%. Here are three realistic customer compositions that clear **$350,000/year**:

| Tier / Offering | Target Buyer | Pricing | Path A: SaaS Sidecar Focus | Path B: DBA + Mid-Market Mix | Path C: Seed-Ready Hybrid + Services |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. `SmplCache Doctor & CI Gate`**<br>*(Automated Postgres/MSSQL Plan, WAL & TopoMap Auditor)* | Working DBAs, Staff Engineers, DevOps teams | **$499 / mo**<br>*($5,988 / yr)* | 12 teams<br>**$71,856 / yr** | 20 teams<br>**$119,760 / yr** | 15 teams<br>**$89,820 / yr** |
| **2. `SmplCache Pro Sidecar`**<br>*(Rust + DuckDB Certified Cache Sidecar, up to 2 DB clusters)* | Series A–C B2B SaaS with slow multi-tenant dashboards or high RDS/Azure SQL load | **$2,500 / mo**<br>*($30,000 / yr)* | 8 customers<br>**$240,000 / yr** | 5 customers<br>**$150,000 / yr** | 6 customers<br>**$180,000 / yr** |
| **3. `SmplCache Enterprise / DBA Remediation`**<br>*(Unlimited shapes, Multi-Tenant Shield, custom CDC + Topological Audit engagement)* | FinTech, Healthcare, E-Commerce with heavy SQL Server / Postgres estates | **$4,000 / mo**<br>*($48,000 / yr)* | 1 enterprise<br>**$48,000 / yr** | 2 enterprises<br>**$96,000 / yr** | 2 enterprises<br>**$96,000 / yr** |
| **Total Annualized Revenue (ARR)** | — | — | **$359,856 / yr** | **$365,760 / yr** | **$365,820 / yr** |

#### What 2027 Seed Investors Will Audit (If Raising $1.5M–$3.0M Seed Instead of Pure Bootstrapping)

Institutional seed investors in developer infrastructure evaluate four verifiable artifacts:
1. **The 60-Second Interactive Demo (`smplcache.anomalon.com`):** A live, zero-install web lab where an investor or technical advisor can mutate rows, drop required CDC evidence, lower auxiliary join capacity, and inspect the resulting certificates and SQLite/DuckDB oracle verification counts.
2. **Architectural Defensibility ("Why Doesn't Redis or DuckDB Just Do This?"):**
   - Redis is key-value and lacks SQL AST dependency decomposition, Theorem 7 CDC evidence checking, and foreign-key fanout state (`SharedJoinAuxPool`).
   - DuckDB is a batch/analytical columnar scanner and lacks streaming WAL transaction buffering, `wtinylfu` auxiliary join eviction with tombstone safety, and per-event repair certification.
   - SmplCache combines a compact boundary-certification engine with DuckDB as its cold-rebuild engine, delivering the speed of an in-memory cache with the relational correctness of a database.
3. **Shadow-Mode Production Receipts:** Telemetry from 5–8 production deployments showing $70\%\text{–}95\%$ reduction in repetitive `GROUP BY` / `JOIN` queries against primary PostgreSQL/SQL Server instances with **zero oracle mismatches**.
4. **Capital-Efficient Wedge:** Because SmplCache deploys as a non-invasive sidecar (reading CDC/WAL and serving read-through queries with automatic fallback to the primary DB or DuckDB), customer adoption takes hours rather than the 9-month migration required by a new primary database.

---

## Part VI: Claim Hygiene & Epistemic Audit Summary

In strict accordance with the project Claim Hygiene Rule:

1. **What This Artifact Supports:**
   - Supports an architectural and operational comparison between SmplCache (`v0.1.0`), Redis/Valkey cache-aside patterns, SQL Server CDC and Indexed Views, PostgreSQL Materialized Views and logical replication (`pgoutput` / `wal2json`), heuristic caching proxies, streaming dataflow engines, and DuckDB across four concrete write scenarios (disjoint column update, missing before-image update, complete aggregate update, and foreign-key dimension move).
   - Grounds the product review in the verified capabilities of [`c:\Users\a343o\source\repos\smplcache\smplcache`](file:///c:/Users/a343o/source/repos/smplcache/smplcache) (177 passing unit, regression, property, and demo acceptance tests; `sqlglot` AST allowlisting; Theorem 7 evidence checking; `JoinAuxState` / `SharedJoinAuxPool` with `CountMinSketch` and `wtinylfu`; `WalStreamProcessor` transaction buffering and invalidation latching; `smplcache doctor`, `smplcache report --topomap`, `smplcache compare`, and `smplcache matrix`).
   - Outlines a concrete 30–180 day engineering roadmap and bottom-up commercial model showing how a lightweight **SmplCache + Embedded DuckDB** sidecar and **Topological Workload Auditor (`doctor`)** can reach $350,000 ARR or seed-stage investment readiness in 2027.

2. **What This Artifact Does Not Support:**
   - Does not claim that SmplCache replaces a general-purpose transactional RDBMS or supports unconstrained arbitrary SQL outside its declared allowlisted fragment.
   - Does not claim that the proposed Rust core port (`smplcache-rs`), bounded Bloom/Cuckoo eviction tombstone filter, `AggregateCache.from_group_state()` constructor, live network Kafka/PostgreSQL/MSSQL streaming connectors, or embedded DuckDB cold-rebuild integration are already implemented in `v0.1.0`; those are explicitly scoped as Days 1–90 roadmap deliverables.
   - Does not treat synthetic benchmark figures (such as the 4-phase Zipfian oscillation sweep in `smplcache/benchmark.py`) as production latency or hardware RAM measurements, nor does a revenue projection constitute realized customer contracts.

3. **Narrowest Defensible Claim:**
   - Within its admitted single-table and foreign-key equijoin SQL fragment (`SUM`, `COUNT`, `COUNT(*)`, `AVG`), SmplCache deterministically classifies decoded CDC/WAL write events into `KEEP` (`preserve`), `FIX IN PLACE` (`repair`), or `REREAD` (`invalidate` / `unsupported`) backed by machine-readable certificates and checked against an independent SQLite oracle across 177 automated tests; extending this core with `AggregateCache.from_group_state()`, live CDC connectors, an embedded DuckDB cold-rebuild substrate, and a Rust state kernel provides a compact sidecar architecture that avoids both the uncertified staleness of key-value caches and the operational footprint of a full secondary RDBMS.

4. **Next Test Needed:**
   - Implement `AggregateCache.from_group_state()` and the Phase 1/2 live PostgreSQL logical replication (`wal2json` / `pgoutput`) + embedded DuckDB cold-rebuild prototype, then execute a concurrent read/write benchmark comparing primary PostgreSQL query load, `KEEP`/`FIX IN PLACE`/`REREAD` decision distribution, $p_{50}/p_{99}$ read latency, and physical process RSS memory against baseline PostgreSQL `REFRESH MATERIALIZED VIEW CONCURRENTLY` and standalone DuckDB scans.

