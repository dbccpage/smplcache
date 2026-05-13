# Operator Lambda Contract (`LambdaOp`)

## Core Definition

> A `LambdaOp` is a **pure local endomorphism** over an admitted carrier. It is a deterministic, stateless, unary morphism that preserves declared structure and satisfies Φ non-increase over its admitted domain. It performs no search, candidate exploration, ranking, orchestration, hidden lift, or repair.

### Classification
**Subsystem:** operator kernel / deterministic transformation algebra
**Not:** engine, search, evaluator, generator, meta layer, pipeline.

The Lambda is the canonical **internal-descent** operator in the autopoietic loop:
*   Must not enlarge the carrier.
*   Must not silently reinterpret the carrier.
*   Must return typed failure rather than hide a lift.
*   Must not change carrier rank, universe, or admissible execution geometry.

---

## Actual Trait Shape

### Working implementation (`operators/lambdas/mod.rs`)

```rust
pub trait LambdaOp<S: BaseType, T: BaseType>: Send + Sync {
    fn name(&self) -> &'static str;
    fn apply(&self, input: &S) -> Result<T, LambdaFailure>;
    fn preserved_structure(&self) -> Vec<PreservedStructure>;
    fn law_declaration(&self) -> LawDeclarationId;
}

pub enum PreservedStructure {
    Topology,
    Cohomology,
    Typing,
    QuotientClass,
    GaugeClass,
}

pub struct DeclaredLaw {
    pub law_id: LawId,
    pub expected_behavior: ExpectedBehavior,
}
```

This is the **active** definition. All 53 lambda implementations use `NemotronValue` as both input and output (endomorphic). Law witnesses are self-reported booleans. These booleans are declarations only; the Evaluator/EEDA must verify the actual before/after diagnostics. The runtime law is: $\text{declared law} \neq \text{accepted law}$.

### Contract-layer definition (`contracts/traits/operator.rs`, DEPRECATED)

```rust
pub trait LambdaOp<S: BaseType, T: BaseType>: OperatorIdentity {
    fn apply(&self, input: &S) -> Result<T, LambdaFailure>;
    fn law(&self) -> &'static LambdaLaw;
}
```

This generic `S → T` shape with `OperatorIdentity` and `LambdaLaw` is the design target but is not used by any implementation.

### Migration target (`base_contracts`)

```rust
pub trait LambdaOp<S: BaseType, T: BaseType>: BoundContract {
    fn apply(&self, input: &S) -> Result<T, LambdaFailure>;
    fn law(&self) -> &'static LambdaLaw;
}
```

---

## Existing Implementations (53 files)

### Mathematical Analysis (12)
| File | Domain |
|---|---|
| `lambda_analysis_tier1.rs` | Unified Tier-1 analysis operations |
| `lambda_cauchy_hadamard.rs` | Radius of convergence |
| `lambda_euler_maclaurin.rs` | Euler-Maclaurin summation |
| `lambda_integral_comparison.rs` | Integral comparison tests |
| `lambda_ratio_test.rs` | D'Alembert ratio test |
| `lambda_root_test.rs` | Cauchy root test |
| `lambda_asymptotic_extraction.rs` | Asymptotic expansion extraction |
| `lambda_closed_form_recognition.rs` | Closed-form expression recognition |
| `lambda_complex_analysis.rs` | Complex analytic operations |
| `lambda_fourier_analysis.rs` | Fourier transform/analysis |
| `lambda_summability_transforms.rs` | Abel/Cesàro/Borel summability |
| `lambda_taylor_series.rs` | Taylor series construction |

### Geometry & Topology (8)
| File | Domain |
|---|---|
| `lambda_differential_geometry.rs` | Connections, curvature |
| `lambda_riemannian_geometry.rs` | Riemannian metric operations |
| `lambda_symplectic_geometry.rs` | Symplectic form operations |
| `lambda_point_set_topology.rs` | Topological space operations |
| `lambda_smooth_manifold.rs` | Smooth manifold constructions |
| `lambda_einstein_equation.rs` | Einstein field equation |
| `lambda_kerr_metric.rs` | Kerr metric computations |
| `lambda_schwarzschild.rs` | Schwarzschild metric |

### Linear Algebra & PDEs (8)
| File | Domain |
|---|---|
| `lambda_eigendecomposition.rs` | Eigenvalue decomposition |
| `lambda_jordan_form.rs` | Jordan normal form |
| `lambda_lu_factorization.rs` | LU factorization |
| `lambda_svd.rs` | Singular value decomposition |
| `lambda_green_function.rs` | Green's function construction |
| `lambda_heat_kernel.rs` | Heat kernel evolution |
| `lambda_laplacian.rs` | Laplacian operator |
| `lambda_wave_operator.rs` | Wave operator |

### Vector Calculus (5)
| File | Domain |
|---|---|
| `lambda_curl.rs` | Curl operator |
| `lambda_divergence.rs` | Divergence operator |
| `lambda_discrete_divergence.rs` | Discrete divergence on graphs |
| `lambda_discrete_gradient.rs` | Discrete gradient on graphs |
| `lambda_green_theorem.rs` / `lambda_stokes_theorem.rs` | Integral theorems |

