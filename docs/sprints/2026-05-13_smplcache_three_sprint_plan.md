# SmplCache Three-Sprint Plan

Date: 2026-05-13

Status: execution plan for turning SmplCache from a small prototype into a professional Apache 2.0 open-source wedge project.

Core product thesis:

> SmplCache does not merely keep caches fresh. SmplCache certifies whether a CDC boundary preserves, repairs, invalidates, or exceeds the declared model.

This plan follows the mandate from `general_theory.md`:

> Every expansion must either prove, descend, or refuse.

For SmplCache, that means every runtime decision must terminate in one of:

- `preserve`: the event is provably invisible to the shape.
- `repair`: the event is exactly repairable by a declared repair program.
- `invalidate`: the event is relevant but not locally repairable.
- `unsupported`: the shape, event, or evidence is outside the declared fragment.

No report may call an event repairable unless a certificate exists.

---

## Competitive Wedge

The market wedge is not "another PostgreSQL cache proxy." PgCache is already positioned as a transparent PostgreSQL proxy with CDC-backed cache maintenance. SmplCache should compete on a different axis:

1. Certified decisions instead of heuristic freshness.
2. Exact aggregate repair instead of blanket invalidation.
3. Observer-relative and policy-masked CDC as a first-class model.
4. Topological workload diagnosis: not only what invalidated, but why the workload is structurally coupled.
5. Typed refusal for unsupported shapes instead of silent overclaiming.

Public language should emphasize finite boundary-certified cache repair. Physics language belongs in theory appendices until a realization theorem is supplied.

---

## Proof Backlog

### P0 Proofs Required For Launch

1. Decision Soundness Theorem

   Statement: for a declared workload fragment and CDC evidence packet, the certifier emits exactly one terminal decision from `preserve`, `repair`, `invalidate`, or `unsupported`, and the decision carries enough evidence to replay the classification.

   Code target: `Decision`, certificate payloads, CLI JSON output.

2. CDC Evidence Sufficiency Theorem

   Statement: SUM, COUNT, and AVG repair are complete exactly when old/new value, old/new group key, and old/new predicate truth are available. Missing required evidence forces `unsupported` or `invalidate`, not `repair`.

   Code target: aggregate certifier and evidence checker.

3. Restricted SQL Realization Theorem

   Statement: for the declared SQL fragment, extracted dependency roles include every semantic dependency needed for sound preservation.

   Fragment:

   - `SELECT`
   - `FROM`
   - deterministic scalar expressions
   - inner equijoins
   - `WHERE`
   - `GROUP BY`
   - `SUM`, `COUNT`, `AVG`

   Code target: extractor plus unsupported-shape reasons.

4. Freshness Clock / Replay Theorem

   Statement: a cache certificate is valid relative to a boundary clock if all committed boundary events since the certificate clock are either preserved or repaired by replayable decisions.

   Code target: event log replay and certificate validation.

5. Policy-Masked Observer Theorem

   Statement: compatible observer masks are passive quotient maps. They may erase obstruction but cannot create obstruction without a core preimage.

   Code target: policy-mask compatibility checks and masked decision output.

### P1 Proofs After Launch

1. Join Invalidation and Repair Theorem for inner equijoins.
2. Query Shape Subsumption Theorem.
3. Schema-Adaptive Lift Witness Theorem.
4. Noncommutation Detection for a finite write-operator fragment.
5. TopoMap partition completeness with isolated singleton components.

---

## Sprint 1: Certificate Kernel

Goal: make the core decision model theorem-driven and executable.

Duration target: 1 focused sprint.

### Deliverables

1. Core decision API

   Files:

   - `smplcache.py`
   - `test_smplcache.py`

   Tasks:

   - Keep `DecisionKind` as the terminal result enum.
   - Add explicit certificate dataclasses or typed dictionaries:
     - `PreserveCertificate`
     - `RepairCertificate`
     - `InvalidationCertificate`
     - `UnsupportedCertificate`
   - Add stable machine-readable fields:
     - `shape`
     - `event_id`
     - `relation`
     - `decision_kind`
     - `reason_code`
     - `required_evidence`
     - `available_evidence`
     - `repair_program`
     - `boundary_clock` placeholder

