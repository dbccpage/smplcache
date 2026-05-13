#[macro_export]
macro_rules! assert_contract_binding {
    ($ty:ty, $contract:ty) => {{
        let _ = $crate::law::contracts::binding::audit_implementation::<$ty, $contract>().unwrap();
    }};
}

#[macro_export]
macro_rules! __impl_contract_base {
    ($ty:ident, $contract:ty, $kind:ty) => {
        #[derive(Debug, Clone, Copy, Default)]
        pub struct $ty;

        impl $crate::law::contracts::sealed::Sealed for $ty {}
        impl $kind for $ty {}

        impl $crate::law::contracts::binding::ImplementsContract<$contract> for $ty {}

        $crate::require_contract!($contract);

        const _: () = {
            fn assert_impl<I: $crate::law::contracts::binding::ImplementsContract<$contract>>() {}
            assert_impl::<$ty>();
        };
    };
}

#[macro_export]
macro_rules! attach_contract_laws {
    ($ty:ident, $contract:ty, $assert_fn:path) => {
        const _: () = {
            fn check<C>() {
                $assert_fn::<C>();
            }
            fn _run() {
                check::<$contract>();
            }
        };
    };
    ($ty:ident, $contract:ty) => {};
}

#[macro_export]
macro_rules! attach_witness_profile {
    ($ty:ident, $contract:ty) => {
        $crate::law::contracts::generated::generated_contracts::emit_contract_witnesses!($ty, $contract);
    };
}

#[macro_export]
macro_rules! attach_test_harness {
    ($ty:ident, $contract:ty, $shape_check:path) => {
        #[cfg(test)]
        mod __witness_obligations {
            use super::*;

            #[test]
            fn contract_binding_is_valid() {
                $crate::assert_contract_binding!($ty, $contract);
            }

            #[test]
            fn shape_is_valid() {
                $shape_check::<$ty, $contract>();
            }
        }
    };
    ($ty:ident, $contract:ty) => {
        #[cfg(test)]
        mod __witness_obligations {
            use super::*;

            #[test]
            fn contract_binding_is_valid() {
                $crate::assert_contract_binding!($ty, $contract);
            }
        }
    };
}

#[macro_export]
macro_rules! impl_lambda {
    (
        type = $ty:ident,
        contract = $contract:ty,
        apply = |$input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::LambdaKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::LambdaKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::operators::LambdaOp<<$contract as $crate::law::contracts::binding::BoundContract>::Domain, <$contract as $crate::law::contracts::binding::BoundContract>::Codomain> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
                fn apply(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<<$contract as $crate::law::contracts::binding::BoundContract>::Codomain, $crate::types::failure_lambda::LambdaFailure> {
                        $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract, $crate::law::contracts::law::assert_lambda_laws);
        $crate::attach_witness_profile!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract, $crate::law::contracts::binding::require_lambda_shape);
    };
}

#[macro_export]
macro_rules! impl_reducer {
    (
        type = $ty:ident,
        contract = $contract:ty,
        apply = |$input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::ReducerKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::ReducerKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::operators::ReducerOp<<$contract as $crate::law::contracts::binding::BoundContract>::Domain> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
                fn apply(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<<$contract as $crate::law::contracts::binding::BoundContract>::Domain, $crate::types::failure_reducer::ReducerFailure> {
                        $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract, $crate::law::contracts::law::assert_reducer_laws);
        $crate::attach_witness_profile!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract, $crate::law::contracts::binding::require_reducer_shape);
    };
}

#[macro_export]
macro_rules! impl_transform {
    (
        type = $ty:ident,
        contract = $contract:ty,
        apply = |$input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::TransformKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::TransformKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::operators::TransformOp<<$contract as $crate::law::contracts::binding::BoundContract>::Domain> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
                fn apply(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<<$contract as $crate::law::contracts::binding::BoundContract>::Domain, $crate::types::failure_transform::TransformFailure> {
                        $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract, $crate::law::contracts::law::assert_transform_laws);
        $crate::attach_witness_profile!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract, $crate::law::contracts::binding::require_transform_shape);
    };
}

