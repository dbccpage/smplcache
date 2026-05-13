use serde::{Deserialize, Serialize};

/// Canonical representation of mathematical structure inside the engine.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StructureArtifact {
    pub id: String,
    pub source_id: Option<String>,
}

/// Certified evidence of fact observation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FactEnvelope {
    pub id: String,
    pub facts: Vec<String>,
}

/// Certified resolution of policy or governance judgement.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecisionEnvelope {
    pub id: String,
    pub decision: String,
}

/// An execution artifact produced by operator-mediated state changes.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Action {
    pub id: String,
    pub action_type: String,
}

/// An unmodifiable, execution-neutral logging artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Record {
    pub id: String,
    pub timestamp: u64,
}

/// Orchestration boundary artifact produced by adapter wrappers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrchestrationArtifact {
    pub id: String,
    pub stage: String,
    pub details: String,
}
