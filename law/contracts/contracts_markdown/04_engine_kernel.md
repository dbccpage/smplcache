# Engine Contract

## Core Definition

> An Engine is a **typed obstruction-reduction state machine**. It is a stateful transition system that iteratively maps `(state, runtime_state)` to `(state, runtime_state, decision)` by measuring obstruction, evaluating Φ, selecting and applying operators, and enforcing termination, rollback, and lift conditions under explicit policy and budget constraints, while emitting a complete execution trace.

### Classification
**Subsystem:** execution kernel contract
**Not:** pipeline, operator algebra, evaluator, solver, adapter, renderer.

The Engine is the ONLY component allowed to:
* perform search
* branch over candidates
* schedule execution
* apply policies
* mutate runtime state over time
* orchestrate lifts/rollbacks

Everything else must explicitly forbid those.

---

## Architectural Mandate & Contractual Obligations

### 1. Step Function (MUST DO)

```rust
pub trait Engine<State, RuntimeState> {
    fn step(
        &self,
        state: &State,
        runtime: &RuntimeState,
    ) -> Result<(State, RuntimeState, EngineDecision), EngineError>;
}
```

Each step MUST:
1. Measure obstruction δ
2. Compute Φ
3. Select operator or terminal action
4. Apply operator OR trigger rollback/lift
5. Update runtime state
6. Emit trace step

---

### 2. Structural Runtime State (MUST DO)

Φ and obstruction are structurally required fields, not optional debug metrics.

```rust
pub enum PhiValue {
    Exact(Rational),
    Symbolic(PhiExprId),
    Regime(PhiRegime),
}

pub struct EngineRuntimeState<State> {
    pub current_state: State,
    pub obstruction: Obstruction,
    pub phi: PhiValue,
    pub step_index: usize,
    pub last_decision: Option<EngineDecision>,
    pub trace: Vec<EngineTraceStep>,
    pub budget: Budget,
}
```

Invariant after each step:

```text
phi = Φ(current_state)
obstruction consistent with state
trace.len() == step_index
```

---

### 3. Typed Decision Output (MUST DO)

```rust
pub enum EngineDecision {
    ApplyOperator(OperatorId),
    Rollback,
    Lift(LiftType),
    Continue,
    Terminate(TerminationCondition),
}

pub enum LiftType {
    Escalation,
    OntologyExtension,
    MssLevelTransition,
    DistinctionIntroduction,
    MetaTransition,
}
```

Decision MUST be derived from:
- Φ comparison
- obstruction structure
- declared policy

Not from hidden heuristics, vibes, score soups, or surface-name matching.

---

### 4. Typed Selection Basis (MUST DO)

```rust
pub enum SelectionBasis {
    PhiSignature,
    ObstructionSignature,
    Policy(PolicyId),
}

pub struct DecisionCertificate {
    pub basis: SelectionBasis,
    pub inputs_used: Vec<InputId>,
    pub policy_id: Option<PolicyId>,
    pub signature: CertificateSignature,
}
```

Selection MUST be reproducible given `(state, runtime_state, policy)`. No hidden inputs.

---

### 5. Termination Law (MUST DO)

```rust
pub enum TerminationCondition {
    Irreducible,
    BudgetExceeded,
    Converged,
    ExternalAbort,
}
```

Every engine MUST define explicit termination conditions. Without this, engines can loop indefinitely.

---

### 6. Rollback Semantics (MUST DO)

Rollback MUST:
- reference a prior state in runtime trace
- be bounded (no infinite rollback loops)
- preserve trace integrity (rolled-back steps remain in trace, marked as rolled back)

---

### 7. Certificate Propagation (MUST DO)

Engine MUST propagate certificates from Observables/Solvers into runtime state or trace.

```rust
pub struct EngineTraceStep {
    pub state_snapshot: StateId,
    pub phi: PhiValue,
    pub decision: EngineDecision,
    pub decision_cert: DecisionCertificate,
    pub certificate: Option<Certificate>,
}
```

Otherwise Φ becomes untrusted.

---

### 8. Trace Completeness (MUST DO)

> Every state transition MUST correspond to exactly one trace step.

Invariant:

```text
trace.len() == runtime_state.step_index
```

If an engine returns only raw state without trace, it is suppressing runtime evidence.

---

### 9. Determinism Declaration (MUST DO)

Engines may be stochastic (e.g., MCTS), but must declare it.

```rust
pub enum Determinism {
    Deterministic,
    Stochastic { seed_required: bool },
}
```

---

### 10. No Internal Solver Implementation (MUST NOT DO)

> Engine MUST NOT implement mathematical solvers internally.

Allowed:

```text
Engine → calls Solver → receives result + certificate
```

Forbidden:

