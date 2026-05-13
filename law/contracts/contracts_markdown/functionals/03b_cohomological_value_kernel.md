# Cohomological Value Contract

## Classification
**Subsystem:** Cohomological Semantics
**Role:** Defines the typed canonical outputs of Functionals.

## Contract
Cohomological Values are strictly typed factual outputs produced *only* by Functionals.

### The Values
1. **$\Phi_1$**: Quotient norm distance to exact repair. Source: `Phi1Functional`.
2. **$\Gamma_2$**: Closure defect of the residual obstruction class. Source: `Gamma2Functional`.
3. **$\Xi$**: Representative distortion / tie-breaker diagnostic. Authority: Diagnostic only.
4. **RawDefectMass**: Raw defect mass before quotient repair.
5. **ExactRepairMass**: Mass for selected repair.
6. **ResidualRepresentative**: Minimal residual $h$.
7. **QuotientClassId**: Canonical ID of the obstruction class.
8. **DualWitness**: Dual variables in $\ker(d_0^*)$.
9. **DualityGap**: Primal/dual error.
10. **ClosureClass**: Rank-2 image.
11. **PrimitiveDecompositionStatus**: separable | primitive | unknown.
12. **AreaLockMass**: Geometric filling cost $m_G(\gamma)$.
13. **RepairExhaustionStatus**: State of combinatorial exhaustion.

**Must Not:**
* Reinterpret these values outside the Functional layer.
* Use $\Xi$ to trigger lifts or decide success.
* Use the term $\Gamma_1$ (use `RawDefectMass` instead).
