# Bicomplex Contract

## Classification
Subsystem: Homology
Artifact Family: StructureArtifact
Role: Mathematical Object
Mathematical Object: Bicomplex

## Contract
two differentials anti-commute: d_h d_v + d_v d_h = 0.

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
* verify_bicomplex_invariants
* verify_carrier_bounds

## Witness Requirements
* BicomplexAdmissibilityWitness
* CarrierConsistencyWitness

## Replay Requirements
* schema hash
* carrier hash
* incidence/degree hash, if applicable
