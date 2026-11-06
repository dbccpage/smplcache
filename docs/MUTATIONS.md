# Mutation protocol

For every self-tuning component that passes its experiment, one specific fault
was introduced, the detector that should catch it was named in advance, the
catch was observed, the fault was removed and the check re-run, and the fault
was re-applied to confirm the catch repeats. Each row is a test in
`tests/test_tuning.py` (SmplCache) or `tests/test_tuner.py` (owed_ai engine);
the tests run the mutate / fix / re-mutate cycle on every invocation, so
"re-check" below is not a one-off observation.

Finding during the protocol: the first version of `Day.slice` reused the day's
seed rows for every window, so cached values disagreed with the database on
windows that did not start at 0. The oracle caught it (89 mismatches on a
500-read segment); the fix carries the database state into the window and is
now row M7. Nothing else was found silently passing.

## SmplCache (`tests/test_tuning.py::TestMutations`)

| # | Mutation | Expected detector | Caught | Fix | Re-check |
| --- | --- | --- | --- | --- | --- |
| M1 | KEEP (preserve) on a write the certifier invalidated | SQL oracle on served values | yes, `oracle_mismatches > 0` | real `process_event` | 0 mismatches; re-mutated: caught again |
| M2 | Drift threshold set to 0 bits | `EntropyDriftDetector` constructor | yes, `ValueError` (check added: nothing else would have caught a detector that fires on every window) | threshold 0.3 bits | constructs; 0 refused again |
| M3 | Authorizer forwards approval in the proposer's name | `authorize` | yes, `SelfAuthorization` | distinct authorizer | — |
| M4 | One receipt hash reversed | `ReceiptLog.verify` / `replay` | yes, index 2, `ReceiptChainBroken` | original log | verifies (-1) |
| M5 | Mistake memory removed (`NullMemory`) | second run's `skipped_known_mistakes` | yes: refuted again, skipped 0 | memory restored | skipped 1 |
| M6 | Store ignores the certifier's invalidation (`_drop` no-op) | SQL oracle | yes, `oracle_mismatches > 0` | real `_drop` | 0 mismatches |
| M7 | Window slice without database state | SQL oracle | yes, `oracle_mismatches > 0` | `Day.slice` replays the prefix | 0 mismatches |
| M8 | Held-out evaluator calls every configuration supported | `audit_config_receipts` recount from the recorded segment | yes, receipt disagrees with recount | honest `HeldOutEvaluator` | audit returns `[]` |
| M9 | Store adapter `delete` fails silently | read-back after eviction | yes, `StoreApplicationError`, no receipt written | real `delete` | key gone, receipt written |
| M10 | Vendored engine edited | `vendored_engine_sha256` vs recorded hash | yes | restore file | hashes match |
| M11 | (control) certifier asked to repair without a before-image | `certify_event` | yes, `invalidate: missing_evidence_for_repair` | full before-image | `repair` |

Baseline failure measured on purpose: the TTL cache-aside baseline serves stale
values by design; the oracle counts them (`test_plain_replay_has_no_oracle_mismatch_and_ttl_does`).

## Engine (`owed_ai/tests/test_tuner.py`)

| # | Mutation | Expected detector | Caught | Fix | Re-check |
| --- | --- | --- | --- | --- | --- |
| G1 | Receipt body edited, hash kept | `ReceiptLog.verify` | yes, index 1 | — | — |
| G2 | Receipt deleted from the middle | `ReceiptLog.verify` (prev_hash) | yes, index 1 | — | — |
| G3 | One receipt hash reversed | `ReceiptLog.verify` / `replay` | yes, index 0 | restore | verifies |
| G4 | Proposer and authorizer share a name | `Loop` constructor | yes, `RoleConflict` | — | — |
| G5 | Rubber-stamp authorizer approves a refuted verdict | `authorize` | yes, downgraded to `denied` | — | — |
| G6 | Mistake memory removed | `skipped_known_mistakes` | yes | restore | skipped 1 |
| G7 | Evaluator never refutes | `verify_receipts` recount, no `learned` receipts | yes | honest evaluator | audit `[]` |
| G8 | `apply` raises | `apply_failed` receipt | yes | — | — |

## What the protocol does not cover

- Mutations inside `smplcache.certify_event` itself are covered by the existing
  oracle property tests in `tests/`, not by this table.
- A lying evaluator for E1 (repartition) and E3 (admission prior) has no
  dedicated audit yet; the E2 audit pattern (`audit_config_receipts`) applies
  and would need two more recount functions. Until then, those verdicts are
  checked only by the oracle-on measurement of whatever they recommend.
