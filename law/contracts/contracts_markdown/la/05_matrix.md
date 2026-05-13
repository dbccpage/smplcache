# Matrix Contract

## Classification

Subsystem: Linear Algebra
Artifact Family: StructureArtifact
Operator Class: Kernel
Role: Linear Map Representation
Mathematical Object: Matrix

## Formal Definition

A matrix $M \in \mathbb{K}^{m 	imes n}$ is the coordinate representation of a linear map $L: V 	o W$ relative to chosen bases $\mathcal{B}_V$ and $\mathcal{B}_W$.

## Structural Requirements

- Explicit domain basis $\mathcal{B}_V$.
- Explicit codomain basis $\mathcal{B}_W$.
- 2D scalar array.

## Requires

- Dimensional alignment $m = \dim(W), n = \dim(V)$.

## Must Preserve

- Linearity mapping.

## Emits

- FactEnvelope<MatrixComputation>

## Must Not

- Multiply against a vector whose basis does not exactly match $\mathcal{B}_V$.

## Validation Requirements

- `verify_inner_dimension_match`
- `verify_domain_basis_match`

## Witness Requirements

- `MatrixDimensionalityWitness`

## Replay Requirements

- input/output basis hashes

## Canonical Laws

- $M(\mathbf{u} + \mathbf{v}) = M\mathbf{u} + M\mathbf{v}$

## Semantic Boundaries

- A Matrix is NOT merely a 2D grid of numbers. It is a strictly bound linear map representation between two explicit spaces.
