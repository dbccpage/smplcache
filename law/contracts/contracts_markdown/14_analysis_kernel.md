```yaml
contract:
  name: AnalysisUnit
  role: fact verification
  depends_on:
    - Paper 003 Quotient Obstruction
    - Paper 007 ℓ¹ Obstruction Framework
    - Paper 014 Obstruction DSL
    - CertificateKernel
    - HealthModelKernel
  forbidden_authority:
    - evaluator judgment
    - engine transition
    - policy interpretation
    - lift licensing
  output_family:
    - FactEnvelope
```
# Analysis Contract

## Core Definition

> An AnalysisUnit is a deterministic, read-only computational unit that produces typed, verifiable measurements, decompositions, validations, or certificates over declared artifacts, without invoking operators, exploring alternatives, mutating state, or influencing execution decisions.

---

## Architectural Mandate & Contractual Obligations

### 1. Trait Shape (MUST DO)

```rust
## Analysis vs Observable
Observable extracts a value from state.
AnalysisUnit interprets or verifies an already-declared artifact boundary.

Observable answers: "What is measurable here?"
AnalysisUnit answers: "What does this artifact/certificate/decomposition verify?"

## Analysis Boundary Law
An AnalysisUnit may consume Observables, SolverResults, TraceArtifacts, or BaseTypes,
but it must not create new state, invoke Adapters, invoke Operators, or emit EngineDecision.

### 1. Trait Shape (MUST DO)
```rust
pub trait AnalysisUnit<Input> {
    type Output;

    fn analyze(&self, input: &Input) -> Result<Self::Output, AnalysisError>;
}
```

Enforced by design:

```text
&self    → no mutation of analysis unit
&Input   → read-only input
Output   → detached, owned result
```

---

### 2. Typed Output Categories (MUST DO)

Every analysis output must be classified:

```rust
pub enum AnalysisKind {
    /// Scalar metrics, bounds, norms
    Measurement,
    /// Structural breakdowns (cycle basis, spectral decomposition)
    Decomposition,
    /// Factual assertions about invariants ("invariant X holds")
    Validation,
    /// Verifiable proof data (dual witness, residual + gap)
    Certificate,
}
```

**Allowed outputs**:

```rust
pub enum AnalysisArtifact {
    Measurement(Measurement),
    Decomposition(Decomposition),
    Validation(ValidationFact),
    Certificate(Certificate),
}
```

**Forbidden output fields** (analysis must never produce):

```rust
suggest_action
recommend_operator
priority_score
risk_level
should_retry
is_good
accepted
rejected
needs_repair
is_admissible
is_safe
is_valid_to_execute
requires_lift
requires_escalation
```

Analysis output is factual, verifiable, and non-actionable. It does not encode decisions, priorities, or recommendations.

---

### 3. Computation vs Execution Boundary (MUST DO)

AnalysisUnit MUST NOT invoke or simulate state-transforming operators.

AnalysisUnit MAY perform closed-form or direct algebraic computations over provided artifacts:

**Allowed** (derived computation):

```text
r = Bσ − τ              (residual computation)
Φ₁ = ||δ_irr||₁         (norm from solver output)
BᵀP = 0 check           (dual feasibility verification)
cycle basis extraction   (linear algebra)
eigenvalue computation   (bounded algebra)
```

**Forbidden** (operator execution):

```text
apply_lift(state)
solve_and_descend(state)
generate_candidates(state)
search_for_better_trace(state)
```

---

### 4. Iteration Law (MUST DO)

**Allowed**:
- Deterministic finite iteration over input structure
- Bounded, data-dependent iteration with fixed semantics
- Summation, linear algebra routines, decomposition

**Forbidden**:
- Branching search over alternatives
- Convergence-driven exploration
- Adaptive exploration based on intermediate results
- Recursive expansion over unknown structures

> Iteration is allowed if it is deterministic and does not explore alternative states or decisions.

---

### 5. Certificate Consistency (MUST DO)

If an AnalysisUnit returns a certificate, it MUST include sufficient data to independently verify the claimed property.

Example:

```rust
pub struct AnalysisOutput<T> {
    pub kind: AnalysisKind,
    pub subject: BoundaryRef,
    pub result: T,
    pub certificate_ref: Option<CertificateRef>,
    pub tolerance: Option<ToleranceSpec>,
}

