use super::artifact::ValidationArtifact;
use super::violation::ValidationViolation;
use crate::law::witness::evidence::ValidationWitness;

pub trait Validator<S> {
    type Witness: ValidationWitness;
    type Violation: ValidationViolation;

    const VALIDATOR_ID: &'static str;
    const SUBJECT_TYPE: &'static str;
    const PROPERTY: &'static str;

    fn validate(subject: &S) -> ValidationArtifact<Self::Witness, Self::Violation>;
}

