#[macro_export]
macro_rules! generate_lambda_witnesses {
    ($impl_ty:ty, $contract:ty) => {
        // ─── Compile-time bound assertions ───────────────────────────────────
        const _: () = {
            // Contract must be a strict lambda contract
            fn assert_strict_lambda<C: $crate::law::contracts::law::StrictLambdaContract>() {}
            fn assert_bound_contract<C: $crate::law::contracts::binding::BoundContract>() {}

            // Domain must implement HasPhi + Arbitrary
            fn assert_domain_phi<T: $crate::law::contracts::witness::capability::HasPhi>() {}
            fn assert_codomain_phi<T: $crate::law::contracts::witness::capability::HasPhi>() {}

            fn _check() {
                assert_strict_lambda::<$contract>();
                assert_bound_contract::<$contract>();
                assert_domain_phi::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>();
                assert_codomain_phi::<<$contract as $crate::law::contracts::binding::BoundContract>::Codomain>();
            }
        };

        #[cfg(test)]
        mod __lambda_property_witnesses {
            use super::*;
            use proptest::prelude::*;

            proptest! {
                #[test]
                fn phi_non_increasing(
                    input in any::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>()
                ) {
                    let op = <$impl_ty>::default();

                    let output = <$impl_ty as $crate::law::traits::operators::LambdaOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                        <$contract as $crate::law::contracts::binding::BoundContract>::Codomain,
                    >>::apply(&op, &input)
                        .expect("contract requires total operator (lambda must not fail)");

                    let before = $crate::law::contracts::witness::capability::HasPhi::phi(&input);
                    let after = $crate::law::contracts::witness::capability::HasPhi::phi(&output);

                    prop_assert!(
                        after <= before,
                        "phi increased: before={:?}, after={:?}, input={:?}",
                        before,
                        after,
                        input
                    );
                }
            }
        }
    };
}

#[macro_export]
macro_rules! generate_reducer_witnesses {
    ($impl_ty:ty, $contract:ty) => {
        // ─── Compile-time bound assertions ───────────────────────────────────
        const _: () = {
            fn assert_strict_reducer<C: $crate::law::contracts::law::StrictReducerContract>() {}
            fn assert_bound_contract<C: $crate::law::contracts::binding::BoundContract>() {}

            fn _check() {
                assert_strict_reducer::<$contract>();
                assert_bound_contract::<$contract>();
            }
        };

        #[cfg(test)]
        mod __reducer_property_witnesses {
            use super::*;
            use proptest::prelude::*;

            proptest! {
                #[test]
                fn reducer_idempotence(
                    input in any::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>()
                ) {
                    let op = <$impl_ty>::default();

                    let once = <$impl_ty as $crate::law::traits::operators::ReducerOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                    >>::apply(&op, &input)
                        .expect("contract requires total operator (reducer must not fail)");

                    let twice = <$impl_ty as $crate::law::traits::operators::ReducerOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                    >>::apply(&op, &once)
                        .expect("contract requires total operator (reducer must not fail on own output)");

                    prop_assert_eq!(
                        once,
                        twice,
                        "reducer is not idempotent: input={:?}",
                        input
                    );
                }
            }
        }
    };
}

#[macro_export]
macro_rules! generate_transform_determinism_witnesses {
    ($impl_ty:ty, $contract:ty) => {
        // ─── Compile-time bound assertions ───────────────────────────────────
        const _: () = {
            fn assert_strict_transform<C: $crate::law::contracts::law::StrictTransformContract>() {}
            fn assert_bound_contract<C: $crate::law::contracts::binding::BoundContract>() {}

            fn _check() {
                assert_strict_transform::<$contract>();
                assert_bound_contract::<$contract>();
            }
        };

        #[cfg(test)]
        mod __transform_property_witnesses {
            use super::*;
            use proptest::prelude::*;

            proptest! {
                #[test]
                fn transform_determinism(
                    input in any::<<$contract as $crate::law::contracts::binding::BoundContract>::Domain>()
                ) {
                    let op = <$impl_ty>::default();

                    let a = <$impl_ty as $crate::law::traits::operators::TransformOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                    >>::apply(&op, &input)
                        .expect("contract requires total operator (transform must not fail)");

                    let b = <$impl_ty as $crate::law::traits::operators::TransformOp<
                        <$contract as $crate::law::contracts::binding::BoundContract>::Domain,
                    >>::apply(&op, &input)
                        .expect("contract requires total operator (transform must not fail on repeat)");

                    prop_assert_eq!(
                        a,
                        b,
                        "transform is not deterministic: input={:?}",
                        input
                    );
                }
            }
        }
    };
}
