# SmplCache Live: a 60-second engineering demonstration

Status: proposed MVP, not deployed. Proposed address: `https://smplcache.anomalon.com`.
Owner: Jeremy H. Carroll. Implementation follows the repaired PR #7 engine.

## The promise

**Keep dashboards correct without rerunning every query after every write.**

A hiring manager should see a working revenue dashboard, change an order, and watch
SmplCache either leave the answer alone, update it incrementally, or request a fresh
query when evidence is insufficient. Every displayed result is compared with a
separate SQLite recomputation. Research and source links sit behind the demo.

The first screen answers three questions: what does it do, does it stay correct,
and how much query work did it avoid in this run? No sign-in, installation, SQL
knowledge, or mathematical background is required.

## The visitor's first minute

1. Open the page. See **Revenue by region**, the current total, a small order table,
   and a button: **Run the 60-second demo**. Explain that this is a live execution
   against synthetic data, with scripted database-change events.
2. Change an order's delivery note. The answer remains unchanged: **Preserve — this
   field does not affect the query.** The table-invalidation baseline reruns its query.
3. Change a paid order from $100 to $125. The revenue increases by $25:
   **Repair — complete before/after evidence permits a delta update.** Both answer
   panels agree with the independently recomputed result.
4. Move a customer to another region. The join scenario moves the affected revenue
   between groups and shows the number of affected orders.
5. Click **Remove required evidence**. A subsequent update omits the old amount.
   SmplCache displays **Invalidate — recomputation required**, withholds the stale
   value, reruns the query, and shows the verified rebuilt answer. The UI never
   displays an invalid result with a green freshness badge.
6. End on measured counts: recomputations, repairs, preserves, invalidations,
   correctness checks, and mismatches. Buttons: **Replay**, **Inspect one decision**,
   **Architecture**, **Source**, and **About Jeremy**.

User-facing decisions use words and icons as well as color. Certificate details
expand on demand; mathematical vocabulary is not needed to use the page.

## Included scenarios

| Scenario | Visitor action | Behavior demonstrated | Engine components |
| --- | --- | --- | --- |
| Live revenue | Change notes, amount, payment status, or customer group | Preserve, predicate entry/exit, incremental repair | `parse_sql`, `AggregateCache.from_rows`, `process_event` |
| Customer and region joins | Move a customer; lower auxiliary row capacity | Join fanout, capacity-triggered invalidation and rebuild | `parse_join_sql`, `JoinAuxState`, `process_join_event` |
| Missing evidence | Remove an old required field from a scripted committed change | Refusal to repair; visible fallback and recovery | `WalStreamProcessor`, explicit rebuild callback |

Use `orders -> customers -> regions`, with integer cents for the initial money
queries. Predefined SQL and seeded fixtures keep numeric and dialect semantics
explicit. The independent SQLite oracle uses its native integer `SUM` and `COUNT`
for these queries, not SmplCache's contribution evaluator or custom sum functions.
The existing Decimal/AVG property tests remain engineering evidence; they do not
justify claiming equivalence with every database's numeric and collation rules.

The bounded-join scenario must disclose that the configured limit counts resident
auxiliary rows. Eviction tombstones, indexes, frequency metadata, aggregate state,
and transient work also consume memory; the row limit is not a total RAM limit.

## Screen layout

- **Header:** SmplCache Live; one-sentence promise; Jeremy H. Carroll / Software Engineer.
- **Result:** current regional revenue and freshness status, with baseline and
  SmplCache results side by side.
- **Controls:** run, pause, step, reset, scenario selection, and a short list of
  permitted mutations. Default walkthrough uses a fixed seed.
- **Evidence strip:** query recomputations avoided, result comparisons passed/total,
  mismatches, and events processed. Display an explicit error on any mismatch.
- **Event timeline:** each committed change and its preserve/repair/invalidate reason.
- **Expandable detail:** query, row before/after, needed/available evidence,
  certificate, engine commit, seed, and run sequence.
- **Footer:** a short explanation of the engineering tradeoff, architecture diagram,
  reproducible report, source, and a concise professional bio.

Mobile must retain the result, explanation, and primary control without horizontal
scrolling. Honor reduced-motion preferences; do not require animation to read evidence.

## Fair comparison and measurement

Both policies receive the same initial snapshot, SQL, committed mutations, and read
schedule. The baseline invalidates affected table queries and refreshes on the next
read. SmplCache processes the same writes and also refreshes on that read when its
result is invalid. Start with one read after each committed mutation so the counts
are easy to understand. Later benchmarks may vary the read/write ratio explicitly.

Count separately:

- Baseline query executions after initialization.
- SmplCache fallback query executions after initialization.
- SmplCache preserve, repair, invalidate, and unsupported decisions.
- Independent validation queries: these are extra demonstration work, excluded from
  both policies' query counts, but disclosed in the report.
- `avoided = baseline_recomputations - smplcache_recomputations`; show percentage
  only when the baseline denominator is positive. Do not force this value positive.
