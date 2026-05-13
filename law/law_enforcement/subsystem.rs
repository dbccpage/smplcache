use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CanonicalTimestamp(pub String);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ArtifactId(pub String);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ContentHash(pub String);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RendererVersion(pub String);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReportSchemaId(pub String);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReportGenerationContext {
    pub generated_at: CanonicalTimestamp,
    pub source_artifact_ids: Vec<ArtifactId>,
    pub source_hashes: Vec<ContentHash>,
    pub renderer_version: RendererVersion,
    pub report_schema_id: ReportSchemaId,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReportAuthority {
    pub may_create_meaning: bool,
    pub may_promote_evidence: bool,
    pub may_certify_transition: bool,
    pub may_rank_for_execution: bool,
    pub may_render_observation: bool,
}

impl Default for ReportAuthority {
    fn default() -> Self {
        Self {
            may_create_meaning: false,
            may_promote_evidence: false,
            may_certify_transition: false,
            may_rank_for_execution: false,
            may_render_observation: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReportManifest {
    pub context: ReportGenerationContext,
    pub authority: ReportAuthority,
    pub rendered_hash: String,
}

impl ReportManifest {
    pub fn dummy() -> Self {
        Self {
            context: ReportGenerationContext {
                generated_at: CanonicalTimestamp("".into()),
                source_artifact_ids: vec![],
                source_hashes: vec![],
                renderer_version: RendererVersion("".into()),
                report_schema_id: ReportSchemaId("".into()),
            },
            authority: ReportAuthority::default(),
            rendered_hash: "".into(),
        }
    }
}
