# Measurement Access Control Contract

## Classification
**Subsystem:** Cohomological Semantics
**Role:** Protects the epistemic boundaries of cohomological measurements.

## Core Invariant
> **No Search Access To Cohomological Semantics.**

## Contract
This contract enforces the absolute read/write separation between search and evaluation.

**Must:**
* Forbid Search from importing `PhiFunctional`, `GammaFunctional`, `XiFunctional`.
* Forbid Search from computing $\Phi$, $\Gamma_2$, $\Xi$.
* Allow Search to consume only opaque Evaluator priority hints.
* Forbid Solvers from emitting $\Phi$, $\Gamma_2$, $\Xi$ as canonical values.
* Ensure Evaluators consume Functional outputs and Policy to judge, but not recompute.
* Ensure Engine routes on typed Evaluator verdicts without computing values directly.
