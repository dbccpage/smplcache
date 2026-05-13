# Replay Kernel

**Role:** deterministic reconstruction of execution from traces, manifests, seeds, schemas, and certificates.

The Replay Kernel ensures that the Omega Engine is completely historically reproducible. It does not control live execution; it controls the capability to prove that a historical execution was sound.

## Core Invariant

> **If it cannot replay, it did not happen in the formal system.**

## The Replay Mandate
To guarantee perfect reproducibility, the Replay Kernel must have access to:
1. **TraceArtifacts**: The immutable causal execution record.
2. **ReplayManifests**: Explicit configurations defining versions and environments.
3. **Seeds**: All stochastic components must be strictly seeded.
4. **Schemas**: Exact topological definitions.
5. **Certificates**: Proved artifacts of computational facts that bypass expensive recomputations safely.

## Strict Prohibitions
The Replay Kernel MUST NOT:
* Guess missing state variables.
* Perform speculative execution.
* Execute active searches or mutate the live ontology.
* Succeed if intermediate certificates are invalid or missing.

## Replay Laws (Theorem Candidates)
* `ReplayDeterminism`
* `TraceImmutability`
* `EvidenceAddressability`
* `CanonicalWitnessLaw`
