use crate::law::law_enforcement::kinds::BaseType;
use crate::law::law_enforcement::trace::TraceArtifact;

// ─── identity.rs ─────────────────────────────
pub trait OperatorIdentity {
    fn name(&self) -> &'static str;
}

pub trait BoundaryOperatorIdentity {
    fn name(&self) -> &'static str;
}

// ─── analysis/diagnostics.rs ─────────────────────────────
pub trait AnalysisArtifact {}

pub trait Diagnostic<Input> {
    type Output: AnalysisArtifact;
}

// ─── analysis/measurement.rs ─────────────────────────────
pub trait Functional<T: BaseType> {
    type Output;
}

// ─── analysis/units.rs ─────────────────────────────
pub trait AnalysisUnit<T: BaseType> {
    type Output: AnalysisArtifact;
}

pub trait MetaRule<T> {
    type Output;
    fn evaluate(&self, subject: &T) -> Result<Self::Output, String>;
}
pub trait EvidenceRef {}
pub trait EvidenceArtifact {}
pub trait WitnessArtifact {}

// ─── evidence/trace_recorder.rs ─────────────────────────────
pub trait TraceRecorder {
    type Event;
    type Artifact: TraceArtifact;
    fn record(&mut self, event: Self::Event);
    fn commit(self) -> Self::Artifact;
}

// ─── evidence/witness.rs ─────────────────────────────
pub trait Witness {}

// ─── execution/0_runtime_trait.rs ─────────────────────────────
/// Runtime: system boundary entrypoint.
///
/// The Runtime is the outermost execution surface. It accepts external
/// input (which may not be a BaseType — e.g., raw bytes, CLI args,
/// HTTP payloads) and produces external output.
///
/// ## Why Input/Output are not BaseType-bounded
/// The Runtime sits at the system boundary where external representations
/// enter the kernel. The Adapter layer between Runtime and Pipeline is
/// responsible for converting external input into BaseType. This is
/// intentional, not accidental — tightening Input to BaseType would push
/// parsing/validation logic into the wrong layer.
///
/// RuntimeState remains BaseType-bounded because it IS internal state.
pub trait Runtime<S>
where
    S: BaseType,
{
    type Pipeline: Pipeline<S>;
    type Input;
    type RuntimeState: BaseType;
    type Output;
    type Trace: TraceArtifact;
    type Error;

    fn start(
        &mut self,
        input: Self::Input,
    ) -> Result<(Self::RuntimeState, Self::Output, Self::Trace), Self::Error>;
}

// ─── execution/1_pipeline_trait.rs ─────────────────────────────

/// Pipeline: multi-step orchestration composing Engine steps.
///
/// A Pipeline owns the trajectory lifecycle: it calls `Engine::step()`
/// in a loop, accumulates traces, manages budgets, and decides
/// continuation / rollback / lift.
///
/// ## Distinction from Engine
/// - Pipeline = **whole-run orchestration** (calls Engine.step() N times)
/// - Engine = **local step** (one operator application + re-evaluation)
///
/// A Pipeline must not bypass the Engine to apply operators directly.
pub trait Pipeline<S>
where
    S: BaseType,
{
    type Engine: Engine<S>;
    type PipelineState: BaseType;
    type Output: BaseType;
    type Trace: TraceArtifact;
    type Error;

    fn run(
        &mut self,
        input: S,
    ) -> Result<(Self::PipelineState, Self::Output, Self::Trace), Self::Error>;
}

// ─── execution/2_engine_trait.rs ─────────────────────────────

/// Engine: stateful single-step execution over obstruction-bearing state.
///
/// An Engine owns runtime state and applies one operator per `step()` call.
/// It measures, selects, applies, and re-evaluates locally.
///
/// ## Distinction from Pipeline
/// - Engine = **one step** of the execution trajectory
/// - Pipeline = **multi-step orchestration** composing Engine steps
///
/// An Engine may iterate internally within a single step (e.g., MCTS rollout),
/// but the external interface is one-step-at-a-time.
pub trait Engine<S>
where
    S: BaseType,
{
    type Operator: OperatorIdentity;
    type RuntimeState: BaseType;
    type Output: BaseType;
    type Trace: TraceArtifact;
    type Error;

    fn step(
        &mut self,
        state: S,
    ) -> Result<(Self::RuntimeState, Self::Output, Self::Trace), Self::Error>;
}

// Removed duplicates of Engine, Pipeline, Runtime

// ─── execution/strategy.rs ─────────────────────────────
pub trait Search<S: BaseType>: Pipeline<S> {}
pub trait Solver<S: BaseType>: Pipeline<S> {}
pub trait SelectionStrategy {}
pub trait ContinuationPolicy {}
pub trait BudgetPolicy {}

// ─── governance/action.rs ─────────────────────────────
pub trait Action {}

