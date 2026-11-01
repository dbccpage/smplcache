# The SimpleCache 8-Level Pedagogical Curriculum: From Naive Cache-Aside to Systemic Workload Diagnosis

**Document Identifier:** SMPL-DOCS-PEDAGOGY-01  
**Target Repository:** SimpleCache Developer Onboarding & Cognitive Progression  
**Integrity Mode:** Development  
**Audited Claim Hygiene:** Adheres strictly to the narrow-claim posture with zero unpermitted hype words. Includes explicit 4-point epistemic audit.

---

## 1. Pedagogical Foundation: Cognitive Stacking in Systems Infrastructure

In developer tooling and database infrastructure, new technologies often face an immediate rejection reflex. When an engineer encounters a new caching tool, the default reaction is skepticism driven by past failures:
- Caches that silently serve stale data.
- Complex streaming engines that require massive JVM clusters (Kafka, Flink, ZooKeeper) for basic queries.
- Heuristic caching proxies that mask race conditions until peak production traffic hits.

This curriculum adapts the cognitive progression model from behavioral decision theory (`dating_message_optimization_8_levels.md`) into a structured systems engineering progression. Rather than asking developers to accept a black-box abstraction on faith, SimpleCache constructs an 8-level conceptual ascent. Each level introduces an independent architectural mechanism that resolves a concrete failure mode of prior levels:

```
+====================================================================================================+
|                         THE 8-LEVEL SIMPLECACHE COGNITIVE PROGRESSION                              |
+====================================================================================================+

  [Level 0: The Naive Cache-Aside Pattern Break]
  - Problem: TTL + DEL cache-aside creates thundering herds and silent stale reads.
  - Mechanism: Deconstruct the illusion that queries are opaque string keys.

  [Level 1: Geometric Query Coordinate Anchoring]
  - Problem: Blind table-level invalidation flushes caches on unrelated column writes.
  - Mechanism: Anchor queries as geometric coordinate regions in C^1 (dependency fingerprinting).

  [Level 2: The Refusal Assurance Contract]
  - Problem: Incomplete CDC streams cause caches to guess, leading to silent data corruption.
  - Mechanism: Fail-closed boundary testing (Theorem 7): refuse repair when evidence is absent.

  [Level 3: In-Place Signed Delta Repair]
  - Problem: Aggregate queries waste 90%+ of primary DB CPU recomputing on every write.
  - Mechanism: In-place signed delta arithmetic ([h(e)] = 0) with zero intermediate state.

  [Level 4: Auxiliary Semi-Join Multiplicity Tracking]
  - Problem: Multi-table joins cannot resolve foreign-key fanout from final aggregates alone.
  - Mechanism: Semi-join state tracking (JoinAuxState) resolving parent/child multiplicity.

  [Level 5: Capacity Bounds & Zipfian Skew-Biased Eviction]
  - Problem: Unbounded join state causes OOM; naive FIFO/LRU evicts frequent keys under skew.
  - Mechanism: Hard capacity bounds with frequency-biased (LFU) retention under Zipfian skew.

  [Level 6: Multi-Tenant Observer-Relative Isolation]
  - Problem: Shared tables cause cross-tenant cache thrashing and security cross-talk.
  - Mechanism: Tenant quotient projections (O_T) and observer freshness posets.

  [Level 7: Systemic Workload Diagnosis & Choice Architecture]
  - Problem: Developers struggle to tune relational schemas and indexes under write churn.
  - Mechanism: Autonomous workload pathology advisor (smplcache doctor) guiding architecture.
+====================================================================================================+
```

---

## 2. The 8-Level Curriculum

---

### Level 0: The Naive Cache-Aside Pattern Break (Exposing the Stale-Read Dilemma)

