# BaseType Contract

## Core Definition

> A BaseType is a non-executable, schema-bound structural data object with explicit fields and declared invariants, supporting only validation and trivial projection, and containing no embedded execution logic, orchestration, search, policy behavior, or external dependencies.

---

## Architectural Mandate & Contractual Obligations

### 1. Formally Enforced Non-Executable Restriction
A BaseType MUST NOT expose any method that:
- Mutates external state
- Performs IO
- Invokes operators, engines, or solvers
- Performs iteration over alternative states

Allowed methods must be strictly restricted to:
- Field accessors
- Pure projections (O(1) or bounded structural computation)
- Validation hooks

### 2. Separation of Invariants vs Validation Logic
BaseType declares invariants; ValidationUnits enforce them.
A BaseType MUST NOT validate itself. It MUST NOT silently "fix" or repair invalid data.

```rust
pub trait ValidationUnit<T: BaseType> {
    fn validate(&self, value: &T) -> Result<ValidationWitness, ValidationViolation>;
}
```

### 3. Strict Mutability Semantics
Mutability is tightly restricted and structurally typed.
```rust
pub enum MutabilityMode {
    Immutable,
    Controlled {
        allowed_fields: &'static [&'static str],
    },
}
```
- Mutation must not violate declared invariants.
- Mutation must not trigger external side effects.

### 4. Boundary Law (Representation Identity)
Two BaseTypes are identical if and only if:
- They share the same `SchemaRef`
- They share the same field structure
- They share the same invariant set

Any change in `SchemaRef` implies a fundamentally different type, thus requiring an `Adapter` to map between them.

### 5. No Lazy Computation
A BaseType MUST NOT contain lazy-evaluated fields that trigger computation when accessed. All heavy derivation belongs in operators or solvers, not type accessors.

### 6. No Internal Caching
A BaseType MUST NOT maintain internal caches that affect observable outputs. Same input must guarantee identical observable behavior to preserve determinism.

### 7. Explicit Fields
All fields within a BaseType MUST be:
- Declared
- Typed
- Serializable

Forbidden patterns include:
- Hidden metadata
- Implicit derived fields
- Runtime-only shadow fields

### 8. Serialization Law
A BaseType MUST support canonical serialization. Serialization must preserve all invariants and type identity, which is critical for tracing, persistence, and mathematical reproducibility.

### 9. Strict Role Classification
A BaseType MUST declare exactly ONE primary role (e.g. State, Artifact, Value). Secondary roles must be explicit extensions to prevent semantic ambiguity.

### 10. No Dependency Handles
A BaseType MUST NOT contain:
- `Arc<...>` to engine services
- Database handles
- Runtime schedulers
- Policy engines
- Registries

Even if the handle is "unused," it violates the strictly pure data boundary of the type.

---

## Canonical Rust Boundary

This interface cleanly separates schema identity and invariant declarations from enforcement. BaseType must not enforce its own invariants.

```rust
pub trait BaseType: Sealed {
    fn schema() -> &'static SchemaRef;
    fn declared_invariants() -> &'static [InvariantRef];
}
```

---

## Required Subsystems

The BaseType cannot carry the burden of schema identity alone. It requires a dedicated `SchemaKernel`:

```text
SchemaKernel
```

with explicit structures for:
* `SchemaRef`
* `SchemaVersion`
* `InvariantRef`
* `CanonicalSerializationId`
* `RoleId`

---

## Canonical Invariant

> BaseType is data, not behavior. Engines execute. Pipelines orchestrate. Operators compute. Policies constrain. BaseTypes only represent.

## Admissibility Checklist

A type qualifies as a BaseType iff all are true:
- No executable logic beyond accessors
- Invariants declared, not enforced internally
- Mutability is explicit and bounded
- Schema identity is explicit
- No lazy computation
- No internal caches
- All fields are explicit and serializable
- Canonical serialization exists
- Exactly one primary semantic role
- No dependency handles

If any condition fails, the type is not a BaseType.

---

## Required Test Infrastructure

### Purity Tests
* `basetype_has_no_io`
* `basetype_has_no_operator_handles`
* `basetype_has_no_lazy_fields`
* `basetype_has_no_internal_cache`
* `schema_change_requires_adapter`
* `canonical_serialization_roundtrip_preserves_identity`
* `validation_unit_enforces_invariants_not_basetype`
* `basetype_declares_exactly_one_primary_role`

### Integration Tests
* Pipeline handoff rejects two structurally similar BaseTypes with different `SchemaRef`.
* Adapter required when schema versions differ.
* Trace hash remains stable under canonical serialization.
* Engine cannot store runtime services inside state BaseTypes.
* Analysis can read BaseType but cannot mutate or repair it.

---

## Target Architecture

The type layer relies on a clear separation of kernels:

```text
BaseType
  inert data only

SchemaKernel
  schema identity and versioning

InvariantKernel
  invariant declarations

ValidationKernel
  invariant enforcement

AdapterKernel
  schema migration and representation conversion

SerializationKernel
  canonical bytes and hashing
```
