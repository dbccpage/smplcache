// License: Apache-2.0
// Copyright: Copyright 2026 Jeremy Carroll
//! smpl_cert: Certified decision packets for smplcache.
//!
//! This is the trust kernel. Every cache decision (preserve, repair, invalidate)
//! becomes a checked packet with authority, proof tags, and fallback behavior.
//!
//! The product rule:
//!   PRESERVE certified when boundary and fingerprint do not intersect.
//!   REPAIR certified when evidence is sufficient.
//!   INVALIDATE certified when evidence is insufficient.
//!
//! There is no "I don't know." Every path emits a certificate.

use serde::{Deserialize, Serialize};
use smpl_evidence::{BoundaryEvent, EvidenceLevel, EvidencePacket};
use std::collections::BTreeSet;

// ─── Decision ──────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Decision {
    Preserve,
    Repair,
    Invalidate,
}

impl Decision {
    pub fn name(&self) -> &'static str {
        match self {
            Decision::Preserve => "PRESERVE",
            Decision::Repair => "REPAIR",
            Decision::Invalidate => "INVALIDATE",
        }
    }
}

// ─── Authority ─────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Authority {
    /// Informational only, no action authority.
    Diagnostic,
    /// Suggested action, needs human approval.
    Proposal,
    /// Machine-checkable decision, safe to execute.
    Certificate,
}

impl Authority {
    pub fn name(&self) -> &'static str {
        match self {
            Authority::Diagnostic => "diagnostic",
            Authority::Proposal => "proposal",
            Authority::Certificate => "certificate",
        }
    }
}

// ─── Repair Class ──────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum RepairClass {
    InvalidateOnly,
    SingleTableGroupSum,
    SingleTableGroupCount,
    PredicateBoundaryCrossing,
    GroupKeyMovement,
    KeyPreservingJoinSum,
}

impl RepairClass {
    pub fn is_repairable(&self) -> bool {
        *self != RepairClass::InvalidateOnly
    }

    pub fn name(&self) -> &'static str {
        match self {
            RepairClass::InvalidateOnly => "INVALIDATE_ONLY",
            RepairClass::SingleTableGroupSum => "SINGLE_TABLE_GROUP_SUM",
            RepairClass::SingleTableGroupCount => "SINGLE_TABLE_GROUP_COUNT",
            RepairClass::PredicateBoundaryCrossing => "PREDICATE_BOUNDARY_CROSSING",
            RepairClass::GroupKeyMovement => "GROUP_KEY_MOVEMENT",
            RepairClass::KeyPreservingJoinSum => "KEY_PRESERVING_JOIN_SUM",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum RepairOperator {
    RelationMismatch,
    FingerprintMiss,
    EvidenceInsufficient,
    InvalidateOnlyClass,
    AggregateDelta,
    PredicateBoundaryCrossing,
    GroupKeyMovement,
    GenericRepair,
}

// ─── Aggregate Function ────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AggregateFunction {
    Sum,
    Count,
    Avg,
    Min,
    Max,
    None,
}

// ─── Query Shape ───────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QueryShape {
    pub name: String,
    pub relation: String,
    pub predicate_cols: BTreeSet<String>,
    pub aggregate_cols: BTreeSet<String>,
    pub group_cols: BTreeSet<String>,
    pub projection_cols: BTreeSet<String>,
    pub join_cols: BTreeSet<String>,
    pub security_cols: BTreeSet<String>,
    pub required_evidence: EvidenceLevel,
    pub repair_class: RepairClass,
    pub aggregate_function: AggregateFunction,
}

impl QueryShape {
    /// The dependency fingerprint: the union of all column classes.
    /// If a CDC event's changed_cols intersects this set, the shape is affected.
    pub fn fingerprint(&self) -> BTreeSet<String> {
        let mut fp = BTreeSet::new();
        fp.extend(self.predicate_cols.iter().cloned());
        fp.extend(self.aggregate_cols.iter().cloned());
        fp.extend(self.group_cols.iter().cloned());
        fp.extend(self.projection_cols.iter().cloned());
        fp.extend(self.join_cols.iter().cloned());
        fp.extend(self.security_cols.iter().cloned());
        fp
    }
}

