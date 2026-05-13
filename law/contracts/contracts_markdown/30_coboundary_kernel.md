# CoboundaryOp Kernel

## Classification

Subsystem: Licensed algebraic operator
Operator Class: Kernel
Artifact Family: Action
Signature: Cochain_k → Cochain_{k+1}
Role: Cohomological coboundary mapping

## Contract

A CoboundaryOp executes the cohomological coboundary operator δ
on declared cochain complexes.

It is algebraically licensed and must preserve declared cochain
admissibility and dual pairing semantics.

## Requires

* Cochain complex license
* Degree declaration
* Dual pairing declaration
* Declared cochain carrier
* Coefficient domain declaration
* δ ∘ δ = 0 law

## Emits

* FactEnvelope<CoboundaryResult>
* Optional Certificate<CoboundaryClosure>

## Must Not

* Be confused with a generic OP
* Be used as a generic Transform
* Execute without cohomological license
* Alter dual pairing semantics
* Inject semantic content
* Collapse quotient/cokernel structure without certificate
* Treat cochains as chains without declared dualization license

## Replay Requirements

* Input cochain hash
* Output cochain hash
* Degree trace
* Dual pairing trace
* Coboundary witness trace

## Validation Requirements

* Verify δ² = 0
* Verify degree raising
* Verify cochain admissibility
* Verify dual pairing declaration
* Verify coefficient domain legality

## Witness Requirements

* CoboundaryClosureWitness
* DegreeRaiseWitness
* CochainAdmissibilityWitness
* DualPairingWitness