```text
Engine reimplements PDHG, Gaussian elimination, eigendecomposition, etc.
```

---

Without this, engine becomes a god-object.

### 12. Delegation of Non-Core Responsibilities (SHOULD DO)

To prevent the Engine from becoming a god-object, it should delegate non-execution tasks:
* **Search schedules:** Delegate to a `SearchKernel`.
* **Policy constrains:** Delegate to a `PolicyKernel`.
* **Meta analysis measures:** Delegate to a `MetaAnalysisKernel`.

The Engine orchestrates these, but does not implement their logic internally.

---

## Escalation Handling and MetaLift Connection

When the Evaluator returns `EvaluationVerdict::Escalate { route }`, it signals that judgment cannot proceed under the current evaluation authority. Escalation is **not** failure, repair, or lift—it is a *change of authority* that only the Engine is allowed to interpret and orchestrate.

### 1. Engine Interpretation of Escalation Routes

*   **`EscalationRoute::Solver`**: Structural reasoning is sound, but numerical realization is insufficient (e.g., $\Phi_1$ stagnates, $\Gamma_2 = 0$, but precision is low). The Engine retries with a different solver backend, precision, or initial condition. **No structural change. No MetaLift.**
*   **`EscalationRoute::MetaLayer`**: Structural reasoning is exhausted (e.g., $\Phi_1 > 0$ and cannot be reduced, $\Gamma_2 = 0$). The Engine enters the **Meta phase**. It orchestrates the check for admissible-context witnesses and non-congruence. *MetaLayer $\neq$ MetaLift*; it is only *permission to consider* MetaLift. The Engine must still prove exhaustion, critical pair existence, and $\Delta$-minimality before any lift occurs.
*   **`EscalationRoute::HumanReview`**: Neither numeric remediation nor structural extension is licensed (e.g., policy forbids lift, missing certificates). The Engine halts autonomous reasoning and emits a certified refusal with the state snapshot and functional report.

### 2. Why Escalation $\neq$ MetaLift

MetaLift requires strictly more evidence than escalation provides. Escalation only means "I cannot decide under policy." MetaLift requires admissible context enumeration, non-congruence proofs, and $\Delta \in \{1,2\}$ validation. If the Evaluator triggered MetaLift directly, solver noise could mutate structure, breaking confluence and $\Delta$-minimality. The Engine orchestrates the gap between the Evaluator's cry for help and the **MetaAnalysisKernel**.

---

## Admissible-Context Witnesses and Critical Pair Detection

The Engine enumerates contexts for exactly one purpose: to decide whether obstruction equivalence is a congruence at the current MSS level. If a difference is exposed that cannot be named, a critical pair exists and a lift is forced. Otherwise, the obstruction is irreducible and the Engine terminates with honest refusal.

### 1. What a Witness Is
At MSS-level $N$, an **admissible context** $C[\square]$ is a one-hole embedding built only from structure available at level $N$. It is monotone and incapable of resolving obstruction (it can only expose it). **Contexts are observers, not operators.**

**Admissibility Law (Context Monotonicity):**
For any admissible context $C$ and state $x$:
$$ \Phi(C[x]) \ge \Phi(x) $$
Contexts preserve or expose obstruction types; they cannot resolve them.

A **witness** is a specific context $C$ where:
$$ x \sim_N y \quad\text{but}\quad C[x] \not\sim_N C[y] $$
Locally, $x$ and $y$ look the same (same $\Phi$). But when embedded into $C$, their failures differ. This context $C$ is the proof object that a critical pair exists and a distinction remains unnamed.

### 2. When the Engine Looks for Critical Pairs
The Engine MUST NOT look for critical pairs arbitrarily. It enters **Meta analysis mode** only when all three preconditions are met:
1. **Exhaustion:** No admissible operator reduces $\Phi_1$.
2. **Closure integrity:** $\Gamma_2 = 0$ (no boundary tearing).
3. **Evaluator escalation:** The Evaluator returned `Escalate { route: MetaLayer }`.

This strict ordering prevents premature lifts, solver-noise artifacts, and policy-driven ontology changes.

### 3. Enumeration is Finite
Enumeration does not cause search explosion because it relies on the finite obstruction basis (Theorem $A_N$).
*   Contexts are composed starting from the identity context.
*   They are canonicalized up to $\alpha$-equivalence.
*   The Engine stops when the depth bound is reached or no new $\alpha$-distinct contexts appear.

### 4. The Mechanical Detection Procedure
In Meta analysis mode, the Engine performs the critical-pair test:
1. **Apply:** Apply the finite set of admissible contexts to the irreducible obstruction representatives.
2. **Compare:** Compare the formal failure signatures:
    $$ \mathrm{FailSig}_N(x) = (\mathrm{schema}(x),\ \Phi\text{-regime},\ \Gamma_2\text{-regime},\ \mathrm{support\ type}) $$
    where `schema` comes from the finite obstruction basis.