// ─── Decision Packet ───────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionPacket {
    pub decision: Decision,
    pub authority: Authority,
    pub shape_id: String,
    pub event_relation: String,
    pub evidence_level: EvidenceLevel,
    pub required_evidence: EvidenceLevel,
    pub repair_class: RepairClass,
    pub operator: RepairOperator,
    pub reason: String,
    pub proof_tags: Vec<String>,
    pub fallback: Decision,
}

// ─── Proof Tag Collection ──────────────────────────────────────

fn collect_proof_tags(shape: &QueryShape, event: &BoundaryEvent) -> Vec<String> {
    let mut tags = Vec::new();

    if event.old.as_ref().map_or(false, |r| !r.is_empty()) {
        tags.push("old_values_present".into());
    }
    if event.new.as_ref().map_or(false, |r| !r.is_empty()) {
        tags.push("new_values_present".into());
    }
    if event.old.as_ref().map_or(false, |r| !r.is_empty())
        && event.new.as_ref().map_or(false, |r| !r.is_empty())
    {
        tags.push("old_new_values_present".into());
    }

    let changed: BTreeSet<&String> = event.changed_cols.iter().collect();

    if shape.predicate_cols.iter().any(|c| changed.contains(c)) {
        tags.push("predicate_boundary_checked".into());
    }
    if shape.group_cols.iter().any(|c| changed.contains(c)) {
        tags.push("group_key_changed".into());
    } else if !shape.group_cols.is_empty() {
        tags.push("group_key_present".into());
    }
    if !shape.aggregate_cols.is_empty() {
        tags.push("aggregate_column_present".into());
    }

    tags
}

// ─── Classifier ────────────────────────────────────────────────

