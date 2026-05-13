# Functional Authority Contract

## Classification
**Subsystem:** Cohomological Semantics
**Role:** Canonical measurement provider.

## Core Rule
**Only Functionals may canonically compute $\Phi_1$, $\Gamma_2$, $\Xi$, quotient mass, closure defect, or cohomological status.**

## Strict Access Law

1. **Search**:
   * Forbidden from importing `PhiFunctional`, `GammaFunctional`, `XiFunctional`.
   * Forbidden from computing $\Phi$, $\Gamma_2$, $\Xi$.
   * May consume only opaque Evaluator priority hints.
2. **Solver**:
   * Forbidden from emitting $\Phi$, $\Gamma_2$, $\Xi$ as canonical values.
   * May emit solver artifacts used by Functionals (e.g., residual vector, dual solution, unsat core, KKT residual).
3. **Functional**:
   * **Sole canonical source of truth for cohomological measurements.**
4. **Evaluator**:
   * Consumes Functional outputs and Policy.
   * May judge but not recompute.
5. **Engine**:
   * Routes on typed Evaluator verdicts.
   * May not compute values directly.

## Contract-Level Invariant
> **No Canonical Cohomological Value Without Functional Emission.**
> **No Search Access To Cohomological Semantics.**
