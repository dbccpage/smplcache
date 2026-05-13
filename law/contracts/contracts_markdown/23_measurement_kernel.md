# Measurement Kernel

**Role:** exact or certified numerical/symbolic value.

This kernel is urgent because `f64` ambiguity fundamentally undermines the structural precision required for irreducibility proofs. Raw floating-point arithmetic is explicitly forbidden from serving as a governance-critical carrier.

## Core Invariant

> **No governance-critical decision may depend on raw f64.**

## The Measurement Value

All scalar and continuous properties must be exact or explicitly bound by a certified envelope:

```rust
pub enum MeasurementValue {
    ExactRational(BigRational),
    Integer(i64),
    BooleanRegime(bool),
    Interval { lower: BigDecimal, upper: BigDecimal },
    CertifiedApproximation {
        center: BigDecimal,
        radius: BigDecimal,
        certificate: CertificateId,
    },
    SymbolicExpression(String),
}
```

## Mandates
1. **Never Silently Clamp:** Measurements must explicitly define range behavior.
2. **Never Collapse Types:** An exact rational remains exact; an interval remains an interval. Conversions down to `f64` are permitted **only** for visualization or logging (dashboard triads).
3. **Traceability:** Every `MeasurementValue` must trace back to the Observable that extracted it or the Solver that computed it.

## Measurement Laws (Theorem Candidates)
* `NoGovernanceOnRawFloat`
* `CertifiedApproximationTransparency`
* `MeasurementReplayInvariant`
