```yaml
contract:
  name: PolicyArtifact
  role: typed admissibility classification
  depends_on:
    - Paper 009 Cohomological Obstruction Dynamics
    - Paper 012 Lift Licensing
  forbidden_authority:
    - metric computation
    - solver invocation
    - candidate ranking
    - state mutation
  output_family:
    - DecisionEnvelope
```
# Policy Contract

## Core Definition

> A `PolicyArtifact` is a deterministic, immutable, typed constraint function over declared decision requests that classifies admissibility without computing metrics, evaluating alternatives, executing actions, or mutating system state.

---

## Final Boundary (Clean Separation)

```text
Policy:
  request → admissibility classification

Evaluator:
  metrics → accept/reject

Engine:
  state + policy → transition

Pipeline:
  engine orchestration

Meta:
  structural admissibility

Analysis:
  measurement

Observable:
  extraction

Functional:
  aggregation
```

---

## Architectural Mandate & Contractual Obligations

### 1. Explicit Typed Requests
A Policy must govern typed `PolicyRequest` inputs. It MUST NOT access undeclared context or evaluate global state directly.
```rust
pub struct PolicyManifest {
    pub policy_id: PolicyId,
    pub clauses: Vec<PolicyClause>,
    pub requirements: Vec<EvidenceRequirement>,
    pub tolerances: Vec<ToleranceSpec>,
}

pub struct PolicyClause {
    pub clause_id: ClauseId,
    pub scope: DecisionScope,
    pub action: ActionKind,
}

pub struct PolicyRequest {
    pub layer: GovernedLayer,
    pub scope: DecisionScope,
    pub action: ActionKind,
    pub context: PolicyContext,
}

pub struct PolicyContext {
    pub identifiers: Vec<ContextId>,
    pub attributes: Vec<Attribute>,
}
```

### 2. Totality Over Declared Scope
For any `(layer, scope)` declared in the policy, `interpret` MUST return a `PolicyDecision`.
There is no silent “not applicable” state allowed for in-scope requests.

### 3. Absolute Determinism
Given identical `PolicyRequest` and policy version, `interpret` MUST return an identical `PolicyDecision`.
A Policy MUST NOT contain:
- Randomness
- Time-based variation
- Trace-based drift

### 4. No Metric Computation
Policy must not compute metrics (e.g. `Φ` obstruction).
- **Allowed**: Uses precomputed classification (e.g., “high risk” tag passed in context).
- **Forbidden**: `if Φ(state) > threshold → Forbidden` (That is the job of an Evaluator).

### 5. No Search or Ranking
Policy classifies ONE request at a time.
A Policy MUST NOT:
- Rank candidates
- Compare alternatives
- Choose a “best” option
- Perform multi-option evaluation

### 6. Strict Immutability
A PolicyArtifact MUST NOT depend on mutable external data (registry, cache, runtime state) during interpretation. Otherwise, immutability is violated indirectly.

### 7. Explicit Bounds Semantics
Bounds are not ambiguous. If an action is bounded, it must provide specific limits and enforcement models.
```rust
pub struct BoundConstraint {
    pub target: ActionKind,
    pub limit: BoundValue,
    pub enforcement: BoundEnforcement, // Hard or Soft
}
```

### 8. Layer Boundary Enforcement
Policies must mechanically reject out-of-scope requests:
```rust
fn interpret(&self, request: &PolicyRequest) -> Result<PolicyDecision, PolicyError> {
    if !self.governed_layers.contains(&request.layer) {
        return Err(PolicyError::LayerViolation);
    }
    // ...
}
```

### 9. Composition Semantics (`PolicySet`)
When multiple policies apply, they must all be interpreted independently before any resolution logic. No short-circuit evaluation is permitted. PolicySet resolution is not Policy interpretation. It is a Meta/Evaluator operation over independent PolicyDecisionEnvelopes.
```rust
pub struct PolicySet {
    pub policies: Vec<PolicyArtifact>,
    pub resolution: ConflictResolution,
}
```

### 10. No Observation Law
A Policy must not call Observables, AnalysisUnits, Diagnostics, Solvers, Search, or Trace queries.
It may only inspect fields explicitly present in PolicyRequest.

### 11. Escalation Semantics
```rust
pub enum PolicyDecision {
    Allowed,
    Forbidden { reason: PolicyReason },
    Bounded { constraints: Vec<BoundConstraint> },
    Undetermined { reason: PolicyReason },
}

pub struct PolicyDecisionEnvelope {
    pub policy_id: PolicyId,
    pub policy_version: PolicyVersion,
    pub request_hash: RequestHash,
    pub decision: PolicyDecision,
    pub derivation: VerdictDerivation,
}
```
Policy may return Undetermined. It must not name an escalation route. Routing Undetermined belongs to Pipeline/Engine.

---

## Minimal Rust Shape (Tightened)

```rust
> **Policies classify typed requests. They do not measure, search, rank, or execute.**

pub trait Policy {
    fn id(&self) -> &PolicyId;
    fn version(&self) -> &PolicyVersion;

    fn interpret(
        &self,
        request: &PolicyRequest,
    ) -> Result<PolicyDecision, PolicyError>;
}
```
