# Adapter Contract

## Core Definition

> An Adapter is a pure, total, non‑executive transformation that maps one BaseType to another BaseType with a different SchemaRef, preserving declared invariants up to an explicitly stated correspondence, and performing no orchestration, search, policy, or side‑effectful computation.

An Adapter is the only lawful mechanism by which representation identity may change.

---

## Architectural Role

Adapters exist to cross representation boundaries safely and explicitly.
They are **not**:
- **Operators** (they do not compute new information),
- **Engines** (they do not reason or decide),
- **Pipelines** (they do not orchestrate),
- **Policies** (they do not authorize or forbid).

They are representation morphisms, nothing more.

---

## 1. Explicit Schema Boundary

Every Adapter MUST declare:
- a source schema `SchemaRef_src`,
- a target schema `SchemaRef_dst`.

If `SchemaRef_src == SchemaRef_dst`, an Adapter is forbidden. Identity is handled by the BaseType itself.

## 2. Totality and Determinism

An Adapter MUST be:
- **Total over admissible source domain** — defined for all valid instances of the source BaseType that meet the explicitly declared admissibility conditions.
- **Deterministic** — identical inputs produce identical outputs.
- **Pure** — no side effects, no IO, no external state.

An Adapter MUST NOT:
- branch on runtime policy,
- consult external registries,
- depend on time, randomness, or environment.

## 3. Invariant Transport Law

Adapters must explicitly declare how invariants are transported. This requires a dedicated `InvariantTransportKernel`.

```rust
struct InvariantTransport {
    source: InvariantId,
    target: Vec<InvariantId>,
    mode: TransportMode,
}

enum TransportMode {
    Preserved,
    Refined,
    Weakened,
    ForgottenExplicitly,
}
```

### 3.1 Invariant correspondence
For each invariant $I_{src}$ of the source schema, the Adapter MUST specify the `TransportMode`.
Dropping an invariant without explicit declaration is forbidden.

### 3.2 No invariant repair
An Adapter MUST NOT:
- silently repair invariant violations,
- coerce invalid source data into valid target data.

If an invariant cannot be transported, the Adapter MUST fail explicitly.

---

## 4. No New Information Law

An Adapter MUST NOT introduce new semantic information.
Formally, target information must be semantically closed under the source information:
`Info(Dst) ⊆ Closure(Info(Src))`
under declared transport semantics.

**Allowed:**
- renaming fields,
- regrouping structure,
- changing coordinate systems (ONLY IF invertibility, invariant transport, and quotient preservation are explicitly declared),
- forgetting fields (with explicit loss declaration).

**Forbidden:**
- inference,
- estimation,
- optimization,
- default filling of missing data.

## 5. Loss Declaration

If information is discarded, the Adapter MUST declare it explicitly, distinguishing between structural and semantic loss.

```rust
pub enum LossClass {
    Structural,
    Semantic,
    QuotientRelevant,
}

pub enum LossMode {
    None,
    Explicit {
        lost_fields: &'static [&'static str],
        loss_class: LossClass,
    },
}
```

Loss MUST be:
- explicit,
- auditable,
- invariant‑compatible.

Silent loss is forbidden. Every adaptation must produce an `AdaptationCertificate` detailing transported invariants, lost invariants, quotient impact, and support mapping.

## 6. Interaction with Φ and Quotient Geometry

Adapters MUST respect obstruction structure:
- Φ‑values may change only if the invariant mapping explicitly changes the quotient structure.
- Adapters MUST NOT mix supports or introduce Φ‑coupling between previously independent components.

Formally:
If $[a] \perp [b]$ in source,
then $\text{Adapter}(a) \perp \text{Adapter}(b)$ in target,
unless loss is explicitly declared.

This ensures quotient separability is preserved under adaptation. 
We strictly distinguish between **representation-preserving adapters** and **quotient-altering projection adapters**. They are different categories.

## 7. Canonical Rust Boundary

```rust
pub trait Adapter<Src: Adaptable, Dst: BaseType>: Sealed {
    fn source_schema() -> &'static SchemaRef;
    fn target_schema() -> &'static SchemaRef;

    fn adapt(src: &Src) -> Result<(Dst, AdaptationCertificate), AdaptationError>;
}
```

`AdaptationError` MUST be structural (never policy‑ or execution‑based).

## 8. Forbidden Behaviors (Hard Errors)

An Adapter MUST NOT:
- call operators, engines, or solvers,
- perform search or branching over alternatives,
- mutate global or external state,
- depend on policy, budgets, or runtime context,
- cache results that affect observable behavior.

If any of these occur, the component is not an Adapter.

## 9. Relationship to Pipeline and Engine

- **Pipelines** select which Adapter to use.
- **Engines** never adapt types implicitly.
- **Adapters** never choose when they are applied.

This enforces a strict separation:
- Pipeline decides *when*
- Adapter decides *how*
- Engine decides *what*

---

## Canonical Invariant

> Adapters change representation, not meaning.
> If meaning changes, it is a lift, not an adaptation.

## Admissibility Checklist

An Adapter is admissible iff:
- Source and target schemas differ
- Transformation is total over the admissible domain and deterministic
- No new information is introduced
- All invariant transport is explicit
- Any information loss is declared
- Φ‑separability is preserved
- No execution, search, or policy logic exists
- No external dependencies or side effects

If any condition fails, the Adapter is invalid.

---

## Required Test Infrastructure

### Purity Tests
* `adapter_has_no_io`
* `adapter_has_no_external_dependencies`
* `adapter_has_no_policy_branching`
* `adapter_has_no_runtime_context`

### Transport Tests
* `all_source_invariants_accounted_for`
* `undeclared_invariant_drop_rejected`
* `loss_mode_required_for_forgetting`

### Quotient Tests
* `adapter_preserves_support_separability`
* `adapter_does_not_merge_independent_components`
* `quotient_collapse_requires_explicit_loss`

### Replay Tests
* `same_input_same_output`
* `same_input_same_loss_certificate`
* `canonical_serialization_stable_under_adaptation`

### Composition Tests
* `adapter_chain_preserves_transport_laws`
* `loss_accumulates_explicitly`

---

## Target Architecture

The Adapter subsystem points toward a formal separation of:

```text
SchemaKernel
BaseTypeKernel
InvariantTransportKernel
AdapterKernel
SerializationKernel
ReplayKernel
```

Adapters themselves should remain tiny: map representation, transport invariants, declare loss, and stop. Nothing else.

### Theorem-Level Infrastructure

* `NoSemanticCreationInsideAdapter`
* `InvariantTransportCompleteness`
* `ExplicitLossLaw`
* `QuotientSeparabilityPreservation`
* `AdapterDeterminism`
* `NoImplicitAdaptationInsideEngine`
* `SchemaBoundaryIntegrity`
* `ReplayStableAdaptation`

## Summary

The Adapter Contract closes the last representation boundary in the UROS/MSS‑N system:
- **BaseTypes** are inert structure.
- **Adapters** are pure representation morphisms.
- **Engines** reason.
- **Pipelines** orchestrate.

With this contract, all semantic motion is explicit, typed, and auditable.
