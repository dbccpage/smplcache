# Basis Contract

## Classification

Subsystem: Linear Algebra
Artifact Family: StructureArtifact
Operator Class: None
Role: Coordinate Frame
Mathematical Object: Basis

## Formal Definition

A basis $\mathcal{B} = \{\mathbf{e}_1, \dots, \mathbf{e}_n\}$ for $V$ is a linearly independent set of vectors that spans $V$. Every $\mathbf{v} \in V$ can be uniquely written as $\sum c_i \mathbf{e}_i$.

## Structural Requirements

- A set of vectors from $V$.
- Rank $N = \dim(V)$.

## Requires

- Proof of linear independence.
- Proof of spanning.

## Must Preserve

- Dimensionality of the space.

## Emits

- None.

## Must Not

- Be conflated with another basis. Vectors in Basis $A$ cannot be added directly to vectors in Basis $B$.

## Validation Requirements

- `verify_linear_independence`
- `verify_spanning_set`

## Witness Requirements

- `BasisLinearIndependenceWitness`

## Replay Requirements

- basis tensor hash

## Canonical Laws

- $\sum \lambda_i \mathbf{e}_i = \mathbf{0} \implies orall i, \lambda_i = 0$

## Semantic Boundaries

- A Basis is NOT inherently orthonormal. Do not assume $\langle \mathbf{e}_i, \mathbf{e}_j angle = \delta_{ij}$ without an `OrthonormalBasis` witness.
