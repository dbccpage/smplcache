// License: Apache-2.0
// Copyright: Copyright 2026 Jeremy Carroll
//! smpl_evidence: CDC evidence normalization for smplcache.
//!
//! This module normalizes raw CDC/workload inputs into checked evidence packets.
//! It decides what the system actually knows about each boundary event.
//!
//! Evidence levels:
//!   E0 = changed column names only
//!   E1 = changed columns + new values
//!   E2 = non-empty old + new row images.
//!        Required-column sufficiency is checked later by the certificate layer.
//!   E3 = full before/after row images + commit metadata

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

// ─── Core Value Type ───────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Value {
    Int(i64),
    Float(f64),
    Text(String),
    Bool(bool),
    Null,
}

impl Value {
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Value::Int(v) => Some(*v),
            Value::Float(v) => Some(*v as i64),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Text(s) => Some(s.as_str()),
            _ => None,
        }
    }
}

// ─── Evidence Level ────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum EvidenceLevel {
    /// Changed column names only. No row data.
    E0 = 0,
    /// Changed columns + new values. No old image.
    E1 = 1,
    /// Non-empty old + new row images.
    /// Required-column sufficiency is checked later by the certificate layer.
    E2 = 2,
    /// Full before/after row images + commit metadata.
    E3 = 3,
}

impl EvidenceLevel {
    pub fn name(&self) -> &'static str {
        match self {
            EvidenceLevel::E0 => "E0",
            EvidenceLevel::E1 => "E1",
            EvidenceLevel::E2 => "E2",
            EvidenceLevel::E3 => "E3",
        }
    }

    /// Returns true if self provides at least as much evidence as `required`.
    pub fn satisfies(&self, required: EvidenceLevel) -> bool {
        *self >= required
    }
}

// ─── Operation ─────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Operation {
    Insert,
    Update,
    Delete,
}

// ─── Row Image ─────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RowImage {
    pub cols: BTreeMap<String, Value>,
}

impl RowImage {
    pub fn new() -> Self {
        Self {
            cols: BTreeMap::new(),
        }
    }

    pub fn with_col(mut self, name: impl Into<String>, value: Value) -> Self {
        self.cols.insert(name.into(), value);
        self
    }

    pub fn get(&self, col: &str) -> Option<&Value> {
        self.cols.get(col)
    }

    pub fn is_empty(&self) -> bool {
        self.cols.is_empty()
    }
}

impl Default for RowImage {
    fn default() -> Self {
        Self::new()
    }
}

// ─── Evidence Source ───────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum EvidenceSource {
    /// PostgreSQL logical replication (pgoutput)
    PgLogicalReplication,
    /// SQL Server CDC tables
    SqlServerCdc,
    /// Debezium connector
    Debezium,
    /// Workload JSON fixture (for testing)
    WorkloadFixture,
    /// Unknown or custom source
    Custom(String),
}

// ─── Boundary Event ────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BoundaryEvent {
    pub relation: String,
    pub op: Operation,
    pub changed_cols: BTreeSet<String>,
    pub old: Option<RowImage>,
    pub new: Option<RowImage>,
    pub commit_lsn: Option<String>,
    pub evidence_level: EvidenceLevel,
}

// ─── Evidence Packet ───────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidencePacket {
    pub event: BoundaryEvent,
    pub normalized_at_epoch_ms: u64,
    pub source: EvidenceSource,
}

// ─── Evidence Level Inference ──────────────────────────────────

/// Infer the evidence level from the payload contents.
/// This is computed, not declared — the system checks what it actually has.
pub fn infer_evidence_level(
    old: &Option<RowImage>,
    new: &Option<RowImage>,
    commit_lsn: &Option<String>,
) -> EvidenceLevel {
    let has_old = old.as_ref().map_or(false, |r| !r.is_empty());
    let has_new = new.as_ref().map_or(false, |r| !r.is_empty());
    let has_lsn = commit_lsn.is_some();

    match (has_old, has_new, has_lsn) {
        (true, true, true) => EvidenceLevel::E3,
        (true, true, false) => EvidenceLevel::E2,
        (false, true, _) => EvidenceLevel::E1,
        _ => EvidenceLevel::E0,
    }
}

/// Normalize a raw event into an EvidencePacket.
/// The evidence_level on the event is overridden by inference from the actual payload.
pub fn normalize(
    mut event: BoundaryEvent,
    source: EvidenceSource,
    epoch_ms: u64,
) -> EvidencePacket {
    // Always infer — don't trust declared evidence levels
    event.evidence_level = infer_evidence_level(&event.old, &event.new, &event.commit_lsn);

    EvidencePacket {
        event,
        normalized_at_epoch_ms: epoch_ms,
        source,
    }
}

// ─── Tests ─────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_evidence_level_ordering() {
        assert!(EvidenceLevel::E3 > EvidenceLevel::E2);
        assert!(EvidenceLevel::E2 > EvidenceLevel::E1);
        assert!(EvidenceLevel::E1 > EvidenceLevel::E0);
        assert!(EvidenceLevel::E3.satisfies(EvidenceLevel::E2));
        assert!(!EvidenceLevel::E0.satisfies(EvidenceLevel::E2));
    }

    #[test]
    fn test_infer_e3_full_row_and_commit() {
        let old = Some(RowImage::new().with_col("id", Value::Int(1)));
        let new = Some(RowImage::new().with_col("id", Value::Int(1)));
        let lsn = Some("0/1234".to_string());
        assert_eq!(infer_evidence_level(&old, &new, &lsn), EvidenceLevel::E3);
    }

    #[test]
    fn test_infer_e2_old_new_no_lsn() {
        let old = Some(RowImage::new().with_col("amount", Value::Int(100)));
        let new = Some(RowImage::new().with_col("amount", Value::Int(150)));
        assert_eq!(infer_evidence_level(&old, &new, &None), EvidenceLevel::E2);
    }

    #[test]
    fn test_infer_e1_new_only() {
        let new = Some(RowImage::new().with_col("status", Value::Text("paid".into())));
        assert_eq!(infer_evidence_level(&None, &new, &None), EvidenceLevel::E1);
    }

    #[test]
    fn test_infer_e0_no_row_data() {
        assert_eq!(infer_evidence_level(&None, &None, &None), EvidenceLevel::E0);
    }

    #[test]
    fn test_infer_e0_empty_images() {
        let old = Some(RowImage::new());
        let new = Some(RowImage::new());
        assert_eq!(infer_evidence_level(&old, &new, &None), EvidenceLevel::E0);
    }

    #[test]
    fn test_normalize_overrides_declared_level() {
        let event = BoundaryEvent {
            relation: "orders".into(),
            op: Operation::Update,
            changed_cols: BTreeSet::from(["amount".into()]),
            old: None,
            new: None,
            commit_lsn: None,
            evidence_level: EvidenceLevel::E3, // declared high, but payload is empty
        };

        let packet = normalize(event, EvidenceSource::WorkloadFixture, 1000);
        // Inferred level should be E0 because no row data
        assert_eq!(packet.event.evidence_level, EvidenceLevel::E0);
    }

    #[test]
    fn test_row_image_builder() {
        let row = RowImage::new()
            .with_col("customer_id", Value::Text("c1".into()))
            .with_col("amount", Value::Int(100));
        assert_eq!(row.get("customer_id"), Some(&Value::Text("c1".into())));
        assert_eq!(row.get("amount").unwrap().as_i64(), Some(100));
        assert!(!row.is_empty());
    }
}
