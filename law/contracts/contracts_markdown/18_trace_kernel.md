```yaml
contract:
  name: TraceArtifact
  role: immutable audit record
  depends_on:
    - Paper 008 Lift Obstruction
    - Paper 012 Lift Licensing
    - Paper 013 Operator Dynamics
  forbidden_authority:
    - execution trigger
    - policy rule
    - hidden cache
    - post-hoc enrichment
  output_family:
    - RecordEnvelope
```
# Trace Contract

## Definition

> **Trace records what happened. It does not make anything happen.**
>
> A TraceArtifact is an immutable, schema-bound record of execution, decision, routing, validation, or failure events, with explicit provenance, boundary context, and outcome status, forming a causally ordered, append-only structure that is fully inspectable within declared retention and visibility constraints and incapable of influencing execution without explicit interpretation.

## Purpose

A **TraceArtifact** is a **schema-bound immutable execution record**.

It exists to record:

* what unit acted
* on what input boundary
* under what declared context
* with what decision
* with what output/resulting artifact
* under what budget/state markers
* with what failure or success outcome

A complete TraceBundle, including all referenced schemas, policy versions, solver versions, adapter versions, seeds, input boundaries, and retention manifests, must be sufficient for deterministic replay. If a trace stream cannot reproduce the execution it records, the trace is incomplete.

A Trace is **not**:

* an Engine
* a Policy
* an Operator
* an Adapter
* a mutable control object
* a hidden cache
* a freeform log blob
* a scheduler
* a runtime planner

---

## Hard definition

A unit is a valid **TraceArtifact** only if all of the following are true:

* immutable after emission
* schema-bound (payload conforms to a declared schema, not arbitrary maps/JSON)
* append-only in lifecycle semantics
* records execution or observation facts produced at emission time
* does not itself mutate runtime behavior
* does not encode hidden control policy
* does not redefine execution semantics
* includes provenance (emitter identity)
* includes boundary identity
* includes outcome identity
* includes failure/success status (typed, not string)
* is admissible for inspection by Analysis and Meta layers
* is not used as an untyped side-channel
* does not contain derived or inferred state not explicitly produced at emission time
* emission is deterministic given identical execution conditions

If any of those fail, it is **not a TraceArtifact**.

---

## Canonical Structure

```rust
pub struct TraceBundle {
    pub traces: Vec<TraceArtifactRef>,
    pub schema_manifest: SchemaManifest,
    pub policy_manifest: PolicyManifest,
    pub solver_manifest: SolverManifest,
    pub adapter_manifest: AdapterManifest,
    pub input_manifest: InputManifest,
    pub retention_manifest: RetentionManifest,
    pub replay_manifest: ReplayManifest,
}

pub struct TraceArtifact<P: TracePayload> {
    pub payload_hash: Hash,
    pub previous_hash: Option<Hash>,
    pub artifact_hash: Hash,
    pub hash_algorithm: HashAlgorithmId,
    pub trace_id: TraceId,
    pub emitter: EmitterId,
    pub subject_kind: SubjectKind,
    pub subject_instance: SubjectInstanceId,
    pub subject_version: SubjectContractVersion,
    pub trace_type: TraceType,

    pub input_boundary: Option<BoundaryRef>,
    pub output_boundary: Option<BoundaryRef>,

    pub decision: Option<DecisionMarker>,
    pub status: TraceStatus,
    pub state_origin: StateOrigin,

    pub payload: P,

    pub parent: Option<TraceId>,
    pub step_index: u64, // Must be deterministic, causal index

    pub timestamp_metadata: Option<Timestamp>, // purely observational, NEVER for ordering
}
```

### Schema-Bound Payload

Payload **must** conform to a declared `TracePayload` trait bound, not a loose `serde_json::Value` or `HashMap<String, String>`.

```rust
pub trait TracePayload: BaseType {}
```

**Invariant:** No unstructured JSON blobs. No arbitrary maps. Every payload field is typed and declared.