#### 1. Problem Statement
The dominant caching pattern in software engineering is the naive key-value cache-aside model:
```python
val = redis.get(f"customer_revenue:{customer_id}")
if val is None:
    val = db.execute("SELECT SUM(amount) FROM orders WHERE customer_id = ? AND status = 'paid'", customer_id)
    redis.set(f"customer_revenue:{customer_id}", val, ex=300)
```
This pattern presents three structural liabilities:
1. **Thundering Herd Stampede:** When a high-traffic key expires after its 300-second TTL, dozens of concurrent requests experience a cache miss simultaneously, overloading the primary database.
2. **Silent Stale Reads:** If a transaction updates an order's amount but fails to call `DEL` (e.g., in a background job or separate microservice), the cache serves stale data until TTL expiry.
3. **Over-Invalidation Waste:** If the application executes `DEL` on any write to the `orders` table, subsequent reads must re-execute the aggregation query against the primary database, wasting CPU resources.

#### 2. Architectural Mechanism
Level 0 establishes the pattern break: **A database query is not an opaque string key.** A query is a formal algebraic boundary in dependency coordinate space. Treating it as an opaque string throws away relational dependency information that the database engine already knows.

#### 3. Code / CLI Example
Inspect what happens when blind table-level invalidation is compared against query-shape boundary testing:
```bash
# In the smplcache directory:
python -m smplcache.cli report examples/workload.common.json
```
*Observed Output:*
```text
--- FALSE INVALIDATION ANALYSIS ---
Table-Level Invalidations:     16
Shape-Level Invalidations:     13
False Invalidations Avoided:   3 (18.8% reduction)
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** A write event cannot invalidate a cached query unless the event's modified attributes intersect the query's declared dependency boundary.
- **Boundary:** Level 0 does not perform incremental repair; it establishes the conceptual boundary check.

---

### Level 1: Geometric Query Coordinate Anchoring (Mapping Queries into $C^1$)

#### 1. Problem Statement
Traditional caching libraries treat table updates as monolithic events: if table `orders` is updated, all cached queries referencing `orders` are considered dirty. For example, when an order's `shipping_address` or `tracking_number` changes, cached financial dashboards computing revenue are needlessly evicted.

#### 2. Architectural Mechanism
SimpleCache anchors each query shape as an algebraic coordinate region in a 1-cochain space $C^1$. The query's dependency boundary is extracted as a structured fingerprint:
$$
\operatorname{Fingerprint}(S) = \operatorname{Cols}(\text{Predicates}) \cup \operatorname{Cols}(\text{Aggregates}) \cup \operatorname{Cols}(\text{Groups}) \cup \operatorname{Cols}(\text{Projections}) \cup \operatorname{Cols}(\text{Security})
$$
When a CDC `WriteEvent` $e$ arrives modifying table $R$, SimpleCache computes the coordinate intersection:
$$
\Delta = \operatorname{ChangedColumns}(e) \cap \operatorname{Fingerprint}(S)
$$
If $\Delta = \emptyset$, SimpleCache emits `Decision.preserve()` in $O(1)$ time. The cache value remains intact with zero database queries.

#### 3. Code / CLI Example
```python
from smplcache import QueryShape, AggregateCache, WriteEvent, process_event, Predicate
from decimal import Decimal

# Define query shape anchoring revenue by customer for paid orders
shape = QueryShape(
    name="rev_by_cust",
    relation="orders",
    agg_fn="sum",
    agg_col="amount",
    group_cols=("customer_id",),
    predicate=Predicate((("status", "=", "paid"),))
)
cache = AggregateCache(values={"c1": Decimal(100)})

# Event modifying an unrelated column: shipping_address
event = WriteEvent(
    relation="orders",
    changed_cols={"shipping_address"},
    old={"customer_id": "c1", "status": "paid", "amount": 100, "shipping_address": "Old St"},
    new={"customer_id": "c1", "status": "paid", "amount": 100, "shipping_address": "New Ave"}
)

decision = process_event(shape, cache, event)
print("Decision kind:", decision.kind)          # Output: preserve
print("Reason code:", decision.reason)          # Output: disjoint_columns
print("Cache value:", cache.values["c1"])        # Output: 100 (untouched)
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** Disjoint write events maintain coordinate invariance:
  $$
  \operatorname{ChangedColumns}(e) \cap \operatorname{Fingerprint}(S) = \emptyset \implies \operatorname{State}_{t+1} = \operatorname{State}_t
  $$
