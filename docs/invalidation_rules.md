# smplcache: Invalidation Rules & Incremental Aggregation

Beyond basic dependency fingerprinting, distinguishing between **Dependency Classes** allows smplcache to not only avoid false invalidations but also incrementally update complex aggregates without executing the query again.

## 1. Join-Aware Invalidation Rules

Most cache products get joins wrong or wildly over-invalidate them. By classifying dependencies by their structural role, smplcache can implement exact invalidation:

| Query construct       | Dependency rule                                        |
| --------------------- | ------------------------------------------------------ |
| `SELECT col`          | invalidate when `col` changes                          |
| `WHERE col = x`       | invalidate when `col` changes                          |
| `GROUP BY col`        | invalidate when `col` changes                          |
| `SUM(col)`            | update/invalidate when `col` changes                   |
| `COUNT(*)`            | insert/delete changes count; unrelated update does not |
| `JOIN a.x = b.y`      | invalidate when either join key changes                |
| `ORDER BY col`        | invalidate when `col` changes                          |
| `LIMIT/OFFSET`        | dangerous: small changes can affect page membership    |
| `DISTINCT col`        | invalidate when `col` changes                          |
| `security policy col` | invalidate/remask when policy column changes           |

---

## 2. The Core Insight: Incremental Updates Instead of Invalidation

For aggregates, the dependency fingerprint can tell you when *not* to invalidate, but rather **incrementally update** the cached result directly from the CDC stream.

**Example Query Shape:**
```sql
SELECT customer_id, SUM(amount)
FROM orders
WHERE status = 'paid'
GROUP BY customer_id;
```

**Scenario 1: Amount Changes**
```sql
UPDATE orders SET amount = 150 WHERE id = 42;
```
If the old row was `amount = 100` and `status = 'paid'`, **do not invalidate**. Apply the delta directly to the cached map:
```text
revenue[customer_id] -= 100
revenue[customer_id] += 150
```

**Scenario 2: Predicate Changes (Entering the Result)**
```sql
UPDATE orders SET status = 'paid' WHERE id = 42;
```
If the old status was `pending`, the row is now valid. Apply the delta:
```text
revenue[customer_id] += amount
```

**Scenario 3: Predicate Changes (Leaving the Result)**
If `status` changes from `paid` to `cancelled`:
```text
revenue[customer_id] -= amount
```

This turns smplcache from a cache invalidator into a **streaming incremental materialized-view maintainer.**

---

## 3. The Cache Correctness Contract

For every cached shape $S$, smplcache stores:

1. Shape hash
2. Relation fingerprints
3. Dependency classes
4. Predicate support
5. Projection support
6. Aggregate support
7. Join support
8. Security/masking support
9. Last boundary clock
10. Validity certificate

**The Formal Contract:**
> A cached result is valid iff no committed boundary event since its boundary clock intersects its dependency fingerprint.

---

## 4. Premium Add-On: smplcache Shield (Masked CDC)

By categorizing `security policy col` dependencies, smplcache can offer safe data-change distribution streams. 

*   **Internal Stream**: sees full before/after.
*   **Analytics Stream**: sees `amount` bucketed, not raw amounts.
*   **Support Stream**: sees `status` and `customer_id`, but strips PII.
*   **Tenant Stream**: sees only tenant-owned rows based on policy filters.

---

## 5. Premium Add-On: smplcache Advisor

Because smplcache sees both query shapes and CDC invalidation streams, it can mathematically tell customers *why* their cache hit rate is bad. 

**Customer-Facing Output:**
> "Your cache is being destroyed by `orders.status`.\n> This column participates in:\n> - Recent Orders\n> - Revenue Dashboard\n> - Fulfillment Queue\n> \n> **Recommendation**: Split the frequently-changing `status` into a narrow relation or materialize status-specific shapes to improve global cache stability."

This shifts smplcache from a proxy into an automated Workload Advisor.