### DecisionMarker

```rust
pub struct DecisionMarker {
    pub actor: DecisionAuthority,
    pub outcome: DecisionOutcome,
}

pub enum DecisionOutcome {
    Accepted,
    Rejected,
    Continued,
    Terminated,
    Routed,
}
```

**Constraint:** `DecisionMarker` records what happened. It **must not** encode policy logic — it is a fact, not a rule.

### TraceStatus

```rust
pub enum TraceStatus {
    Success,
    Failure { reason: FailureReason },
}
```

No string-based status. Every failure has a typed reason.

### StateOrigin

```rust
pub enum StateOrigin {
    Emitted,
    Derived,
    Recomputed,
}
```

Differentiates explicit runtime emission from post-hoc or derived state. Derived states must refer to an originating context.

### Hash & Serialization Semantics

Hash equivalence requires exact formalization to survive serialization changes.

```rust
pub struct HashAlgorithmId(String);
pub trait CanonicalSerializationLaw {}
pub trait PayloadNormalizationRule {}
```

---

## Canonical Contract

```yaml
trace:
  id: TRACE001
  name: ExampleTraceArtifact
  kind: TraceArtifact

  trace_signature:
    subject_kind:
      - BasisOperator
      - Adapter
      - Engine
      - Pipeline
      - Runtime
      - AnalysisUnit
      - MetaRule

    trace_type:
      - ExecutionTrace
      - DecisionTrace
      - FailureTrace
      - ValidationTrace
      - RoutingTrace

    schema_ref: schemas/trace_artifact.yaml

  provenance_law:
    emitter_identity_required: true
    parent_trace_link_allowed: true
    causal_chain_explicit: true
    anonymous_emission_forbidden: true
    witness: proof_or_test_ref

  immutability_law:
    immutable_after_emit: true
    append_only_lifecycle: true
    retroactive_mutation_forbidden: true
    retroactive_enrichment_forbidden: true
    witness: proof_or_test_ref

  content_law:
    input_boundary_required: true
    output_boundary_required: conditional
    decision_marker_required: conditional
    status_required: true
    timestamp_or_step_index_required: true
    schema_bound_payload_required: true
    freeform_log_only_forbidden: true
    derived_state_forbidden: true
    witness: proof_or_test_ref

  separation_law:
    direct_policy_execution_forbidden: true
    state_mutation_via_trace_forbidden: true
    hidden_side_channel_control_forbidden: true
    implicit_consumption_forbidden: true
    witness: proof_or_test_ref

  ordering_law:
    total_order_within_session: true
    partial_order_via_parent_links: true
    deterministic_emission_required: true
    witness: proof_or_test_ref

  causal_chain_law:
    parent_must_exist: true
    parent_step_index_less_than_current: true
    pruning_must_not_break_chain: true
    witness: proof_or_test_ref

  visibility_law:
    analysis_readable: true
    meta_readable: true
    engine_readable: true
    runtime_readable: true
    silent_private_trace_class_forbidden: true
    witness: proof_or_test_ref

  retention_law:
    retention_mode:
      - Session
      - Persistent
      - BudgetBounded
    pruning_policy_explicit: true
    pruning_preserves_causal_chain: true
    witness: proof_or_test_ref

  failure_contract:
    modes:
      - MissingEmitterIdentity
      - MissingBoundary
      - UntypedPayload
      - MutableTraceViolation
      - TraceLawViolation
      - CausalChainViolation
      - DerivedStateViolation
```

---

## Laws

### Provenance Law

This is non-negotiable.

Every trace must say:

* who emitted it
* where in the causal chain it lives
* what parent trace or originating context exist, if any

Anonymous logging is garbage. If you cannot attribute a trace, you cannot audit the system.

---

### Immutability Law

Trace is **write-once, append-only**.

* No mutation after emission
* No retroactive correction
* No "log enrichment" that changes the historical record
* No post-hoc annotation or recomputation