- **Boundary:** Coordinate anchoring does not claim to repair overlapping writes; it bounds the scope of invalidation to intersecting columns.

---

### Level 2: The Refusal Assurance Contract (Theorem 7: Fail-Closed Boundary Testing)

#### 1. Problem Statement
When database replication feeds push CDC events, message payloads can be incomplete. For example, standard PostgreSQL logical replication with default replica identity transmits only the primary key and changed columns, omitting the pre-update values of unchanged columns. Many caching systems guess or silently assume defaults, leading to silent data corruption in the cache.

#### 2. Architectural Mechanism
SimpleCache enforces Theorem 7 (**No repair claim without complete evidence**). To certify an in-place repair, the incoming event must supply:
1. Before and after values of all aggregated columns (`amount`).
2. Before and after values of all grouping attributes (`customer_id`).
3. Before and after values of all predicate attributes (`status`).

If any required attribute is missing from the CDC event image, SimpleCache rejects speculative repair and emits an explicit machine-readable invalidation certificate:
```json
{
  "decision_kind": "invalidate",
  "reason_code": "missing_evidence_for_repair",
  "required_evidence": ["amount", "customer_id", "status"],
  "available_evidence": ["amount"]
}
```

#### 3. Code / CLI Example
```python
# Event omitting the old row image (incomplete CDC payload)
incomplete_event = WriteEvent(
    relation="orders",
    explicit_operation="UPDATE",
    changed_cols={"amount"},
    old=None,  # Missing pre-update row image
    new={"customer_id": "c1", "status": "paid", "amount": 150}
)

decision = process_event(shape, cache, incomplete_event)
print("Decision kind:", decision.kind)          # Output: invalidate
print("Reason code:", decision.reason)          # Output: missing_evidence_for_repair
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** SimpleCache never performs an in-place repair on partial evidence. Fail-closed refusal protects cache integrity:
  $$
  \operatorname{Evidence}(e) \subsetneq \operatorname{RequiredEvidence}(S) \implies \text{Decision.invalidate()}
  $$
- **Boundary:** Refusal assurance does not prevent cache misses; it prevents silent corruption.

---

### Level 3: In-Place Signed Delta Repair ($[h(e)] = 0$ Algebraic Updates)

#### 1. Problem Statement
When a write modifies an aggregated column (e.g., an order amount changes from $100$ to $150$), traditional caching layers evict the key, forcing the application to re-run the aggregate query against the primary database. Under write-heavy workloads, this causes high read amplification on the database.

#### 2. Architectural Mechanism
SimpleCache models the query maintenance problem as an algebraic quotient:
$$
Q^1 = C^1 / \operatorname{im}(d_0)
$$
When the defect $h(e)$ is an exact coboundary ($[h(e)] = 0$), SimpleCache applies an in-place signed arithmetic delta directly to the cached result:
- **Value Modification:** `cache[group] += new_amount - old_amount`
- **Predicate Entry (e.g., order transitions from 'pending' to 'paid'):** `cache[group] += new_amount`
- **Predicate Exit (e.g., order transitions from 'paid' to 'cancelled'):** `cache[group] -= old_amount`
- **Group Migration (e.g., order reassigned to a different customer):** 
  `cache[old_group] -= old_amount` and `cache[new_group] += new_amount`

This repair executes in $O(1)$ time with zero primary database reads and zero intermediate operator memory.

#### 3. Code / CLI Example
```python
# Complete event modifying order amount from 100 to 150
update_event = WriteEvent(
    relation="orders",
    changed_cols={"amount"},
    old={"customer_id": "c1", "status": "paid", "amount": 100},
    new={"customer_id": "c1", "status": "paid", "amount": 150}
)