- Oracle mismatches and a comparison count, including comparisons after rebuilds.
- Optional timings measured on the server with a monotonic clock. Label workload,
  row count, repetitions, hardware/runtime, and warmup. Correctness checking overhead
  is reported separately. A small synthetic run is not a production latency claim.

No invented speedups, RAM reductions, dollar savings, or compliance claims. An
invalidation is a successful safety behavior, not an incorrect result. A mismatch
is a failed run: stop playback, retain the failing input and receipt, and show red.

## Proposed implementation boundary

A small frontend and Python HTTP API run the actual `smplcache` package. Keep the
library dependency direction one way: `demo -> smplcache`; no web framework imports
in the engine. One deployable container may serve the built frontend and API.
FastAPI and a TypeScript frontend are implementation proposals, not added dependencies
in PR #7. A native PostgreSQL replication deployment is a later milestone.

Suggested repository layout:

```text
demo/
  api/          # HTTP adapter and bounded run service
  scenarios/    # seeded fixtures and ordered committed mutations
  web/          # dashboard and evidence inspection
  tests/        # API, scenario correctness, and browser smoke checks
```

Suggested API contract:

- `GET /api/scenarios`: allowed scenario IDs, descriptions, input bounds.
- `POST /api/runs`: scenario, seed, dataset size, auxiliary capacity, bounded step count;
  returns actual per-step results, counters, certificates, and provenance.
- `GET /healthz`: readiness.

Initially make each bounded run request self-contained: reconstruct its seeded
snapshot, execute its ordered events once, validate, and return the trace. Client
playback only animates those computed results. Changes to a control submit a fresh
run; the frontend never manufactures decisions. This avoids a shared mutable cache
between visitors and avoids claiming durable CDC delivery guarantees.

Use explicit qualified table identities consistently in shapes, snapshots, and
CDC records. No implicit search-path inference. A rebuild callback must read a
snapshot at the committed boundary being processed; rebuilding from a later snapshot
and then replaying earlier events would double-count changes.

Serve synthetic data only. Accept scenario IDs and bounded numeric controls, not
arbitrary SQL, database URLs, uploads, or credentials. Bound rows, events, request
size, and execution time; rate-limit public execution. Encode money as integer
cents (or decimal strings for optional numeric cases), not JSON floating-point money.

## Release acceptance gates

1. A new visitor completes the default walkthrough without setup or reading a paper.
2. Each successful preserve/repair and each rebuilt answer matches native SQLite
   recomputation after the same committed boundary. A deliberately wrong result
   activates the mismatch UI and stops playback.
3. Missing evidence produces invalidation, hides the stale answer, and triggers one
   rebuild at the correct boundary. No later repair runs on invalid state.
4. A same-named table in another schema cannot alter this query's cache. An unresolved
   qualified/unqualified binding is rejected rather than guessed.
5. Forced auxiliary eviction produces either correct repair or explicit fallback;
   counts never imply that row capacity is measured RAM.
6. Replaying the same seed/configuration and engine revision reproduces decisions,
   results, and counts; runtime timings may differ.
7. Baseline, fallback, and oracle-query counters reconcile with an instrumented log.
8. Unit/property checks, API tests, browser run/step/reset/failure flows, clean install,
   keyboard navigation, and a mobile layout smoke check pass in CI.
9. The deployed service exposes its engine commit and has a recorded health check,
   HTTPS verification, and a rollback to the previous image.

## Delivery sequence

1. **Core vertical slice:** single-table revenue, real API, native SQLite oracle,
   baseline counters, replay, evidence panel, and missing-evidence recovery.
2. **Complete MVP:** join/fanout and capacity scenarios, browser verification,
   downloadable JSON run report, bio, architecture explanation, and mobile polish.
3. **Publish:** select hosting compatible with the existing anomalon.com setup,
   configure `smplcache.anomalon.com`, verify TLS/health/replay, then add the live link
   to the resume. Hosting and DNS have not been inspected or changed in this review.

Defer arbitrary SQL, user accounts, external database connections, payments, live
Debezium infrastructure, multi-tenant customer data, and comparative vendor claims.
They are not needed to demonstrate the core engineering judgment.

## Resume and interview positioning

After deployment and validation, a defensible resume bullet is:

> Built SmplCache, a Python engine that incrementally maintains supported SQL query
> results from change events and falls back to recomputation when evidence is
> insufficient; shipped an interactive demo with independent database verification,
> reproducible decision traces, and bounded auxiliary join state.

Link the project name to the live demo and put GitHub second. Add a measured number
only after it has a published workload, revision, denominator, and reproducible
result. Do not describe the proposed demo as shipped before it is live.

The interview discussion can then move from a visible product to SQL semantics,
CDC evidence, cache invalidation, independent verification, memory tradeoffs, and
failure recovery. The deeper research becomes available evidence, rather than a
prerequisite for understanding what Jeremy built.
