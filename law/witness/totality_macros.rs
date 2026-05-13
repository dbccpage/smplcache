#[macro_export]
macro_rules! generate_totality_witnesses {
    ($impl_ty:ty, $contract:ty, lambda) => {
        #[cfg(test)]
        mod __totality_property_witnesses {
            use super::*;
            use proptest::prelude::*;

            proptest! {
                #[test]
                fn operator_is_total(
                    input in any::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>()
                ) {
                    let op = <$impl_ty>::default();

                    let result = <$impl_ty as $crate::law::traits::operators::LambdaOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                        <$contract as $crate::law::contracts::binding::BoundContract>::Codomain,
                    >>::apply(&op, &input);

                    prop_assert!(
                        result.is_ok(),
                        "operator must be total over domain: input={:?}, error={:?}",
                        input,
                        result.err()
                    );
                }
            }
        }
    };
    ($impl_ty:ty, $contract:ty, reducer) => {
        #[cfg(test)]
        mod __totality_property_witnesses {
            use super::*;
            use proptest::prelude::*;

            proptest! {
                #[test]
                fn operator_is_total(
                    input in any::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>()
                ) {
                    let op = <$impl_ty>::default();

                    let result = <$impl_ty as $crate::law::traits::operators::ReducerOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                    >>::apply(&op, &input);

                    prop_assert!(
                        result.is_ok(),
                        "operator must be total over domain: input={:?}, error={:?}",
                        input,
                        result.err()
                    );
                }
            }
        }
    };
    ($impl_ty:ty, $contract:ty, transform) => {
        #[cfg(test)]
        mod __totality_property_witnesses {
            use super::*;
            use proptest::prelude::*;

            proptest! {
                #[test]
                fn operator_is_total(
                    input in any::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>()
                ) {
                    let op = <$impl_ty>::default();

                    let result = <$impl_ty as $crate::law::traits::operators::TransformOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                    >>::apply(&op, &input);

                    prop_assert!(
                        result.is_ok(),
                        "operator must be total over domain: input={:?}, error={:?}",
                        input,
                        result.err()
                    );
                }
            }
        }
    };
}