pub struct FactEnvelope<T> {
    pub subject: BoundaryRef,
    pub fact: T,
    pub evidence: EvidenceSet,
}

pub enum MeasurementValue {
    /// Exact symbolic/integer value
    Exact(i64),
    /// Replayable fixed-point value
    Fixed(FixedPoint),
    /// Heuristic float (forbidden for governance judgment)
    Heuristic(f64),
}

pub struct Phi1AnalysisOutput {
    pub phi1: MeasurementValue,
    pub residual_l1: MeasurementValue,
    pub dual_value: MeasurementValue,
    pub duality_gap: MeasurementValue,
}
```

Invariant: `|residual_l1 - dual_value| ≤ tolerance`

Certificates that report a value without verification data are contract violations.

---

### 6. Determinism & Tolerance Semantics (MUST DO)

Same input artifact, same analysis result.

Numerical outputs MUST specify tolerance semantics:
- absolute tolerance
- relative tolerance
- ordering guarantees

No hidden randomness. No sampling-based judgment unless sampling is explicitly declared and seed-controlled.

---

### 7. Domain Signature Typing (MUST DO)

```rust
pub struct AnalysisSignature {
    pub subject_types: &'static [TypeId],
}
```

Input MUST match declared types exactly. No implicit coercion.

---

### 8. No Adapter Invocation (MUST NOT DO)

AnalysisUnit MUST NOT invoke Adapters implicitly or explicitly. Otherwise analysis becomes hidden representation conversion.

If representation conversion is needed, it must happen outside the analysis boundary and be declared in the pipeline.

---

### 9. Multi-Subject Independence (MUST DO)

pub enum SubjectInteraction {
    /// Subjects are analyzed independently.
    Independent,
    /// Subjects are analyzed together via an explicit interaction model.
    Coupled(InteractionModelId),
}

If observing multiple artifact classes, the AnalysisUnit must declare:
- `SubjectInteraction`, AND
- explicit interaction model (if coupled)

Otherwise hidden inference emerges.

Examples:

- "Φ = 2.1 and τ ≠ 0" → **Analysis** (factual observation)
- "therefore admissibility is rejected" → **Meta** (governance decision)
- "therefore apply lift now" → **Engine** (execution action)

---

### 10. Validation vs Meta-Judgment (MUST DO)

**ValidationFact** (allowed in Analysis):

```text
"invariant X holds"
"BᵀP feasibility: true"
"duality gap: 1e-9"
```

**Admissibility judgment** (forbidden — belongs to Meta):

```text
"artifact is admissible"
"trace is acceptable"
"system should proceed"
```

---

### 11. Complexity Bound (MUST DO)

AnalysisUnit MUST have bounded computational complexity relative to input size.

Disallowed:
- Unbounded exploration
- Adaptive depth growth
- Recursive expansion over unknown structures
- Convergence loops with no iteration bound

---

## Typed Errors

```rust
pub enum AnalysisError {
    InvalidInput,
    SignatureMismatch,
    MeasurementFailure,
    InvariantViolation { invariant: String },
    DomainViolation,
    CertificateIncomplete,
}
```

Errors are not decisions. They only indicate failure to compute the analysis.

---

## Admissibility Checklist

An AnalysisUnit is admissible only if ALL are true:

1. Domain is explicit (typed `AnalysisSignature`)
2. Codomain is typed (`AnalysisKind` + concrete output)
3. Read-only behavior is enforced (`&self, &Input`)
4. No state transition occurs
5. No operator execution occurs
6. No runtime governance occurs
7. No adapter invocation occurs
8. Output is factual, not actionable
9. Certificates include verification data
10. Iteration is bounded and non-branching
11. Multi-subject independence is declared
12. Complexity is bounded

If any are missing, reject it.

---

## Classification Boundary

| Layer | Role |
|---|---|
| **AlgebraLaw** | Defines what must hold, non-executably |
| **AnalysisUnit** | Computes/validates what is observed, read-only |
| **MetaRule** | Judges admissibility/composability/governance |
| **Engine** | Acts on runtime consequences, owns state progression |

If a file both measures Φ and triggers lift, split it.

---

## Canonical Invariant

> **Analysis verifies facts over declared artifacts. It never decides what the system should do.**
