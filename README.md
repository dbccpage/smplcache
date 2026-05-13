# SmplCache: A Theorem-Backed Cache Diagnostic Engine

SmplCache is an open-source (Apache 2.0) diagnostic substrate that tests the repairability of cached database workloads under streaming CDC (Change Data Capture) updates. 

Unlike traditional heuristic caches that simply flush everything or rely on error-prone logical replication guessing, SmplCache executes exact mathematical boundary testing. Every cache invalidation or repair decision is certified.

## Core Philosophy

SmplCache was built to prove that modern data systems are overcomplicated because they route state changes through machinery that cannot certify repair. 

Relational theory normalizes data at rest. SmplCache certifies the repairability of data in motion.

### The Professional Bar
1. **No silent under-invalidation.**
2. **No repair claim without evidence.**
3. **No unsupported SQL feature quietly accepted.**

## Quickstart

```bash
# Clone the repository
git clone https://github.com/anomalon/smplcache.git
cd smplcache

# Run tests
python -m pytest

# Run the workload advisor on the example schema
python cli.py report examples/workload.common.json
```

## How It Works

SmplCache decomposes a cached query shape into its essential topological boundary (predicates, aggregates, groups, projections). 

When a CDC WriteEvent occurs, SmplCache:
1. Validates the event intersects the shape boundary.
2. Certifies if the event contains sufficient old/new row evidence to safely repair the cache (Theorem 7).
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
  "required_evidence": ["amount", "customer_id", "status"],
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
  "repair_program": "paid_sum_by_group_key"
}
```

## Documentation

The mathematical theory behind SmplCache is fully documented in `docs/quotient_repairability.md`. 
See `docs/` for specific implementations regarding CDC evidence, SQL restriction, and topology maps.

## License
Apache 2.0 License.
