# MetaLift Contract

**Role:** licensed carrier extension.

A Lift defines exactly how the structural carrier expands to internalize an irreducible obstruction. It is not an arbitrary modification.

## What a Lift is NOT
A Lift is not:
* repair
* retry
* adapter conversion
* operator application
* search expansion
* policy override

## What a Lift IS
A Lift is:
> **a typed transition $C_N \to C_{N+1}$ that introduces the minimum licensed structure required to restore congruence or name a persistent obstruction.**

## Core Invariant
> **No lift without inhabited LiftLicense.**

## The Lift Artifact

Every lift requires a rigorous structural derivation:

```yaml
lift:
  source_carrier: "C_N"
  target_carrier: "C_{N+1}"
  delta: "schema_extension"
  obstruction_witness: "witness_id"
  descent_blocked_witness: "exhaustion_witness_id"
  persistence_witness: "persistence_witness_id"
  authority_license: "license_id"
  cost_derivation: "eta_cost_struct"
  minimality_argument: "congruence_restoration_proof"
```
