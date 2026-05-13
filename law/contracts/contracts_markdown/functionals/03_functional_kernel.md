# Functional Contract

## Core Definition

> A Functional is a deterministic, stateless mathematical mapping from declared state types to typed scalar, vector, or certified measurement outputs. It may call declared Observables or Solver interfaces to compute those values, but it must not mutate state, orchestrate execution, search alternatives, or return acceptance/rejection decisions.

Functionals define exactly how "obstruction magnitude" or "closure defect" is measured by composing primitive Observable quantities into aggregate metrics.

---

## The Minimal Functional Basis

From the theoretical proofs of $\Delta$-minimality, obstruction equivalence, and honest refusal, the required functionals fall into **four irreducible classes**. No more are needed. Adding more is optional but not foundational.

### Class I — Primary Obstruction Magnitude ($\Phi_1$)
*   **Required:** $\Phi_1$ ($\ell^1$ quotient obstruction norm)
*   **Role:** Measures irreducible closure failure. Well-founded, lexicographically dominant, and observer-forced by additivity + non-cancellation.
*   **Definition:** $\Phi_1(\omega) := \min_s \| \omega - B s \|_1$
*   **Status:** Canonical within the declared MSS obstruction framework. Necessary and sufficient. Nothing else can replace $\Phi_1$ without breaking confluence or allowing fake repair, under locality and non-cancellation assumptions.

### Class II — Higher Obstruction / Closure Stress ($\Gamma_2$)
*   **Required:** $\Gamma_2$ (two-cell closure defect)
*   **Definition:** $\Gamma_2(h_1) := \| d_1 h_1 \|_1$
*   **Role:** Detects structural failure of repair. Prevents fake descent where $\Phi_1$ decreases by tearing topology.
*   **What $\Gamma_2 = 0$ means:** 
    *   **Case A ($\Gamma_2 = 0, \Phi_1 > 0$):** Marks a stable survivor. A lift may be licensed only if the current layer is required to resolve or represent that survivor, internal descent is exhausted, and admissible contexts witness a failure of congruence.
    *   **Case B ($\Gamma_2 = 0, \Phi_1 = 0$):** Terminal success state. Exact representative.
*   **Safeguard:** If $\Gamma_2$ is computed as non-finite or is underestimated (false zero), the engine might prematurely lift a fake obstruction. Therefore, $\Gamma_2$ must be computed exactly or conservatively via $d_1$ application, never by heuristic.

### Class III — Conditioning / Regime Gate ($R_{cl}$)
*   **Required:** $R_{cl}$ (closure regime measure)
*   **Definition:** $R_{cl} : (\Phi_1, \Gamma_2) \longrightarrow \text{ClosureRegime}$
*   **Role:** This classifies operating regimes to prevent silent regime errors. Measurement interpretation depends on regime topology:
    
    ```rust
    pub enum ClosureRegime {
        Normal,            // Φ₁ > ε
        TerminalZero,      // Φ₁ ≤ ε ∧ Γ₂ ≤ ε
        PathologicalTear,  // Φ₁ ≤ ε ∧ Γ₂ > ε
    }
    ```
    
    *   **Normal:** Normal operating regime.
    *   **TerminalZero:** The only legitimate zero state. Prevents lifting on noise.
    *   **PathologicalTear:** Pathological state. Structure is being torn with no first-order obstruction. A certified alarm condition forbidding fake repair.

### Class IV — Representative Distortion / Tie-Breaking ($\Xi$)
*   **Required:** $\Xi$ (representative distortion functional)
*   **Definition:** $\Xi := \|\rho(\omega) - \rho(h_1^*)\|_1$
*   **Role:** Measures *how* obstruction is represented. It is fundamentally invisible to MetaLift by design.
*   **Operational Power:** $\Xi$ is **intentionally powerless**. It cannot cause descent, cannot trigger MetaLift, and cannot certify closure. It is allowed to influence behavior ONLY as a local solver tie-breaker or diagnostic metric.

---

## Functional Authority Matrix

This matrix governs the exact execution architecture. Any code violating these constraints must be rejected.

| Functional | Can lower M (Descent) | Can trigger lift | Can block lift | Can decide success |
| :--- | :---: | :---: | :---: | :---: |
| **$\Phi_1$** | ✅ | ✅ | ✅ | ✅ |
| **$\Gamma_2$** | ❌ | ✅ | ✅ | ✅ |
| **$R_{cl}$** | ❌ | ❌ | ✅ | ✅ |
| **$\Xi$** | ❌ | ❌ | ❌ | ❌ |

---

## The Triad Functional (Derived)

The `Triad` functional is **not fundamental**. A product-based triad has a known degeneracy ($\Xi = 0 \Rightarrow \text{Triad} = 0$, even if $\Phi_1 > 0$ and $\Gamma_2 > 0$). This can falsely imply system health. 

