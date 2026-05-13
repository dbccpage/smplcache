#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubjectKind {
    BaseType,
    Lambda,
    Reducer,
    Transform,
    Adapter,
    AlgebraLaw,
    Engine,
    MetaRule,
    AnalysisUnit,
    Pipeline,
    PolicyArtifact,
    ActionExecutor,
    TraceArtifact,
    ValidationUnit,
    Validation,
    Runtime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TypeRef(pub &'static str);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Boundary {
    UbiquitousSheaf,
    Domain { domain: TypeRef },
    DomainCodomain { domain: TypeRef, codomain: TypeRef },
    SourceTarget { source: TypeRef, target: TypeRef },
    Carrier { carrier: TypeRef },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Guarantee {
    Deterministic,
    Pure,
    ReadOnly,
    SameDomain,
    PhiNonIncreasing,
    Idempotent,
    BoundedComputation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Prohibition {
    NoSearch,
    NoMutation,
    NoHiddenState,
    NoRuntimeExecution,
    NoExternalEffects,
    NoHeuristics,
    NoTraversal,
    NoOptimization,
    NoOperatorSimulation,
    NoImplicitCoercion,
    NoPolicyInference,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Admissibility {
    pub explicit: bool,
    pub hidden_state_forbidden: bool,
    pub mixed_roles_forbidden: bool,
    pub domain_restricted: bool,
    pub parameter_restricted: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Totality {
    Total,
    Partial,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ImplementationRef {
    pub module: &'static str,
    pub type_name: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WitnessRef {
    pub test: &'static str,
    pub enforced: bool,
}

#[derive(Debug, Clone, Copy)]
pub struct ContractInstance {
    pub id: &'static str,
    pub name: &'static str,
    pub subject_kind: SubjectKind,
    pub version: &'static str,
    pub boundary: Boundary,
    pub totality: Totality,
    pub schema_ref: Option<&'static str>,
    pub implementation: ImplementationRef,
    pub guarantees: &'static [Guarantee],
    pub prohibitions: &'static [Prohibition],
    pub witnesses: &'static [WitnessRef],
    pub admissibility: Admissibility,
    pub details: ContractDetails,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConversionSemantics {
    Preserving,
    Lossy,
    Partial,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PhiBehavior {
    Preserving,
    DeclaredChange,
    Undefined,
}

#[derive(Debug, Clone, Copy)]
pub struct SemanticBasis {
    pub equivalence_relation: &'static str,
    pub invariant_set: &'static [&'static str],
}

#[derive(Debug, Clone, Copy)]
pub struct DomainConstraint {
    pub predicate: &'static str,
    pub witness: &'static str,
}

#[derive(Debug, Clone, Copy)]
pub struct LossProfile {
    pub reversible: bool,
    pub information_loss_basis: &'static str,
}

#[derive(Debug, Clone, Copy)]
pub struct AdapterDetails {
    pub semantics: ConversionSemantics,
    pub semantic_basis: Option<SemanticBasis>,
    pub invariants_preserved: &'static [&'static str],
    pub invariants_dropped: &'static [&'static str],
    pub phi_behavior: PhiBehavior,
    pub loss_profile: Option<LossProfile>,
    pub domain_constraint: Option<DomainConstraint>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EqualityNotion {
    Structural,
    Semantic,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AssociativityMode {
    Strict,
    OrderedOnly,
    NotApplicable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClosureMode {
    Typed,
    Partial,
    ForbiddenAcrossBoundary,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NormalizationStatus {
    None,
    Exists {
        canonical_form: &'static str,
        uniqueness: bool,
    },
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum PhiCompatibility {
    NonIncreasing,
    StrictlyReducing,
    PreservedAcrossAdapter,
    MonotoneBound { factor: f64 },
    Constrained { relation: &'static str },
    Unconstrained,
    NotApplicable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObjectKind {
    BasisOperator,
    Adapter,
    StateSignature,
    Other(&'static str),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MorphismKind {
    TypedMorphism,
    Operator,
    Adapter,
    Signature,
    Other(&'static str),
}

#[derive(Debug, Clone, Copy)]
pub struct Carrier {
    pub objects: &'static [ObjectKind],
    pub morphisms: &'static [MorphismKind],
    pub equality: EqualityNotion,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CompositionRule {
    StrictTyping,
    AdapterMediated,
    ForbiddenAcrossBoundary,
}

#[derive(Debug, Clone, Copy)]
pub struct Signature {
    pub name: &'static str,
}

#[derive(Debug, Clone, Copy)]
pub struct MorphismType {
    pub domain: Signature,
    pub codomain: Signature,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BoundaryLaw {
    pub requires_adapter: bool,
    pub implicit_coercion_forbidden: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AlgebraFailureMode {
    LawNotSatisfied,
    SignatureMismatch,
    NonComposablePair,
    MissingIdentity,
    BoundaryViolation,
    PhiLawViolation,
}

#[derive(Debug, Clone, Copy)]
pub struct AlgebraLawDescriptor {
    pub id: &'static str,
    pub name: &'static str,
    pub carrier: Carrier,
    pub composition_rule: CompositionRule,
    pub associativity: AssociativityMode,
    pub has_identity: bool,
    pub closure: ClosureMode,
    pub normalization: NormalizationStatus,
    pub phi_compatibility: PhiCompatibility,
    pub boundary: BoundaryLaw,
    pub morphism_types: &'static [MorphismType],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransformParameterConstraint {
    /// Disallows parameters that degenerate into a native identity mapping, ensuring
    /// the transform functionally mutates the domain.
    CanonicalIdentityExclusion,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReducerParameterConstraint {
    ExcludesTerminalCollapse,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LambdaParameterConstraint {
    PreservesFiberDimension,
}

#[derive(Debug, Clone, Copy)]
pub struct TransformDetails {
    pub parameter_constraints: &'static [TransformParameterConstraint],
}

#[derive(Debug, Clone, Copy)]
pub struct ReducerDetails {
    pub parameter_constraints: &'static [ReducerParameterConstraint],
}

#[derive(Debug, Clone, Copy)]
pub struct LambdaDetails {
    pub parameter_constraints: &'static [LambdaParameterConstraint],
}

#[derive(Debug, Clone, Copy)]
pub enum ContractDetails {
    None,
    Adapter(AdapterDetails),
    Algebra(AlgebraLawDescriptor),
    Transform(TransformDetails),
    Reducer(ReducerDetails),
    Lambda(LambdaDetails),
}

/// Associates a contract struct with its generated semantic metadata instance.
/// This trait is strictly implemented by codegen, reflecting the YAML source of truth.
pub trait GeneratedContractMetadata {
    const INSTANCE: ContractInstance;
}