```rust
struct FailureSchema {
    pub obstruction_basis_id: BasisId,
    pub support_signature: SupportSignature,
    pub closure_regime: ClosureRegime,
}
```

    This ensures states with different obstruction supports are not incorrectly collapsed just because they share a Boolean regime. Raw numerical values and $\Xi$ are strictly ignored.
3. **Decide:**
    * **Witness found (Critical pair):** If any context distinguishes the failures, obstruction equivalence is not a congruence. Unary naming is insufficient. The Engine emits `EngineDecision::Lift`. MetaLift introduces $\Delta \in \{1,2\}$ symbols to name the distinction.
    * **No witness found:** If no context distinguishes them, obstruction equivalence is a congruence. The obstruction is irreducible. The Engine emits `EngineDecision::Terminate(Irreducible)` (Honest Refusal).

This is a **structural analogy** to Newman-style confluence completion, adapted for obstruction regimes and finite obstruction bases.

---

## Canonical Execution Loop

This is the minimal lawful engine behavior:

```text
Input state
→ measure obstruction δ
→ evaluate Φ
→ decompose δ into (Bf, τ)
→ if τ is irreducible → trigger lift/projection path
→ else → select admissible operator by obstruction/Φ signature + policy
→ apply operator
→ re-measure δ and Φ
→ accept / rollback / continue
→ emit trace step with certificate
→ check termination conditions
```

---

## Typed Errors

```rust
pub enum EngineError {
    InvalidInput,
    SignatureMismatch,
    RuntimeFailure(String),
    BudgetExceeded,
    OperatorSelectionFailure,
    PhiEvaluationFailure,
    RollbackFailure,
    LiftFailure,
    TerminationViolation,
}
```

---

## Admissibility Checklist

An Engine is admissible only if ALL are true:

1. Runtime state is explicit (typed `EngineRuntimeState`)
2. Φ and obstruction are structural fields
3. Step function produces `(State, RuntimeState, EngineDecision)`
4. Selection basis is typed and declared
5. Re-evaluation occurs after each operator application
6. Termination conditions are explicit
7. Rollback is bounded and trace-preserving
8. Trace emission is complete (`trace.len() == step_index`)
9. Certificates propagated from solvers/observables
10. Determinism mode is declared
11. No internal solver reimplementation
12. Layer boundaries preserved

If any are missing, reject it.

---

## Tests to Add Immediately

### Engine Boundary Tests
* `engine_cannot_execute_adapter_logic`
* `engine_cannot_redefine_operator`
* `engine_cannot_mutate_pipeline_state`

### Exact Arithmetic Tests
* `phi_comparison_exact_under_replay`
* `no_float_rounding_divergence`

### Meta Analysis Isolation Tests
* `engine_requests_meta_analysis_not_internal_enumeration`
* `meta_contexts_are_observers_only`

### Witness Correctness Tests
* `witness_requires_contextual_separation`
* `equal_failsig_no_false_lift`
* `noncongruence_triggers_lift_request`

### Trace Integrity Tests
* `trace_len_equals_step_index`
* `rollback_preserves_trace_history`

---

## Best Future Architecture

The Engine is converging toward a minimal **EngineKernel** that delegates non-execution concerns to specialized kernels:
* **OperatorKernel**: Pure transforms.
* **ObservableKernel**: Extraction.
* **SolverKernel**: Computation.
* **EvaluatorKernel**: Judgment.
* **MetaAnalysisKernel**: Obstruction/Witness analysis.
* **SearchKernel**: Scheduling.
* **PolicyKernel**: Constraint governance.
* **PipelineKernel**: Orchestration.

The EngineKernel's sole job is to execute steps, apply decisions, and maintain the runtime trace.

---

## Theorem-Level Infrastructure Required

* **ContextMonotonicity**: Proof that contexts do not resolve obstruction.
* **NoHiddenStatefulness**: Verification of cache/ambient service purity.
* **WitnessSoundness**: Validation of contextual separation.
* **EscalationDoesNotImplyLift**: Enforcement of MetaAnalysis mode gating.
* **RollbackTracePreservation**: Integrity of the historical record.
* **ExactPhiReplayInvariant**: Guaranteed determinism via `PhiValue`.
* **AdmissibleContextFiniteness**: Tractability of witness enumeration.
* **NoOperatorExecutionInMeta**: Contexts remain read-only observers.

---

## Canonical Invariant

> **Engine executes. Generator proposes. Observable extracts. Solver computes. Evaluator judges. Search schedules. Policy constrains. Adapter converts. Analysis measures. Pipeline orchestrates.**
