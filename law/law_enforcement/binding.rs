use super::schema::{
    ContractDescriptor, EffectLawDecl, Kind, PhiLawDecl, StructureLawDecl,
};
use core::any::TypeId;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LawBinding<T> {
    Required(T),
    Forbidden,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TypeBinding {
    Concrete(TypeId, &'static str),
    Forbidden,
}

impl TypeBinding {
    pub fn of<T: 'static>(name: &'static str) -> Self {
        Self::Concrete(TypeId::of::<T>(), name)
    }

    pub fn name(&self) -> Option<&'static str> {
        match self {
            Self::Concrete(_, name) => Some(name),
            Self::Forbidden => None,
        }
    }
}

#[derive(Debug)]
pub struct SealedContractDescriptor {
    pub(crate) id: &'static str,
    pub(crate) kind: Kind,

    pub(crate) phi_law: LawBinding<PhiLawDecl>,
    pub(crate) structure_law: LawBinding<StructureLawDecl>,
    pub(crate) effect_law: LawBinding<EffectLawDecl>,

    pub(crate) domain_type: TypeBinding,
    pub(crate) codomain_type: TypeBinding,
    pub(crate) subject_type: TypeBinding,
    pub(crate) runtime_state_type: TypeBinding,

    /// Stable hash computed from canonical schema YAML contents.
    /// Used to detect drift between generated code and YAML source.
    pub(crate) schema_hash: u64,
}

impl SealedContractDescriptor {
    pub fn id(&self) -> &'static str {
        self.id
    }
    pub fn kind(&self) -> &Kind {
        &self.kind
    }
    pub fn phi_law(&self) -> &LawBinding<PhiLawDecl> {
        &self.phi_law
    }
    pub fn structure_law(&self) -> &LawBinding<StructureLawDecl> {
        &self.structure_law
    }
    pub fn effect_law(&self) -> &LawBinding<EffectLawDecl> {
        &self.effect_law
    }
    pub fn domain_type(&self) -> TypeBinding {
        self.domain_type
    }
    pub fn codomain_type(&self) -> TypeBinding {
        self.codomain_type
    }
    pub fn subject_type(&self) -> TypeBinding {
        self.subject_type
    }
    pub fn runtime_state_type(&self) -> TypeBinding {
        self.runtime_state_type
    }
    pub fn schema_hash(&self) -> u64 {
        self.schema_hash
    }
}

/// Only codegen may implement this trait.
///
/// Hand-written user code cannot implement it because
/// `crate::law::law_enforcement::sealed::Sealed` is
/// gated by module visibility.
pub trait ContractImage: crate::law::law_enforcement::sealed::Sealed {
    const DESCRIPTOR: &'static SealedContractDescriptor;
}

// ——————————————————————————————————————————————————————————
// User-facing contract trait
// ——————————————————————————————————————————————————————————

/// Public contract surface.
///
/// Implementors do not declare contract IDs, laws, or type strings
/// manually. They bind themselves to a generated contract image.
///
/// The associated types are checked at audit time against the
/// `TypeBinding` slots in the descriptor.
pub trait BoundContract: ContractImage {
    type Domain: 'static;
    type Codomain: 'static;
    type Subject: 'static;
    type RuntimeState: 'static;
}

/// Bridge trait: marks that type `I` implements contract `C`.
///
/// Used by `impl_lambda!`, `impl_reducer!`, etc. to tie the
/// user's implementation struct to a generated contract image.
pub trait ImplementsContract<C: BoundContract>: Sized {
    fn contract_descriptor() -> &'static SealedContractDescriptor {
        C::DESCRIPTOR
    }
}

/// Compile-time trap for unskippable validation.
/// Ensures the contract binding is evaluated at compile time.
#[macro_export]
macro_rules! require_contract {
    ($contract:ty) => {
        const _: () = {
            fn assert_bound<C: $crate::law::law_enforcement::binding::BoundContract>() {}
            assert_bound::<$contract>();

            // Force type evaluation (monomorphization)
            let _ = <$contract as $crate::law::law_enforcement::binding::ContractImage>::DESCRIPTOR;
        };
    };
}

// ——————————————————————————————————————————————————————————
// Audit errors (forensic-grade)
// ——————————————————————————————————————————————————————————

#[derive(Debug)]
pub enum ContractAuditError {
    KindMismatch {
        contract_id: &'static str,
        expected: String,
        actual: String,
        schema_hash: u64,
    },
    LawMismatch {
        contract_id: &'static str,
        field: &'static str,
        expected: String,
        actual: String,
        schema_hash: u64,
    },
    TypeMismatch {
        contract_id: &'static str,
        field: &'static str,
        schema_hash: u64,
    },
}

impl core::fmt::Display for ContractAuditError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::KindMismatch {
                contract_id,
                expected,
                actual,
                schema_hash,
            } => write!(
                f,
                "CONTRACT VIOLATION [{}] kind mismatch: expected={}, actual={}, schema_hash={:#018x}",
                contract_id, expected, actual, schema_hash
            ),

            Self::LawMismatch {
                contract_id,
                field,
                expected,
                actual,
                schema_hash,
            } => write!(
                f,
                "CONTRACT VIOLATION [{}] {} mismatch: expected={}, actual={}, schema_hash={:#018x}",
                contract_id, field, expected, actual, schema_hash
            ),

            Self::TypeMismatch {
                contract_id,
                field,
                schema_hash,
            } => write!(
                f,
                "CONTRACT VIOLATION [{}] {} type identity mismatch, schema_hash={:#018x}",
                contract_id, field, schema_hash
            ),
        }
    }
}

