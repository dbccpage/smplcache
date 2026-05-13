# BoundaryOp Kernel

## Classification

Subsystem: Licensed algebraic operator
Operator Class: Kernel
Artifact Family: Action
Signature: Chain_k → Chain_{k-1}
Role: Homological boundary mapping

## Contract

A BoundaryOp executes the homological boundary operator
on declared chain complexes.

The operator is algebraically licensed and must preserve
declared chain admissibility.

## Requires

* Chain complex license
* Degree declaration
* Declared carrier chain space
* Boundary admissibility witness
* ∂ ∘ ∂ = 0 law

## Emits

* FactEnvelope<BoundaryResult>
* Optional Certificate<BoundaryClosure>

## Must Not

* Be used as a generic transform
* Inject semantic content
* Alter carrier topology outside declared chain structure
* Execute without homological license
* Create or destroy obstruction classes implicitly
* Collapse quotient structure without certificate

## Replay Requirements

* Chain degree trace
* Boundary witness trace
* Input chain hash
* Output chain hash

## Validation Requirements

* Verify ∂² = 0
* Verify degree lowering
* Verify admissible chain carrier
* Verify coefficient domain legality

## Witness Requirements

* BoundaryClosureWitness
* DegreeReductionWitness
* ChainAdmissibilityWitness
