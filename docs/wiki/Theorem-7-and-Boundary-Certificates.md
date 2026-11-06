# Theorem 7 and Boundary Certificates

`SmplCache` treats incremental cache maintenance as an explicit boundary-certification problem: given a query shape $Q$ and a write event $e = (\text{rel}, \text{op}, \text{old}, \text{new})$, does $e$ contain enough evidence to update $Q(D)$ without querying the database?

## The Three-Move Classification

Every write event evaluated by `certify_event` or `certify_join_event` produces a `Certificate` with one of three `DecisionKind` outcomes:

| DecisionKind | UI Move Label | Meaning | Database Queries |
| :--- | :--- | :--- | :--- |
| `PRESERVE` | `KEEP` | The write touches another relation, modifies columns disjoint from the query shape, or both `old` and `new` row images fail the query predicate. | `0` |
| `REPAIR` | `FIX IN PLACE` | The write intersects the query's dependency footprint and carries all required before/after columns (and resident join partners) to compute the exact group delta $\Delta$. | `0` |
| `INVALIDATE` | `REREAD` | Required columns are missing from `old`/`new` payloads, a join partner PK was evicted from bounded auxiliary state, or the query uses an unsupported operator. | `1` (on read) |

## Machine-Readable Certificate Structure

```python
@dataclass(frozen=True)
class Certificate:
    shape_name: str
    event_relation: str
    operation: str
    decision: DecisionKind
    reason_code: str
    impacted_columns: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    delta: Any = None
    boundary_clock: int | str | None = None
```

### `boundary_clock` Watermarks

When events are ingested through `parse_debezium_event` or `WalStreamProcessor`, `WriteEvent.boundary_clock` is populated automatically from the CDC envelope (`source.lsn`, `source.ts_ns`, `source.txId`, or MongoDB oplog timestamp) and propagated to `Certificate.boundary_clock` and `TransactionBoundaryReport.boundary_clock`. Downstream consumers can compare `Certificate.boundary_clock` against database snapshot watermarks to detect out-of-order delivery.

## Cold-Miss Seeding via `AggregateCache.from_group_state`

When a cache starts cold or recovers from a latched `INVALIDATE` (`REREAD`) state, scanning the full base table in Python is unnecessary if the SQL database can execute the `GROUP BY` query directly. `AggregateCache.from_group_state(shape, group_stats)` seeds the cache from pre-aggregated `(sum, count)` tuples with `_from_rows = True`, allowing subsequent CDC events with complete before/after images to execute `REPAIR` (`FIX IN PLACE`) without pulling raw table rows into Python memory.

## Claim Hygiene Summary

1. **What this page supports:** Deterministic three-way classification (`PRESERVE`, `REPAIR`, `INVALIDATE`) and certificate generation for single-table and inner-join aggregate shapes under explicit evidence checks.
2. **What this page does not support:** Sub-millisecond latency claims on unindexed cold-miss SQL queries or automatic recovery from corrupted CDC payloads that misreport column values.
3. **Narrowest defensible claim:** `certify_event` and `certify_join_event` emit `REPAIR` only when all filter, group-by, aggregate, and join-key columns required by the parsed query shape are present in the event and auxiliary state; otherwise they fail closed to `INVALIDATE`.
4. **Next test needed:** Property-based fuzzing of out-of-order LSN watermarks across multi-statement rollbacks and savepoints.
