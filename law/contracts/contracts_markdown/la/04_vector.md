# Vector Contract

## Classification

Subsystem: Linear Algebra
Artifact Family: StructureArtifact
Operator Class: None
Role: Carrier Element
Mathematical Object: Vector

## Formal Definition

An element $\mathbf{v} \in V$. Operationally, it is an ordered tuple of coordinates $(c_1, \dots, c_n)$ strictly bound to a declared Basis $\mathcal{B}$.

## Structural Requirements

- Coordinate array.
- Explicit `BasisDescriptor` binding.

## Requires

- Operations between vectors MUST verify identical `BasisDescriptor`.

## Must Preserve

- Coordinate values under identical basis transformations.

## Emits

- None.

## Must Not

- Be added or scaled against vectors of a different basis without an explicit change-of-basis `AdapterOp`.

## Validation Requirements

- `verify_basis_match_on_add`

## Witness Requirements

- None

## Replay Requirements

- vector trace hash

## Canonical Laws

- $\mathbf{v} = \sum_{i=1}^n c_i \mathbf{e}_i$

## Semantic Boundaries

- A Vector is NOT an Array. It is an array *modulo a basis*. Two different arrays in different bases can represent the identical vector.
