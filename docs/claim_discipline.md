# Claim Discipline

In the SmplCache project, we enforce strict **Claim Discipline**. 

We do not make heuristic guesses and call them optimizations. We compute boundaries and certify them. If we do not have a mathematical proof that a cache entry is safe to repair or preserve, we invalidate it.

### The Professional Bar
1. **No silent under-invalidation**: If an event intersects a shape boundary, it is invalidated unless accompanied by a strict repair certificate.
2. **No repair claim without evidence**: A cache aggregate cannot be repaired unless the CDC stream explicitly provides the before/after values, before/after group keys, and before/after predicate truths (Theorem 7).
3. **No unsupported SQL feature quietly accepted**: If a SQL query uses an unsupported feature (like a window function or a subquery), it is rejected with an `unsupported` reason code.

If you are contributing to this project or comparing it to heuristic caching layers, adhere to these rules. We sell verifiable topological truth, not "good enough" caching.
