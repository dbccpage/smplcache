# Morphism Contract

## Classification

Subsystem: Category Theory
Artifact Family: StructureArtifact
Operator Class: None
Role: Mathematical Arrow
Mathematical Object: Morphism

## Formal Definition

A morphism $f \in \operatorname{Hom}_{\mathcal{C}}(A, B)$ is a directed, structure-preserving mapping from a source object $A$ to a target object $B$ within a category $\mathcal{C}$.

## Structural Requirements

- Explicit source object $A = \operatorname{src}(f)$.
- Explicit target object $B = \operatorname{tgt}(f)$.

## Requires

- Both $A, B \in \operatorname{Ob}(\mathcal{C})$.
- Composability with adjacent morphisms.

## Must Preserve

- Directionality (source to target).

## Emits

- None.

## Must Not

- Change its declared source or target.
- Act outside its parent category without a functorial lift.

## Validation Requirements

- `verify_morphism_boundaries`

## Witness Requirements

- `MorphismAdmissibilityWitness`

## Replay Requirements

- morphism hash
- source/target hash

## Canonical Laws

- $\operatorname{src}(f) = A, \operatorname{tgt}(f) = B$

## Semantic Boundaries

- A Morphism is NOT a function (it need not evaluate elements).
- A Morphism is NOT an execution action.
