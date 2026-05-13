# Kernel Contract

**Role:** bounded authority unit.

A Kernel is a small subsystem that owns exactly one class of semantics. 

The Kernel definition enforces architectural decomposition, preventing the emergence of universal "god-objects" inside the engine.

Examples of Kernels:
* `EvaluatorKernel`
* `SearchKernel`
* `WitnessKernel`
* `ObstructionKernel`
* `ReplayKernel`
* `MeasurementKernel`
* `CertificateKernel`
* `PolicyKernel`

## Core Invariant

> **A Kernel owns semantics, not orchestration.**

## The Splitting Rule

> **If a Kernel owns more than one semantic class, split it.**

## Kernel Declaration Definition

Every Kernel must declare its bounds via a YAML manifest:

```yaml
kernel:
  name: "KernelName"
  role: "Specific semantic responsibility"
  owns: ["ArtifactType1", "SemanticProperty"]
  forbidden_authority: ["Orchestrating execution", "Mutating global state"]
  input_family: ["InputType"]
  output_family: ["OutputType"]
  stateful: true | false
  may_execute: true | false
  may_mutate: true | false
  may_authorize: true | false
```

## Prohibitions
Kernels are foundational units. A Kernel MUST NOT:
1. Orchestrate control flow beyond its own localized execution.
2. Cross its semantic boundaries to resolve out-of-scope errors.
3. Absorb unrelated artifacts to simplify function signatures.
