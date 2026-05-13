use super::artifact::ValidationArtifact;
use super::violation::ValidationViolation;
use crate::law::witness::evidence::ValidationWitness;

pub struct FileAuditSubject {
    pub path: &'static str,
    pub declared_kind: &'static str,
    pub schema_hash: u64,
    pub parts: &'static [FilePart],
}

pub struct FilePart {
    pub name: &'static str,
    pub required: bool,
    pub kind: &'static str,
}

pub trait FileValidator {
    type Witness: ValidationWitness;
    type Violation: ValidationViolation;

    const FILE_KIND: &'static str;

    fn validate_file(subject: &FileAuditSubject)
        -> ValidationArtifact<Self::Witness, Self::Violation>;
}

