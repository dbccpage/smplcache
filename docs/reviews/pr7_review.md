# PR #7 review and repair

Reviewed base: `d8467e7f0d26a63fbf3184ef9ee5ab8a74864016` on
`feature/upgrades-1-3-join-wtinylfu-shared-pool`. Review date: 2026-11-01.
Scope: Python engine, SQL admission, decoded WAL handling, auxiliary join state,
packaging/CI, public claims, and a proposed resume-facing demonstration. This is
an engineering review, not a proof of the mathematical monograph or production
PostgreSQL/SQL Server equivalence.

The original suite passed **97 tests**. The first **20 new counterexample tests
all failed against that head**. The repaired suite passes **129 tests**, including
32 added regressions. Passing the original suite was therefore insufficient
support for a blanket soundness statement.

## Findings and implemented changes

| Priority / code | Reproduction and consequence | Repair | Recurrence-prevention condition |
| --- | --- | --- | --- |
| P1 / AB | SQL for `tenant_a.orders` became bare `orders`; an insert into `tenant_b.orders` changed the first tenant's cached sum from 10 to 60. The old SQLite oracle also stripped schema qualifiers. | Preserve namespace identity in parsing/normalization; invalidate unresolved qualified/unqualified bindings; validate join column schemas. Execute qualified oracle queries in attached SQLite databases. | Both single-table and join schema regressions must preserve the other schema's result; same-schema mutation must match independent SQLite execution; ambiguous binding must invalidate. |
| P1 / AB | Unknown `UPSERT`/transaction operations fell through to a disjoint-column preserve. Wildcard data relations were treated as unrelated. A caller-declared `amount` change could disappear when partial row images had a different symmetric key difference. | Reject unknown operations, unknown target relations, and empty explicit inserts/deletes. Retain caller-declared changes when deriving the effective boundary. | Unknown operation and unknown relation parameterizations must invalidate in both certifiers. Missing required amount evidence cannot produce preserve/repair. |
| P1 / RL | A totals-only grouped cache `{a: 30}` fabricated a one-row count; deleting a row worth 10 removed the whole group rather than leaving 20. Incomplete snapshot fields were also treated as SQL NULL. | Permit processing repairs only with seeded sufficient aggregate state; seed from rows; reject incomplete seed evidence. Update executable examples and tests to supply real seeds. | Totals-only SUM/COUNT/COUNT(*)/AVG repair tests must invalidate without mutation. Missing seed evidence must raise, while an explicitly supplied SQL NULL remains valid. |
| P1 / AB | After an incomplete 10→20 WAL update invalidated the cache, a complete 20→30 event repaired the stale 10 to 20 and returned REPAIR. Unsupported shapes did not trigger the rebuild callback. Later transaction events could be labeled repair even though execution had been skipped. | Latch WAL invalidation until successful rebuild; treat unsupported as requiring fallback; require fresh join auxiliary state; label skipped events `cache_requires_rebuild`. | The missing-evidence sequence must remain invalid until a callback rebuilds at the committed boundary; the next legitimate update must then yield 50 in the regression. Unsupported must invoke fallback. |
| P1 / AB | Float NaN/infinity could be admitted as aggregate evidence. Decimal arithmetic rounded `10000000000000000000000000000 + 1` back to the original value. Invalid group keys could reach mutation. | Reject nonfinite floats; size local Decimal addition precision for finite input exponents; validate group keys before repair. | Single-table and join sums retain the 29th digit; deleting the large component leaves exactly 1. Nonfinite or unhashable group evidence invalidates without changing values. |
| P1 / RL | Join seeding accepted incomplete rows, NULL primary keys, or duplicate primary keys. Dictionary replacement silently collapsed duplicate rows and missing fields were interpreted as NULL/absent joins. | Validate all participating tables, required seed columns, and non-null unique hashable primary keys before indexing/eviction. | Incomplete, NULL-key, and duplicate-key seeds must raise; incomplete table sets cannot silently represent empty tables. |
| P2 / AP | Calling `certify_join_event` changed access counters and Count-Min Sketch state, so inspection affected future eviction outcomes. | Make certification contribution evaluation read-only; keep actual processing accesses in the execution path. | Certification leaves access clock and sketch additions unchanged. The phase-window test checks an observed short-window benefit without asserting unjustified strict monotonicity between all windows. |
| P2 / RL | README presented unmeasured RAM savings, compliance features, commercial pricing, and native replication implications as implemented differentiators. CI description mentioned mypy but the workflow did not run it. Root documentation links were broken. | Describe implemented behavior and integration limits; distinguish proposals from evidence; mark prior benchmark tables historical; fix links and add mypy to CI. | CI runs type checks, tests, formatting, and clean-install smoke. New performance claims require a reproducible workload, revision, metric definition, and measured result. |