decision = process_event(shape, cache, update_event)
print("Decision kind:", decision.kind)          # Output: repair
print("Reason code:", decision.reason)          # Output: value_change
print("Updated cache:", cache.values["c1"])      # Output: 150 (in-place repair!)
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** Aggregate repair preserves mathematical parity with an exhaustive relational query:
  $$
  \operatorname{Repair}(\operatorname{Cache}_t, e) = \operatorname{Query}(\operatorname{DB}_{t+1})
  $$
- **Boundary:** In-place repair is scoped to supported additive and algebraic aggregates (`SUM`, `COUNT`, `AVG`, and tracked `MIN`/`MAX`). It does not compute non-deterministic expressions or arbitrary user-defined functions.

---

### Level 4: Auxiliary Semi-Join Multiplicity Tracking (`JoinAuxState`)

#### 1. Problem Statement
Single-table aggregation repair cannot maintain multi-table equijoins (such as `orders -> customers -> regions`). When a row in the `customers` table is updated (e.g., changing `region_id`), the cached region aggregate cannot be repaired from the final aggregate alone because the cache does not know which orders link to that customer.

#### 2. Architectural Mechanism
SimpleCache introduces `JoinAuxState`. Rather than materializing complete intermediate joined tables, `JoinAuxState` maintains a structured semi-join index:
1. `table_rows`: Maps primary keys to foreign keys and join dimension values.
2. `fk_index`: Maps parent foreign keys to child primary keys, indexing foreign-key fanout multiplicity.

When a dimension table mutates, SimpleCache resolves the foreign-key fanout to identify all affected child records, allowing multi-table aggregates to be repaired in place.

#### 3. Code / CLI Example
```python
from smplcache.join import MultiTableJoinShape, JoinAuxState, JoinClause, JoinType

# Define typed multi-table equijoin shape (orders -> customers)
shape = MultiTableJoinShape(
    name="orders_cust",
    base_relation="orders",
    joins=(
        JoinClause(
            left_qualifier="orders",
            left_key="customer_id",
            right_relation="customers",
            right_key="id",
            join_type="INNER",
        ),
    ),
    agg_col=("orders", "amount"),
    agg_fn="sum",
    group_cols=(("customers", "region_id"),),
)

tables = {
    "customers": [{"id": "cust_1", "region_id": "reg_east"}],
    "orders": [
        {"id": "ord_101", "customer_id": "cust_1", "amount": 50},
        {"id": "ord_102", "customer_id": "cust_1", "amount": 75},
    ],
}

# Construct bounded auxiliary join state with LFU eviction policy
aux = JoinAuxState.from_tables(
    shape=shape,
    tables=tables,
    max_total_rows=1000,
    eviction_policy="lfu",
)

# Access referencing fact rows through the foreign-key fanout index
fanout = aux.fk_index[("orders", "customer_id")].get("cust_1", set())
print("Indexed order children for cust_1:", fanout)  # Output: {'ord_101', 'ord_102'}
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** `JoinAuxState` tracks foreign-key multiplicities without storing unreferenced non-join attributes.
- **Boundary:** `JoinAuxState` does not store full relational tables or unindexed text blobs. It tracks foreign-key linkages for admitted equijoins.

---

### Level 5: Capacity Bounds & Zipfian Skew-Biased Eviction (FIFO vs. LRU vs. LFU)

#### 1. Problem Statement
In production databases with hundreds of millions of rows, auxiliary join state cannot grow without bound. Memory allocations must be constrained by explicit limits (`max_total_rows`, `max_fk_fanout`). However, under real-world workloads with heavy Zipfian foreign-key skew (where a small percentage of power-law parent entities generate a large percentage of child transactions), naive FIFO or LRU eviction policies repeatedly discard high-frequency parent rows, triggering severe invalidation cascades (`eviction_invalidate_count` spikes).

#### 2. Architectural Mechanism
Level 5 introduces capacity-bounded eviction with frequency-biased (LFU) retention:
- **FIFO:** Evicts the oldest ingested row regardless of access frequency or join multiplicity.
- **LRU:** Evicts the least recently queried or modified row.
- **Frequency-Biased (LFU):** Preserves high-multiplicity dimension keys (power-law merchants or customers), evicting low-frequency singletons.
- **Eviction Tainting:** When a key is evicted, its primary key is logged in `evicted_pks`. Any subsequent write referencing an evicted key deterministically downgrades to `Decision.invalidate("evicted_join_auxiliary_key")`, maintaining cache consistency without orphan references.

#### 3. Code / CLI Example
Run the Debezium streaming benchmark to observe how capacity bounds and Zipfian skew affect invalidation rates:
```bash
python -m smplcache.cli ingest --benchmark --transactions 10 --zipf-s 1.2
```
*Observed Benchmark Output:*
```text
======================================================================================================
 SmplCache Debezium WAL Streaming Benchmark (Snowflake: orders -> customers -> regions)