2. Aggregate certifier

   Tasks:

   - Support `SUM`, `COUNT`, `AVG`.
   - Reject or invalidate `MIN`, `MAX` unless auxiliary extremum state is declared.
   - Classify events by operation:
     - `INSERT`
     - `UPDATE`
     - `DELETE`
   - Detect predicate entry, predicate exit, value change, group move, and no-op update.
   - Require old/new evidence according to Theorem 7.

3. CLI correctness

   Files:

   - `cli.py`

   Tasks:

   - Replace current "aggregate_cols means repairable" logic.
   - Count only certified repairs as repairable.
   - Add `--format json`.
   - Emit certificates in JSON mode.
   - Keep markdown output readable, but make it certificate-backed.

4. Tests

   Minimum tests:

   - preserve unrelated relation
   - preserve disjoint changed columns
   - SUM amount update
   - SUM predicate entry
   - SUM predicate exit
   - SUM group move
   - COUNT insert/delete/update
   - AVG repair as SUM plus COUNT
   - MIN/MAX unsupported without auxiliary state
   - missing old value refuses repair
   - missing group key refuses repair
   - missing predicate evidence refuses repair

### Exit Criteria

- All tests pass.
- No CLI path calls an aggregate event repairable without a repair certificate.
- Every decision has a machine-readable reason code.
- Examples still run.
- README includes one preserve certificate and one repair certificate.

### Sprint 1 Definition Of Done

The following command should pass:

```bash
python -m pytest omega_engine\.research\.codename_simplexdb\smplcache
```

The following CLI flow should produce certificate-backed output:

```bash
python omega_engine\.research\.codename_simplexdb\smplcache\cli.py report omega_engine\.research\.codename_simplexdb\smplcache\examples\workload.common.json --format json
```

---

## Sprint 2: SQL and CDC Realization

Goal: stop relying only on hand-authored fingerprints. Build the restricted realization path from SQL shape to role-labeled dependency fingerprint.

Duration target: 1 focused sprint.

### Deliverables

1. Workload schema v1

   Files:

   - `schema/smplcache.workload.v1.schema.json`
   - migration examples under `examples/`

   Tasks:

   - Replace single-relation assumptions with per-relation dependencies.
   - Represent role-labeled dependencies:
     - predicate
     - projection
     - aggregate
     - group
     - join
     - security
   - Represent aggregate specs:
     - function
     - value expression
     - group expression
     - predicate expression
     - auxiliary state requirement
   - Represent required CDC evidence.

2. SQL fragment extractor

   New file candidates:

   - `sqlshape.py`
   - `extractor.py`

   Tasks:

   - Parse or conservatively inspect the supported SQL fragment.
   - Extract role-labeled dependencies.
   - Return `unsupported` with a reason for excluded constructs.
   - Never silently ignore columns in expressions.

   Unsupported examples:

   - nondeterministic functions
   - user-defined functions without dependency declaration
   - subqueries
   - window functions
   - outer joins
   - `LIMIT/OFFSET`
   - expression forms the extractor cannot account for

3. CDC evidence checker

   Tasks:

   - Determine whether a CDC event contains the fields required by the shape.
   - Distinguish "event intersects fingerprint" from "event has enough evidence for repair."
   - Emit a typed refusal when evidence is missing.

4. Realization tests

   Minimum tests:

   - `SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id`
   - inner equijoin dependency extraction
   - expression dependency extraction, such as `amount * tax_rate`
   - predicate dependency extraction, such as `amount > 100`
   - unsupported window function
   - unsupported UDF
   - no under-invalidation when extracted dependencies intersect CDC boundary

### Exit Criteria

- Supported SQL shapes produce complete role-labeled fingerprints.
- Unsupported SQL shapes produce explicit refusal.
- CDC events are classified by evidence sufficiency.
- The restricted SQL realization theorem has an executable witness in tests.

