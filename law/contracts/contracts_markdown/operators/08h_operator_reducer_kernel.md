# Operator Reducer Contract (`ReducerOp`)

## Core Definition

> A `ReducerOp` is a pure, deterministic, unary, idempotent endomorphism `S → S` that removes declared structural redundancy or normalizes admitted state without increasing the declared reduction measure.

---

## Tower Placement

A `ReducerOp` is same-carrier canonicalization inside internal repair. It may remove redundancy, project to canonical form, or collapse noise, but it may not add basis elements, vertices, dimensions, or carrier structure.

If obstruction survives every admissible reducer, exhaustion has been reached on the current carrier. The next lawful move is explicit Meta lift, not a stronger reducer with hidden carrier growth.

---

## Actual Trait Shape

### Working implementation (`operators/reducers/mod.rs`)

```rust
pub trait ReducerOp: Send + Sync {
    fn name(&self) -> &'static str;
    fn apply(&self, input: &NemotronValue) -> Result<NemotronValue, ReducerFailure>;
    fn is_idempotent(&self) -> bool { true }
    fn reduces_support(&self) -> bool { true }
}
```

Operates on `NemotronValue`. Law witnesses are self-reported. No `ReducerLaw` descriptor is attached.

### Contract-layer definition (`contracts/traits/operator.rs`, DEPRECATED)

```rust
pub trait ReducerOp<S: BaseType>: OperatorIdentity {
    fn apply(&self, input: &S) -> Result<S, ReducerFailure>;
    fn law(&self) -> &'static ReducerLaw;
}
```

Uses deprecated `OperatorIdentity` and `ReducerLaw` from `contracts::traits::operator_identity`.

### Migration target (`base_contracts`)

```rust
pub trait ReducerOp<S: BaseType>: BoundContract {
    fn apply(&self, input: &S) -> Result<S, ReducerFailure>;
    fn law(&self) -> &'static ReducerLaw;
}
```

---

## Existing Implementations

| File | Status | κ Measure | Description |
|---|---|---|---|
| `reducer.rs` | Active | — | Base reducer infrastructure |
| `reducer_clip_dual.rs` | Active | Magnitude exceeding bounds | Clips edge weights to declared bounds |
| `reducer_h1.rs` | Active | H1 cohomology rank | Projects to H1-reduced representative |
| `reducer_partial_trace.rs` | Active | Subsystem dimension | Traces out declared subsystem |
| `reducer_support_predicates.rs` | Active | Support cardinality | Removes edges failing declared predicates |
| `reducer_l1_projector.rs` | Disabled | L1 norm | Projects to L1-minimal representative |
| `reducer_observables.rs` | Disabled | Observable count | Reduces to essential observable set |
| `reducer_series_truncate.rs` | Disabled | Series length | Truncates power series to finite order |
| `reducer_synthesis_generate.rs` | Disabled | Synthesis complexity | Generates reduced synthesis candidates |
| `reducer_threshold.rs` | Disabled | Values below threshold | Zero-out small values |
| `reducer_tqc_optimization.rs` | Disabled | TQC circuit depth | Optimizes topological quantum circuits |

The `admitted_reducers()` registry function currently returns an **empty Vec** — no reducers are registered despite implementations existing. Any theorem relying on reducers must be conditional on explicit registry admission.

---

## Algebraic Contract

A Reducer MUST declare a reduction measure `κ : S → R≥0`.

Required law: `κ(R(s)) ≤ κ(s)`.

If `R(s) = s`, then `s` is reduced with respect to this Reducer's declared `κ`.

### Constraints

1. **Unary & Endomorphic** — `S → S` only
2. **Pure & Deterministic** — no random side-effects
3. **Idempotent** — `apply(apply(x)) == Ok(apply(x))`
4. **Structure-Reducing** — κ-monotone
5. **Single Application** — repeated application until stability is Engine orchestration, not reducer semantics

> **Engine Isolation Limit:** If a ReducerOp performs search, iterative convergence, ranking, or external IO, it is an Engine, not a reducer.

---

## EEDA Integration

Reducer steps within the UROS pipeline produce `EedaDiagnostics` snapshots:

```rust
use crate::eeda::evaluator_bridge::eeda_accepts;
use crate::eeda::diagnostics::EedaDiagnostics;

// Before and after reducer application
let before = eval_state(&state);
let after = eval_state(&reduced_state);

// Reducers are expected to satisfy structural acceptance
assert!(eeda_accepts(&before, &after, R_CL_MAX));
```

The EEDA evaluator enforces:
- `after.r_cl ≤ R_CL_MAX` (repair complexity budget)
- `after.sw ≤ before.sw` (no hidden branching)
- `after.na ≤ before.na` (no holonomy violations)
- $(\Phi, \Gamma_2, \kappa/R_{cl})$ structural acceptance, with $\Xi$ tie-break only.

---

## UROS Witness Integration

When a reducer is applied within a traced execution, the `ExecutionWitness` must record:
- Exactly one attempt per step (`ctx.attempts() == 1`)
- State hash before and after
- Payload binding in the `ReplayLedger`

If a reducer introduces hidden branching (attempting multiple reduction strategies internally), the witness will fail at `NonDeterministicReplay`.

---

## Typed Failure

```rust
pub enum ReducerFailure {
    OutsideAdmissibleDomain,
    InvariantViolation { invariant: InvariantId },
    ReductionUndefined { reason: &'static str },
    NonFiniteInput,
}
```

---

## Canonical Invariant

> **Reducer deflates. Transform rearranges. Lambda bridges. Encoder encodes. Generator proposes. Constructor validates. Engine orchestrates.**

$$ 
\boxed{
\text{Operators may transform or propose; only Evaluators accept; only Engines execute; only MetaLift changes carrier expressivity.}
}
$$

$$ 
\boxed{
\text{No operator is allowed to become an Engine in disguise.}
}
$$
