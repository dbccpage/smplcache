use crate::law::law_enforcement::sealed::Sealed;

// ─── base.rs ─────────────────────────────────────────────────────────────────
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchemaRef(pub &'static str);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BaseValidationWitness {
    pub validation_id: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BaseValidationViolation {
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MutabilityMode {
    Immutable,
    Controlled {
        allowed_fields: &'static [&'static str],
    },
}

pub trait BaseType: Sealed {
    fn schema() -> &'static SchemaRef;
    fn validate(&self) -> Result<BaseValidationWitness, BaseValidationViolation>;
}

// ─── operator.rs ─────────────────────────────────────────────────────────────
pub trait LambdaKind: Sealed {}
pub trait ReducerKind: Sealed {}
pub trait TransformKind: Sealed {}
pub trait AdapterKind: Sealed {}
pub trait ConstructorKind: Sealed {}

// ─── algebraic_operator.rs ───────────────────────────────────────────────────
pub trait BoundaryKind: Sealed {}
pub trait CoboundaryKind: Sealed {}

// ─── observer.rs ─────────────────────────────────────────────────────────────
pub trait ValidationKind: Sealed {}
pub trait AnalysisUnitKind: Sealed {}
pub trait MetaKind: Sealed {}

// ─── algebra.rs ──────────────────────────────────────────────────────────────
pub trait AlgebraLawKind: Sealed {}

// ─── execution.rs ────────────────────────────────────────────────────────────
pub trait EngineKind: Sealed {}
pub trait PipelineKind: Sealed {}
pub trait PipelineAdapterKind: Sealed {}
pub trait ReportProjectionAdapterKind: Sealed {}
pub trait EgressAdapterKind: Sealed {}
pub trait RuntimeKind: Sealed {}
pub trait ActionExecutorKind: Sealed {}

// ─── artifact.rs ─────────────────────────────────────────────────────────────
pub trait TraceKind: Sealed {}
pub trait PolicyArtifactKind: Sealed {}
pub trait ValidationArtifactKind: Sealed {}
pub trait AnalysisArtifactKind: Sealed {}
pub trait DecisionArtifactKind: Sealed {}
pub trait ActionResultArtifactKind: Sealed {}
pub trait OrchestrationArtifactKind: Sealed {}

// ─── law_policy.rs ───────────────────────────────────────────────────────────
pub trait MayCarryLaws: Sealed {}
pub trait MustNotCarryLaws: Sealed {}
pub trait MayExecuteOperators: Sealed {}
pub trait MustNotExecuteOperators: Sealed {}
pub trait MayMutateState: Sealed {}
pub trait MustNotMutateState: Sealed {}
pub trait MayOrchestrateRuntime: Sealed {}
pub trait MustNotOrchestrateRuntime: Sealed {}

// ─── legacy.rs ───────────────────────────────────────────────────────────────
#[deprecated(note = "use generated sealed descriptors (BoundContract + ContractImage)")]
pub trait DeclaresContract {
    const CONTRACT: crate::law::law_enforcement::contracts_contract::ContractInstance;
}

#[deprecated(note = "use generated sealed descriptors (BoundContract + ContractImage)")]
pub trait DeclaresSubjectKind {
    const SUBJECT_KIND: crate::law::law_enforcement::contracts_contract::SubjectKind;
}

// ─── From mod.rs ─────────────────────────────────────────────────────────────
pub trait FunctionalKind: Sealed {}
pub trait DiagnosticKind: Sealed {}
pub trait EvaluatorKind: Sealed {}
pub trait PolicyUnitKind: Sealed {}
pub trait SearchKind: Sealed {}
pub trait SolverKind: Sealed {}


pub trait ValidationWitness {}