/// Classify a shape × event pair into a certified DecisionPacket.
///
/// Decision tree:
/// 1. Relation mismatch → PRESERVE certified
/// 2. Fingerprint miss → PRESERVE certified
/// 3. Evidence insufficient → INVALIDATE certified
/// 4. Repair class = InvalidateOnly → INVALIDATE certified
/// 5. Evidence met + repairable class → REPAIR certified
pub fn classify(shape: &QueryShape, packet: &EvidencePacket) -> DecisionPacket {
    let event = &packet.event;

    // 1. Relation mismatch
    if event.relation != shape.relation {
        return DecisionPacket {
            decision: Decision::Preserve,
            authority: Authority::Certificate,
            shape_id: shape.name.clone(),
            event_relation: event.relation.clone(),
            evidence_level: event.evidence_level,
            required_evidence: shape.required_evidence,
            repair_class: shape.repair_class,
            operator: RepairOperator::RelationMismatch,
            reason: "unrelated relation".into(),
            proof_tags: vec!["relation_mismatch".into()],
            fallback: Decision::Preserve,
        };
    }

    // 2. Fingerprint miss
    let fingerprint = shape.fingerprint();
    let intersection: BTreeSet<_> = fingerprint
        .intersection(&event.changed_cols)
        .cloned()
        .collect();

    if intersection.is_empty() {
        return DecisionPacket {
            decision: Decision::Preserve,
            authority: Authority::Certificate,
            shape_id: shape.name.clone(),
            event_relation: event.relation.clone(),
            evidence_level: event.evidence_level,
            required_evidence: shape.required_evidence,
            repair_class: shape.repair_class,
            operator: RepairOperator::FingerprintMiss,
            reason: "changed columns do not intersect fingerprint".into(),
            proof_tags: vec!["fingerprint_miss".into()],
            fallback: Decision::Preserve,
        };
    }

    // 3. Evidence insufficient
    if !event.evidence_level.satisfies(shape.required_evidence) {
        return DecisionPacket {
            decision: Decision::Invalidate,
            authority: Authority::Certificate,
            shape_id: shape.name.clone(),
            event_relation: event.relation.clone(),
            evidence_level: event.evidence_level,
            required_evidence: shape.required_evidence,
            repair_class: shape.repair_class,
            operator: RepairOperator::EvidenceInsufficient,
            reason: format!(
                "insufficient evidence (have {}, need {})",
                event.evidence_level.name(),
                shape.required_evidence.name()
            ),
            proof_tags: vec!["evidence_insufficient".into()],
            fallback: Decision::Invalidate,
        };
    }

    // 4. Invalidate-only class
    if !shape.repair_class.is_repairable() {
        return DecisionPacket {
            decision: Decision::Invalidate,
            authority: Authority::Certificate,
            shape_id: shape.name.clone(),
            event_relation: event.relation.clone(),
            evidence_level: event.evidence_level,
            required_evidence: shape.required_evidence,
            repair_class: shape.repair_class,
            operator: RepairOperator::InvalidateOnlyClass,
            reason: "shape is invalidate-only".into(),
            proof_tags: vec!["repair_class_invalidate_only".into()],
            fallback: Decision::Invalidate,
        };
    }

    // 5. Repair certified
    let proof_tags = collect_proof_tags(shape, event);

    let predicate_changed = shape
        .predicate_cols
        .iter()
        .any(|c| event.changed_cols.contains(c));
    let group_key_changed = shape
        .group_cols
        .iter()
        .any(|c| event.changed_cols.contains(c));
    let aggregate_changed = shape
        .aggregate_cols
        .iter()
        .any(|c| event.changed_cols.contains(c));

    let operator = if group_key_changed {
        RepairOperator::GroupKeyMovement
    } else if predicate_changed {
        RepairOperator::PredicateBoundaryCrossing
    } else if aggregate_changed {
        RepairOperator::AggregateDelta
    } else {
        RepairOperator::GenericRepair
    };

    DecisionPacket {
        decision: Decision::Repair,
        authority: Authority::Certificate,
        shape_id: shape.name.clone(),
        event_relation: event.relation.clone(),
        evidence_level: event.evidence_level,
        required_evidence: shape.required_evidence,
        repair_class: shape.repair_class,
        operator: operator.into(),
        reason: format!("repairable via {}", shape.repair_class.name()),
        proof_tags,
        fallback: Decision::Invalidate,
    }
}

