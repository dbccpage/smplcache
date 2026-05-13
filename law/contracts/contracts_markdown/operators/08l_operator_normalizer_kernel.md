# NormalizerOp Kernel

## Classification
**Subsystem:** State operator
**Role:** Canonical mapping (`S → Canonical(S)`).

## Contract
A `NormalizerOp` transforms a state into its defined normal form. It may be treated as a strict subtype of `ReducerOp`.

**Must:**
* Be strongly normalizing (always terminates at a unique normal form).
* Be idempotent.

**Must Not:**
* Hallucinate normal forms that lose semantic meaning unless governed by a projection license.
