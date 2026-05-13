use serde::{Deserialize, Serialize};

pub trait TraceArtifact: Clone + Serialize + for<'de> Deserialize<'de> {
    const IMMUTABLE_AFTER_EMIT: bool = true;
}

pub trait ImmutableTrace: TraceArtifact {}
pub trait AnalysisArtifact {}
pub trait ValidationArtifact {}
pub trait DecisionArtifact {}
pub trait ActionResultArtifact {}

/// Sequential trace: an ordered list of step records.
#[derive(Clone, Serialize, Deserialize)]
pub struct StepTrace<T> {
    steps: Vec<T>,
}

impl<T> StepTrace<T> {
    pub fn new(steps: Vec<T>) -> Self {
        Self { steps }
    }

    pub fn steps(&self) -> &[T] {
        &self.steps
    }

    pub fn into_steps(self) -> Vec<T> {
        self.steps
    }
}

impl<T> TraceArtifact for StepTrace<T> where T: Clone + Serialize + for<'de> Deserialize<'de> {}
impl<T> ImmutableTrace for StepTrace<T> where T: Clone + Serialize + for<'de> Deserialize<'de> {}

/// Layered trace: a flat collection of layer records.
/// Not hierarchical â€” use nested `StepTrace<StepTrace<T>>` for true nesting.
#[derive(Clone, Serialize, Deserialize)]
pub struct LayeredTrace<T> {
    layers: Vec<T>,
}

impl<T> LayeredTrace<T> {
    pub fn new(layers: Vec<T>) -> Self {
        Self { layers }
    }

    pub fn layers(&self) -> &[T] {
        &self.layers
    }

    pub fn into_layers(self) -> Vec<T> {
        self.layers
    }
}

impl<T> TraceArtifact for LayeredTrace<T> where T: Clone + Serialize + for<'de> Deserialize<'de> {}
impl<T> ImmutableTrace for LayeredTrace<T> where T: Clone + Serialize + for<'de> Deserialize<'de> {}

/// Sentinel trace for engine/pipeline stages that produce no trace output.
/// Does not bypass artifact guarantees â€” it is the explicit "nothing to trace" marker.
#[derive(Clone, Serialize, Deserialize)]
pub struct VoidTrace;
impl TraceArtifact for VoidTrace {}
impl ImmutableTrace for VoidTrace {}
