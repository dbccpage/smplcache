# QuadraticForm Contract

## Classification
Subsystem: La
Artifact Family: StructureArtifact
Role: Mathematical Object
Mathematical Object: QuadraticForm

## Contract
Q(av)=a^2 Q(v); polar form recovers bilinear form (char ≠ 2).

It forms a foundational structural layer upon which operator kernels
can safely define morphisms and obstructions.

## Requires
* Declared carrier
* Declared coefficient domain, if applicable
* Declared dimension/degree, if applicable
* Declared incidence/face relation, if applicable

## Must Preserve
* Identity of carrier
* Declared adjacency/incidence/face structure
* Declared coefficient semantics

## Must Not
* Smuggle topology not declared in the carrier
* Treat numeric labels as arithmetic unless licensed
* Treat cycles as faces without declared 2-cells
* Collapse quotient structure without certificate

## Validation Requirements
* verify_quadraticform_invariants
* verify_carrier_bounds

## Witness Requirements
* QuadraticFormAdmissibilityWitness
* CarrierConsistencyWitness

## Replay Requirements
* schema hash
* carrier hash
* incidence/degree hash, if applicable
