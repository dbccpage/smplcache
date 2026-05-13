```yaml
contract:
  name: Observable
  role: state extraction
  depends_on:
    - Paper 001 Coordinate Obstruction
    - Paper 002 Gauge Obstruction
    - Paper 004 Vector Gauge Obstruction
  forbidden_authority:
    - evaluator judgment
    - state mutation
    - search scheduling
  output_family:
    - FactEnvelope
```
# Observable Contract

## Core Role

Observables (`src/observables/`) define strict, non-mutating extraction interfaces for state-derived measurable quantities.

These quantities may be algebraic, topological, geometric, quantum, probabilistic, or structural.

Examples include:

- Fidelity
- Entanglement entropy
- Coboundary obstruction Φ₁
- Instability Γ₂
- Structural coherence Ξ
- Closure ratio r_cl

An Observable defines what can be measured from a reasoning state. It does not decide how that measurement affects search, descent, acceptance, pruning, or evaluation.

---

## Architectural Mandate & Contractual Obligations

### 1. Pure Definition & Extraction (MUST DO)

An Observable MUST define:

- input state type
- output type
- domain validity conditions
- extraction semantics
- error conditions

An Observable MAY return:

- scalar
- vector
- tensor
- residual
- witness
- certificate
- structured measurement output

Example trait shape:

```rust
pub struct ObservableSignature {
    pub input_schema: &'static SchemaRef,
    pub output_schema: &'static SchemaRef,
    pub required_invariants: &'static [&'static str],
    pub solver_required: bool,
}

pub trait Observable<State> {
    fn signature(&self) -> ObservableSignature;
    type Output;

    fn observe(&self, state: &State) -> Result<Self::Output, ObservableError>;
}
```

Observation MUST be a pure extraction from the caller's perspective.

---

### 2. Zero State Collapse / Non-Mutation

Observation MUST NOT permanently mutate, collapse, normalize, project, truncate, overwrite, or otherwise alter the underlying engine state.

Required signature shape:

```rust
fn observe(&self, state: &State) -> Result<Self::Output, ObservableError>;
```

Forbidden signature shape:

```rust
fn observe(&mut self, state: &mut State) -> Result<Self::Output, ObservableError>;
```

Any temporary internal workspace must be local to the call or stored in explicitly external solver/workspace objects that do not mutate the input state.

---

### 3. Separation from Functionals and Evaluators

Observables extract quantities.

Functionals may compose observables into global metrics.

Evaluators decide descent, acceptance, rejection, pruning, or ranking.

Observables MUST NOT perform:

* acceptance decisions
* rejection decisions
* descent decisions
* pruning
* candidate ranking
* search branching
* engine state mutation

Allowed:

```rust
Phi1Output {
    value,
    residual,
    dual_witness,
    certificate,
}
```

Forbidden:

```rust
Phi1Output {
    value,
    accepted: true,
    should_descend: true,
}
```

---

### 4. Solver-Backed Observables

An Observable MAY use a solver when extraction requires optimization, eigendecomposition, linear algebra, or numerical certification.

The Observable remains responsible only for invoking extraction and returning the computed measurement.

The Solver is responsible for computation and certificates.

**Solver-backed Observable Law:**
If an Observable invokes a Solver, the Observable is responsible only for domain validation, solver invocation, and packaging the SolverResult. The Observable must not interpret the result as acceptable, sufficient, converged-for-action, or structurally decisive.

**Certificate Propagation:**
If the observable output depends on a certificate-bearing solver, the output must carry the certificate or certificate reference. Dropping solver certificate data is a contract violation.

The Evaluator is responsible for decisions.

Pipeline:

```text
state → observable → solver → certificate → evaluator
```

---

### 5. Typed Errors & Domain Validation

Each Observable MUST define its domain of validity and return a typed error when the input state is outside that domain.

Examples:

```text
Fidelity requires compatible density matrix dimensions.
Entropy requires positive semidefinite trace-one matrix.
Φ₁ requires τ length = |E|.
```

Error type shape:

```rust
pub enum ObservableError {
    InvalidDomain(String),
    DimensionMismatch { expected: usize, got: usize },
    NonFiniteInput,
    SolverFailed(String),
}
```

---

### 6. Determinism & Reproducibility

Given identical input state, observable configuration, and solver parameters, observation SHOULD be deterministic.

If stochastic computation is used, randomness MUST be seed-configurable:

```rust
pub struct ObservableParams {
    pub seed: Option<u64>,
    pub abs_tol: f64,
    pub rel_tol: f64,
}
```

---

## Canonical Invariant

> **Observables extract measurable quantities. They do not interpret consequences.**
