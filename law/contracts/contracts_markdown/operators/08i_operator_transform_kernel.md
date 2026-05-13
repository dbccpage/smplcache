# Operator Transform Contract (`TransformOp`)

> [!WARNING]
> **STATUS: DISABLED.** The `operators/transforms/` module is currently **commented out** in `operators/mod.rs` (line 15: `// pub mod transforms;`). The 19 transform implementations exist on disk but do not compile into the binary. This document describes the architectural intent for when the module is resurrected or its contents are migrated.

## Core Definition

> A `TransformOp` is a deterministic, stateless, unary endomorphism `S → S` that rearranges or modifies admitted state within the same carrier, without Φ guarantees, search, ranking, orchestration, repair, representation conversion, or hidden lift.

---

## Tower Placement

A `TransformOp` is the tower kind for **same-carrier rearrangement**. It may change arrangement, parameterization, or internal structure, but it stays inside the declared carrier.

A Transform may not resolve an inadmissible request by reinterpreting the carrier. Converting a discrete support into a continuous interval, or otherwise switching geometry so the operation type-checks, is hidden lift/coercion and is forbidden. That boundary belongs to explicit Meta lift.

---

## Actual Trait Shape (as implemented)

There are **two competing trait definitions** in the codebase:

### 1. Working implementation (`operators/transforms/mod.rs`)

```rust
pub trait TransformOp: Send + Sync {
    fn name(&self) -> &'static str;
    fn apply(&self, input: &NemotronValue) -> Result<NemotronValue, TransformFailure>;
    fn is_invertible(&self) -> bool { true }
    fn preserves_dimension(&self) -> bool { true }
}
```

This is the **active** definition (when the module is enabled). It operates on `NemotronValue` and has no generic type parameters. Law witnesses are self-reported booleans.

### 2. Contract-layer definition (`contracts/traits/operator.rs`)

```rust
pub trait TransformOp<S: BaseType>: OperatorIdentity {
    fn apply(&self, input: &S) -> Result<S, TransformFailure>;
    fn law(&self) -> &'static TransformLaw;
}
```

This is the **design target**. It is generic over `BaseType`, requires `OperatorIdentity`, and demands a `TransformLaw` descriptor. **Neither `OperatorIdentity` nor `TransformLaw` are used by any transform implementation.**

### Migration target

The `contracts/traits/operator.rs` definition uses deprecated `OperatorIdentity`. The canonical migration path is:

```rust
pub trait TransformOp<S: BaseType>: BoundContract {
    fn apply(&self, input: &S) -> Result<S, TransformFailure>;
    fn law(&self) -> &'static TransformLaw;
}
```

using `BoundContract` from `contracts::base_contracts::binding`.

---

## Existing Implementations (19 files, all disabled)

| File | Category | Description |
|---|---|---|
| `transform_caesar.rs` | Cipher | Caesar shift on sequence values |
| `transform_caesar_reverse.rs` | Cipher | Inverse Caesar shift |
| `transform_atbash.rs` | Cipher | Atbash substitution |
| `transform_xor_mask.rs` | Bitwise | XOR mask application |
| `transform_not.rs` | Bitwise | Bitwise NOT |
| `transform_rotate_left.rs` | Bitwise | Left rotation |
| `transform_rotate_right.rs` | Bitwise | Right rotation |
| `transform_shift_left.rs` | Bitwise | Left shift |
| `transform_shift_right.rs` | Bitwise | Right shift |
| `transform_reverse.rs` | Sequence | Reverse element order |
| `transform_affine.rs` | Arithmetic | Affine transformation |
| `transform_base2_to10.rs` | Encoding | Base-2 to base-10 conversion |
| `transform_roman_to_int.rs` | Encoding | Roman numeral to integer |
| `transform_complex_numbers.rs` | Arithmetic | Complex number operations |
| `transform_op_remap_plus_is_mult.rs` | OP remap | Reinterpret `+` as `×` |
| `transform_op_remap_mult_is_pow.rs` | OP remap | Reinterpret `×` as `^` |
| `transform_series_shift.rs` | Series | Index shift on power series |
| `transform_helpers.rs` | Utility | Shared helper functions |

---

## Same-Carrier Invariant

A Transform MUST preserve:
- carrier type
- carrier rank
- admissible domain
- representation universe
- Transformations must declare a `TransformLaw` preventing hidden adapters/lifts (e.g., $\kappa$-preserving, $\kappa$-bounded, or same-carrier).

A Transform MAY change:
- arrangement, labels, parameters
- internal edge/weight/data layout

A Transform MUST NOT change cardinality or introduce/remove structure unless explicitly declared in its `TransformLaw`.

---

## Φ Behavior

Transform makes **no default Φ monotonicity claim**. Any Φ claim must be separately declared and witnessed.

```rust
pub enum PhiClaim {
    Unrestricted,         // default
    Preserved,            // Φ(T(s)) = Φ(s)
    NonIncreasing,        // Φ(T(s)) ≤ Φ(s)
    Bounded { factor: f64 }, // Φ(T(s)) ≤ factor · Φ(s)
}
```

---

## EEDA Integration

When transforms are re-enabled, every transform application within the UROS pipeline must produce an `EedaDiagnostics` snapshot. The evaluator bridge checks:

```text
eeda_accepts(before, after, r_cl_max)
```

Since transforms make no Φ claim by default, the EEDA evaluator will accept a transform step only if the lexicographic tuple `(Φ, Γ, Ξ)` does not increase — which for a pure rearrangement is expected to hold trivially.

---

## Strict Differentiators

| Kind | Domain | Guarantee | Φ |
|---|---|---|---|
| **Transform** | `S → S` | Prevents hidden lifts (declared `TransformLaw`) | Unrestricted (unless declared) |
| **Reducer** | `S → S` | Idempotent + κ-monotone: `κ(R(s)) ≤ κ(s)` | Reducing |
| **Lambda** | `S → T` | May change dimensionality | Non-increasing (declared) |
| **Encoder** | `S → DensityMatrix` | PSD, trace-one postcondition | Representation boundary |
| **Generator** | `S → Vec<Candidate<S>>` | Blind proposal, zero evaluation | N/A |
| **Constructor** | `Raw → BaseType` | Validation-gated instantiation | N/A |

---

## Canonical Invariant

> **Transform rearranges. Reducer deflates. Lambda bridges. Encoder encodes. Generator proposes. Constructor validates. Engine orchestrates.**

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
