# SmplCache vs Heuristic Caches

This document outlines the fundamental architectural differences between SmplCache and traditional logical-replication heuristic caches (e.g., standard WAL-parsing proxy layers). 

## 1. Product Model
*   **Heuristic Caches**: Transparent cache proxies that attempt to parse WAL (Write-Ahead Logs) to guess what cache entries should be invalidated.
*   **SmplCache**: A certified repair advisor. It formally extracts topological boundaries from workloads and issues cryptographic-style certificates dictating exactly why a cache entry should be preserved, repaired, or invalidated.

## 2. Unpredictable SQL vs The Restricted Fragment
*   **Heuristic Caches**: Accept general SQL and attempt to track dependencies heuristically. When a complex query (e.g., window functions, subqueries) is cached, these tools often silently under-invalidate or panic into full flushes.
*   **SmplCache**: Strictly limits automated extraction to a mathematically proven SQL fragment (Theorem 11). If an unsupported feature is detected, the query is explicitly rejected. No silent under-invalidation.

## 3. Heuristic Invalidation vs Certified Repair
*   **Heuristic Caches**: Focus on *invalidation* (flushing the cache when a table changes). 
*   **SmplCache**: Focuses on *repair*. When an aggregate (`SUM`, `COUNT`) query's underlying data changes, SmplCache can mathematically repair the cached value *without* re-querying the database, provided the CDC event contains sufficient evidence.

## 4. CDC Evidence Requirements
*   **Heuristic Caches**: Consume whatever WAL throws at them. If an old value is missing, they cannot dynamically repair.
*   **SmplCache**: Enforces Theorem 7. A repair is refused if the CDC stream lacks the explicit before/after values, group keys, or predicate truths required to prove the repair is exact.

## 5. Masked Observer Behavior (Multi-Tenant)
*   **Heuristic Caches**: Generally unaware of complex RLS (Row Level Security) observer masking. A change by one tenant often invalidates the cache for all tenants.
*   **SmplCache**: Observer masks are treated as quotient projections. If a change occurs outside a specific observer's visibility mask, that observer's cache is certified as preserved.
