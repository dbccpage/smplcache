pub trait HasPhi {
    fn phi(&self) -> f64;
}

#[deprecated(note = "use proptest Arbitrary instead")]
pub trait SampleState: Sized {
    fn samples() -> Vec<Self>;
}

pub trait WitnessRelation<S> {
    fn preserves(before: &S, after: &S) -> bool;
}
