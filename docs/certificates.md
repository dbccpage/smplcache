# Certificate Data Model

In the Omega Engine `smplcache` framework, cache invalidation and preservation are not heuristic guesses; they are verifiable decisions backed by evidence. 

Every CDC event against a registered Query Shape results in a `Decision` containing a `Certificate`.

## Certificate Shape

```json
{
  "shape": "revenue_by_customer_paid",
  "event_id": "evt_5",
  "relation": "orders",
  "decision_kind": "repair",
  "reason_code": "group_move",
  "required_evidence": [
    "amount",
    "customer_id",
    "status"
  ],
  "available_evidence": [
    "amount",
    "customer_id",
    "status"
  ],
  "repair_program": "paid_sum_by_group_key",
  "boundary_clock": null
}
```

### Fields

*   **`shape`**: The name or hash of the Query Shape this certificate evaluates.
*   **`event_id`**: A unique identifier for the CDC WriteEvent.
*   **`relation`**: The underlying database table that changed.
*   **`decision_kind`**: A member of `DecisionKind` (`preserve`, `repair`, `invalidate`, `unsupported`).
*   **`reason_code`**: A machine-readable string indicating *why* the decision was made.
    *   *Examples:* `unrelated_relation`, `disjoint_columns`, `missing_evidence_for_repair`, `predicate_entry`, `value_change`.
*   **`required_evidence`**: A list of column names or state keys strictly required to execute a `repair`. If a CDC event lacks these (e.g., missing an old row value), the decision defaults to `invalidate`.
*   **`available_evidence`**: The keys available in the CDC event's `old` and `new` maps.
*   **`repair_program`**: If the decision is `repair`, this indicates the certified aggregate or reduction program (e.g., `paid_sum_by_group_key`) that safely reconciles the boundary.
*   **`boundary_clock`**: (Placeholder) Will hold the monotonic sequence number of the event for strict causal ordering and replay validation.

## The Evidence Principle (Theorem 7)

A core tenet of `smplcache` is **Theorem 7**: *SUM, COUNT, and AVG repair are complete exactly when old/new value, old/new group key, and old/new predicate truth are available.*

If an event intersects an aggregate shape but lacks the necessary evidence (e.g., an `UPDATE` that only supplies the changed `amount` but omits the `customer_id` grouping key), the certifier emits an `invalidate` decision with `reason_code = "missing_evidence_for_repair"`. No cache is ever incorrectly "repaired" based on partial data.
