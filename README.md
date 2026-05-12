# smplcache

`smplcache` is a small local tool for analyzing CDC-style write events against cached query shapes.

Instead of invalidating every cached query when a table changes, `smplcache` stores a dependency fingerprint for each query shape and invalidates only when a write boundary intersects that fingerprint.

It can also mathematically identify when cached aggregates can be incrementally repaired instead of dropped.

### Cache invalidation by shape, not table.

---

## Public Positioning

`smplcache` is not a cache server yet. It is a dependency-fingerprint simulator and advisor for CDC-backed caches. It proves that topological CDC routing can eliminate false cache invalidations and enable real-time aggregate repairs.

## Quick Start & Demos

The repository includes a numbered curriculum of demos to prove the architecture works both in application middleware and natively inside an RDBMS.

### Python Middleware Simulators
These simulate how `smplcache` operates as a Rust/Go middleware consuming a logical replication stream (no database required):
1. `python examples/01_python_orders_revenue_demo.py` - Proves how orthogonal writes preserve the cache, while intersecting writes incrementally repair the aggregate.
2. `python examples/02_python_inventory_demo.py` - A secondary mock proving basic dependency fingerprint routing.

### Native SQL Implementations
If you want to test the math natively inside a database engine, run these scripts in your SQL client:
3. `examples/sql/03_sqlserver_invalidation_trigger.sql` - Implements standard dependency fingerprinting using T-SQL triggers.
4. `examples/sql/04_sqlserver_incremental_repair.sql` - **(Recommended)** The ultimate proof. Implements streaming incremental materialized views inside SQL Server using `inserted` and `deleted` topological boundaries.
5. `examples/sql/05_postgres_invalidation_trigger.sql` - Implements standard dependency fingerprinting using PL/pgSQL and `JSONB` diffing.

## Documentation

- [Dependency Fingerprinting](docs/dependency_fingerprinting.md)
- [Invalidation Rules & Incremental Repair](docs/invalidation_rules.md)


## License
Licensed under the Apache License, Version 2.0.

## Copyright
Copyright 2026 Jeremy Carroll