# Pipeline Contract

## Core Definition

> A Pipeline is a **typed control-category over engines**. It is a deterministic or policy-driven orchestration system that transitions between Engines according to an explicit stage graph, enforcing typed handoffs, adapter boundaries, rollback rules, and termination conditions, while emitting a complete execution trace and never executing operator-core logic directly.

### Classification
**Subsystem:** pipeline / orchestration contract
**Not:** engine, evaluator, validator, adapter, solver, renderer, operator kernel.

In the MSS/UROS stack, **the Pipeline is not a computational layer**. It is a **control-category morphism**:
$$ \mathsf{Pipe} : (\mathcal{E}_1, \mathcal{E}_2, \dots, \mathcal{E}_k) \longrightarrow (\mathcal{E}_1, \mathcal{E}_2, \dots) $$
where each $\mathcal{E}_i$ is an Engine operating at a fixed MSS level. 

**The Pipeline does not reason. It only sequences reasoning systems.** It cannot call operators, invoke solvers, compute structural metrics, or reinterpret results.

---

## Interaction with $\kappa$, $\Delta$-Minimality, and MSS Typing

Here is the clean separation of authority over the structural hierarchy:

| Layer | Allowed to affect $\kappa$? | Allowed to affect MSS level? |
|---|---|---|
| **Operator** | ✅ (locally) | ❌ |
| **Engine** | ✅ (detects) | ✅ (via MetaLift) |
| **Pipeline** | ❌ | ❌ |

$\kappa$ changes are detected inside Engines, $\Delta$-minimality is enforced inside MetaLift, and the Pipeline merely routes control based on typed outcomes. A Pipeline cannot skip MSS levels, create implicit oracle jumps, or simulate a higher $\Sigma^0_k$ via heuristic branching.

---

## Architectural Mandate & Contractual Obligations

### 1. Step Semantics (MUST DO)

```rust
pub trait Pipeline<Input, Output, PState> {
    fn step(
        &self,
        input: &Input,
        state: &PState,
    ) -> Result<(PState, PipelineEvent), PipelineError>;
}

/// Pipeline routes based on engine-issued status.
/// It must not inspect Φ, Γ₂, or κ directly.
pub enum EngineStepStatus {
    Progressed(EngineTraceId),
    Completed(EngineCertificateId),
    Failed(EngineFailure),
    LiftRequested(LiftRequestId),
}

pub struct EngineTerminationCertificate {
    pub engine_id: EngineId,
    pub status: EngineStepStatus,
    pub certificate_hash: Hash256,
}
```

```rust
pub enum PipelineEvent {
    Transition { from: EngineId, to: EngineId },
    Rollback { from: EngineId, to: EngineId },
    Branch { from: EngineId, to: EngineId },
    Terminate(PipelineTermination),
}
```

Each step maps `(input, pipeline_state)` to `(next_pipeline_state, event)` via a declared stage graph.

---

### 2. Explicit Stage Graph (MUST DO)

A Pipeline must explicitly declare stages, edges, handoff types, and rollback edges.

```rust
pub struct StageEdge {
    pub from: EngineId,
    pub to: EngineId,
    pub handoff: HandoffSignature,
    pub adapter: Option<AdapterId>,
}

pub struct RollbackEdge {
    pub from: EngineId,
    pub to: EngineId,
    pub condition: RollbackCondition,
}
```

If the graph is implicit in code, the contract is weak. If stages are created ad hoc at runtime, it is orchestration sludge.

---

### 3. Typed Handoff Signatures (MUST DO)

```rust
pub struct HandoffSignature {
    pub state_type: TypeRef,
    pub schema_ref: SchemaRef,
}
```

Invariant:

```text
type(output(engine_i)) MUST exactly match type(input(engine_j))
or an explicit Adapter MUST exist on the edge
```

Rules:

```text
If representation differs → adapter MUST be declared on edge
If representation matches → adapter MUST be absent
```

No "close enough" typing. No implicit coercion.

---

### 4. Typed Rollback Conditions (MUST DO)