Regression source: [`tests/regressions/test_pr7_review.py`](../../tests/regressions/test_pr7_review.py).

## Compatibility changes

- `normalize_relation` retains schema qualifiers. Bind shapes, snapshot table keys,
  and CDC records consistently; implicit SQL search-path inference is not provided.
  Synthetic benchmark fixtures now use a consistent unqualified namespace.
- `AggregateCache(values=...)` does not supply trustworthy row/non-null counts.
  Use `AggregateCache.from_rows`, or `rebuild_join_cache` for join output state.
- Join seeds must explicitly contain every participating table, including empty
  tables. Invalid seeds fail during initialization rather than generating a
  successful certificate over incomplete evidence.
- Low-level callers remain responsible for discarding/rebuilding results after
  invalidate/unsupported. `WalStreamProcessor` now enforces its own invalid state.
- Read-only certification changes eviction telemetry and therefore trajectories.
  The previous benchmark artifacts are historical; their percentages must not be
  quoted as measurements of this repaired revision.

## Validation performed

Environment: Python 3.12, sqlglot 30.27.0. Commands run locally:

```sh
python -m pytest -q
python -m mypy smplcache
python -m black --check .
git diff --check
python examples/01_python_orders_revenue_demo.py
python examples/02_python_inventory_demo.py
python -m smplcache.cli ingest --benchmark --transactions 10 --zipf-s 1.2 --format json
```

Results: **129 passed** at the suite's default Hypothesis settings (10,000 examples
for the single-table properties and 1,000 for the main join/WAL properties);
mypy passed all eight package modules; formatting and whitespace checks passed;
both examples completed with expected results. A separate clean virtual environment
installed the package without development extras and ran `smplcache report
examples/workload.common.json` successfully. The CI Python 3.10–3.13 matrix was not
executed locally.

The short synthetic WAL smoke benchmark produced 15 decisions per configuration:
without a row cap, 6 preserves and 9 repairs; at 80 rows / fanout 16, 6 preserves,
5 repairs, and 4 invalidations. Every tested capacity reported oracle agreement.
These counts are a smoke-test result, not a latency or production savings claim.

## Remaining boundaries

This review does not establish arbitrary-SQL support, database collation/type
coercion equivalence, exactly-once CDC ingestion, crash recovery, concurrent readers,
native binary pgoutput decoding, or snapshot/offset alignment in a live deployment.
Adapters must deliver ordered changes once or provide upstream deduplication.
Rebuild callbacks must return a snapshot at the boundary being processed.

Resident-row limits do not bound all metadata or total RAM. AVG rendering follows
the Decimal context; the SQLite test oracle uses custom Decimal aggregates and
float conversions for some generated values, so it is not an independent test of
every numeric edge case. The new large-number tests use explicit exact expected
values. The MVP should start with integer cents and native SQLite SUM/COUNT.

No PR merge, public deployment, DNS change, or hosted demo has been performed.
The next deliverable is defined in
[`mvp_smplcache_anomalon.md`](../mvp_smplcache_anomalon.md).