#[macro_export]
macro_rules! impl_meta {
    (
        type = $ty:ident,
        contract = $contract:ty,
        evaluate = |$self:ident, $input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::MetaKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::MetaKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::analysis::MetaRule<<$contract as $crate::law::contracts::binding::BoundContract>::Subject> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn evaluate(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract, $crate::law::contracts::law::assert_meta_laws);
        $crate::attach_witness_profile!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract, $crate::law::contracts::binding::require_meta_shape);
    };
}

#[macro_export]
macro_rules! impl_engine {
    (
        type = $ty:ident,
        contract = $contract:ty,
        step = |$self:ident, $input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::EngineKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::EngineKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::execution::Engine<<$contract as $crate::law::contracts::binding::BoundContract>::Domain> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type RuntimeState = <$contract as $crate::law::contracts::binding::BoundContract>::RuntimeState;
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Domain;
            type Trace = <$contract as $crate::law::contracts::binding::BoundContract>::TraceType;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn step(&mut self, input: <$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<(Self::RuntimeState, Self::Output, Self::Trace), Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract);
    };
}

#[macro_export]
macro_rules! impl_pipeline {
    (
        type = $ty:ident,
        contract = $contract:ty,
        run = |$self:ident, $input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::PipelineKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::PipelineKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::execution::Pipeline<<$contract as $crate::law::contracts::binding::BoundContract>::Domain> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type PipelineState = <$contract as $crate::law::contracts::binding::BoundContract>::RuntimeState;
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Domain;
            type Trace = <$contract as $crate::law::contracts::binding::BoundContract>::TraceType;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn run(&mut self, input: <$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<(Self::PipelineState, Self::Output, Self::Trace), Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract);
    };
}

#[macro_export]
macro_rules! impl_runtime {
    (
        type = $ty:ident,
        contract = $contract:ty,
        start = |$self:ident, $arg:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::RuntimeKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::RuntimeKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::execution::Runtime for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type Input = <$contract as $crate::law::contracts::binding::BoundContract>::Domain;
            type RuntimeState = <$contract as $crate::law::contracts::binding::BoundContract>::RuntimeState;
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;
            type Trace = <$contract as $crate::law::contracts::binding::BoundContract>::TraceType;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn start(&mut self, arg: Self::Input) -> Result<(Self::RuntimeState, Self::Output, Self::Trace), Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract);
    };
}

#[macro_export]
macro_rules! impl_analysis_unit {
    (
        type = $ty:ident,
        contract = $contract:ty,
        analyze = |$self:ident, $input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::AnalysisUnitKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::AnalysisUnitKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::analysis::AnalysisUnit<<$contract as $crate::law::contracts::binding::BoundContract>::Subject> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn analyze(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract, $crate::law::contracts::law::assert_validation_laws);
        $crate::attach_test_harness!($ty, $contract);
    };
}

#[macro_export]
macro_rules! impl_policy_unit {
    (
        type = $ty:ident,
        contract = $contract:ty,
        decide = |$self:ident, $input:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::PolicyUnitKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::PolicyUnitKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::analysis::PolicyUnit<<$contract as $crate::law::contracts::binding::BoundContract>::Subject> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn decide(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract);
    };
}

#[macro_export]
macro_rules! impl_action_executor {
    (
        type = $ty:ident,
        contract = $contract:ty,
        execute = |$self:ident, $decision:ident| $body:expr
    ) => {
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::ActionExecutorKind);

        const _: () = {
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::ActionExecutorKind>() {}
            assert_kind::<$contract>();
        };
        impl $crate::law::traits::operators::OperatorIdentity for $ty {
            fn name(&self) -> &'static str {
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }
        }
        impl $crate::law::traits::analysis::ActionExecutor<<$contract as $crate::law::contracts::binding::BoundContract>::Subject> for $ty where $contract: $crate::law::contracts::binding::BoundContract {
            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;
            type Error = $crate::types::failure_operator::OperatorFailure;
            fn execute(&mut self, decision: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error> {
                let $self = self;
                $body
            }
        }

        $crate::attach_contract_laws!($ty, $contract);
        $crate::attach_test_harness!($ty, $contract);
    };
}
