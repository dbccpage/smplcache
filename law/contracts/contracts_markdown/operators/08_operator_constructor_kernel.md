# Constructor Contract

## Core Definition

> A Constructor is a deterministic boundary unit that converts raw or heterogeneous input into a validated internal `BaseType`. It may parse, decode, type-check, and validate. It may not repair, solve, optimize, search, register with the engine, or mutate runtime state.

---

## Tower Placement

Constructors are the **intake boundary** of the Omega Engine. They sit at the outermost edge, accepting untrusted data and producing validated mathematical objects.

```text
Untrusted external data → Constructor → validated BaseType → Engine pipeline
```

**CRITICAL: Constructors are NOT Adapters.**
- Constructors ingest untrusted/raw data and create lawful internal objects
- Adapters convert between already-declared, already-validated representations

---

## Actual Implementation Locations

Constructors do not live in a centralized `src/constructors/` directory. They are distributed across the codebase:

| Location | Constructs |
|---|---|
| `engines/quantum/core/density_matrix.rs` | `DensityMatrix::new()`, `DensityMatrix::pure()` |
| `engines/quantum/adapters/quantum_density_engine.rs` | `from_prompt_embedding_checked()`, `from_states_checked()` |
| `engines/quantum/adapters/text_density_encoder.rs` | `TextDensityEncoder::encode()` |
| `engines/quantum/adapters/graph_density.rs` | `GraphDensityMatrix` construction |
| `engines/quantum/channels/quantum_channel.rs` | `QuantumChannel::try_new()` |
| `engines/prompt_intake_engine.rs` | `PromptIntakeEngine::parse_cot_record()`, `parse_jsonl_record()` |
| `types/trace_step.rs` | `TraceStep::new_validated()` |
| `types/trace_dependency.rs` | `TraceDependency::new_validated()` |
| `types/section.rs` | `Section::new_validated()` |
| `trace/replay_ledger.rs` | `ReplayLedger::from_payloads()` |
| `trace/witness.rs` | `ExecutionWitness::from_execution()` |

---

## Architectural Mandate

### 1. Mandatory Validation (MUST DO)

Constructor output MUST be impossible without validation.

Required invariant:
```text
Ok(T) ⇒ T.validate().is_ok()
```

Example from `DensityMatrix::new()`:
```rust
pub fn new(dim: usize, data: Vec<C64>) -> Result<Self, DensityMatrixError> {
    if dim == 0 { return Err(DensityMatrixError::ZeroDimension); }
    if data.len() != dim * dim {
        return Err(DensityMatrixError::ShapeMismatch { ... });
    }
    // ... additional validation
    Ok(Self { dim, data })
}
```

### 2. Separate Parsing from Construction

- **Parser**: bytes/text → intermediate syntax
- **Constructor**: intermediate/raw structure → validated `BaseType`

Parsing errors and invariant violations MUST remain distinct error types.

### 3. ZERO Mathematical Solving or Repair (MUST NOT DO)

**Forbidden:**
- no filling missing fields
- no guessing defaults
- no normalizing invalid topology
- no deleting bad nodes/edges
- no solving constraints to make input valid

**Allowed:**
- canonical decoding
- type conversion
- invariant checking
- explicit default only if schema declares it

### 4. Pure Owned Instantiation

- MUST NOT take `&mut Engine`
- MUST NOT maintain own state
- MUST NOT register, schedule, attach, or mutate global state

---

## UROS Evidence Integration

When a constructor creates an object within a traced UROS execution:

1. The constructed object's state hash is recorded in the `ReplayLedger`
2. If the constructor is the first step, its output hash becomes the `genesis_hash`
3. The `EvidencePacket` records:
   - `initial_state`: serialized constructor input
   - `operator_morphism`: constructor identity
   - `hypothesis_invariant`: the validation predicate that was checked

```rust
// trace/evidence.rs
pub struct EvidencePacket {
    pub claim_id: Uuid,
    pub initial_state: String,
    pub operator_morphism: String,
    pub hypothesis_invariant: String,
    pub formal_translation: String,
    pub oracle_execution_trace: Option<String>,
    pub proof: Option<OracleProof>,
}
```

---

## SymSys Integration

Constructors can be proposed by the SymSys pipeline:

1. **Stage 1 (Ideation)**: Propose a new constructor for a raw data format
2. **Stage 2 (Audit)**: Red-team the constructor for repair smuggling
3. **Stage 3 (L2 Extraction)**: Generate the Rust skeleton with validation checks
4. **Stage 4 (Countermodel)**: Find inputs that bypass validation
5. **Stage 5 (Evidence Plan)**: Write `#[test]` functions verifying all rejection paths

---

## Required Boundary Flow

```text
Raw input → Constructor → validated BaseType     ✓ LAWFUL
Raw input → Constructor → repaired candidate     ✗ FORBIDDEN
```

---

## Canonical Invariant

> **Constructor validates. Encoder encodes. Lambda bridges. Reducer deflates. Transform rearranges. Generator proposes. Engine orchestrates.**

$$ 
\boxed{
\text{Operators may transform or propose; only Evaluators accept; only Engines execute; only MetaLift changes carrier expressivity.}
}
$$

$$ 
\boxed{
\text{No operator is allowed to become an Engine in disguise.}
}
$$
