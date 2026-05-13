import os

"""
Generates Rust macro implementations for base contracts.
"""

output = []

output.append(
    """#[macro_export]
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
"""
)

macros = [
    (
        "impl_lambda",
        "LambdaKind",
        "LambdaOp<<$contract as $crate::law::contracts::binding::BoundContract>::Domain, <$contract as $crate::law::contracts::binding::BoundContract>::Codomain>",
        "apply",
        "(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<<$contract as $crate::law::contracts::binding::BoundContract>::Codomain, $crate::types::failure_lambda::LambdaFailure>",
        "require_lambda_shape",
        "$crate::law::contracts::law::assert_lambda_laws",
    ),
    (
        "impl_reducer",
        "ReducerKind",
        "ReducerOp<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>",
        "apply",
        "(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<<$contract as $crate::law::contracts::binding::BoundContract>::Domain, $crate::types::failure_reducer::ReducerFailure>",
        "require_reducer_shape",
        "$crate::law::contracts::law::assert_reducer_laws",
    ),
    (
        "impl_transform",
        "TransformKind",
        "TransformOp<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>",
        "apply",
        "(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<<$contract as $crate::law::contracts::binding::BoundContract>::Domain, $crate::types::failure_transform::TransformFailure>",
        "require_transform_shape",
        "$crate::law::contracts::law::assert_transform_laws",
    ),
    (
        "impl_meta",
        "MetaKind",
        "MetaRule<<$contract as $crate::law::contracts::binding::BoundContract>::Subject>",
        "evaluate",
        "(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error>",
        "require_meta_shape",
        "$crate::law::contracts::law::assert_meta_laws",
    ),
    (
        "impl_engine",
        "EngineKind",
        "Engine<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>",
        "step",
        "(&mut self, input: <$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<(Self::RuntimeState, Self::Output, Self::Trace), Self::Error>",
        None,
        None,
    ),
    (
        "impl_pipeline",
        "PipelineKind",
        "Pipeline<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>",
        "run",
        "(&mut self, input: <$contract as $crate::law::contracts::binding::BoundContract>::Domain) -> Result<(Self::PipelineState, Self::Output, Self::Trace), Self::Error>",
        None,
        None,
    ),
    (
        "impl_runtime",
        "RuntimeKind",
        "Runtime",
        "start",
        "(&mut self, arg: Self::Input) -> Result<(Self::RuntimeState, Self::Output, Self::Trace), Self::Error>",
        None,
        None,
    ),
    (
        "impl_analysis_unit",
        "AnalysisUnitKind",
        "AnalysisUnit<<$contract as $crate::law::contracts::binding::BoundContract>::Subject>",
        "analyze",
        "(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error>",
        None,
        "$crate::law::contracts::law::assert_validation_laws",
    ),
    (
        "impl_policy_unit",
        "PolicyUnitKind",
        "PolicyUnit<<$contract as $crate::law::contracts::binding::BoundContract>::Subject>",
        "decide",
        "(&self, input: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error>",
        None,
        None,
    ),
    (
        "impl_action_executor",
        "ActionExecutorKind",
        "ActionExecutor<<$contract as $crate::law::contracts::binding::BoundContract>::Subject>",
        "execute",
        "(&mut self, decision: &<$contract as $crate::law::contracts::binding::BoundContract>::Subject) -> Result<Self::Output, Self::Error>",
        None,
        None,
    ),
]

