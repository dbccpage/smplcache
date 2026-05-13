# Contract Constitution

Every component must declare:
1. which paper-level concepts it depends on;
2. which artifact family it emits: Structure, Fact, Decision, Action, or Record;
3. which layer has authority over its output;
4. which later-layer concepts it is forbidden to use;
5. which certificates are required for replay or audit.

A component that emits facts and decisions from the same interface is invalid.
A component that changes representation and meaning at once is invalid.
A component that performs a lift without license is invalid.
A component that records a trace and influences execution implicitly is invalid.

## Canon Dependency Law
A contract may not introduce theoretical authority beyond its declared paper dependency.
If a component requires concepts from a later paper, the dependency must be declared explicitly.

## Typed Categorical Governance
The Omega Engine is not merely software architecture; it is a **typed semantic category with governance morphisms**. Representation morphisms, admissibility layers, obstruction persistence, congruence restoration, replay determinism, and authority stratification are all explicitly categorically bound.

## Core Invariant

> **Facts may flow upward into judgment. Judgment may not flow downward into facts.**
> **Structure, Facts, Judgments, Actions, and Records are different artifact families. Contract violations occur when one pretends to be another.**

## Constitutional Laws (Theorem Candidates)
* `NoJudgmentInsideFacts`
* `NoActionInsideRecords`
* `NoLiftWithoutWitness`
* `NoSemanticCreationInsideAdapters`
