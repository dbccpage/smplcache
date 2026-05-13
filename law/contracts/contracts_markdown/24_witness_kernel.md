# Witness Kernel

**Role:** replayable evidence object for a structural claim.

A Witness is not a proof by itself unless paired with a verifier. It provides the exact contextual boundary required to verify that a structural failure exists or that a condition holds.

## Core Invariant

> **Witness supplies inspectable evidence; it does not decide admissibility.**

## Witness Artifact Definition
A Witness records:
* `subject`: The entity or state being evaluated.
* `claim_type`: The typed assertion being made.
* `construction`: How the evidence was built or isolated.
* `source_artifacts`: References to trace elements or state snapshots.
* `verification_method`: The required verifier to test the witness.
* `canonical_form`: An $\alpha$-equivalent, structurally hashable representation.

## Strict Prohibition
A Witness must **not**:
* Trigger a lift directly.
* Authorize a claim.
* Mutate state.
* Act as a policy.

## Typed Witnesses
```rust
pub enum WitnessType {
    ContextWitness,
    CountermodelWitness,
    DualCertificateWitness,
    ReplayWitness,
    LiftWitness,
    DescentBlockedWitness,
    NonCongruenceWitness,
}
```
