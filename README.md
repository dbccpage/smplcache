# SmplCache: Cache Boundary Diagnostic Engine

SmplCache is an open-core diagnostic substrate and database caching engine that tests the repairability of cached database workloads under streaming CDC (Change Data Capture) updates.

Unlike traditional heuristic caches that flush everything or rely on logical replication guessing, SmplCache executes explicit boundary testing. The model is analyzed on paper for a finite relational fragment; single-relation `SUM`, `COUNT`, `COUNT(*)`, `AVG`, and projection shapes are implemented and checked against a brute-force SQLite oracle.

---

## Quick Navigation

- [15-Minute Adoption Quickstart](../docs/quickstart_15_minute_adoption.md)
- [8-Level Pedagogical Curriculum](../docs/pedagogical_8_level_curriculum.md)
- [SOTA Systems Review & Commercial Strategy](../docs/sota_caching_review_and_commercial_strategy.md)
- [Research Forum Dossier](../docs/research_forum/README.md)

---

## Core Philosophy

SmplCache explores whether data systems over-invalidate because they route state changes through machinery that does not check repair evidence.

Relational theory normalizes data at rest. SmplCache evaluates the repairability of data in motion within its declared SQL fragment.

### The Professional Bar
1. **No silent under-invalidation.**
2. **No repair claim without evidence.**
3. **No unsupported SQL feature quietly accepted.**

---

## 15-Minute Adoption Quickstart

Get started with SmplCache in under 15 minutes:

```bash
# 1. Install package in editable mode
pip install -e ".[dev]"

# 2. Run the workload advisor on the example schema
python -m smplcache.cli report examples/workload.common.json

# 3. Run query shape doctor diagnostics
python -m smplcache.cli doctor examples/workload.common.json

# 4. Execute streaming in-place delta repair demo
python examples/01_python_orders_revenue_demo.py

# 5. Run multi-table join benchmark under Zipfian foreign-key skew
python -m smplcache.cli ingest --benchmark --transactions 10 --zipf-s 1.2

# 6. Run unit tests
python -m pytest tests/test_smplcache.py
```

For the complete walk-through and terminal output logs, refer to the [15-Minute Adoption Guide](../docs/quickstart_15_minute_adoption.md).

---

## How It Works

SmplCache decomposes a cached query shape into its dependency boundary (predicates, aggregates, groups, projections, security columns).

When a CDC `WriteEvent` occurs, SmplCache:
1. Checks whether the event intersects the shape boundary.
2. Checks whether the event contains sufficient old/new row evidence to repair the cache (Theorem 7).
3. Emits a `Decision` (`preserve`, `repair`, `invalidate`, `unsupported`) backed by a machine-readable `Certificate`.

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
- **Level 3 (In-Place Delta Repair):** Applying $O(1)$ arithmetic updates ($[h(e)]=0$) directly to output tuples.
- **Level 4 (Auxiliary Semi-Join Tracking):** Tracking foreign-key fanout in `JoinAuxState`.
- **Level 5 (Capacity Bounds & Skew Eviction):** Enforcing hard row limits with LFU retention under Zipfian skew.
- **Level 6 (Multi-Tenant Isolation):** Eliminating cross-tenant cache thrashing via tenant quotient projections $\mathcal{O}_T$.
- **Level 7 (Systemic Workload Diagnosis):** Autonomous workload pathology analysis with `smplcache doctor`.

See [8-Level Curriculum](../docs/pedagogical_8_level_curriculum.md) for full details.

---

## Five Commercial Differentiators

1. **Zero-Intermediate-State In-Place Delta Repair:** $O(|\text{Result}|)$ memory state, delivering 80%+ RAM reduction vs. streaming IVM engines.
2. **Refusal Assurance Contract & RFC 8785 Merkle Audit Trail:** Eliminates silent under-invalidation for SOC 2 Type II, HIPAA, and FedRAMP compliance ($50k–$250k ACV).
3. **Multi-Tenant Observer-Relative Cache Isolation:** Prevents cross-tenant invalidation thrashing and enforces freshness SLAs via tenant quotient projections ($50k–$200k ACV).
4. **Capacity-Bounded `JoinAuxState` with Frequency-Biased Skew Retention:** Reduces invalidation rates by 40%–70% under Zipfian foreign-key skew ($40k–$150k ACV).
5. **CI/CD Schema Evolution Advisor & Migration Gate:** Tests pre-flight schema boundary containment in CI/CD pipelines ($25k–$100k ACV).

See [SOTA Review & Commercial Strategy](../docs/sota_caching_review_and_commercial_strategy.md) for full architecture and licensing specifications.

---

## Claim Discipline & Epistemic Audit

In accordance with the project Claim Hygiene Rule:
1. **What It Supports:** Supports boundary-quotient certification, certified in-place arithmetic repair for admitted SPJA single-block aggregates, capacity-bounded join tracking under Zipfian skew, and diagnostic workload pathology analysis.
2. **What It Does Not Support:** Does not claim support for arbitrary unconstrained SQL (e.g., recursive CTEs, window functions, non-deterministic expressions), does not eliminate physical memory bounds for unbounded cardinality, and confers no operational execution authority (`AuthorityEffect: None`).
3. **Narrowest Defensible Claim:** Within its declared single-block SPJA equijoin SQL fragment and given complete before-and-after CDC row images, SmplCache identifies repairable state transitions, executes exact in-place delta updates without intermediate operator state, and deterministically falls back to certified invalidation whenever evidence is absent or capacity bounds are exceeded.
4. **Next Test Needed:** Live multi-tenant PostgreSQL replication testing under concurrent write workloads and large-scale empirical Zipfian eviction parameter sweeps.

---

## Documentation

- Mathematical foundations: `docs/smplcache_theory_quotient_repairability.md`
- Dependency fingerprinting: `docs/dependency_fingerprinting.md`
- Invalidation rules: `docs/invalidation_rules.md`
- Research Forum Dossier: `../docs/research_forum/`

## License
Apache 2.0 License (Community Edition). Enterprise extensions available under Commercial / BSL 1.1 licensing.