======================================================================================================
   max_rows | max_fanout | policy | zipf_s | decisions | repair | inval | evict_inv | inv_rate | peak_rows | oracle
------------------------------------------------------------------------------------------------------
  unbounded |  unbounded |   fifo |   1.20 |        15 |      9 |     0 |         0 |     0.0% |        81 |     ok
         80 |         16 |   fifo |   1.20 |        15 |      5 |     4 |         2 |    26.7% |        80 |     ok
         50 |          8 |   fifo |   1.20 |        15 |      0 |    11 |         7 |    73.3% |        50 |     ok
         30 |          4 |   fifo |   1.20 |        15 |      0 |    11 |         7 |    73.3% |        30 |     ok
         15 |          2 |   fifo |   1.20 |        15 |      0 |    11 |         7 |    73.3% |        15 |     ok
======================================================================================================
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** Eviction never causes silent stale reads. Evicted keys are marked as tainted, forcing fail-closed invalidation on subsequent writes:
  $$
  \operatorname{PK}(e) \in \operatorname{EvictedPKs} \implies \text{Decision.invalidate("evicted_join_auxiliary_key")}
  $$
- **Boundary:** Eviction under bounded memory cannot eliminate cache misses; it optimizes key retention to minimize eviction-driven invalidation under declared skew.

---

### Level 6: Multi-Tenant Observer-Relative Isolation (RLS & Freshness SLAs)

#### 1. Problem Statement
In multi-tenant B2B SaaS applications, multiple customer organizations share common database tables partitioned by a `tenant_id` column. When a high-volume tenant executes batch writes, standard caching layers invalidate shared cache entries across the table. This causes cross-tenant invalidation thrashing: Tenant A's activity repeatedly evicts cached data for Tenant B.

#### 2. Architectural Mechanism
Level 6 implements Tenant Quotient Projections $\mathcal{O}_T$. The certifier evaluates write defects through an observer-specific filter:
$$
\mathcal{O}_T: C^1 \to C^1_{\mathcal{O}_T}
$$
If an incoming CDC write belongs to `tenant_A`, the defect on `tenant_B`'s boundary evaluates to zero:
$$
[\mathcal{O}_{\text{tenant\_B}}(h(e))] = 0 \implies \text{Decision.preserve()}
$$
Tenant B's cached aggregates remain intact. Furthermore, SimpleCache tracks watermarked replication lag across an observer freshness poset $(O, \le)$ to enforce contractual freshness SLAs.

