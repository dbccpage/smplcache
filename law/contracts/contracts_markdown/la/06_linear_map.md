# LinearMap Contract

## Classification

Subsystem: Linear Algebra
Artifact Family: StructureArtifact
Operator Class: Kernel
Role: Functorial Arrow
Mathematical Object: LinearMap

## Formal Definition

A homomorphism of vector spaces $L: V 	o W$ preserving addition and scalar multiplication.

## Structural Requirements

- Domain $V$.
- Codomain $W$.

## Requires

- Proof of linearity over $\mathbb{K}$.

## Must Preserve

- The zero vector: $L(\mathbf{0}_V) = \mathbf{0}_W$.

## Emits

- FactEnvelope<LinearMapResult>

## Must Not

- Introduce affine translation ($L(\mathbf{x}) = A\mathbf{x} + \mathbf{b}$ is FORBIDDEN as a LinearMap).

## Validation Requirements

- `verify_homomorphism`

## Witness Requirements

- `LinearityWitness`

## Replay Requirements

- map signature hash

## Canonical Laws

- $L(lpha \mathbf{u} + eta \mathbf{v}) = lpha L(\mathbf{u}) + eta L(\mathbf{v})$

## Semantic Boundaries

- A Linear Map is NOT an Affine Map. It must strictly preserve the origin.
