# Generator Contract (`Generator`)

## Core Definition

> A Generator is a **pure proposal morphism**. It is a mechanism that applies domain-specific transformation schemas to an immutable input state and returns detached candidate states with transition metadata. Generators propose transitions. They do not evaluate, schedule, or execute them.

### Classification
**Subsystem:** proposal kernel / candidate synthesis layer
**Not:** engine, evaluator, search kernel, solver, pipeline.

The crucial architectural distinction:
* **Generator** proposes transitions.
* **Evaluator** accepts/rejects transitions.
* **SearchKernel** schedules transitions.
* **Engine** executes transitions.

---

## Tower Placement

Generators sit at the **proposal layer**, below Engines (which orchestrate) and the Evaluator (which accepts/rejects). They produce raw material for the search pipeline:

```text
Generator → candidates → SearchCatalog / MCTS → Evaluator → accept/reject
```

---

## Actual Trait Shape

There is **no unified `Generator` trait** in the working codebase. Generators are implemented as standalone functions or structs with ad-hoc `generate()` methods. The contract-layer definition exists only in this document.

### Design target

```rust
pub trait Generator<State, OpId, Metadata> {
    fn generate(
        &self,
        state: &State,
        ctx: &GeneratorContext,
    ) -> Result<Vec<Candidate<State, OpId, Metadata>>, GeneratorError>;
}

pub struct GeneratorContext {
    pub grammar_ref: GrammarId,
    pub admissible_operators: Vec<OperatorId>,
    pub deterministic_seed: Option<u64>,
}
```

### Candidate Semantics

```rust
pub struct Candidate<State, OpId, Metadata> {
    pub state: State,
    pub operator_id: OpId,
    pub metadata: Metadata,
    pub preconditions: Vec<Precondition>,
    pub postconditions: Vec<Postcondition>,
}
```

### Actual patterns used

Most generators follow one of two patterns:

**Pattern A: Function-based** (mathematical domain generators)
```rust
pub fn generate(input: &NemotronValue) -> Vec<NemotronValue> { ... }
```

**Pattern B: Struct-based** (structural generators)
```rust
pub struct GraphTopologyGenerator;
impl GraphTopologyGenerator {
    pub fn generate(&self, graph: &FiniteDirectedGraph) -> Vec<FiniteDirectedGraph> { ... }
}
```

---

## Existing Implementations (57 files)

### Mathematical Domain Generators (48 files: `gen_l001`–`gen_l086`)

These are **algebraic proposal engines** spanning mathematics:

| Range | Domain | Count |
|---|---|---|
| `gen_l001`–`gen_l008` | Analysis (power series, integrals, comparison tests, alternating series, differentiation, asymptotics, closed form, Fourier) | 8 |
| `gen_l010`–`gen_l017` | Applied (summability, multivariable, linear algebra, optimization, Lebesgue integration, special functions, numerical methods) | 7 |
| `gen_l021` | Real analysis / Topology | 2 |
| `gen_l022`–`gen_l029` | Geometry (manifolds, topological manifolds, differential geometry, Riemannian, symplectic, general relativity, algebraic topology, fiber bundles, differential topology) | 8 |
| `gen_l030`–`gen_l040` | Algebra (functional analysis, Hilbert spaces, abstract algebra, ring theory, measure theory, module theory, Galois theory, commutative algebra, combinatorics, graph theory, number theory, representation theory, homological algebra) | 13 |
| `gen_l057` | Complex analysis / PDEs | 2 |
| `gen_l086` | Category theory | 1 |

### Structural Generators (9 files)

| File | Role |
|---|---|
| `generator_graph_topology.rs` | Generates topological variations of `FiniteDirectedGraph` |
| `operator_evolution_generator.rs` | Generates evolved operator variants |
| `immune_projector.rs` | Generates immune-space projections |
| `projective_antibody.rs` | Generates antibody defense candidates |
| `semantic_dag_planner.rs` | Plans semantic DAG expansions |
| `cycle_aware.rs` | Cycle-aware graph generation (25KB, largest) |
| `critical_graph_lift_generator.rs` | Generates critical lift candidates |
| `vertex_flip_history.rs` | Tracks and generates vertex flip histories |
| `predicate_split.rs` | Splits by predicate |

### Infrastructure (3 files)

| File | Role |
|---|---|
| `candidate.rs` | `Candidate<State>` struct definition |
| `deterministic_sampling.rs` | Deterministic sampling utilities |
| `dirichlet_arm_proposal.rs` | Dirichlet-distributed arm proposals |

---

## Contractual Obligations

### 1. Blind Proposal Generation (MUST DO)

A Generator MUST apply a specific transformation rule to an immutable input state.

