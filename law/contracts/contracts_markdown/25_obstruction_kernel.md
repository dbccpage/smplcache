# Obstruction Kernel

**Role:** typed structural failure that cannot be ignored.

An Obstruction formally classifies a non-congruent state that blocks descent. It is a mathematical geometry, not a transient runtime status.

## What an Obstruction is NOT
An Obstruction is not:
* an error
* an exception
* a bad score
* low confidence
* solver failure
* LLM uncertainty

## What an Obstruction IS
An Obstruction is:
> **a typed, persistent structural incompatibility relative to a declared carrier, equivalence, context family, and measurement regime.**

## Core Invariant
> **Operational failure is not mathematical obstruction.**

## The Typed Obstruction Artifact

```yaml
obstruction:
  id: "obs_uuid"
  carrier_ref: "carrier_n"
  equivalence_ref: "equiv_class_id"
  support: "topological_boundary"
  obstruction_kind: "BoundaryFailure"
  measurement_refs: ["phi_1", "gamma_2"]
  witness_refs: ["context_witness_id"]
  persistence_status: "Persistent"
  reducibility_status: "Irreducible"
```

## Obstruction Kinds
```rust
pub enum ObstructionKind {
    BoundaryFailure,
    ClosureDefect,
    NonCongruentQuotient,
    DescentBlocked,
    AuthorityConflict,
    EvidenceMissing,
    RepresentationFailure,
}
```
