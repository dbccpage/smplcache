# Global Contract Taxonomy

The ontology distinguishes heavily between artifact families, execution boundaries, and authority governance.

## 0. Artifact Family Separation

* **StructureArtifact**: BaseType, Operator schema, Lift schema, obstruction schema.
* **FactEnvelope**: Output of Analysis, Solvers, Diagnostics, Observables.
* **DecisionEnvelope**: Output of Evaluator, Policy, Meta, Engine.
* **Action**: State transitions and executions.
* **Record**: TraceArtifacts and history.

## 1. Authority Graph

* **StructureArtifact** may inform **FactEnvelope**.
* **FactEnvelope** may inform **DecisionEnvelope**.
* **DecisionEnvelope** may trigger **Action**.
* **Record** may record **all**, but may control **none**.

## 2. Core Operational Entities

* **Evaluator**:
  * owns: admissibility_logic
  * emits: DecisionEnvelope
  * governs: admissibility
  * forbidden: state_mutation, structure_discovery
* **Diagnostic**:
  * owns: health_checks
  * emits: FactEnvelope
  * forbidden: state_mutation, policy_judgment
* **AdapterOp** (Boundary):
  * owns: representation_transport
  * emits: StructureArtifact
  * forbidden: meaning_creation
* **LambdaOp** (Kernel):
  * owns: functional_morphism
  * emits: StructureArtifact
  * forbidden: impurity, state_mutation_without_return
* **ForgetfulOp** (Kernel):
  * owns: lawful_information_forgetting
  * requires: loss_certificate, replay_boundary, admissibility_scope
* **GaugeOp** (Kernel):
  * owns: zero_cost_equivalence_retraction
  * preserves: obstruction_mass, semantic_equivalence
  * forbidden: semantic_injection, topology_change
* **ReflectorOp** (Kernel/Meta):
  * modes: observational, transformational
  * observational emits: FactEnvelope
  * transformational emits: StructureArtifact (requires reflection_license)

## 3. Semantic Creation (Doctrine)
*Introducing undeclared semantic content not derivable from input structure, declared lifts, declared witnesses, or declared licenses.* This is strictly forbidden without an explicit Meta lift.