It MAY return:
- zero candidates (defined but no applicable transitions)
- one or more candidates
- a typed error (input violated generator's domain)

### 2. Zero Evaluation (MUST NOT DO)

Generators MUST NOT: score, rank, accept, reject, prune for quality, compare by expected usefulness, inspect evaluator metrics, or call functionals/observables for decision-making.

**Forbidden fields** on candidates (Evaluation Leakage):
```text
score, fitness, quality, rank, accepted, rejected, should_keep, confidence, priority
```

**Allowed fields** (Descriptive Metadata):
```text
operator_id, source_state_id, parameters, preconditions, postconditions, semantic_tags
```

**Strict Metadata Rule**: `semantic_tags` must be descriptive only. They must not encode ranking hints or hidden evaluation metrics.

### 3. Pure State Immutability

Generators MUST receive state by immutable reference and return new owned candidates.

### 4. Mandatory Transition Metadata

Every candidate MUST contain `TransitionMetadata` identifying the operator, source, parameters, and pre/postconditions.

---

## Search Coupling

The `SearchCatalog` aggregates multiple generators. However, the catalog's implementation is currently underdefined and risks hiding search policy.

### Missing Subsystem: SearchKernel

The Generator subsystem must delegate all scheduling, breadth control, and exploration bias to a formal **SearchKernel**. 

Current Search logic leaks across:
* **Generator**: proposing too many/few candidates.
* **Engine**: deciding which path to take.
* **SearchCatalog**: ordering generators.

Without a dedicated `SearchKernel` contract, search remains ambient behavior rather than a governed subsystem.

---

## UROS Trace Integration

When generators produce candidates within a traced execution:
- Each candidate that is **accepted** by the evaluator becomes a `TraceGraph` edge
- Each accepted transition is recorded in the `ReplayLedger` with a payload hash
- The generator's operator identity is preserved for auditability
- Rejected candidates do not become state-transition edges, but proposed/evaluated candidates must be accounted for in the evaluation audit to prevent silent pruning.

```text
Generator.generate(state) → candidates
  → Evaluator.accepts(before, after) → accepted_candidate
    → TraceGraph.add_edge(before_node, after_node)
    → ReplayLedger.append(payload_hash, state_hash)
```

Each proposal requires a **ProposalCertificate**:
```rust
pub struct ProposalCertificate {
    pub generator_id: GeneratorId,
    pub generation_rule: RuleId,
    pub deterministic_seed: Option<u64>,
    pub enumeration_policy: EnumerationPolicy,
}
```

---

## EEDA Integration

Generators do not directly interact with EEDA. Their output is evaluated by the `eeda_step()` function:

```rust
let accepted = eeda_step(
    &state, &candidates, &policy,
    &eval_state, &eval_candidate, r_cl_max
)?;
```

The evaluator bridge applies lexicographic descent checking to each candidate independently.

---

## Bounded Description Complexity (Compression Objective)

Generators are **bounded transformation systems** acting over an existing grammar and carrier. 

1. **LLM-mediated synthesis**: Proposes new generator implementations (external to Generator semantics).
2. **Solver-mediated synthesis**: Evolves generator parameters.
3. **Complexity Reduction**: A generator that produces candidates with lower **description complexity** under a declared encoding contributes to the system's compression objective. 

**Warning:** Kolmogorov complexity is not computable. "Self-compression" must be operationalized as bounded description complexity or compression heuristics, and must never reintroduce hidden evaluation inside the generator. Generators remain epistemically blind to the "productivity" of their output.

---

## Typed Errors

```rust
pub enum GeneratorError {
    InvalidDomain(String),
    DimensionMismatch { expected: usize, got: usize },
    UndefinedOperation(String),
    NonFiniteInput,
    InternalInvariantViolation(String),
    BudgetExceeded,
    BranchLimitReached,
}
```

### Generator Governance

To prevent search explosion, Generators must respect:
* **GeneratorBudget**: CPU/Memory limits.
* **GeneratorBranchLimit**: Max candidates per state.
* **ProposalDepthBound**: Max recursive generation depth.

---

---

## Tests to Add Immediately

### Purity Tests
* `generator_does_not_mutate_input`
* `generator_has_no_runtime_state`
* `generator_has_no_hidden_cache`

### Evaluation Isolation Tests
* `generator_cannot_access_phi`
* `generator_cannot_rank_candidates`
* `generator_cannot_emit_confidence`

### Search Separation Tests
* `search_orders_candidates_not_generator`
* `catalog_contains_no_evaluation_logic`

### Metadata Integrity Tests
* `candidate_contains_transition_metadata`
* `candidate_preserves_operator_identity`

### Determinism Tests
* `deterministic_sampling_replayable`
* `same_seed_same_candidates`

---

## Best Future Architecture

The system is converging toward:
* **ProposalKernel** (The Generator): transformation application.
* **SearchKernel**: Exploration scheduling and bias.
* **EvaluationKernel**: Acceptance judgment.
* **ExecutionKernel**: State updates and trace emission.
* **MetaKernel**: Congruence restoration and lift authority.

---

## Theorem-Level Infrastructure Required

* **NoEvaluationInsideGenerator**: Proof that generators remain epistemically blind.
* **GeneratorPurity**: Verification of immutability and no-state laws.
* **ProposalReplayInvariant**: Guaranteed deterministic candidate set reconstruction.
* **ImmutableInputLaw**: Formal state ownership boundary.
* **NoHiddenSchedulingInsideGenerator**: Separation of proposal from search.
* **ProposalMetadataCompleteness**: Adherence to transition metadata requirements.
* **SearchGeneratorSeparation**: Decoupling of ordering from synthesis.

---

## Canonical Invariant

> **Generator proposes. Observable extracts. Solver computes. Evaluator judges. Search schedules. Pipeline orchestrates.**
