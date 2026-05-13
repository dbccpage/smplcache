```yaml
contract:
  name: HealthModelKernel
  role: health state formalization
  depends_on:
    - Diagnostic
    - CertificateKernel
```
# Health Model Kernel Contract

## Core Definition

> The HealthModelKernel formalizes the "health" of the system relative to declared invariant families and admissibility regimes. It provides the ground truth for what constitutes a "nominal" vs "deviant" state, without prescribing how to fix it.

---

## Health State Taxonomy

### 1. Invariant Families
Health is always relative to a family of invariants:
- **Structural**: Topology, graph connectivity, basis consistency.
- **Algebraic**: Boundary conditions, quotient norms, dual feasibility.
- **Operational**: Resource bounds, liveness, reachability.

### 2. Admissibility Regimes
A state's health may be interpreted differently depending on the regime:
- **Strict**: All invariants must hold exactly.
- **Heuristic**: Small violations allowed if bounded by certificates.
- **Recovery**: Active search for a healthy state; deviations expected.

---

## Core Types

### 1. Health Status
```rust
pub enum HealthStatus {
    /// All invariants in the regime hold.
    Nominal,
    /// Measurable deviation detected, but within tolerance.
    Degraded(StructuralDeviation),
    /// Invariant failure that requires judgment (Evaluator).
    Stalled(InvariantThreat),
    /// Fundamental boundary collapse.
    Terminal(BoundaryFailure),
}
```

### 2. Anomaly Classes (Descriptive)
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

---

## Contractual Invariants

### 1. No Action Prescriptiveness
The Health Model reports the *state of being*, not the *state of doing*.
It answers "How are we?" not "What should we do?"

### 2. Evidence Grounding
Every health status change must be backed by a `DiagnosticReport` and associated `Certificates`.

---

## Canonical Invariant

> **Health is the measurable alignment of state with intent.**
