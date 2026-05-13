# Solver Contract

## Core Role
Solvers (`src/solvers/`) are the heavy mathematical engines. They execute exact or iterative algorithms (CDCL SAT, PDHG, Lanczos Iteration, Dinic's Max Flow) to compute precise algebraic, topological, or numerical quantities on a given state.

## Architectural Mandate & Contractual Obligations

### 1. Pure Mathematical Execution (MUST DO)
Solvers compute mathematically defined quantities and return witnesses, diagnostics, or certified failure. They determine irreducible harmonics, exact saddle points, or minimal vertex covers. 

Because solvers compute in different epistemic classes, their outputs must be explicitly typed. They return `SolverEvidenceClass`, not broad "mathematically guaranteed results":

```rust
pub enum SolverEvidenceClass {
    ExactProof,
    CertifiedApproximation,
    ResidualOnlyApproximation,
    HeuristicOutput,
    CertifiedFailure,
}
```

A solver returns computational facts. Only the Evaluator may turn those facts into admissibility judgments.

### 2. ZERO Heuristics or Evaluation (MUST NOT DO)
Solvers **MUST NEVER** contain evaluation booleans (e.g., `is_good`, `accepted = true`). They cannot contain subjective logic. They answer "What is the $L_1$ norm?" not "Is this $L_1$ norm acceptable?".

### 3. Decoupling from Orchestration
Solvers are strictly stateless sub-routines. They do not know about the pipeline, the LLM swarms, or the global search strategy. They take in raw mathematical structures (graphs, matrices, posets) and return exact solutions or witnesses.

### 4. Certificate Boundary
A solver may return diagnostics such as convergence status, dual feasibility, KKT residuals, unsat cores, eigen residuals, or primal/dual gaps.

The architecture requires a dedicated `CertificateKernel` shared across Solver, Analysis, Trace, and Meta layers, rather than a monolithic `Certificate` type.

```rust
pub enum Certificate {
    UnsatCore(UnsatCoreCertificate),
    DualFeasibility(DualCertificate),
    KktResidual(KktCertificate),
    EigenResidual(EigenCertificate),
    HodgeDecomposition(HodgeCertificate),
    ReplayWitness(ReplayCertificate),
}
```

These diagnostics are facts about the computation, not acceptance decisions. Only Evaluators may interpret them as pass/fail for a reasoning trace. Do not let `converged: true` or `SolverEvidenceClass::ExactProof` carry unstated authority.

### 5. Approximate Solver Disclosure
If a solver is iterative or approximate, it must expose:
- convergence flag
- residual / gap
- iteration count
- tolerance used
- witness if available

```rust
pub enum SolverStatus {
    Evidence(SolverEvidenceClass),
    Approximate {
        converged: bool,
        tolerance: f64,
        iterations: usize,
        primal_residual: Option<f64>,
        dual_residual: Option<f64>,
        gap: Option<f64>,
    },
    Infeasible {
        certificate: Option<Certificate>,
    },
    Failed {
        reason: String, // Or SolverFailureReason
    },
}

pub struct SolverOutput<T> {
    pub value: Option<T>,
    pub status: SolverStatus,
    pub certificate: Option<Certificate>,
}
```

It must not silently present approximate output as exact. Silent approximation causes false convergence by hiding critical pairs and starving MetaLift.

### 6. Interaction with the Core Stack (MUST DO)
*   **With the Engine:** The Engine may route on typed solver outcomes such as `SolverFailed`, `Converged`, or `CertificateMissing`, but thresholds and mathematical accept/reject judgments belong to the Evaluator.
*   **With Search:** Search must NEVER invoke solvers, inspect solver output, or prune based on solver diagnostics. Search is completely blind to mathematical truth.
*   **With MetaLift:** MetaLift relies on solver output only indirectly (through $\Phi_1$ and $\Gamma_2$). It is never triggered by "solver confidence", but strictly by structural proofs of non-congruence via admissible contexts.

### 7. Contract Violation Consequences (The Cost of Betrayal)
If a solver breaks the contract, the entire reasoning engine collapses silently:
*   **Returning "Accepted":** The Evaluator's authority is bypassed. Policy becomes silently embedded inside mathematical subroutines. Reproducibility is instantly lost.
*   **Hiding Approximation:** If an approximate solver claims exactness, $\Phi_1$ might falsely appear to decrease, or $\Gamma_2$ may falsely report 0. Critical pairs become invisible, MetaLift starves, and the system falls into **false convergence**.
*   **Pipeline State Inspection:** If solvers inspect the surrounding Engine/Search state, their behavior becomes context-dependent. Debugging becomes impossible and mathematical guarantees evaporate.

### 8. Vocabulary Enforcement

**Forbidden identifiers** (solvers must never define or return):

```rust
accepted
is_good_trace
verdict
diamond
should_apply
reward
quality_score
```

**Allowed identifiers**:

```rust
converged
dual_feasible
primal_residual
certificate_valid
unsat_core
eigen_residual
```

---

## Required Test Infrastructure

### Purity Tests
* `solver_does_not_return_verdict`
* `solver_does_not_import_engine_or_search`

### Epistemic Tests
* `approximate_solver_reports_tolerance`
* `approximate_solver_reports_iteration_count`
* `failed_solver_returns_typed_failure`
* `unsat_solver_returns_unsat_core_when_available`

### Boundary Tests
* `search_cannot_call_solver`
* `solver_output_not_accepted_without_evaluator`

---

## Integration Tests

**Run Pipeline:**
```text
Generator → Search → Evaluator → Solver → Evaluator → Engine
```

**Verify:**
* Search never sees `SolverOutput`.
* Solver never emits `Accepted`/`Rejected`.
* Evaluator is the absolute only layer mapping `SolverOutput` to judgment.
* Trace explicitly records solver version, tolerance, certificate, and status.

---

## Target Architecture

The solver must be split into isolated epistemic kernels:

```text
SolverKernel
  - ExactSolver
  - ApproximateSolver
  - CertificateEmitter
  - FailureReporter

Shared infrastructure:
  CertificateKernel
  ToleranceKernel
  NumericalReproducibilityKernel
```

---

## Canonical Invariant

> **Solver computes. Functional measures. Evaluator judges. Engine executes. Search schedules.**
