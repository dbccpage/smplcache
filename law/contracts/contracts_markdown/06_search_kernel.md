# Search Contract

## Core Role
Search (`src/searches/`) is a **semantically opaque frontier traversal morphism** over a proposal graph.

Search does not decide ontology. It only controls:
* expansion order
* frontier scheduling
* bookkeeping
* replayable traversal
* pruning under explicitly declared orchestration rules

## Architectural Mandate & Contractual Obligations

### 1. Semantically Opaque Exploration & Scheduling (MUST DO)
Search algorithms coordinate the order in which `Generators` are called. They manage the DAG of exploring paths. They must maintain rigorous tracking of explored states (e.g., avoiding cycles). They are semantically opaque relative to evaluator internals.

### 2. ZERO Internal Acceptance Logic (MUST NOT DO)
Searches **MUST NOT** internally evaluate the structural quality of a state. While stochastic scheduling may use UCB1 to guide exploration, the actual "reward" or "acceptance" score must come exclusively from external `Evaluators` calling `Functionals`.

### 3. Isolation from Solvers
Searches propose paths; they do not solve them. A search algorithm must never invoke `solver_cdcl.rs` to determine if a node is valid during expansion. Verification happens in the Evaluator phase. Search is strictly for topological expansion and tree management.

### 4. Evaluator Result Opacity & Priority Laundering
Searches may consume opaque scores or decisions returned by Evaluators, but must not inspect, recompute, transform, or reinterpret Functionals or Solver outputs. To prevent semantic payload laundering through hints, `priority_hint` must use strict bounded semantics:

```rust
enum PriorityHint {
    FrontierOrder(u32),
}
```

A search may rank candidates by this externally supplied priority, but it may not derive acceptance criteria from Φ₁, Γ₂, Ξ, CDCL, SAT/UNSAT, or any solver diagnostic.

### 5. Caching as Opaque Memoization (MUST DO)
Search may cache **verbatim outputs** returned by the Evaluator. 
*   **✅ Allowed:** Avoiding re-evaluation, or ranking the frontier by bounded `priority_hint`. This is orchestration efficiency (memoization).
*   **❌ Forbidden:** Inferring structure from rejection reasons, recomputing metrics from the cache, or branching differently because a rejection reason "sounds bad". Search must never reinterpret what it caches.

### 6. Strict Pruning Rules (MUST DO)
Search pruning is allowed only as **orchestration**, never as **reasoning**.
All pruning authority must be explicitly typed to prevent policy ambiguity:

---

```rust
enum PruneAuthority {
    Duplicate,
    BudgetExhausted,
    EvaluatorTerminal,
}
```

*   **✅ Allowed (Safe):** Duplicate-state pruning under a formally defined `StateEquivalenceCertificate`, Hard-verdict pruning under `PruneAuthority::EvaluatorTerminal` (where the rejection is explicitly recorded in the trace), and Budget-based pruning (`PruneAuthority::BudgetExhausted`).
*   **✅ Allowed (Ranking):** `PriorityHint` may order the frontier, but may not delete a candidate except through explicit budget exhaustion recorded in trace.
*   **❌ Forbidden (Unsound):** Metric-based pruning (e.g., `if phi < best { keep }`), Closure-based pruning (e.g., `if gamma2 > 0 { prune }`), Lift-avoidance pruning (suppressing branches that require MetaLift), or Reward-shaped pruning.

### 7. Transforming MCTS into a Stochastic Scheduler
### 7. Transforming MCTS into a Stochastic Scheduler
Under this contract, MCTS must **not** be a value-optimizing reasoner. MCTS is forbidden from using $\Phi_1$, $\Gamma_2$, or solver success as a reward signal, because doing so creates hidden ontology biases. Instead, MCTS becomes a **probabilistic exploration scheduler**: it balances exploration by ranking children based purely on the opaque `PriorityHint`.

### 8. The Ultimate Confluence and Fairness Guarantee
This contract exists to protect confluence and honest refusal. If Search pruned branches because $\Phi_1$ "looked worse" or a solver "failed", admissible-context witnesses would never be exposed. MetaLift would starve. 

To prevent stochastic suppression, Search must obey an explicit fairness law:
> **Every admissible branch receives nonzero asymptotic visitation probability.**

Search must guarantee **witness reachability**:
> If $\Phi_1 > 0$ and $\Gamma_2 = 0$ persists for $K$ steps, either an admissible critical pair witness (obstruction witness, quotient incompatibility, or MetaLift trigger) is found, or irreducibility is formally certified.

---

## Import Boundary Enforcement

**Forbidden imports** (files under `searches/` must never depend on):

```text
obstruction_engine::solvers
obstruction_engine::functionals
obstruction_engine::evaluators::descent_evaluator
```