for name, kind, op_trait, fn_name, fn_sig, shape_check, strict_law in macros:
    if name == "impl_engine":
        op_trait_full = f"$crate::law::traits::execution::{op_trait}"
        assoc_types = (
            "type RuntimeState = <$contract as $crate::law::contracts::binding::BoundContract>::RuntimeState;\n"
            "            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Domain;\n"
            "            type Trace = <$contract as $crate::law::contracts::binding::BoundContract>::TraceType;\n"
            "            type Error = $crate::types::failure_operator::OperatorFailure;"
        )
        closure_args = "|$self:ident, $input:ident|"
    elif name == "impl_pipeline":
        op_trait_full = f"$crate::law::traits::execution::{op_trait}"
        assoc_types = (
            "type PipelineState = <$contract as $crate::law::contracts::binding::BoundContract>::RuntimeState;\n"
            "            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Domain;\n"
            "            type Trace = <$contract as $crate::law::contracts::binding::BoundContract>::TraceType;\n"
            "            type Error = $crate::types::failure_operator::OperatorFailure;"
        )
        closure_args = "|$self:ident, $input:ident|"
    elif name == "impl_runtime":
        op_trait_full = "$crate::law::traits::execution::Runtime"
        assoc_types = (
            "type Input = <$contract as $crate::law::contracts::binding::BoundContract>::Domain;\n"
            "            type RuntimeState = <$contract as $crate::law::contracts::binding::BoundContract>::RuntimeState;\n"
            "            type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;\n"
            "            type Trace = <$contract as $crate::law::contracts::binding::BoundContract>::TraceType;\n"
            "            type Error = $crate::types::failure_operator::OperatorFailure;"
        )
        closure_args = "|$self:ident, $arg:ident|"
    elif name in (
        "impl_meta",
        "impl_analysis_unit",
        "impl_policy_unit",
        "impl_action_executor",
    ):
        op_trait_full = f"$crate::law::traits::analysis::{op_trait}"
        assoc_types = (
            "type Output = <$contract as $crate::law::contracts::binding::BoundContract>::Codomain;\n"
            "            type Error = $crate::types::failure_operator::OperatorFailure;"
        )
        if name == "impl_action_executor":
            closure_args = "|$self:ident, $decision:ident|"
        else:
            closure_args = "|$self:ident, $input:ident|"
    else:
        op_trait_full = f"$crate::law::traits::operators::{op_trait}"
        assoc_types = ""
        closure_args = "|$input:ident|"

    if "self" in closure_args:
        closure_decl = closure_args
        let_self = "let $self = self;"
    else:
        closure_decl = closure_args
        let_self = ""

    sig = fn_sig

    id_impl = f"""
        impl $crate::law::traits::operators::OperatorIdentity for $ty {{
            fn name(&self) -> &'static str {{
                <$contract as $crate::law::contracts::binding::ContractImage>::DESCRIPTOR.id()
            }}
        }}"""

    assert_kind = f"""
        const _: () = {{
            fn assert_kind<C: $crate::law::contracts::binding::BoundContract + $crate::law::contracts::kinds::{kind}>() {{}}
            assert_kind::<$contract>();
        }};"""

    shape_call = f", $crate::law::contracts::binding::{shape_check}" if shape_check else ""
    law_call = f", {strict_law}" if strict_law else ""
    witness_call = f"$crate::attach_witness_profile!($ty, $contract);" if name in ("impl_lambda", "impl_reducer", "impl_transform", "impl_meta") else ""

    macro_str = f"""#[macro_export]
macro_rules! {name} {{
    (
        type = $ty:ident,
        contract = $contract:ty,
        {fn_name} = {closure_decl} $body:expr
    ) => {{
        $crate::__impl_contract_base!($ty, $contract, $crate::law::contracts::kinds::{kind});
{assert_kind}{id_impl}
        impl {op_trait_full} for $ty where $contract: $crate::law::contracts::binding::BoundContract {{
            {assoc_types}
            fn {fn_name}{sig} {{
                {let_self}
                $body
            }}
        }}

        $crate::attach_contract_laws!($ty, $contract{law_call});
        {witness_call}
        $crate::attach_test_harness!($ty, $contract{shape_call});
    }};
}}
"""
    # Replace any empty lines where witness_call was empty
    macro_str = macro_str.replace("        \n", "")
    output.append(macro_str)

with open(os.path.join(os.path.dirname(__file__), "macros.rs"), "w", encoding="utf-8") as f:
    f.write("\n".join(output))