// ─── Tests ─────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use smpl_evidence::*;

    fn revenue_shape() -> QueryShape {
        QueryShape {
            name: "revenue_by_customer_paid".into(),
            relation: "orders".into(),
            predicate_cols: BTreeSet::from(["status".into()]),
            aggregate_cols: BTreeSet::from(["amount".into()]),
            group_cols: BTreeSet::from(["customer_id".into()]),
            projection_cols: BTreeSet::from(["customer_id".into()]),
            join_cols: BTreeSet::new(),
            security_cols: BTreeSet::new(),
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::SingleTableGroupSum,
            aggregate_function: AggregateFunction::Sum,
        }
    }

    fn dashboard_shape() -> QueryShape {
        QueryShape {
            name: "fulfillment_dashboard".into(),
            relation: "orders".into(),
            predicate_cols: BTreeSet::from(["status".into()]),
            aggregate_cols: BTreeSet::new(),
            group_cols: BTreeSet::new(),
            projection_cols: BTreeSet::from([
                "customer_id".into(),
                "status".into(),
                "amount".into(),
            ]),
            join_cols: BTreeSet::new(),
            security_cols: BTreeSet::new(),
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::InvalidateOnly,
            aggregate_function: AggregateFunction::None,
        }
    }

    fn make_packet(
        relation: &str,
        changed: &[&str],
        old: Option<RowImage>,
        new: Option<RowImage>,
    ) -> EvidencePacket {
        let changed_cols: BTreeSet<String> = changed.iter().map(|s| s.to_string()).collect();
        let commit_lsn = None;
        let evidence_level = infer_evidence_level(&old, &new, &commit_lsn);

        EvidencePacket {
            event: BoundaryEvent {
                relation: relation.into(),
                op: Operation::Update,
                changed_cols,
                old,
                new,
                commit_lsn,
                evidence_level,
            },
            normalized_at_epoch_ms: 1000,
            source: EvidenceSource::WorkloadFixture,
        }
    }

    #[test]
    fn test_preserve_different_relation() {
        let shape = revenue_shape();
        let packet = make_packet("inventory", &["quantity"], None, None);
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Preserve);
        assert_eq!(result.authority, Authority::Certificate);
        assert_eq!(result.operator, RepairOperator::RelationMismatch);
    }

    #[test]
    fn test_preserve_fingerprint_miss() {
        let shape = revenue_shape();
        let packet = make_packet(
            "orders",
            &["shipping_address"],
            Some(RowImage::new().with_col("shipping_address", Value::Text("old".into()))),
            Some(RowImage::new().with_col("shipping_address", Value::Text("new".into()))),
        );
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Preserve);
        assert_eq!(result.operator, RepairOperator::FingerprintMiss);
    }

    #[test]
    fn test_invalidate_insufficient_evidence() {
        let shape = revenue_shape();
        // E0: no row data
        let packet = make_packet("orders", &["amount"], None, None);
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Invalidate);
        assert_eq!(result.authority, Authority::Certificate);
        assert_eq!(result.operator, RepairOperator::EvidenceInsufficient);
        assert!(!result.evidence_level.satisfies(shape.required_evidence));
        assert!(result.proof_tags.contains(&"evidence_insufficient".into()));
    }

    #[test]
    fn test_invalidate_only_class() {
        let shape = dashboard_shape();
        let packet = make_packet(
            "orders",
            &["status"],
            Some(RowImage::new().with_col("status", Value::Text("pending".into()))),
            Some(RowImage::new().with_col("status", Value::Text("paid".into()))),
        );
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Invalidate);
        assert_eq!(result.authority, Authority::Certificate);
        assert_eq!(result.operator, RepairOperator::InvalidateOnlyClass);
        assert!(result.proof_tags.contains(&"repair_class_invalidate_only".into()));
    }

    #[test]
    fn test_repair_aggregate_delta() {
        let shape = revenue_shape();
        let packet = make_packet(
            "orders",
            &["amount"],
            Some(RowImage::new().with_col("amount", Value::Int(100))),
            Some(RowImage::new().with_col("amount", Value::Int(150))),
        );
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Repair);
        assert_eq!(result.authority, Authority::Certificate);
        assert_eq!(result.repair_class, RepairClass::SingleTableGroupSum);
        assert_eq!(result.operator, RepairOperator::AggregateDelta);
        assert!(result.proof_tags.contains(&"old_new_values_present".into()));
        assert!(result.proof_tags.contains(&"aggregate_column_present".into()));
    }

    #[test]
    fn test_repair_predicate_boundary_crossing() {
        let shape = revenue_shape();
        let packet = make_packet(
            "orders",
            &["status"],
            Some(
                RowImage::new()
                    .with_col("status", Value::Text("pending".into()))
                    .with_col("amount", Value::Int(80)),
            ),
            Some(
                RowImage::new()
                    .with_col("status", Value::Text("paid".into()))
                    .with_col("amount", Value::Int(80)),
            ),
        );
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Repair);
        assert_eq!(result.operator, RepairOperator::PredicateBoundaryCrossing);
        assert!(result.proof_tags.contains(&"predicate_boundary_checked".into()));
    }

    #[test]
    fn test_repair_group_key_movement() {
        let shape = revenue_shape();
        let packet = make_packet(
            "orders",
            &["customer_id"],
            Some(RowImage::new().with_col("customer_id", Value::Text("c1".into()))),
            Some(RowImage::new().with_col("customer_id", Value::Text("c3".into()))),
        );
        let result = classify(&shape, &packet);
        assert_eq!(result.decision, Decision::Repair);
        assert_eq!(result.operator, RepairOperator::GroupKeyMovement);
        assert!(result.proof_tags.contains(&"group_key_changed".into()));
    }
}