**Allowed imports**:

```text
generators
state types
trace/tree/DAG bookkeeping
opaque evaluator result type
```

**Forbidden patterns** (any occurrence is a contract violation):

```rust
solve_cnf(...)
solve_l1_hodge(...)
gamma2(...)
phi1(...)
if score > threshold { accept }
if sat { expand }
```

**Allowed patterns**:

```rust
let candidates = generator.expand(state);
scheduler.enqueue(candidates);
```

```rust
let priority = evaluator_result.priority_hint;
scheduler.rank(priority);
```

---

## Detecting Silent Pruning (Diagnostic Guide)

Silent pruning occurs when Search suppresses paths without emitting a traceable decision and without going through the Evaluator/Engine authority chain. It is the most dangerous failure mode in the architecture because it causes false convergence, MetaLift starvation, and the collapse of $\Delta$-minimality.

### 1. Observable Symptoms
*   **Stagnant $\Phi_1$ without escalation:** $\Phi_1 > 0$ stalls, $\Gamma_2 = 0$, but no MetaLift fires and no `Terminate(Irreducible)` is issued. Search is almost certainly suppressing the admissible-context witnesses.
*   **Collapsed Evaluator verdict diversity:** Evaluator outputs only `Reject` or `Accept`. `Escalate` never appears, meaning Search is pre-filtering candidates semantically.
*   **Trace gaps:** Missing rejected branches, suspiciously short traces, or missing certificates. In a correct system, failed ideas leave scars in the trace.
*   **Sensitivity to search heuristics:** Changing the MCTS exploration constant or random seed changes whether MetaLift occurs or the system converges. Ontology must be seed-invariant.

### 2. Mechanical Detection Invariants
To detect contract violations, the Engine must strictly enforce:
*   **Invariant A (Escalation Completeness):** Every `Escalate(MetaLayer)` must be followed by exactly one of `EngineDecision::Lift` or `EngineDecision::Terminate(Irreducible)`.
*   **Invariant B (Witness Reachability):** If $\Phi_1 > 0$ and $\Gamma_2 = 0$ persists for $K$ steps, either an admissible obstruction witness is found, or irreducibility is certified.
*   **Cross-Search Reproducibility Test:** Running DFS, BFS, and MCTS must yield identical ontology endpoints (lift existence, irreducibility, final $\Phi_1$). Ontology must be seed-invariant.
*   **Negative Control Test:** If completely disabling pruning and priority-ranking suddenly triggers missing MetaLifts, silent pruning is mathematically proven to have occurred.
*   **Evaluator Visibility Audit:** Any non-zero "pre-evaluator drop count" (Search dropping candidates without passing them to the Evaluator) is a hard contract violation.

---

## Required Test Infrastructure

### Search Purity Tests
* `search_cannot_call_solver`
* `search_cannot_import_functionals`
* `search_cannot_compute_phi`
* `search_cannot_compute_gamma2`

### Frontier Tests
* `duplicate_pruning_preserves_reachability`
* `budget_pruning_emits_trace`
* `all_pruned_states_traceable`

### Fairness Tests
* `all_frontier_nodes_eventually_schedulable`
* `seed_changes_order_not_ontology`
* `mcts_does_not_starve_branch`

### MetaLift Tests
* `critical_pair_reachable_under_all_searches`
* `meta_lift_not_suppressed_by_priority`
* `irreducibility_requires_witness_or_certificate`

### Replay Tests
* `same_seed_same_frontier_order`
* `same_search_same_trace`
* `same_ontology_across_dfs_bfs_mcts`

---

## Target Architecture (Scheduler Decomposition)

Search currently bundles too many distinct concerns. The architecture must decompose into:

```text
GeneratorKernel
  proposal mechanics

SearchKernel
  traversal abstraction only

SchedulerKernel
  queue ordering, fairness, replay determinism

PruningKernel
  admissible pruning only

FrontierKernel
  DAG/tree bookkeeping

MemoizationKernel
  opaque evaluator caching
```

Search itself should eventually become very small: expand frontier, schedule traversal, manage bookkeeping, emit traversal trace, stop.

### Theorem-Level Infrastructure

* `SearchSemanticOpacity`
* `NoSolverInsideSearch`
* `FrontierReachability`
* `WitnessNonStarvation`
* `SeedInvariantOntology`
* `TraceCompletePruning`
* `DuplicatePruningSoundness`
* `EvaluatorSearchSeparation`

## Search Laws (Theorem Candidates)
* `SemanticOpacityOfSearch`
* `WitnessReachability`
* `NonStarvation`
* `DuplicatePruningSoundness`

---

## Canonical Invariant

> **Search schedules. Generator proposes. Evaluator judges. Solver proves. Engine executes.**