```rust
pub enum RollbackCondition {
    LiftRejected,
    EngineFailure,
    BoundaryViolation,
    PolicyTriggered,
}
```

Rollback invariants:

```text
- Rollback must reference a prior reachable stage
- Rollback must not create infinite loops without budget guard
- Rollback transitions must appear in declared rollback edges
```

---

### 5. Termination Law (MUST DO)

```rust
pub enum PipelineTermination {
    Completed,
    BudgetExceeded,
    Failed(PipelineError),
}
```

Every pipeline MUST define explicit termination conditions. Pipeline must terminate when budget is exhausted.

---

### 6. Budget Semantics (MUST DO)

```rust
pub struct PipelineBudget {
    pub max_steps: usize,
    pub max_rollbacks: usize,
}
```

Invariant: pipeline MUST terminate when budget exhausted.

---

### 7. Determinism Declaration (MUST DO)

```rust
pub enum PipelineDeterminism {
    Deterministic,
    PolicyDriven { policy: PolicyId },
}
```

If policy-driven, the policy must be explicitly referenced. No hidden routing logic.

---

### 8. Pipeline Runtime State (MUST DO)

```rust
pub struct PipelineRuntime<PState> {
    pub current_stage: EngineId,
    pub state: PState,
    pub step_index: usize,
    pub trace: Vec<PipelineTraceStep>,
    pub budget: PipelineBudget,
}

pub struct PipelineTraceStep {
    pub from: EngineId,
    pub to: EngineId,
    pub event: PipelineEvent,
}
```

Pipeline state is orchestration-scoped. It must not silently absorb engine-local internals or become a secret second runtime.

---

### 9. Trace Invariants (MUST DO)

```text
PipelineTrace must satisfy:
- total order of stage transitions
- each transition corresponds to a declared edge
- rollback transitions correspond to declared rollback edges
- trace.len() == step_index
```

---

### 10. No Operator Execution (MUST NOT DO)

Pipeline MUST NOT:
- call `LambdaOp` / `ReducerOp` / `TransformOp` directly
- bypass Engine to execute operators
- embed operator logic
- invoke solvers directly
- compute observables directly

Pipeline orchestrates **Engines only**.

---

### 11. No Engine Redefinition (MUST NOT DO)

Pipeline must not redefine engine-local semantics, silently mutate engine-local state, or absorb engine runtime internals.

---

## Typed Errors

```rust
pub enum PipelineError {
    InvalidInput,
    SignatureMismatch,
    StageTransitionFailure,
    EngineInvocationFailure,
    BoundaryViolation,
    RollbackFailure,
    BudgetExceeded,
    UndeclaredTransition { from: EngineId, to: EngineId },
}
```

---

## Admissibility Checklist

A Pipeline is admissible only if ALL are true:

1. Stage graph is explicit (stages + edges + rollback edges)
2. Stages are Engines (not operators, adapters, or analysis units)
3. Handoff signatures are typed (`HandoffSignature`)
4. Adapter edges declared where representation changes
5. Branching conditions are explicit
6. Rollback conditions are typed and bounded
7. Termination conditions are explicit
8. Budget is declared
9. Determinism mode is declared
10. Pipeline state is explicit and orchestration-scoped
11. Direct operator execution is forbidden
12. Trace satisfies completeness invariants
13. Typed orchestration outputs are emitted

If any are missing, reject it.

---

## Unsoundness Prevented

This strict contract blocks several classic orchestration failure modes:
1. **Accidental Turing Completeness:** The Pipeline itself cannot introduce unbounded recursion or hidden computation beyond its finite stage graph and budget.
2. **Policy Masquerading as Reasoning:** Policies may choose paths, but they may not create new paths or increase expressivity implicitly.
3. **Silent Expressivity Escalation:** No adapter → no coercion → no jump in MSS level.
4. **Trace Erasure:** Every transition is logged, ordered, and auditable.

---

## Global Pipeline Termination and Ordinal Typing

Global orchestration inherits well-foundedness without re-introducing recursion.