Therefore, it should either be explicitly named $\text{Triad}_{\mathrm{display}}$ to denote its use only for dashboards, or safely replaced by a tuple display:
$$ (\Phi_1, \Gamma_2, R_{cl}, \Xi) $$
*   **What it IS:** A composite score useful *only* for dashboards, logging, regression testing, or comparative benchmarking.
*   **What it MUST NEVER be:** It must never be used for acceptance decisions, lift triggering, operator gating, or ordering primary descent. Descent must remain strictly lexicographic based on the tuple components.

---

## Explicitly Forbidden Functionals

The theory explicitly forbids several tempting scalar evaluations:

| Forbidden Functional | Why it is Forbidden |
| :--- | :--- |
| $\ell^2$ norms | Allows cancellation, breaks obstruction localization |
| Averaged scores | Hides localized failure |
| Learned scalar reward | Violates evaluator immutability |
| Entropy as primary | Conflates multiplicity with obstruction |
| Softmax / exp | Non-monotone under descent |
| Probabilistic confidence | Not structural |

Together, this minimal basis guarantees descent order: $\Phi_1$ supplies the primary descent order (termination further requires a discrete spectrum, finite search space, declared tolerance, or a well-founded descent certificate), $\Gamma_2$ prevents fake repair, $R_{cl}$ prevents silent failure, and $\Xi$ provides safe diagnostic stability.

---

## Operational Engine Architecture Flow

Functionals exist strictly inside an ordered operational pipeline. They bridge the mathematics to the evaluator:

$$ \text{Axiom of Distinction} \to \ell^1 \to Q^1,\Phi_1 \to \Gamma_2 \to R_{cl} \to \text{Evaluator} \to \text{MetaLift} \to \text{Stabilization} $$

$\Xi$ sits beside this as a diagnostic and tie-breaker:
$$ \Xi \dashv \text{authority} $$
Meaning it observes but does not govern.

---

## Architectural Mandate & Contractual Obligations

### 1. Trait Shape (MUST DO)

```rust
pub trait Functional<State> {
    type Output;

    fn compute(&self, state: &State) -> Result<Self::Output, FunctionalError>;
}
```

Typical output:

```rust
pub enum MeasurementValue {
    ExactRational(BigRational),
    Algebraic(AlgebraicNumber),
    Interval(IntervalBound),
    CertifiedApproximation {
        center: BigDecimal,
        radius: BigDecimal,
        certificate: CertificateId,
    },
}

pub struct MeasurementCertificate {
    pub solver_id: String,
    pub approximation_class: String,
    pub residuals: std::collections::HashMap<String, MeasurementValue>,
    pub proof_scope: String,
}

pub struct FunctionalValue {
    pub value: MeasurementValue,
    pub certificate: Option<MeasurementCertificate>,
}
```

Required properties:

```text
&self    → no mutation of functional (stateless)
&State   → read-only input
Output   → detached measurement value
```

---

### 2. Relationship to Observables (MUST DO)

```text
Observable:  extracts primitive measurable quantities from state
Functional:  composes observables into scalar/vector objective values
Evaluator:   converts functional values into verdicts
```

Example composition:

```text
metrics(state) = (Φ₁(state), Γ₂(state), R_cl(state), Ξ(state))
```

Or for dashboard output:
```text
display_score(state) = w₁·Φ₁(state) + w₂·Γ₂(state)
```
*(Note: Display scores are never used for acceptance logic.)*

The Functional computes the expression. It does not judge it.

Functionals MAY invoke Observables or Solver interfaces as part of pure computation. They MUST NOT orchestrate execution — that word belongs to Engine/Pipeline.

---

### 3. Zero Decision Making (MUST NOT DO)

Functionals return measured values. They do not return verdicts.

**Allowed outputs**:

```rust
phi = MeasurementValue::ExactRational(...)
gap = MeasurementValue::Interval(...)
bounded = true
residual = MeasurementValue::CertifiedApproximation(...)
```

**Forbidden outputs**:

```rust
accepted = true
should_descend = true
route = Pharmacy
is_good = true
verdict = Accept
```

Decision logic belongs strictly to Evaluators.

---

### 4. Stateless but Configurable (MUST DO)

Stateless does not mean parameter-free. Configuration is immutable; history is forbidden.

**Allowed**:

```rust
pub struct PhiFunctional {
    pub weights: PhiWeights,
    pub solver_params: SolverParams,
}
```

**Forbidden**:

```rust
previous_phi
last_candidate
cache_affecting_result
accumulated_history
```

---

### 5. Typed Divergence Handling (MUST DO)

Silent clamping is forbidden. If a metric can diverge, the functional must declare its range behavior explicitly.

```rust
pub enum RangeBehavior {
    /// Output guaranteed within [min, max]
    Bounded { min: MeasurementValue, max: MeasurementValue },
    /// Unbounded output is valid
    UnboundedAllowed,
    /// Divergence returns error
    DivergenceIsError,
    /// Clamping explicitly declared (not silent)
    DeclaredClamp { min: MeasurementValue, max: MeasurementValue },
}
```