// ——————————————————————————————————————————————————————————
// Audit helpers
// ——————————————————————————————————————————————————————————

fn ensure_law_eq<T: core::fmt::Debug + PartialEq>(
    contract_id: &'static str,
    field: &'static str,
    expected: &LawBinding<T>,
    actual: &LawBinding<T>,
    schema_hash: u64,
) -> Result<(), ContractAuditError> {
    if expected != actual {
        Err(ContractAuditError::LawMismatch {
            contract_id,
            field,
            expected: format!("{:?}", expected),
            actual: format!("{:?}", actual),
            schema_hash,
        })
    } else {
        Ok(())
    }
}

pub fn ensure_kind(
    expected: Kind,
    actual: Kind,
    id: &'static str,
    hash: u64,
) -> Result<(), ContractAuditError> {
    if expected != actual {
        return Err(ContractAuditError::KindMismatch {
            contract_id: id,
            expected: format!("{:?}", expected),
            actual: format!("{:?}", actual),
            schema_hash: hash,
        });
    }
    Ok(())
}

fn ensure_type_eq(
    contract_id: &'static str,
    field: &'static str,
    expected: TypeBinding,
    actual: TypeBinding,
    schema_hash: u64,
) -> Result<(), ContractAuditError> {
    if expected != actual {
        Err(ContractAuditError::TypeMismatch {
            contract_id,
            field,
            schema_hash,
        })
    } else {
        Ok(())
    }
}

pub fn audit_contract<T>() -> Result<&'static SealedContractDescriptor, ContractAuditError>
where
    T: BoundContract,
{
    let d = T::DESCRIPTOR;

    let to_binding = |expected_binding: TypeBinding, type_id: TypeId| -> TypeBinding {
        if type_id == TypeId::of::<()>() {
            TypeBinding::Forbidden
        } else {
            TypeBinding::Concrete(type_id, expected_binding.name().unwrap_or("unknown"))
        }
    };

    ensure_type_eq(
        d.id,
        "domain_type",
        d.domain_type,
        to_binding(d.domain_type, TypeId::of::<T::Domain>()),
        d.schema_hash,
    )?;

    ensure_type_eq(
        d.id,
        "codomain_type",
        d.codomain_type,
        to_binding(d.codomain_type, TypeId::of::<T::Codomain>()),
        d.schema_hash,
    )?;

    ensure_type_eq(
        d.id,
        "subject_type",
        d.subject_type,
        to_binding(d.subject_type, TypeId::of::<T::Subject>()),
        d.schema_hash,
    )?;

    ensure_type_eq(
        d.id,
        "runtime_state_type",
        d.runtime_state_type,
        to_binding(d.runtime_state_type, TypeId::of::<T::RuntimeState>()),
        d.schema_hash,
    )?;

    Ok(d)
}

/// Audit that implementation `I` correctly binds to contract `C`.
pub fn audit_implementation<I, C>() -> Result<&'static SealedContractDescriptor, ContractAuditError>
where
    I: ImplementsContract<C>,
    C: BoundContract,
{
    let d = I::contract_descriptor();
    debug_assert!(
        core::ptr::eq(d, C::DESCRIPTOR),
        "implementation descriptor must match contract"
    );
    audit_contract::<C>()
}

// ——————————————————————————————————————————————————————————
// Compile-time shape gates
/// Compile-time gate: `T` must be a valid Lambda mapping Domain -> Codomain.
pub fn require_lambda_shape<T, C>()
where
    T: ImplementsContract<C>
        + crate::law::law_enforcement::kinds::LambdaKind
        + crate::law::law_enforcement::traits::LambdaOp<C::Domain, C::Codomain>,
    C: BoundContract,
    C::Domain: crate::law::law_enforcement::kinds::BaseType,
    C::Codomain: crate::law::law_enforcement::kinds::BaseType,
{
}

/// Compile-time gate: `T` must be a valid Transform on Domain -> Domain.
pub fn require_transform_shape<T, C>()
where
    T: ImplementsContract<C>
        + crate::law::law_enforcement::kinds::TransformKind
        + crate::law::law_enforcement::traits::TransformOp<C::Domain>,
    C: BoundContract,
    C::Domain: crate::law::law_enforcement::kinds::BaseType,
{
}

/// Compile-time gate: `T` must be a valid Reducer on Domain -> Domain.
pub fn require_reducer_shape<T, C>()
where
    T: ImplementsContract<C>
        + crate::law::law_enforcement::kinds::ReducerKind
        + crate::law::law_enforcement::traits::ReducerOp<C::Domain>,
    C: BoundContract,
    C::Domain: crate::law::law_enforcement::kinds::BaseType,
{
}

/// Compile-time gate: `T` must be a valid Meta observing Subject.
pub fn require_meta_shape<T, C>()
where
    T: ImplementsContract<C>
        + crate::law::law_enforcement::kinds::MetaKind
        + crate::law::law_enforcement::traits::MetaRule<C::Subject, Output = C::Codomain>,
    C: BoundContract,
{
}
