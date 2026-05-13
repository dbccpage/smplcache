```yaml
contract:
  name: Diagnostic
  role: health reporting
  depends_on:
    - Paper 003 Quotient Obstruction
  forbidden_authority:
    - action prescription
    - escalation
    - policy interpretation
  output_family:
    - FactEnvelope
```
# Diagnostic Contract

## Core Role
`Diagnostic ⊂ AnalysisUnit`

A Diagnostic is a specialized subtype of `AnalysisUnit` strictly responsible for health reporting. It exists to produce typed health reports about declared engine states or artifacts. 

Diagnostics may summarize measurements, violations, and structural findings, but they **must not mutate state, invoke execution, schedule control flow, classify admissibility, or prescribe actions**.

## Architectural Mandate & Contractual Obligations

### 1. Pure Reporting (No Decisions)
Diagnostic results **must not** encode decisions. They are factual observations of the state.
- **Allowed**: `findings: Vec<DiagnosticFinding>`, `measurements: Vec<Measurement>`.
- **Forbidden**: `should_rollback`, `should_retry`, `priority`.

### 2. Typed Failure without Side-Effects
A Diagnostic failure must return a typed `DiagnosticError`. The caller decides logging, suppression, escalation, or abort behavior. Diagnostics **must not** own logging policy (e.g. they should not `log::warn!` internally).

### 3. Solver Isolation
Diagnostics may consume solver outputs if provided, but **Diagnostics must not invoke heavy solvers directly.** If solver invocation is needed, that belongs in an `AnalysisUnit` or `Observable`, not a Diagnostic.

### 4. Strictly Read-Only Enforceability
A diagnostic evaluates state non-destructively:
```rust
fn diagnose(&self, input: &Input) -> Result<Self::Output, DiagnosticError>;
```
It **must not** take `&mut self` or `&mut Input`.

### 5. Factual Severity (Anomaly Classes)
Severity can easily leak into policy. You must use factual, descriptive anomaly classes from the `HealthModelKernel`, not action priority.

```rust
pub enum AnomalyClass {
    /// Informational finding with no structural impact.
    Informational,
    /// Measurable structural difference from ideal.
    StructuralDeviation,
    /// Finding that threatens a core invariant.
    InvariantThreat,
    /// Absolute failure of a boundary condition.
    BoundaryFailure,
}
```

Severity is descriptive, not prescriptive.
No anomaly class implies rollback, retry, lift, abort, or escalation.
Only Policy/Evaluator/Engine may map these classes to action.

## Health Report Shape
```rust
pub struct DiagnosticReport {
    pub subject: BoundaryRef,
    pub findings: Vec<DiagnosticFinding>,
    pub measurements: Vec<MeasurementRef>,
    pub certificate_refs: Vec<CertificateRef>,
}

pub struct DiagnosticFinding {
    pub code: DiagnosticCode,
    pub severity: DiagnosticSeverity,
    pub message: String,
    pub evidence: Vec<EvidenceRef>,
}
```

## Evidence-Addressability
Diagnostic findings must be evidence-addressable.
A diagnostic that says "bad state" without a subject, code, and evidence reference is invalid.

- **Forbidden**: `Retry`, `Rollback`, `Abort`.

## Tightened Definition

> **Diagnostics report health facts. They do not prescribe treatment.**
