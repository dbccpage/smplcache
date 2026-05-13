# Operator Encoder Contract (`Encoder`)

## Core Definition

> An Encoder is a **typed representation functor** mapping a structured domain to an admissible density representation under strict codomain invariants. Encoders shape the representation space but do not decide candidate acceptance.

### Classification
**Subsystem:** representation-boundary operator / admissible embedding morphism
**Not:** adapter, evaluator, physics engine, solver, engine.

Encoders map:
$$ \text{structured domain} \longrightarrow \text{admissible density representation} $$
under strict codomain invariants: PSD, trace-one, Hermitian, and finite.

---

## Tower Placement

Encoders sit at the **representation boundary** between the structural domain (graphs, text, embeddings) and the density-representation layer. They are closely related to Adapters but distinguished by their postcondition: every encoder output MUST be a valid density matrix.

```text
Raw domain state → Encoder → DensityMatrix (PSD, Tr=1)
                               ↓
                   metrics/ (entropy, purity, coherence)
                               ↓
                   telemetry/ (SBE diagnostic trace)
```

An Encoder is NOT an Adapter. An Adapter converts between already-declared representations. An Encoder crosses the representation boundary from an unstructured/heterogeneous domain into a strictly constrained physical representation.

---

## Actual Implementation

### Module location

```text
operators/encoders/
  mod.rs          — "Density matrix encoder operators" (module doc)
  density.rs      — DensityEncoder implementation (17KB)
```

### Module documentation (`operators/encoders/mod.rs`)

```rust
//! Density matrix encoder operators.
//!
//! Encoders map structured representations into density matrices. They shape
//! the representation space, but they do not decide candidate acceptance.
```

### Related adapters

The `TextDensityEncoder` is the canonical example of an encoder that maps raw text → Bigram Gram matrix → PSD trace-one density matrix. It was deliberately named "encoder" rather than "quantum" to enforce the epistemic firewall: **text does not become quantum by being encoded as a density matrix.** The density matrix is a diagnostic formalism, not a physical ontology.

---

## Contract Shape

```rust
pub trait Encoder<S> {
    fn encode(&self, input: &S) -> Result<DensityMatrix, EncoderError>;
}
```

### Postconditions (MUST DO)

Every `Ok(rho)` returned by an encoder MUST satisfy:

1. **PSD**: All eigenvalues of `rho` are ≥ 0 (within numerical tolerance). **Requirement:** Encoders should ideally provide a `PSDCertificate` or symbolic factorization witness to ensure hardware-independent replay.
2. **Trace-one**: `Tr(rho) = 1` (within `DensityMatrixConfig::trace_tolerance`)
3. **Hermitian**: `rho = rho†` (within `DensityMatrixConfig::hermiticity_tolerance`)
4. **Finite**: No NaN or Inf entries

### Preconditions

An encoder MUST reject inputs that cannot be mapped to a valid density matrix:
- Empty inputs (zero-length text, empty graph)
- Non-finite inputs (NaN embeddings)
- Degenerate inputs (all-zero vectors that would produce a zero matrix)

---

## Constraints & Exclusions

1. **Deterministic** — same input always produces identical output.
2. **Stateless** — no mutable state, no caches, no external bindings.
3. **No evaluation** — encoders do not score, rank, or accept/reject.
4. **Epistemic Firewall** — encoding data as a density matrix does not make it "quantum."
5. **No repair** — if the input cannot be encoded, return `EncoderError`, do not fix the input.
6. **No Hidden Weights** — Encoders must not smuggle ranking or feature selection via `EncodingCertificate`.

```rust
pub struct EncodingCertificate {
    pub normalization_rule: RuleId,
    pub basis_construction: BasisId,
    pub preprocessing_steps: Vec<StepId>,
}
```

---

## EEDA Integration

Encoders produce the `DensityDiagnostics` that feed into the SBE telemetry layer:

```text
input → Encoder → DensityMatrix → diagnostics() → DensityDiagnostics
                                                        ↓
                                                   SbeTrace::new(&diag)
                                                        ↓
                                                   EedaDiagnostics
```

The encoder itself is not evaluated by EEDA. Its output (the density matrix) is what the evaluator measures.

---

## Typed Errors

```rust
pub enum EncoderError {
    EmptyInput,
    NonFiniteInput { index: usize },
    DegenerateInput { reason: &'static str },
    DimensionMismatch { expected: usize, actual: usize },
    EncodingFailed { reason: String },
    PsdViolation,
}

pub struct RepresentationSemantics {
    pub basis_meaning: BasisMeaning,
    pub admissible_metrics: Vec<MetricId>,
}
```

---

## Epistemic Firewall

> [!IMPORTANT]
> Encoding a representation as a density matrix does not promote the representation to physical quantum mechanics. The density matrix formalism provides useful mathematical properties (PSD, trace-one, eigendecomposition, entropy) that serve as diagnostic tools. The encoder contract does not license claims about quantum entanglement, quantum coherence, or quantum computation over the encoded data.

---

---

## Tests to Add Immediately

* `encoder_psd_verified`
* `encoder_trace_one_verified`
* `encoder_hermitian_verified`
* `encoder_deterministic_replay`
* `encoder_rejects_empty_input`
* `encoder_has_no_evaluation_logic`
* `encoder_cannot_rank_candidates`
* `representation_semantics_declared`

---

## Best Future Architecture

The system is converging toward:
* **EncoderKernel**: transformation to density representations.
* **RepresentationKernel**: Management of codomain invariants.
* **RepresentationLawKernel**: Formal admissibility checking.
* **MetricExtractionKernel**: Interpreting entropy/purity/coherence relative to declared semantics.

---

## Theorem-Level Infrastructure Required

* **EncoderCodomainValidity**: PSD and Trace-one invariance.
* **RepresentationBoundaryIntegrity**: Strict separation of source/target domains.
* **NoPhysicsFromEncoding**: Formal enforcement of the epistemic firewall.

---

## Canonical Invariant

> **Encoder encodes. Lambda bridges. Reducer deflates. Transform rearranges. Generator proposes. Constructor validates. Engine orchestrates.**