// ─── governance/policy.rs ─────────────────────────────
use crate::law::law_enforcement::contracts_contract::SubjectKind;
pub trait Policy {
    fn governed_layers(&self) -> Vec<SubjectKind>;
}

// ─── operators/algebraic.rs ─────────────────────────────
pub trait AlgebraicStructure {}
pub trait NonDecisionAlgebraic {}

// ─── operators/boundary.rs ─────────────────────────────
pub trait BoundaryOperator {}

// ─── operators/constructor.rs ─────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConstructorError {
    ParseError(String),
    SchemaMismatch,
    MissingRequiredField(&'static str),
    InvalidInvariant(String),
    NonFiniteNumericInput,
    UnsupportedRepresentation,
}

// Removed duplicate BoundaryOperatorIdentity

/// A Constructor is a deterministic boundary unit that converts raw or heterogeneous input
/// into a validated internal `BaseType`. It may parse, decode, type-check, and validate.
/// It may not repair, solve, optimize, search, register with the engine, or mutate runtime state.
pub trait ConstructorOp<Raw, T: BaseType>: BoundaryOperatorIdentity {
    type Error;

    fn construct(&self, raw: Raw) -> Result<T, Self::Error>;
}

// ─── operators/identity_trait.rs ─────────────────────────────
// Removed duplicate OperatorIdentity

// ─── operators/kernel.rs ─────────────────────────────
pub trait KernelOperator {}

// ─── operators/lambda_trait.rs ─────────────────────────────

/// Lambda: pure morphism `S -> T`.
pub trait LambdaOp<S: BaseType, T: BaseType>: OperatorIdentity {
    fn apply(&self, input: &S) -> Result<T, String>;
}

// ─── operators/reducer_trait.rs ─────────────────────────────

/// Reducer: idempotent structure-shrinking endomorphism `S -> S`.
pub trait ReducerOp<S: BaseType>: OperatorIdentity {
    fn apply(&self, input: &S) -> Result<S, String>;
}

// ─── operators/transform_trait.rs ─────────────────────────────

/// Transform: pure endomorphism `S -> S` with unrestricted phi.
pub trait TransformOp<S: BaseType>: OperatorIdentity {
    fn apply(&self, input: &S) -> Result<S, String>;
}

// ─── operators/adapters/adapter_trait.rs ─────────────────────────────

/// Adapter: converts raw or external input into a verified BaseType.
pub trait AdapterOp<S, T: BaseType>: BoundaryOperatorIdentity {
    type Error;
    fn adapt(&self, input: &S) -> Result<T, Self::Error>;
}

// ─── operators/adapters/decoder_trait.rs ─────────────────────────────

/// Decoder: maps an encoded BaseType back to its original BaseType.
pub trait DecoderOp<S: BaseType, T: BaseType>: BoundaryOperatorIdentity {
    type Error;
    fn decode(&self, input: &S) -> Result<T, Self::Error>;
}

// ─── operators/adapters/encoder_trait.rs ─────────────────────────────

/// Encoder: maps one BaseType to another BaseType.
pub trait EncoderOp<S: BaseType, T: BaseType>: BoundaryOperatorIdentity {
    type Error;
    fn encode(&self, input: &S) -> Result<T, Self::Error>;
}

// ─── structure/base_type_behavior.rs ─────────────────────────────

// ─── structure/base_type_behavior_trait.rs ─────────────────────────────

/// BaseType Behavior: Any behavior explicitly required by BaseType beyond the marker contract.
pub trait BaseTypeBehavior: HasCarrier {}

/// QuotientableBaseType: A BaseType that must explicitly support quotienting.
pub trait QuotientableBaseType: BaseTypeBehavior + HasQuotient {}

// ─── structure/has_carrier_trait.rs ─────────────────────────────
/// A type with an underlying representation space.
pub trait HasCarrier {
    type Carrier;
    fn carrier(&self) -> &Self::Carrier;
}

// ─── structure/has_quotient_trait.rs ─────────────────────────────
/// A type that admits quotienting by an equivalence relation.
pub trait HasQuotient {
    type Equivalence;
    type Quotient;

    fn quotient(&self, relation: &Self::Equivalence) -> Self::Quotient;
}

// ─── structure/observable_trait.rs ─────────────────────────────
/// Observable: structural entity that can be formally measured.
pub trait Observable {
    type Observation;
    fn observe(&self) -> Self::Observation;
}

// ─── structure/obstruction_trait.rs ─────────────────────────────
/// Obstruction: mathematically formal violation or gap in structure.
pub trait Obstruction {
    type Signature;
    fn signature(&self) -> Self::Signature;
}

// ─── structure/tensor_trait.rs ─────────────────────────────
/// Tensor: structured multi-dimensional state or parameters.
pub trait Tensor {
    fn shape(&self) -> &[usize];
}