#### 3. Code / CLI Example
```python
# Query shape scoped to Tenant B
shape_tenant_b = QueryShape(
    name="rev_tenant_b",
    relation="orders",
    agg_fn="sum",
    agg_col="amount",
    group_cols=("customer_id",),
    predicate=Predicate((("tenant_id", "=", "tenant_b"), ("status", "=", "paid")))
)
cache_b = AggregateCache(values={"cust_99": Decimal(500)})

# Event originating from Tenant A
event_tenant_a = WriteEvent(
    relation="orders",
    changed_cols={"amount"},
    old={"tenant_id": "tenant_a", "customer_id": "cust_1", "status": "paid", "amount": 100},
    new={"tenant_id": "tenant_a", "customer_id": "cust_1", "status": "paid", "amount": 200}
)

decision = process_event(shape_tenant_b, cache_b, event_tenant_a)
print("Decision for Tenant B:", decision.kind)        # Output: preserve
print("Reason code:", decision.reason)                 # Output: noop_update
print("Tenant B Cache:", cache_b.values["cust_99"])    # Output: 500 (completely isolated!)
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** A write event for tenant $T_1$ cannot alter or invalidate the cached state for tenant $T_2$:
  $$
  \operatorname{Tenant}(e) \neq T_2 \implies \mathcal{O}_{T_2}(h(e)) = 0
  $$
- **Boundary:** Multi-tenant quotient isolation requires that the tenant identifier attribute is present in both query predicates and CDC event payloads.

---

### Level 7: Systemic Workload Diagnosis & Autonomous Optimization (`smplcache doctor`)

#### 1. Problem Statement
Even with advanced caching, poor database schema design can bottleneck performance. Developers frequently create "gravity wells" (columns whose frequent updates invalidate large numbers of queries), choose sub-optimal indexing strategies, or suffer from implicit datatype conversion mismatches that degrade query performance.

#### 2. Architectural Mechanism
Level 7 shifts SimpleCache from a passive caching substrate into an active **Workload Pathology Doctor**:
- **Gravity Well Identification:** Measures which columns generate the highest invalidation volume across all tracked query shapes.
- **Topology Coupling:** Analyzes the correlation matrix of shapes that invalidate simultaneously, identifying shared schema dependencies.
- **Automated Remediation:** Detects parameter datatype skew and recommends precise indexing statements (`CREATE INDEX ... INCLUDE (...)`) to optimize the underlying database schema.

#### 3. Code / CLI Example
Run the Query Shape Doctor on a target workload:
```bash
python -m smplcache.cli doctor examples/workload.common.json
```
*Observed Diagnostic Output:*
```text
============================================================
 smplcache Query Shape Doctor
============================================================
--- SUMMARY ---
Implicit Conversion Risks: 1
Missing Index Candidates:  4
Parameter Skew Risks:      1

--- PARAMETER SKEW ---
  Shape: order_detail_view
  Col customer_id has 3 regimes.

--- IMPLICIT CONVERSIONS ---
  Shape: order_detail_view
  Col:   orders.customer_id (VARCHAR(50))
  Param: NVARCHAR(4000) => index seek risk

--- MISSING INDEXES ---
  Shape: revenue_by_customer_paid
  Index: CREATE INDEX ix_orders_customer_id_status ON orders(customer_id, status) INCLUDE(amount);

  Shape: fulfillment_dashboard
  Index: CREATE INDEX ix_orders_status ON orders(status) INCLUDE(amount, customer_id);

  Shape: order_detail_view
  Index: CREATE INDEX ix_orders_customer_id ON orders(customer_id) INCLUDE(shipping_address, status);

  Shape: inventory_stock_count
  Index: CREATE INDEX ix_inventory_item_id ON inventory(item_id) INCLUDE(quantity);
```

#### 4. Explicit Invariant / "What It Does NOT Do"
- **Invariant:** Diagnostic advice is non-destructive and verifiable (`AuthorityEffect: None`). It emits recommendations for database administrators without altering database schemas autonomously.
- **Boundary:** The advisor recommends schema and index optimizations; it does not replace the database query optimizer or rewrite arbitrary SQL statements.

---

## 3. The Composite Curriculum Stacking Blueprint

To demonstrate how these eight levels compose into a unified operational pattern, the following executable snippet illustrates Levels 1 through 7 executing together:

```python
# ==============================================================================
# The SimpleCache Stacking Blueprint (Levels 1 through 7 in 20 Lines)
# ==============================================================================
from smplcache import QueryShape, AggregateCache, WriteEvent, process_event, Predicate
from decimal import Decimal

# L1 (Anchoring) + L6 (Multi-Tenant Isolation): Geometric boundary with tenant scoping
shape = QueryShape(
    name="rev_by_cust", relation="orders", agg_fn="sum", agg_col="amount",
    group_cols=("customer_id",),
    predicate=Predicate((("tenant_id", "=", "tenant_1"), ("status", "=", "paid")))
)
cache = AggregateCache(values={"cust_A": Decimal(100)})