### Theorem 1 (Pipeline Termination)
Let a pipeline consist of a finite stage graph $G = (V,E)$ whose vertices are Engines $E_i$, each equipped with a well-founded local measure and ordinal level $\alpha_i$. 

**Correction:** Pipeline may reference engine-declared termination certificates. Pipeline must not inspect or compute $\Phi, \Gamma_2, \kappa$ directly.

The global pipeline termination measure depends on:
$$ \text{budget} + \text{finite graph} + \text{engine certificates} $$

Assume:
1. Every pipeline step invokes exactly one engine step or a declared transition.
2. All rollback edges consume rollback budget.
3. All forward transitions satisfy either $\alpha_j = \alpha_i$ (same level) or $\alpha_j = \alpha_i + 1$ (MetaLift transition).
4. The pipeline stage graph is finite and budget-bounded.

Then every pipeline execution terminates. (Proof sketch: Engines terminate locally. Rollback may decrease stage ordinal only along declared rollback edges and only by consuming rollback budget, making infinite loops impossible. MetaLift strictly increases ordinal index up to the finite graph bound. Limit ordinals are unrepresentable.)

### Ordinal-Indexed Pipeline Type
Pipelines are typed by their maximum ordinal bound to enforce an expressivity ceiling:
```rust
struct Pipeline<MaxOrd> {
    stages: Vec<Engine<Ord <= MaxOrd>>,
}
```
A transition edge $E_i \to E_j$ type-checks iff $\alpha_j = \alpha_i$ or $\alpha_j = \alpha_i + 1$. Any attempt to skip levels or jump beyond `MaxOrd` is a compile-time failure.

### Safe MSS-9 → MSS-11 Pipeline Example
Consider $E_9 (\Sigma^0_6) \xrightarrow{\text{lift}} E_{10} (\Sigma^0_7) \xrightarrow{\text{lift}} E_{11} (\Sigma^0_8)$ with internal rollbacks. 
This is maximally permissive yet provably safe: $E_9$ cannot skip $E_{10}$ to reach $E_{11}$ without a certified $\Sigma^0_7$ witness. No rollback can decrease the ordinal index, and no edge can bypass the rigorous $\kappa$-increase mandated by the Meta layer.

---

## Tests to Add Immediately

### Unit Tests
* `pipeline_rejects_operator_stage`
* `pipeline_rejects_solver_edge_without_engine`
* `pipeline_rejects_implicit_adapter`
* `pipeline_rejects_signature_mismatch`
* `pipeline_terminates_on_budget_exhaustion`
* `rollback_requires_declared_edge`
* `rollback_consumes_budget`
* `trace_transition_must_match_declared_edge`
* `pipeline_cannot_mutate_engine_state`
* `pipeline_cannot_read_phi_gamma_kappa_directly`

### Integration Tests
Test this full sequence:
```text
Stage0Engine → Stage1Engine → Stage2AuditEngine → Stage2_5ExtractionEngine → Stage3EvaluatorEngine → Stage4CountermodelEngine
```

Required checks:
* No operator appears as a graph vertex.
* Every edge has a `HandoffSignature`.
* Every representation change has an `AdapterId`.
* Every rollback edge is declared.
* Every transition emits `PipelineTraceStep`.
* No Stage 3 entry without Stage 2.5 readiness certificate.

---

## Best Future Architecture

The Pipeline should be split into four discrete contracts to maintain strict hierarchy:
1. **PipelineContract**: Orchestration logic.
2. **EngineContract**: Execution semantics.
3. **AdapterContract**: Type transformation.
4. **CertificateContract**: Termination and lift proofing.

Pipeline should traffic only in certificates and typed artifacts:
* `PipelineArtifact`
* `EngineCertificate`
* `HandoffSignature`
* `AdapterId`
* `PipelineTraceStep`

Engine-local math remains inside engines.

---

## Canonical Invariant

> **Pipeline can sequence reasoning systems, but it cannot increase expressivity except by invoking Engines that perform licensed MetaLift.**
