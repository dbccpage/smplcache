use crate::law::law_enforcement::kinds::{LambdaKind, ReducerKind, TransformKind};
use crate::law::law_enforcement::kinds::BaseType;
use crate::law::witness::capability::{HasPhi, SampleState};
use crate::law::witness::evidence::{DeterminismWitness, IdempotenceWitness, LawViolation, PhiMonotonicityWitness};
use crate::law::law_enforcement::traits::{LambdaOp, ReducerOp, TransformOp};

#[allow(deprecated)]
pub fn produce_lambda_phi_witness<T, S>() -> Result<PhiMonotonicityWitness, LawViolation>
where
    T: Default + LambdaKind + LambdaOp<S, S>,
    S: BaseType + HasPhi + SampleState,
{
    let op = T::default();
    let mut max_before = f64::NEG_INFINITY;
    let mut max_after = f64::NEG_INFINITY;
    let mut min_phi = f64::INFINITY;
    let mut samples_verified = 0;

    for sample in S::samples() {
        let before = sample.phi();
        let output = op
            .apply(&sample)
            .map_err(|_| LawViolation { property: "phi_monotonicity", description: "lambda operator failed on sample".into() })?;
        let after = output.phi();

        if after > before {
            return Err(LawViolation {
                property: "phi_monotonicity",
                description: format!("lambda increased phi: before={}, after={}", before, after)
            });
        }
        max_before = max_before.max(before);
        max_after = max_after.max(after);
        min_phi = min_phi.min(before).min(after);
        samples_verified += 1;
    }

    Ok(PhiMonotonicityWitness { 
        contract_id: std::any::type_name::<T>().to_string(),
        schema_hash: "empirical_fallback".to_string(),
        sample_count: samples_verified,
        maximum_observed_phi: max_before.max(max_after),
        minimum_observed_phi: min_phi,
        violations: vec![],
        distribution_metadata: "SampleState generic iterator".to_string()
    })
}

#[allow(deprecated)]
pub fn produce_reducer_idempotence_witness<T, S>() -> Result<IdempotenceWitness, LawViolation>
where
    T: Default + ReducerKind + ReducerOp<S>,
    S: BaseType + SampleState + PartialEq + core::fmt::Debug,
{
    let op = T::default();
    let mut samples_verified = 0;

    for sample in S::samples() {
        let once = op
            .apply(&sample)
            .map_err(|_| LawViolation { property: "idempotence", description: "reducer failed on initial sample".into() })?;
        let twice = op
            .apply(&once)
            .map_err(|_| LawViolation { property: "idempotence", description: "reducer failed on own output".into() })?;

        if once != twice {
            return Err(LawViolation { property: "idempotence", description: "reducer is not idempotent".into() });
        }
        samples_verified += 1;
    }

    Ok(IdempotenceWitness { 
        contract_id: std::any::type_name::<T>().to_string(),
        schema_hash: "empirical_fallback".to_string(),
        samples_verified 
    })
}

#[allow(deprecated)]
pub fn produce_transform_determinism_witness<T, S>() -> Result<DeterminismWitness, LawViolation>
where
    T: Default + TransformKind + TransformOp<S>,
    S: BaseType + SampleState + PartialEq + core::fmt::Debug + Clone,
{
    let op = T::default();
    let mut samples_verified = 0;

    for sample in S::samples() {
        let first = op
            .apply(&sample)
            .map_err(|_| LawViolation { property: "determinism", description: "transform failed on sample".into() })?;
        let second = op
            .apply(&sample)
            .map_err(|_| LawViolation { property: "determinism", description: "transform failed on repeat".into() })?;

        if first != second {
            return Err(LawViolation { property: "determinism", description: "transform is not deterministic".into() });
        }
        samples_verified += 1;
    }

    Ok(DeterminismWitness { 
        contract_id: std::any::type_name::<T>().to_string(),
        schema_hash: "empirical_fallback".to_string(),
        samples_verified 
    })
}

