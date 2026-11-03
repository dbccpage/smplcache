# SmplCache vs Heuristic Caches

This document outlines the architectural differences between SmplCache and traditional logical-replication heuristic caches (e.g., standard WAL-parsing proxy layers).

## 1. Product Model
*   **Heuristic Caches**: Transparent cache proxies that attempt to parse WAL (Write-Ahead Logs) to guess what cache entries should be invalidated.
*   **SmplCache**: A boundary-checked repair advisor. It extracts dependency boundaries from supported SQL workloads and issues structured machine-readable certificates recording why a cache entry should be preserved, repaired, or invalidated.

## 2. Unpredictable SQL vs The Restricted Fragment
*   **Heuristic Caches**: Accept general SQL and attempt to track dependencies heuristically. When a complex query (e.g., window functions, subqueries) is cached, these tools often silently under-invalidate or fall back to full flushes.
*   **SmplCache**: Restricts automated extraction to an explicit allowlisted SQL fragment (Theorem 11). If an unsupported construct is detected, the query is explicitly rejected with `unsupported`.

## 3. Heuristic Invalidation vs Evidence-Checked Repair
*   **Heuristic Caches**: Focus on *invalidation* (flushing the cache when a table changes).
*   **SmplCache**: Supports incremental *repair* for single-relation `SUM`, `COUNT`, `COUNT(*)`, and `AVG` queries when the CDC event supplies complete before/after evidence.

## 4. CDC Evidence Requirements
*   **Heuristic Caches**: Consume whatever WAL throws at them. If an old value is missing, they cannot dynamically repair.
*   **SmplCache**: Enforces Theorem 7. A repair is refused if the CDC stream lacks the explicit before/after values, group keys, or predicate columns required to compute the exact delta.

## 5. Masked Observer Behavior (Multi-Tenant)
*   **Heuristic Caches**: Generally unaware of RLS (Row Level Security) observer masking. A change by one tenant often invalidates the cache for all tenants.
*   **SmplCache**: Treats security columns as explicit dependencies that trigger invalidation when mutated.