### Sprint 2 Definition Of Done

The project can take a supported SQL query plus a CDC event and emit:

```text
preserve | repair | invalidate | unsupported
```

with a replayable reason.

No supported SQL expression may lose a referenced dependency column during extraction.

---

## Sprint 3: Professional Open-Source Release

Goal: make SmplCache credible to external users and difficult to dismiss.

Duration target: 1 focused sprint.

### Deliverables

1. Project packaging

   Files:

   - `pyproject.toml`
   - package directory, likely `smplcache/`
   - `README.md`
   - `LICENSE`
   - `NOTICE`
   - `CONTRIBUTING.md`
   - `SECURITY.md`

   Tasks:

   - Package the CLI as `smplcache`.
   - Keep Apache 2.0 licensing explicit.
   - Add install instructions.
   - Add a small "claim discipline" section.

2. CI and quality gates

   Tasks:

   - Add GitHub Actions for tests.
   - Add linting.
   - Add type checking if practical.
   - Add JSON schema validation tests.
   - Add example smoke tests.

3. Benchmarks and demos

   Demos:

   - blind table invalidation vs shape invalidation
   - certified aggregate repair
   - missing-evidence refusal
   - TopoMap workload coupling
   - masked observer CDC demo

   Metrics:

   - false invalidations avoided
   - certified repairs
   - invalidations
   - unsupported/refused cases
   - top invalidating columns
   - coupling components
   - beta_1 cycle rank

4. Documentation

   Docs:

   - `docs/certificates.md`
   - `docs/sql_fragment.md`
   - `docs/cdc_evidence.md`
   - `docs/aggregate_repair.md`
   - `docs/topomap.md`
   - `docs/claim_discipline.md`
   - `docs/comparison_heuristic_caches.md`

   Comparison document rules:

   - Be factual and source-cited.
   - Do not attack people.
   - Compare product models:
     - transparent cache proxy
     - certified repair advisor
     - CDC evidence requirements
     - masked observer behavior
     - unsupported-shape handling

5. Public release checklist

   Tasks:

   - Confirm license headers.
   - Confirm no private paths in docs.
   - Confirm examples are deterministic.
   - Confirm README quickstart works.
   - Tag first release candidate.

### Exit Criteria

- A new user can install and run SmplCache from the README.
- The CLI produces certificate-backed reports.
- Unsupported claims are refused in product output.
- The theorem docs and code behavior agree.
- The project looks like a serious Apache 2.0 open-source tool.

---

## Professional Bar

SmplCache should be judged by these standards:

1. No silent under-invalidation.
2. No repair claim without evidence.
3. No unsupported SQL feature quietly accepted.
4. No theorem-grade physics claim without a finite realization map.
5. No benchmark without reproducible inputs.
6. No public comparison without citations.
7. No generated report without enough evidence for replay.

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Overclaiming SQL completeness | Destroys credibility | Restricted fragment plus `unsupported` decisions |
| Calling aggregates repairable without evidence | Incorrect cache results | Theorem 7 certifier and tests |
| Treating policy masks as cosmetic | Security failure | Observer quotient compatibility checks |
| Building too much proxy infrastructure too early | Slows the wedge | Stay advisor/certifier first |
| Physics language leading the README | Looks unserious to database engineers | Keep physics in appendix until realization proof |
| Hardcoded examples driving logic | Brittle prototype | Schema-driven certifier |
| No packaging/CI | Not professional-grade | Sprint 3 release gates |

---

## Immediate Next Tasks

1. Implement Sprint 1 aggregate certifier.
2. Update CLI repair reporting to use certificates.
3. Add JSON report output.
4. Add theorem-backed aggregate tests.
5. Add `docs/certificates.md` after the certificate shape stabilizes.

Recommended next implementation step:

> Build `certify_event(shape, event) -> Decision` and make `process_event` call it before applying any cache mutation.