Append-only means:

* new traces may reference old traces
* old traces do not get rewritten

---

### Content Law

A trace must contain typed execution fact, not narrative fog.

Depending on trace type, it should include:

* input boundary
* output boundary if relevant
* decision marker if relevant
* success/failure status (typed)
* step index or timestamp
* schema-bound payload

**No derived state rule:** Trace must not contain derived or inferred state not explicitly produced at emission time. Derived state may appear in a Trace only if the component emitting the Trace actually produced that derived state as its declared output, and the derivation source is referenced. Post-hoc recomputation is forbidden.

Forbidden:

* post-hoc enrichment
* recomputed metrics
* retroactive annotation

A stack of strings is not a trace system. It is logging sludge.

---

### Separation Law

This is the critical defense.

A trace may be read by the system. It **cannot**:

* trigger execution
* alter state
* encode policy decisions

Trace must not be used as input to execution without **explicit interpretation** by:

* Analysis
* Meta
* Policy
* Engine

No implicit consumption. If a component reads a trace and changes behavior, that interpretation must be through a declared, typed interface — not by pattern-matching on trace payloads.

That is how side-channel control and debugging hell appear.

---

### Ordering Law

Trace ordering must use **step-indexed causal ordering** instead of wall-clock time. Timestamps are strictly metadata and never provide ordering semantics.

**Deterministic emission:** Given identical execution conditions, trace emission must be deterministic. No random IDs without seed. No non-deterministic timestamps used for ordering.

Otherwise replay becomes ambiguous.

### Replay & Sufficiency Law

Trace interpretation and replay requires a unified structure, formalizing the core theorem of `TraceBundle` sufficiency:

```rust
pub struct ReplayManifest;
pub struct ReplayDeterminismCertificate;
pub struct ReplayEnvironment;
pub struct ReplaySeed;
pub struct ReplayOrdering;
```

A `TraceBundle` must be sufficient for deterministic replay.

### Trace Consumption Law

Even with explicit interpretation rules, traces can become implicit policy channels. Therefore, any component that interprets a trace for runtime decision-making must implement a `TraceConsumerContract` that explicitly declares its interpretative assumptions.

---

### Causal Chain Law

If `parent` exists:

* `parent.trace_id` must resolve to an existing trace
* `parent.step_index < current.step_index`

This prevents broken lineage. A trace that claims a parent must prove the parent exists and precedes it.

---

### Visibility Law

All trace artifacts must be accessible through declared interfaces. No hidden trace classes.

No secret invisible trace classes that only some hidden subsystem can see. That is how side-channel control appears.

---

### Retention Law

Retention is allowed to differ:

* session-only
* persistent
* budget-bounded

But the policy must be explicit.

```rust
pub struct RetentionPolicy {
    pub mode: RetentionMode,
    pub max_entries: Option<usize>,
    pub max_age: Option<Duration>,
}
```

**Invariant:** Pruning must not break causal chain invariants. If a trace is pruned, all traces that reference it as parent must either be pruned or have their lineage explicitly marked as truncated via a `TruncatedLineageCertificate`.

```rust
pub struct TruncatedLineageCertificate {
    pub pruned_trace_id: TraceId,
    pub causal_boundary_hash: Hash,
}
```

Trace disappearance without declared retention policy is operational dishonesty.

---

## Explicit Non-Trace Cases

These are **not TraceArtifacts**:

### 1. Mutable engine control state

That is runtime state, not trace.

### 2. Freeform console logs

That is logging, not typed trace.

### 3. Hidden analytics buffer

That is side-channel infrastructure.

### 4. Policy table pretending to be a trace history

That is policy, not trace.

### 5. Artifact that records and mutates execution simultaneously

That is illegal layer collapse.

### 6. Post-hoc enriched record

That is derived state, not trace.

---

## Admissibility Rules

A TraceArtifact is admissible only if all are true:

