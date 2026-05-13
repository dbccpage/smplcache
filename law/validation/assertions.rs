use std::fmt::Debug;
use super::artifact::{ValidationArtifact, ValidationResult};
use super::violation::ValidationViolation;
use crate::law::witness::evidence::ValidationWitness;

pub fn assert_validation_artifact_contract<W, V>(
    artifact: &ValidationArtifact<W, V>
)
where
    W: ValidationWitness + Debug,
    V: ValidationViolation + Debug,
{
    assert!(!artifact.validator_id.is_empty(), "validator_id must be non-empty");
    assert!(!artifact.subject_type.is_empty(), "subject_type must be non-empty");
    assert!(!artifact.property.is_empty(), "property must be non-empty");

    match &artifact.result {
        ValidationResult::Valid(ws) => {
            assert!(!ws.is_empty(), "Valid result must carry non-empty witnesses");
            // Assuming witness has a property method in the new model or we ignore for now
        }
        ValidationResult::Invalid(vs) => {
            assert!(!vs.is_empty(), "Invalid result must carry non-empty violations");
        }
        ValidationResult::DomainError(vs) => {
            assert!(!vs.is_empty(), "DomainError result must carry non-empty violations");
        }
    }
}

