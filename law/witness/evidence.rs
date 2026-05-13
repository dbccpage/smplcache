use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum WitnessClass {
    Structural,
    Behavioral,
    Empirical,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum EvidenceLevel {
    StructuralProof,
    PropertyWitness,
    ExhaustiveFiniteProof,
    StatisticalWitness,
    HumanDeclared,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LawViolation {
    pub property: &'static str,
    pub description: String,
}

pub trait ValidationWitness: core::fmt::Debug + Send + Sync + Serialize + 'static {
    fn property(&self) -> &'static str;
    fn class(&self) -> WitnessClass;
    fn evidence_level(&self) -> EvidenceLevel;
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct PhiMonotonicityWitness {
    pub contract_id: String,
    pub schema_hash: String,
    pub sample_count: usize,
    pub maximum_observed_phi: f64,
    pub minimum_observed_phi: f64,
    pub violations: Vec<LawViolation>,
    pub distribution_metadata: String,
}

impl ValidationWitness for PhiMonotonicityWitness {
    fn property(&self) -> &'static str { "phi_monotonicity" }
    fn class(&self) -> WitnessClass { WitnessClass::Empirical }
    fn evidence_level(&self) -> EvidenceLevel { EvidenceLevel::StatisticalWitness }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct IdempotenceWitness {
    pub contract_id: String,
    pub schema_hash: String,
    pub samples_verified: usize,
}

impl ValidationWitness for IdempotenceWitness {
    fn property(&self) -> &'static str { "idempotence" }
    fn class(&self) -> WitnessClass { WitnessClass::Empirical }
    fn evidence_level(&self) -> EvidenceLevel { EvidenceLevel::StatisticalWitness }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DeterminismWitness {
    pub contract_id: String,
    pub schema_hash: String,
    pub samples_verified: usize,
}

impl ValidationWitness for DeterminismWitness {
    fn property(&self) -> &'static str { "determinism" }
    fn class(&self) -> WitnessClass { WitnessClass::Empirical }
    fn evidence_level(&self) -> EvidenceLevel { EvidenceLevel::StatisticalWitness }
}

pub trait WitnessProducer<S> {
    type Witness: ValidationWitness;
    fn produce(subject: &S) -> Self::Witness;
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct VectorSpaceAdmissibilityWitness {
    pub dimension: usize,
    pub basis_hash: String,
    pub operations_verified: bool,
}

impl ValidationWitness for VectorSpaceAdmissibilityWitness {
    fn property(&self) -> &'static str { "vector_space_admissibility" }
    fn class(&self) -> WitnessClass { WitnessClass::Structural }
    fn evidence_level(&self) -> EvidenceLevel { EvidenceLevel::StructuralProof }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct BasisLinearIndependenceWitness {
    pub dimension: usize,
    pub basis_hash: String,
    pub rank_verified: bool,
}

impl ValidationWitness for BasisLinearIndependenceWitness {
    fn property(&self) -> &'static str { "basis_linear_independence" }
    fn class(&self) -> WitnessClass { WitnessClass::Structural }
    fn evidence_level(&self) -> EvidenceLevel { EvidenceLevel::StructuralProof }
}

impl<T> WitnessProducer<linear_algebra::vector::CoordinateVector<T>> for VectorSpaceAdmissibilityWitness {
    type Witness = Self;
    fn produce(subject: &linear_algebra::vector::CoordinateVector<T>) -> Self::Witness {
        // We call the validation bridge which returns (dimension, basis_id_string)
        // Since CoordinateVector panics/errors on construction if it's invalid, 
        // this is mathematically sound as a total structural proof.
        let (dimension, basis_hash) = subject.verify_admissibility().expect("Vector violated its structural invariants");
        Self {
            dimension,
            basis_hash,
            operations_verified: true, // Structurally guaranteed by the linear_algebra type
        }
    }
}
