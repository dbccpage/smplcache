use crate::law::law_enforcement::binding::BoundContract;
use crate::law::law_enforcement::kinds::{
    AlgebraLawKind, LambdaKind, MetaKind, ReducerKind, TransformKind, ValidationKind,
};

// ─── Axis ────────────────────────────────────────────────────────────────────
pub trait PhiLaw {}
pub trait StructureLaw {}
pub trait EffectLaw {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NonIncreasing;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Reducing;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Unrestricted;

impl PhiLaw for NonIncreasing {}
impl PhiLaw for Reducing {}
impl PhiLaw for Unrestricted {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Preserving;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Reductive;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Transforming;

impl StructureLaw for Preserving {}
impl StructureLaw for Reductive {}
impl StructureLaw for Transforming {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pure;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReadOnly;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Stateful;

impl EffectLaw for Pure {}
impl EffectLaw for ReadOnly {}
impl EffectLaw for Stateful {}

// ─── Operator ────────────────────────────────────────────────────────────────
pub trait StrictLambdaContract: LambdaKind + BoundContract {
    type Phi: PhiLaw;
    type Structure: StructureLaw;
    type Effect: EffectLaw;
}

pub trait StrictReducerContract: ReducerKind + BoundContract {
    type Phi: PhiLaw;
    type Structure: StructureLaw;
    type Effect: EffectLaw;

    const IDEMPOTENT: bool;
}

pub trait StrictTransformContract: TransformKind + BoundContract {
    type Phi: PhiLaw;
    type Structure: StructureLaw;
    type Effect: EffectLaw;
}

// ─── Observer ────────────────────────────────────────────────────────────────
pub trait StrictMetaContract: MetaKind + BoundContract {
    type Effect: EffectLaw;

    const STATELESS: bool;
    const OPERATOR_EXECUTION_FORBIDDEN: bool;
    const RUNTIME_ORCHESTRATION_FORBIDDEN: bool;
}

pub trait StrictValidationContract: ValidationKind + BoundContract {
    type Effect: EffectLaw;

    const READ_ONLY: bool;
    const MUTATION_FORBIDDEN: bool;
    const TYPED_OUTPUT_REQUIRED: bool;
}

// ─── Algebra ─────────────────────────────────────────────────────────────────
pub trait StrictAlgebraContract: AlgebraLawKind + BoundContract {
    const DECLARATIVE_ONLY: bool;
    const NO_EXECUTION: bool;
    const NO_HIDDEN_STATE: bool;
    const NO_RUNTIME_DEPENDENCY: bool;
}

// ─── Assertions ──────────────────────────────────────────────────────────────
pub trait SameLaw<T> {}

impl<T> SameLaw<T> for T {}

pub fn assert_lambda_laws<C>()
where
    C: StrictLambdaContract<
        Phi = NonIncreasing,
        Structure = Preserving,
        Effect = Pure,
    >,
{
}

pub fn assert_reducer_laws<C>()
where
    C: StrictReducerContract<
        Phi = Reducing,
        Structure = Reductive,
        Effect = Pure,
    >,
{
}

pub fn assert_transform_laws<C>()
where
    C: StrictTransformContract<
        Phi = Unrestricted,
        Structure = Transforming,
        Effect = Pure,
    >,
{
}

pub fn assert_meta_laws<C>()
where
    C: StrictMetaContract<Effect = ReadOnly>,
{
}

pub fn assert_validation_laws<C>()
where
    C: StrictValidationContract<Effect = ReadOnly>,
{
}