No silent clamp. If clamping is used, it must be declared in the functional's contract.

---

### 6. Determinism (MUST DO)

Given identical input state and functional configuration, computation MUST produce identical output.

No hidden randomness, no sampling-based variation, no dependency on external mutable state.

---

## Typed Errors

```rust
pub enum FunctionalError {
    Undefined,
    Divergent,
    NonFiniteInput,
    SolverFailed(String),
    DomainViolation(String),
    ObservableFailed(String),
}
```

Errors are computational failures, not acceptance decisions.

---

## Admissibility Checklist

A Functional is admissible only if ALL are true:

1. Input is typed state reference (`&State`)
2. Output is typed measurement (scalar, vector, certificate), not verdict
3. Stateless — no history, no cache affecting result
4. Configuration is immutable
5. Range behavior is declared (bounded, unbounded, or divergence-is-error)
6. No silent clamping
7. No acceptance/rejection logic
8. No state mutation
9. No execution orchestration
10. Deterministic given same inputs

If any are missing, reject it.

---

## The Unifying Mechanism: Functionals, Contexts, and MetaLift

The functional basis does not exist in a vacuum; it acts as the numerical payload for the structural tests run by **Admissible Contexts**, which in turn trigger the **MetaLift** congruence restorations.

### 1. The Harmless Failure Modes of $\Xi$
$\Xi$ is deliberately weak. It measures representative distortion, not obstruction. It may exhibit:
*   **False confidence:** $\Xi$ is small even when $\Phi_1$ is large.
*   **False alarms:** $\Xi$ is large due to solver noise even when $\Phi_1 = \Gamma_2 = 0$.
*   **Non-monotonicity:** $\Xi$ oscillates during descent.
Because $\Xi$ is explicitly excluded from the Authority Matrix, all of these failure modes are harmless. $\Xi$ can be wrong without the system being wrong.

### 2. How MetaLift Ensures Confluence
MetaLift exists **only** to restore confluence, defined as obstruction equivalence ($\sim_N$) acting as a true congruence under all admissible contexts. 
MetaLift is triggered exactly when:
1.  **Exhaustion:** No operator lowers $\Phi_1$.
2.  **Context Witness:** An admissible context $C$ distinguishes a critical pair ($x \sim_N y$ but $C[x] \not\sim_N C[y]$).
3.  **Minimal Repair:** MetaLift splits the critical pair into distinct normal forms by introducing new symbols ($\Delta \in \{1,2\}$).
This refines $\sim_{N+1}$ into the smallest refinement making it a congruence.

### 3. The Role of Admissible Contexts
Admissible contexts are the test apparatus. They observe **failure shapes** (specifically the $\Phi_1$ and $\Gamma_2$ regimes), but explicitly **do not observe $\Xi$**. 
*   If any context distinguishes $x$ and $y$, confluence fails.
*   By being finite and monotone, contexts prevent spurious lifts and infinite inflation.
*   Only real, structural, context-witnessed non-congruence forces a lift.

---

## Required Test Infrastructure

### Functional Purity Tests
* `functional_cannot_mutate_state`
* `functional_cannot_emit_verdict`
* `functional_cannot_call_engine`
* `functional_has_no_history`

### Determinism Tests
* `same_state_same_measurement`
* `same_config_same_measurement`
* `same_solver_same_certificate`

### Regime Tests
* `gamma_zero_phi_positive_is_survivor`
* `gamma_zero_phi_zero_is_terminal`
* `pathological_regime_detected`

### Authority Tests
* `xi_cannot_trigger_lift`
* `triad_cannot_drive_evaluator`
* `functional_cannot_route_execution`

### Numerical Integrity Tests
* `false_zero_detection`
* `nonfinite_gamma_rejected`
* `declared_clamp_required`

---

## Target Architecture

The functional layer requires distinct kernels to eliminate floating-point ambiguity:

```text
ObservableKernel
FunctionalKernel
MeasurementKernel
CertificateKernel
EvaluatorKernel
MetaKernel
```

The `MeasurementKernel` must explicitly manage: `MeasurementValue`, `MeasurementSemantics`, `ToleranceSpec`, `RangeBehavior`, `MeasurementCertificate`, and `MeasurementRegime`.

Functionals themselves should remain small: compose observables, invoke declared solvers, emit typed measurements, and stop.

### Theorem-Level Infrastructure

* `NoGovernanceInsideFunctional`
* `MeasurementDeterminism`
* `ExactOrCertifiedApproximation`
* `XiAuthorityExclusion`
* `RegimeClassificationSoundness`
* `NoScalarizedOntology`
* `FunctionalReplayInvariant`
* `ContextBlindnessToXi`

---

## Canonical Invariant

> **Functional computes values. Evaluator judges values. Observable extracts quantities. Solver produces witnesses. Generator proposes. Search schedules. Policy constrains. Engine executes. Pipeline orchestrates.**
