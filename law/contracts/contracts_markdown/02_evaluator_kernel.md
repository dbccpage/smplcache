```yaml
contract:
  name: Evaluator
  role: admissibility judgment
  depends_on:
    - AnalysisUnit
    - Diagnostic
    - CertificateKernel
    - HealthModelKernel
  forbidden_authority:
    - structural discovery
    - operator execution
    - state mutation
  output_family:
    - EvaluationVerdict
```
# Evaluator Contract

## Core Definition

> An Evaluator is a deterministic, read-only decision layer that consumes `FactEnvelopes` (from Analysis) and `DiagnosticReports` (from Diagnostics), applies explicit `EvaluationPolicy`, and returns a typed `EvaluationResult`. It is the critical semantic bridge where factual observations are transformed into admissibility judgments.

A deterministic **judgment function** over already-computed facts.
It does not measure.
It does not search.
It does not execute.
It does not discover obstruction structure.
It applies declared admissibility policy to evidence.

---

## Architectural Mandate & Contractual Obligations

### 1. Trait Shape (MUST DO)

```rust
pub trait Evaluator<C> {
    fn evaluate(
        &self,
        input: &EvaluationInput<C>,
        policy: &EvaluationPolicy,
    ) -> Result<EvaluationResult, EvaluationError>;
}
```

Required properties:
- `&self`: No mutation of evaluator.
- `&EvaluationInput<C>`: Read-only input containing candidates and pre-computed fact envelopes.
- `&EvaluationPolicy`: Explicit thresholds using `ToleranceSpec` or `MeasurementValue`.

### 2. Typed Input: Facts, not Raw State (MUST DO)

The Evaluator MUST NOT compute metrics itself. It consumes the outputs of the Analysis layer.

```rust
pub struct EvaluationInput<C> {
    pub parent: C,
    pub candidate: C,
    pub facts: Vec<FactEnvelope>,
    pub health: DiagnosticReport,
}
```

### 3. Typed Verdict (MUST DO)

Verdicts are categorical judgments.

```rust
pub enum EvaluationVerdict {
    /// Candidate meets all admissibility criteria.
    Accept,
    /// Candidate is rejected due to specific invariant failure.
    Reject { 
        reason: RejectionReason,
        evidence: EvidenceRef 
    },
    /// Admissibility cannot be determined at this layer; requires higher authority.
    Escalate { 
        route: EscalationRoute,
        witness: ProofWitness 
    },
}
```

### 4. Deterministic Threshold Policy (MUST DO)

Policies must avoid raw floating-point comparison for critical governance and must be fully typed and configurable without modifying Evaluator logic.

```rust
pub struct EvaluationPolicy {
    pub policy_id: PolicyId,
    pub clauses: Vec<EvaluationClause>,
    pub precedence: ClausePrecedence,
    pub evidence_requirements: EvidenceRequirements,
}
```

The Evaluator should apply policy, not define policy. Policy must be versioned, hashable, replayable, and externally inspectable.

### 5. Verdict Derivation (MUST DO)

Verdicts must explicitly state how they were derived from the policy to ensure they are fully replayable and auditable.

```rust
pub struct EvaluationResult {
    pub verdict: EvaluationVerdict,
    pub policy_id: PolicyId,
    pub clauses_fired: Vec<ClauseId>,
    pub evidence_used: Vec<EvidenceRef>,
}
```

### 6. Separation from Discovery (MUST NOT DO)

The Evaluator MUST NOT:
- Discover new critical pairs.
- Propose structural repairs.
- Invoke MetaLift.
- Call Adapters.

It simply judges if the facts provided by Analysis satisfy the criteria defined in Policy.

---

## Escalation Mechanics

Escalation is a **Verdict**, not an **Action**.

1. **Evaluator** returns `Escalate`.
2. **Engine** or **MetaLayer** receives the verdict and decides whether to trigger a `Lift` or a `Solver` change.
3. **Evaluator** remains pure and read-only.

---

## Admissibility Checklist

1. Input consists of facts and health reports, not raw state.
2. Thresholds are governed by explicit Policy clauses and `ToleranceSpec`, not raw `f64`.
3. Verdict is categorical, evidence-backed, and includes full derivation (`clauses_fired`).
4. No structural discovery or operator execution.
5. Deterministic given the same facts and policy.

---

## Required Test Infrastructure

### Purity & Separation Tests
* `evaluator_cannot_compute_phi`
* `evaluator_cannot_call_solver`
* `evaluator_cannot_call_adapter`
* `evaluator_cannot_emit_lift`

### Determinism & Traceability Tests
* `same_facts_same_policy_same_verdict`
* `reject_requires_evidence_ref`
* `escalate_requires_witness`
* `raw_f64_threshold_rejected`
* `policy_hash_recorded_in_verdict`

---

## Integration Tests

**Run Pipeline:**
```text
Analysis → Diagnostic → Evaluator → Engine
```

**Required Invariants:**
* Analysis computes facts.
* Diagnostic reports health.
* Evaluator emits verdict.
* Engine executes consequence.
* The Evaluator must never mutate state or trigger control flow directly.

---

## Target Architecture

The evaluation layer must separate judgment application from policy definition, utilizing distinct kernels:

```text
EvaluatorKernel
  - VerdictDeriver
  - EvidenceChecker
  - PolicyApplier

PolicyKernel
  - PolicyManifest
  - ClauseEvaluator
  - ToleranceSemantics
  - EvidenceRequirements
```

---

## Canonical Invariant

> **Evaluator applies declared policy to evidence; it does not measure or execute.**