1. Emitter identity is explicit
2. Provenance is explicit
3. Payload is schema-bound (not merely "typed")
4. Immutability after emission is guaranteed
5. Status is typed (not string)
6. Boundary context is explicit where relevant
7. No hidden control semantics exist
8. Retention policy is explicit
9. Causal chain is valid (parent exists, ordering correct)
10. No derived state present
11. Emission is deterministic

If any are missing, reject it.

---

## Minimal Example

```yaml
trace:
  id: TRACE014
  name: EngineDecisionTrace
  kind: TraceArtifact

  trace_signature:
    subject_kind:
      - Engine
    trace_type:
      - DecisionTrace
    schema_ref: schemas/trace_artifact.yaml

  provenance_law:
    emitter_identity_required: true
    parent_trace_link_allowed: true
    causal_chain_explicit: true
    anonymous_emission_forbidden: true
    witness: tests::trace014::provenance_suite

  immutability_law:
    immutable_after_emit: true
    append_only_lifecycle: true
    retroactive_mutation_forbidden: true
    retroactive_enrichment_forbidden: true
    witness: tests::trace014::immutability_suite

  content_law:
    input_boundary_required: true
    output_boundary_required: true
    decision_marker_required: true
    status_required: true
    timestamp_or_step_index_required: true
    schema_bound_payload_required: true
    freeform_log_only_forbidden: true
    derived_state_forbidden: true
    witness: tests::trace014::content_suite

  separation_law:
    direct_policy_execution_forbidden: true
    state_mutation_via_trace_forbidden: true
    hidden_side_channel_control_forbidden: true
    implicit_consumption_forbidden: true
    witness: tests::trace014::separation_suite

  ordering_law:
    total_order_within_session: true
    partial_order_via_parent_links: true
    deterministic_emission_required: true
    witness: tests::trace014::ordering_suite

  causal_chain_law:
    parent_must_exist: true
    parent_step_index_less_than_current: true
    pruning_must_not_break_chain: true
    witness: tests::trace014::causal_chain_suite

  visibility_law:
    analysis_readable: true
    meta_readable: true
    engine_readable: true
    runtime_readable: true
    silent_private_trace_class_forbidden: true
    witness: tests::trace014::visibility_suite

  retention_law:
    retention_mode:
      - Session
    pruning_policy_explicit: true
    pruning_preserves_causal_chain: true
    witness: tests::trace014::retention_suite

  failure_contract:
    modes:
      - MissingEmitterIdentity
      - MissingBoundary
      - UntypedPayload
      - MutableTraceViolation
      - TraceLawViolation
      - CausalChainViolation
      - DerivedStateViolation

---

## Required Test Infrastructure

### Immutability tests
* `trace_cannot_mutate_after_emit`
* `retroactive_enrichment_rejected`
* `payload_hash_changes_on_mutation`

### Replay tests
* `same_execution_same_trace_chain`
* `same_seed_same_trace_order`
* `trace_bundle_replays_execution`

### Provenance tests
* `missing_emitter_rejected`
* `parent_must_exist`
* `step_index_monotone`

### Separation tests
* `trace_cannot_trigger_execution`
* `trace_cannot_encode_policy`
* `trace_not_consumed_implicitly`

### Retention tests
* `pruning_preserves_chain`
* `truncated_lineage_marked`

---

## Target Architecture

The Trace subsystem points toward a formal separation of:

```text
TraceKernel
ReplayKernel
WitnessKernel
RetentionKernel
ProvenanceKernel
```

The Trace subsystem itself should remain minimal: record immutable causal fact, preserve provenance, preserve ordering, and stop.

### Theorem-Level Infrastructure

* `ReplaySufficiency`
* `TraceImmutability`
* `NoImplicitTraceControl`
* `DeterministicEmission`
* `ProvenanceCompleteness`
* `CausalChainIntegrity`
* `NoDerivedStateInjection`
* `RetentionPreservesReplayability`
```