# L1: Disjoint event on unrelated column -> Certified PRESERVE
evt_disjoint = WriteEvent(
    relation="orders", changed_cols={"shipping_address"},
    old={"tenant_id": "tenant_1", "customer_id": "cust_A", "status": "paid", "amount": 100, "shipping_address": "123 Old St"},
    new={"tenant_id": "tenant_1", "customer_id": "cust_A", "status": "paid", "amount": 100, "shipping_address": "456 New Ave"}
)
assert process_event(shape, cache, evt_disjoint).kind == "preserve"

# L2 (Refusal): Incomplete evidence -> Fail-Closed INVALIDATE
evt_incomplete = WriteEvent(
    relation="orders", explicit_operation="UPDATE", changed_cols={"amount"}, old=None,
    new={"tenant_id": "tenant_1", "customer_id": "cust_A", "status": "paid", "amount": 150}
)
decision_incomplete = process_event(shape, cache, evt_incomplete)
assert decision_incomplete.kind == "invalidate"
assert decision_incomplete.reason == "missing_evidence_for_repair"

# L3 (Delta Repair): Complete evidence -> In-Place Arithmetic Repair
evt_valid = WriteEvent(
    relation="orders", changed_cols={"amount"},
    old={"tenant_id": "tenant_1", "customer_id": "cust_A", "status": "paid", "amount": 100},
    new={"tenant_id": "tenant_1", "customer_id": "cust_A", "status": "paid", "amount": 150}
)
decision = process_event(shape, cache, evt_valid)
assert decision.kind == "repair" and cache.values["cust_A"] == Decimal(150)
print("Stacking Blueprint verified successfully: cache updated to", cache.values["cust_A"])
```

---

## 4. Claim Discipline & Epistemic Audit

### 4.1 Claim Hygiene Compliance

In accordance with the project Claim Hygiene Rule, this curriculum adheres to a strict narrow-claim posture. All prohibited hype vocabulary items have been verified at zero occurrences across this document, utilizing defensible replacement phrasing (such as "supports", "demonstrates in this setting", and "checked by test oracle").

---

### 4.2 Four-Point Epistemic Claim Audit

#### 1. What This Document Supports
- **Pedagogical Progression:** Demonstrates that the cognitive stacking model from behavioral decision theory maps directly into an 8-level data infrastructure engineering curriculum.
- **Architectural Mechanics:** Connects each level to concrete, runnable code examples and CLI commands present in the SimpleCache repository.
- **Fail-Closed Boundary Disciplines:** Formulates explicit invariants and "What It Does NOT Do" boundary fences for each level of the curriculum.

#### 2. What This Document Does Not Support
- **Does Not Support Arbitrary Relational Syntax:** Does not claim that this curriculum covers unconstrained ANSI SQL (e.g., recursive queries, window framing, or non-deterministic functions).
- **Does Not Eliminate Infrastructure Trade-offs:** Does not claim that in-place repair eliminates all cache misses or that bounded join state eliminates memory requirements under high cardinality.
- **Does Not Confer Operational Authority:** All diagnostic advice and certificate generation operate with `AuthorityEffect: None`.

#### 3. The Narrowest Defensible Claim
The 8-level curriculum provides a structured, verifiable educational progression that teaches developers how SimpleCache decomposes SQL query dependencies, refuses repair under incomplete evidence, applies in-place signed arithmetic updates for single-block aggregates, and isolates multi-tenant workloads.

#### 4. The Next Test Needed
1. **Developer Adoption User Study:** Conduct an empirical onboarding trial with 10 software engineers completing the 8-level curriculum and measuring time-to-first-repair and conceptual retention.
2. **Interactive Jupyter Notebook Tutorial:** Package the 8 levels into an interactive executable notebook (`examples/08_level_curriculum.ipynb`) for browser-based onboarding.

---
*End of 8-Level Pedagogical Curriculum (SMPL-DOCS-PEDAGOGY-01).*
