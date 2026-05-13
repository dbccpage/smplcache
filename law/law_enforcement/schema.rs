use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum ArtifactFamily {
    StructureArtifact,
    FactEnvelope,
    DecisionEnvelope,
    Action,
    Record,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum OperatorClass {
    Kernel,
    Boundary,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum UrosSystem {
    GeneralTheory,
    Kernel,
    ArtifactFamilies,
    Runtime,
    Codex,
    Encyclopedia,
    LevelSystem,
    Tiers,
    OperatorLevels,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum RegistryKind {
    Codex,
    Encyclopedia,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ExpressivityCoordinate {
    pub domain: String,
    pub operator_height: Option<u16>,
    pub meta_height: Option<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct UrosBlock {
    pub system: UrosSystem,
    pub subsystem: String,
    pub artifact_family: ArtifactFamily,
    pub registry: RegistryKind,
    pub domain: String,
    pub tier: u8,
    pub operator_height: Option<u16>,
    pub meta_height: Option<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum AuthorityLayer {
    Structure,
    Fact,
    Judgment,
    Action,
    Record,
    Runtime,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ConstitutionalBlock {
    #[serde(default)]
    pub paper_dependencies: Vec<String>,
    pub emits: ArtifactFamily,
    pub authority_layer: AuthorityLayer,
    #[serde(default)]
    pub forbidden_later_layers: Vec<AuthorityLayer>,
    #[serde(default)]
    pub replay_certificates: Vec<String>,
}

impl ConstitutionalBlock {
    pub fn validate(&self) -> Result<(), String> {
        self.validate_forbidden_mixes()?;
        self.validate_paper_dependencies()?;
        self.validate_certificate_requirements()?;
        Ok(())
    }

    fn validate_forbidden_mixes(&self) -> Result<(), String> {
        // Enforce that later layers are not present in forbidden_later_layers if we already hold that authority
        if self.forbidden_later_layers.contains(&self.authority_layer) {
            return Err("constitution cannot forbid its own authority layer".into());
        }
        
        // Emitted artifact must generally match or precede authority
        match (&self.emits, &self.authority_layer) {
            (ArtifactFamily::Action, AuthorityLayer::Structure) |
            (ArtifactFamily::Action, AuthorityLayer::Fact) => {
                return Err("Structure/Fact authority cannot emit Action artifacts".into());
            }
            (ArtifactFamily::DecisionEnvelope, AuthorityLayer::Structure) |
            (ArtifactFamily::DecisionEnvelope, AuthorityLayer::Fact) => {
                return Err("Structure/Fact authority cannot emit Decision artifacts".into());
            }
            _ => {}
        }
        
        Ok(())
    }

    fn validate_paper_dependencies(&self) -> Result<(), String> {
        // If we are dealing with high-assurance structure/facts, we typically expect paper proofs
        if (self.authority_layer == AuthorityLayer::Structure || self.authority_layer == AuthorityLayer::Fact)
            && self.paper_dependencies.is_empty() {
            return Err("Structure and Fact layers require at least one paper_dependency".into());
        }
        Ok(())
    }

    fn validate_certificate_requirements(&self) -> Result<(), String> {
        if !self.replay_certificates.contains(&"schema_hash".to_string()) {
            return Err("constitution must require 'schema_hash' certificate".into());
        }
        Ok(())
    }
}


#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct SignatureBlock {
    pub input: Option<String>,
    pub output: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct RequiresBlock {
    #[serde(default)]
    pub licenses: Vec<String>,
    #[serde(default)]
    pub declarations: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct ValidationBlock {
    #[serde(default)]
    pub validators: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct WitnessesBlock {
    #[serde(default)]
    pub required: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct ReplayBlock {
    #[serde(default)]
    pub required: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct LawRequirementsBlock {
    #[serde(default)]
    pub required: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum Kind {
    OperatingSystemDoctrine,
    ContractConstitution,
    GlobalTaxonomy,
    BaseType,
    Lambda,
    Reducer,
    Transform,
    Adapter,
    AlgebraLaw,
    ValidationUnit,
    AnalysisUnit,
    MetaRule,
    Engine,
    Pipeline,
    Runtime,
    TraceArtifact,
    PolicyArtifact,
    ActionExecutor,
    BoundaryOp,
    CoboundaryOp,
    EvaluatorUnit,
    SearchUnit,
    SolverUnit,
    DiagnosticUnit,
    ObservableUnit,
    CertificateArtifact,
    HealthUnit,
    MeasurementUnit,
    WitnessArtifact,
    ObstructionUnit,
    LiftUnit,
    ReplayArtifact,
    KernelArtifact,
    ForgetfulOp,
    GaugeOp,
    TensorOp,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum PhiLawDecl {
    NonIncreasing,
    Reducing,
    Unrestricted,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum StructureLawDecl {
    Preserving,
    Reductive,
    Transforming,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum EffectLawDecl {
    Pure,
    ReadOnly,
    Stateful,
}

/// Unambiguous law presence â€” mirrors `LawBinding` in the sealed layer.
///
/// `Required(T)` means the law MUST hold.
/// `Forbidden` means the law MUST NOT be declared.
///
/// There is no "unknown", "not yet decided", or `None` state.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(untagged)]
#[derive(Default)]
pub enum LawPresence<T> {
    Required(T),
    #[default]
    Forbidden,
}

impl<T> LawPresence<T> {
    pub fn is_required(&self) -> bool {
        matches!(self, LawPresence::Required(_))
    }

    pub fn as_option(&self) -> Option<&T> {
        match self {
            LawPresence::Required(v) => Some(v),
            LawPresence::Forbidden => None,
        }
    }
}

/// Bridge: convert from legacy Option for backward compatibility.
impl<T> From<Option<T>> for LawPresence<T> {
    fn from(opt: Option<T>) -> Self {
        match opt {
            Some(v) => LawPresence::Required(v),
            None => LawPresence::Forbidden,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LawBlock {
    #[serde(default)]
    pub phi: LawPresence<PhiLawDecl>,
    #[serde(default)]
    pub structure: LawPresence<StructureLawDecl>,
    #[serde(default)]
    pub effect: LawPresence<EffectLawDecl>,
    #[serde(default)]
    pub required: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConstraintBlock {
    pub total_over_domain: Option<bool>,
    pub deterministic: Option<bool>,
    pub search_forbidden: Option<bool>,
    pub hidden_state_forbidden: Option<bool>,
    pub iteration_forbidden: Option<bool>,
    pub mutation_forbidden: Option<bool>,
    pub read_only: Option<bool>,
    pub typed_output_required: Option<bool>,
    pub operator_execution_forbidden: Option<bool>,
    pub runtime_orchestration_forbidden: Option<bool>,
    pub immutable_after_emit: Option<bool>,
    pub policy_version_required: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContractDescriptor {
    pub id: String,
    pub name: String,
    pub kind: Kind,

    pub domain_type: Option<String>,
    pub codomain_type: Option<String>,
    pub subject_type: Option<String>,
    pub runtime_state_type: Option<String>,

    pub governed_layer: Option<Kind>,
    pub decision_scope: Option<Vec<String>>,

    pub laws: Option<LawBlock>,

    /// Algebra-layer law declarations (string-based, distinct from operator LawBlock).
    #[serde(default)]
    pub algebra_laws: Option<Vec<String>>,

    /// Algebra-layer governed layer label (string-based, e.g. "1_Scalars").
    #[serde(default)]
    pub governed_algebra_layer: Option<String>,

    pub constitution: Option<ConstitutionalBlock>,
    pub operator_class: Option<OperatorClass>,
    pub signature: Option<SignatureBlock>,
    pub requires: Option<RequiresBlock>,
    pub validation: Option<ValidationBlock>,
    pub witnesses: Option<WitnessesBlock>,
    pub replay: Option<ReplayBlock>,
    #[serde(default)]
    pub forbidden: Vec<String>,
    pub uros: Option<UrosBlock>,

    #[serde(default)]
    pub constraints: ConstraintBlock,
}

impl ContractDescriptor {
    pub fn validate(&self) -> Result<(), String> {
        self.validate_common()?;

        match self.kind {
            Kind::BaseType => self.validate_base_type(),
            Kind::Lambda => self.validate_lambda(),
            Kind::Reducer => self.validate_reducer(),
            Kind::Transform => self.validate_transform(),
            Kind::Adapter => self.validate_adapter(),
            Kind::ValidationUnit => self.validate_validation(),
            Kind::AnalysisUnit => self.validate_analysis(),
            Kind::MetaRule => self.validate_meta(),
            Kind::Engine => self.validate_engine(),
            Kind::Pipeline => self.validate_pipeline(),
            Kind::Runtime => self.validate_runtime(),
            Kind::TraceArtifact => self.validate_trace(),
            Kind::PolicyArtifact => self.validate_policy(),
            Kind::ActionExecutor => self.validate_action_executor(),
            Kind::BoundaryOp => self.validate_boundary_op(),
            Kind::CoboundaryOp => self.validate_coboundary_op(),
            Kind::OperatingSystemDoctrine | Kind::ContractConstitution | Kind::GlobalTaxonomy => Ok(()),
            Kind::EvaluatorUnit => Ok(()), // TODO: add strict validation
            Kind::SearchUnit => Ok(()), // TODO: add strict validation
            Kind::SolverUnit => Ok(()), // TODO: add strict validation
            Kind::DiagnosticUnit => Ok(()), // TODO: add strict validation
            Kind::ObservableUnit => Ok(()), // TODO: add strict validation
            Kind::CertificateArtifact => Ok(()), // TODO: add strict validation
            Kind::HealthUnit => Ok(()), // TODO: add strict validation
            Kind::MeasurementUnit => Ok(()), // TODO: add strict validation
            Kind::WitnessArtifact => Ok(()), // TODO: add strict validation
            Kind::ObstructionUnit => Ok(()), // TODO: add strict validation
            Kind::LiftUnit => Ok(()), // TODO: add strict validation
            Kind::ReplayArtifact => Ok(()), // TODO: add strict validation
            Kind::KernelArtifact => Ok(()), // TODO: add strict validation
            Kind::ForgetfulOp => Ok(()), // TODO: add strict validation
            Kind::GaugeOp => Ok(()), // TODO: add strict validation
            Kind::TensorOp => Ok(()), // TODO: add strict validation
            Kind::AlgebraLaw => self.validate_algebra(),
        }
    }

    fn validate_common(&self) -> Result<(), String> {
        if self.id.trim().is_empty() {
            return Err("contract id must not be empty".into());
        }

        if self.name.trim().is_empty() {
            return Err("contract name must not be empty".into());
        }

        if let Some(constitution) = &self.constitution {
            constitution.validate().map_err(|e| format!("constitution error: {}", e))?;
        }

        Ok(())
    }

    fn validate_lambda(&self) -> Result<(), String> {
        let laws = self.laws.as_ref().ok_or("lambda requires laws")?;

        if laws.phi != LawPresence::Required(PhiLawDecl::NonIncreasing) {
            return Err("lambda must have phi=NonIncreasing".into());
        }

        if laws.structure != LawPresence::Required(StructureLawDecl::Preserving) {
            return Err("lambda must have structure=Preserving".into());
        }

        if laws.effect != LawPresence::Required(EffectLawDecl::Pure) {
            return Err("lambda must have effect=Pure".into());
        }

        if self.domain_type.is_none() || self.codomain_type.is_none() {
            return Err("lambda requires domain_type and codomain_type".into());
        }

        if self.constraints.search_forbidden != Some(true) {
            return Err("lambda must forbid search".into());
        }

        if self.constraints.hidden_state_forbidden != Some(true) {
            return Err("lambda must forbid hidden state".into());
        }

        Ok(())
    }

    fn validate_reducer(&self) -> Result<(), String> {
        let laws = self.laws.as_ref().ok_or("reducer requires laws")?;

        if laws.phi != LawPresence::Required(PhiLawDecl::Reducing) {
            return Err("reducer must have phi=Reducing".into());
        }

        if laws.structure != LawPresence::Required(StructureLawDecl::Reductive) {
            return Err("reducer must have structure=Reductive".into());
        }

        if laws.effect != LawPresence::Required(EffectLawDecl::Pure) {
            return Err("reducer must have effect=Pure".into());
        }

        if self.domain_type.is_none() || self.codomain_type.is_none() {
            return Err("reducer requires domain_type and codomain_type".into());
        }

        Ok(())
    }

    fn validate_transform(&self) -> Result<(), String> {
        let laws = self.laws.as_ref().ok_or("transform requires laws")?;

        if laws.phi != LawPresence::Required(PhiLawDecl::Unrestricted) {
            return Err("transform must have phi=Unrestricted".into());
        }

        if laws.structure != LawPresence::Required(StructureLawDecl::Transforming) {
            return Err("transform must have structure=Transforming".into());
        }

        if laws.effect != LawPresence::Required(EffectLawDecl::Pure) {
            return Err("transform must have effect=Pure".into());
        }

        if self.domain_type.is_none() || self.codomain_type.is_none() {
            return Err("transform requires domain_type and codomain_type".into());
        }

        Ok(())
    }

    fn validate_adapter(&self) -> Result<(), String> {
        if self.domain_type.is_none() || self.codomain_type.is_none() {
            return Err("adapter requires source and target types".into());
        }

        if self.laws.is_some() {
            return Err("adapter must not declare operator laws".into());
        }

        if self.subject_type.is_some() {
            return Err("adapter must not declare subject_type".into());
        }

        if self.runtime_state_type.is_some() {
            return Err("adapter must not declare runtime_state_type".into());
        }

        if self.governed_layer.is_some() || self.decision_scope.is_some() {
            return Err("adapter must not declare governed_layer or decision_scope".into());
        }

        Ok(())
    }

    fn validate_validation(&self) -> Result<(), String> {
        if self.subject_type.is_none() {
            return Err("validation unit requires subject_type".into());
        }

        if self.constraints.read_only != Some(true) {
            return Err("validation must be read-only".into());
        }

        if self.constraints.mutation_forbidden != Some(true) {
            return Err("validation must forbid mutation".into());
        }

        if self.constraints.typed_output_required != Some(true) {
            return Err("validation must require typed output".into());
        }

        Ok(())
    }

    fn validate_analysis(&self) -> Result<(), String> {
        if self.subject_type.is_none() {
            return Err("analysis unit requires subject_type".into());
        }

        if self.constraints.read_only != Some(true) {
            return Err("analysis must be read-only".into());
        }

        if self.constraints.operator_execution_forbidden != Some(true) {
            return Err("analysis must not execute operators".into());
        }

        Ok(())
    }

    fn validate_meta(&self) -> Result<(), String> {
        if self.subject_type.is_none() {
            return Err("meta rule requires subject_type".into());
        }

        if self.constraints.operator_execution_forbidden != Some(true) {
            return Err("meta must not execute operators".into());
        }

        if self.constraints.runtime_orchestration_forbidden != Some(true) {
            return Err("meta must not orchestrate runtime".into());
        }

        Ok(())
    }

    fn validate_engine(&self) -> Result<(), String> {
        let laws = self.laws.as_ref().ok_or("engine requires laws")?;

        if laws.effect != LawPresence::Required(EffectLawDecl::Stateful) {
            return Err("engine must have effect=Stateful".into());
        }

        if self.runtime_state_type.is_none() {
            return Err("engine requires runtime_state_type".into());
        }

        if self.constraints.iteration_forbidden == Some(true) {
            return Err("engine must allow iteration".into());
        }

        Ok(())
    }

    fn validate_pipeline(&self) -> Result<(), String> {
        if self.runtime_state_type.is_none() {
            return Err("pipeline requires pipeline_state".into());
        }

        if self.constraints.operator_execution_forbidden != Some(true) {
            return Err("pipeline must not execute operators directly".into());
        }

        Ok(())
    }

    fn validate_runtime(&self) -> Result<(), String> {
        if self.runtime_state_type.is_none() {
            return Err("runtime requires runtime_state_type".into());
        }

        if self.constraints.operator_execution_forbidden != Some(true) {
            return Err("runtime must not execute operators directly".into());
        }

        Ok(())
    }

    fn validate_trace(&self) -> Result<(), String> {
        if self.constraints.immutable_after_emit != Some(true) {
            return Err("trace must be immutable after emit".into());
        }

        if self.constraints.mutation_forbidden != Some(true) {
            return Err("trace must forbid mutation".into());
        }

        Ok(())
    }

    fn validate_policy(&self) -> Result<(), String> {
        if self.governed_layer.is_none() {
            return Err("policy must declare governed_layer".into());
        }

        if self.decision_scope.is_none() {
            return Err("policy must declare decision_scope".into());
        }

        if self.constraints.policy_version_required != Some(true) {
            return Err("policy must declare versioning".into());
        }

        Ok(())
    }


    fn validate_boundary_op(&self) -> Result<(), String> {
        if self.operator_class != Some(OperatorClass::Kernel) {
            return Err("boundary op must be kernel class".into());
        }
        if self.signature.is_none() {
            return Err("boundary op must define a signature".into());
        }
        if self.requires.is_none() {
            return Err("boundary op must define requires block with licenses".into());
        }
        Ok(())
    }


    fn validate_coboundary_op(&self) -> Result<(), String> {
        if self.operator_class != Some(OperatorClass::Kernel) {
            return Err("coboundary op must be kernel class".into());
        }
        if self.signature.is_none() {
            return Err("coboundary op must define a signature".into());
        }
        if self.requires.is_none() {
            return Err("coboundary op must define requires block with licenses".into());
        }
        Ok(())
    }

    fn validate_action_executor(&self) -> Result<(), String> {
        let laws = self.laws.as_ref().ok_or("action executor requires laws")?;

        if laws.effect != LawPresence::Required(EffectLawDecl::Stateful) {
            return Err("action executor must have effect=Stateful".into());
        }

        if self.subject_type.is_none() {
            return Err("action executor requires subject_type".into());
        }

        if self.constraints.typed_output_required != Some(true) {
            return Err("action executor must require typed_output_required".into());
        }

        Ok(())
    }

    fn validate_base_type(&self) -> Result<(), String> {
        if self.domain_type.is_some() || self.codomain_type.is_some() {
            return Err("base type must not declare domain/codomain".into());
        }

        if self.laws.is_some() {
            return Err("base type must not declare laws".into());
        }

        if self.subject_type.is_some() {
            return Err("base type must not declare subject_type".into());
        }

        if self.runtime_state_type.is_some() {
            return Err("base type must not declare runtime_state_type".into());
        }

        if self.governed_layer.is_some() || self.decision_scope.is_some() {
            return Err("base type must not declare governed_layer or decision_scope".into());
        }

        Ok(())
    }

    fn validate_algebra(&self) -> Result<(), String> {
        // Algebra contracts must NOT use operator-layer LawBlock.
        if self.laws.is_some() {
            return Err("algebra law must not use LawBlock (operator layer)".into());
        }

        // Algebra contracts must NOT hold runtime state.
        if self.runtime_state_type.is_some() {
            return Err("algebra law must not declare runtime_state_type".into());
        }

        // Algebra contracts must NOT use the typed governed_layer (Kind).
        if self.governed_layer.is_some() {
            return Err("algebra law must use governed_algebra_layer, not governed_layer".into());
        }

        // Algebra contracts SHOULD declare algebra_laws.
        // domain_type, codomain_type, subject_type are permitted for algebra typing.

        Ok(())
    }
}

pub fn validate_registry(contracts: &[ContractDescriptor]) -> Result<(), String> {
    use std::collections::HashSet;

    let mut ids = HashSet::new();

    for c in contracts {
        c.validate()?;

        if !ids.insert(&c.id) {
            return Err(format!("duplicate contract id: {}", c.id));
        }
    }

    Ok(())
}
