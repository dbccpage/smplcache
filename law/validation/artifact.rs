use serde::{Deserialize, Serialize};
use super::decision::ValidationDecision;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ValidationResult<W, V> {
    Valid(Vec<W>),
    Invalid(Vec<V>),
    DomainError(Vec<V>),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationArtifact<W, V> {
    pub validator_id: &'static str,
    pub subject_type: &'static str,
    pub property: &'static str,
    pub result: ValidationResult<W, V>,
}