### Category Theory & Algebra (3)
| File | Domain |
|---|---|
| `lambda_category_basics.rs` | Functors, natural transformations |
| `lambda_hilbert_sheaf.rs` | Sheaf cohomology on Hilbert spaces |
| `lambda_universal_kernel.rs` | Universal computation kernel |

### Applied / Structural (9)
| File | Domain |
|---|---|
| `lambda_reasoning_kernel.rs` | Core reasoning lambda (largest: 26KB) |
| `lambda_compute_exact_from_potential.rs` | Exact solution from potential function |
| `lambda_compute_residual.rs` | Residual computation |
| `lambda_basis_projection.rs` | Projection onto basis elements |
| `lambda_l2_projection.rs` | L2 orthogonal projection |
| `lambda_graph.rs` | Graph-specific operations |
| `lambda_measure_probability.rs` | Measure-theoretic probability |
| `lambda_numerical_error.rs` | Numerical error estimation |
| `lambda_numerical_methods.rs` | Numerical method implementations |

### Disabled (3)
| File | Status |
|---|---|
| `lambda_autopoietic.rs` | Commented out in mod.rs |
| `lambda_weyl_transform.rs` | Commented out in mod.rs |
| `lambda_optimization.rs` | Active but contains search-like behavior (review needed) |
| `lambda_special_functions.rs` | Active |
| `lambda_stochastic_processes.rs` | Active |
| `lambda_penrose_diagram.rs` | Active |

---

## Algebraic Contract

For every admitted input `s`:

**Exact Algebraic Law:**
$$ \Phi(\mathrm{apply}(s)) \le \Phi(s) $$

**Admissible Noise Gating:**
Where numerical precision is an issue, operators must provide a **Typed Admissible Numerical Tolerance Certificate** rather than using naked floating-point epsilon. Replay equivalence must be hardware-independent via **exact symbolic regime comparison**.

### Composability

Lambdas compose when the codomain of one matches the admitted domain of the next. Since the current implementation is endomorphic (`NemotronValue → NemotronValue`), all lambdas are naively composable. The `codensity.rs` and `transport.rs` modules in `operators/` provide the algebraic infrastructure for lawful composition.

```text
f: NemotronValue → NemotronValue
g: NemotronValue → NemotronValue
g ∘ f: NemotronValue → NemotronValue (lawful if both are structure-preserving)
```

**Forbidden:** candidate-space search, adaptive traversal, convergence-driven exploration, ranking, runtime orchestration.

**Allowed:** finite deterministic traversal over input structure, fixed-iteration algebraic computation. 

**Static Bound Rule:** Operator branching factor must be statically bounded, and the execution path must be independent of evaluation outcomes.

---

## EEDA Integration

Lambda steps produce `EedaDiagnostics` snapshots that the evaluator bridge checks:

```text
eeda_accepts(before, after, r_cl_max)
```

The Φ non-increase claim is enforced structurally: the EEDA evaluator rejects any step where `after.phi > before.phi`.

---

## UROS Witness Integration

When a lambda is applied within a traced execution, the `ExecutionWitness` records:
- Exactly one attempt per step
- State hash before and after application
- The lambda's operator identity in the `ReplayLedger` payload

If a lambda internally retries or branches, the witness will fail with `NonDeterministicReplay`.

---

## Typed Failure

```rust
pub enum LambdaFailure {
    OutsideAdmissibleDomain(AdmissibleDomainCertificate),
    InvariantViolation { invariant: InvariantId },
    UndefinedTransformation { reason: &'static str },
    NonFiniteInput,
}
```

---

---

## Tests to Add Immediately

### Operator Purity Tests
* `lambda_has_single_execution_path`
* `lambda_has_no_retry_logic`
* `lambda_has_no_hidden_cache`
* `lambda_has_no_candidate_generation`

### Replay Tests
* `same_input_same_output_hash`
* `same_input_same_trace`
* `no_branching_under_replay`

### Structural Preservation Tests
* `preserved_structure_declared`
* `preserved_structure_verified_by_evaluator`

### Admissibility Tests
* `outside_domain_returns_typed_failure`
* `invalid_carrier_rejected`

### Composition Tests
* `typed_composition_required`
* `illegal_codomain_composition_rejected`

---

## Best Future Architecture

The system is converging toward:
* **OperatorKernel**: Pure local transforms.
* **OperatorLawKernel**: Admissibility and monotonicity declarations.
* **CompositionKernel**: Codensity and transport.
* **ReplayKernel**: Determinism verification.

The operator itself remains a tiny deterministic bridge between states.

---

## Theorem-Level Infrastructure Required

* **OperatorDeterminism**: Proof of single-path execution.
* **NoHiddenSearchInsideOperator**: Verification of static branching bounds.
* **ReplayDeterminism**: Guaranteed state hash preservation.
* **TypedCompositionLaw**: Enforcement of domain/codomain matching.
* **NoImplicitCarrierExtension**: Formal prohibition of lift-like behavior.
* **EvaluatorSeparatesDeclaredFromAcceptedLaw**: Hierarchy of validation.
* **AdmissibilityPreservation**: Closure properties of operator domains.

---

## Canonical Invariant

> **Lambda bridges. Reducer deflates. Transform rearranges. Encoder encodes. Generator proposes. Constructor validates. Engine orchestrates.**
