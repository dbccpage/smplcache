use std::any::Any;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ValidatorDescriptor {
    pub validator_id: &'static str,
    pub subject_type_name: &'static str,
    pub property: &'static str,
}

#[derive(Debug, thiserror::Error)]
pub enum ValidationRegistryError {
    #[error("validator not found")]
    ValidatorNotFound,
}

pub trait ErasedValidator: Send + Sync {
    fn descriptor(&self) -> ValidatorDescriptor;
    fn validate_erased(&self, subject: &dyn Any) -> Result<Box<dyn Any>, ValidationRegistryError>;
}
