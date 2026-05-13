// License: Apache-2.0
// Copyright: Copyright 2026 Jeremy Carroll
//! smpl_repair: SQL repair plan generation for smplcache.
//!
//! Takes a DecisionPacket and shape metadata, classifies the repair scenario,
//! and emits executable SQL for SQL Server or PostgreSQL.
//!
//! Supported scenarios:
//!   - Predicate boundary crossing (row enters/leaves WHERE)
//!   - Group key movement (GROUP BY column changed)
//!   - Aggregate delta (SUM column changed, predicate stable)
//!   - Count adjust (COUNT shape, row enters/leaves)
//!   - Invalidation fallback (DELETE cached entry)

use serde::{Deserialize, Serialize};
use smpl_cert::{AggregateFunction, DecisionPacket, Decision, QueryShape};
use smpl_evidence::{BoundaryEvent, Value};

// ─── Repair Scenario ───────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum RepairScenario {
    PredicateBoundaryCrossing {
        pred_col: String,
        old_val: String,
        new_val: String,
    },
    GroupKeyMovement {
        group_col: String,
        old_key: String,
        new_key: String,
    },
    AggregateDelta {
        aggr_col: String,
        old_val: i64,
        new_val: i64,
        delta: i64,
    },
    CountAdjust {
        direction: i8, // +1 or -1
    },
    Invalidation {
        reason: String,
    },
    Preserve,
}

// ─── Repair Plan ───────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RepairPlan {
    pub packet: DecisionPacket,
    pub scenario: RepairScenario,
    pub statements: Vec<String>,
    pub shape_hash: String,
}

// ─── SQL Emitters ──────────────────────────────────────────────

pub trait RepairEmitter {
    fn emit(&self, plan: &RepairPlan, shape: &QueryShape) -> Vec<String>;
}

pub struct SqlServerEmitter;
pub struct PostgresEmitter;

impl SqlServerEmitter {
    fn merge_stmt(shape_hash: &str, group_col: &str, delta: &str) -> String {
        format!(
            "MERGE dbo.cached_aggregates AS target\n\
             USING (SELECT '{shape_hash}' AS shape_hash, @{group_col} AS group_key, {delta} AS delta_value) AS src\n\
             ON target.shape_hash = src.shape_hash\n\
             AND target.group_key = src.group_key\n\
             WHEN MATCHED THEN\n\
             \x20   UPDATE SET value = value + src.delta_value\n\
             WHEN NOT MATCHED THEN\n\
             \x20   INSERT (shape_hash, group_key, value)\n\
             \x20   VALUES (src.shape_hash, src.group_key, src.delta_value);"
        )
    }

    fn update_stmt(shape_hash: &str, group_col: &str, delta: &str) -> String {
        format!(
            "UPDATE dbo.cached_aggregates\n\
             SET value = value + ({delta})\n\
             WHERE shape_hash = '{shape_hash}'\n\
             \x20 AND group_key = @{group_col};"
        )
    }

    fn delete_stmt(shape_hash: &str) -> String {
        format!("DELETE FROM dbo.cached_aggregates WHERE shape_hash = '{shape_hash}';")
    }
}

impl RepairEmitter for SqlServerEmitter {
    fn emit(&self, plan: &RepairPlan, shape: &QueryShape) -> Vec<String> {
        let hash = &plan.shape_hash;
        let group_col = shape
            .group_cols
            .iter()
            .next()
            .map(|s| s.as_str())
            .unwrap_or("group_key");

        match &plan.scenario {
            RepairScenario::Invalidation { .. } => {
                vec![Self::delete_stmt(hash)]
            }
            RepairScenario::Preserve => vec![],
            RepairScenario::AggregateDelta { delta, .. } => {
                vec![Self::update_stmt(hash, group_col, &delta.to_string())]
            }
            RepairScenario::CountAdjust { direction } => {
                vec![Self::update_stmt(hash, group_col, &direction.to_string())]
            }
            RepairScenario::PredicateBoundaryCrossing { .. } => {
                let delta = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{}", shape.aggregate_cols.iter().next().unwrap_or(&"value".to_string())),
                };
                vec![Self::merge_stmt(hash, group_col, &delta)]
            }
            RepairScenario::GroupKeyMovement {
                group_col: gcol,
                ..
            } => {
                let aggr = shape.aggregate_cols.iter().next().map(|s| s.as_str()).unwrap_or("value");
                let delta = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{aggr}"),
                };
                vec![
                    format!(
                        "-- Step 1: subtract from old group key\n\
                         UPDATE dbo.cached_aggregates\n\
                         SET value = value - {delta}\n\
                         WHERE shape_hash = '{hash}'\n\
                         \x20 AND group_key = @old_{gcol};"
                    ),
                    format!(
                        "-- Step 2: add to new group key\n{}",
                        Self::merge_stmt(hash, &format!("new_{gcol}"), &delta)
                    ),
                ]
            }
        }
    }
}

impl PostgresEmitter {
    fn upsert_stmt(shape_hash: &str, group_col: &str, delta: &str) -> String {
        format!(
            "INSERT INTO cached_aggregates (shape_hash, group_key, value)\n\
             VALUES ('{shape_hash}', @{group_col}, {delta})\n\
             ON CONFLICT (shape_hash, group_key)\n\
             DO UPDATE SET value = cached_aggregates.value + EXCLUDED.value;"
        )
    }

    fn update_stmt(shape_hash: &str, group_col: &str, delta: &str) -> String {
        format!(
            "UPDATE cached_aggregates\n\
             SET value = value + ({delta})\n\
             WHERE shape_hash = '{shape_hash}'\n\
             \x20 AND group_key = @{group_col};"
        )
    }

    fn delete_stmt(shape_hash: &str) -> String {
        format!("DELETE FROM cached_aggregates WHERE shape_hash = '{shape_hash}';")
    }
}

impl RepairEmitter for PostgresEmitter {
    fn emit(&self, plan: &RepairPlan, shape: &QueryShape) -> Vec<String> {
        let hash = &plan.shape_hash;
        let group_col = shape
            .group_cols
            .iter()
            .next()
            .map(|s| s.as_str())
            .unwrap_or("group_key");

        match &plan.scenario {
            RepairScenario::Invalidation { .. } => {
                vec![Self::delete_stmt(hash)]
            }
            RepairScenario::Preserve => vec![],
            RepairScenario::AggregateDelta { delta, .. } => {
                vec![Self::update_stmt(hash, group_col, &delta.to_string())]
            }
            RepairScenario::CountAdjust { direction } => {
                vec![Self::update_stmt(hash, group_col, &direction.to_string())]
            }
            RepairScenario::PredicateBoundaryCrossing { .. } => {
                let delta = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{}", shape.aggregate_cols.iter().next().unwrap_or(&"value".to_string())),
                };
                vec![Self::upsert_stmt(hash, group_col, &delta)]
            }
            RepairScenario::GroupKeyMovement {
                group_col: gcol,
                ..
            } => {
                let aggr = shape.aggregate_cols.iter().next().map(|s| s.as_str()).unwrap_or("value");
                let delta = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{aggr}"),
                };
                vec![
                    format!(
                        "-- Step 1: subtract from old group key\n\
                         UPDATE cached_aggregates\n\
                         SET value = value - {delta}\n\
                         WHERE shape_hash = '{hash}'\n\
                         \x20 AND group_key = @old_{gcol};"
                    ),
                    format!(
                        "-- Step 2: add to new group key\n{}",
                        Self::upsert_stmt(hash, &format!("new_{gcol}"), &delta)
                    ),
                ]
            }
        }
    }
}

// ─── Plan Builder ──────────────────────────────────────────────

/// Build a RepairPlan from a DecisionPacket and event data.
pub fn build_plan(
    packet: DecisionPacket,
    shape: &QueryShape,
    event: &BoundaryEvent,
    shape_hash: &str,
) -> RepairPlan {
    let scenario = match packet.decision {
        Decision::Preserve => RepairScenario::Preserve,
        Decision::Invalidate => RepairScenario::Invalidation {
            reason: packet.reason.clone(),
        },
        Decision::Repair => classify_scenario(shape, event),
    };

    RepairPlan {
        packet,
        scenario,
        statements: vec![], // filled by emitter
        shape_hash: shape_hash.to_string(),
    }
}

fn classify_scenario(shape: &QueryShape, event: &BoundaryEvent) -> RepairScenario {
    let group_key_changed = shape.group_cols.iter().any(|c| event.changed_cols.contains(c));
    let predicate_changed = shape.predicate_cols.iter().any(|c| event.changed_cols.contains(c));
    let aggregate_changed = shape.aggregate_cols.iter().any(|c| event.changed_cols.contains(c));

    if group_key_changed {
        let gcol = shape.group_cols.iter().find(|c| event.changed_cols.contains(*c)).unwrap().clone();
        let old_key = event.old.as_ref()
            .and_then(|r| r.get(&gcol))
            .map(val_to_string)
            .unwrap_or_default();
        let new_key = event.new.as_ref()
            .and_then(|r| r.get(&gcol))
            .map(val_to_string)
            .unwrap_or_default();
        RepairScenario::GroupKeyMovement { group_col: gcol, old_key, new_key }
    } else if predicate_changed {
        let pcol = shape.predicate_cols.iter().find(|c| event.changed_cols.contains(*c)).unwrap().clone();
        let old_val = event.old.as_ref()
            .and_then(|r| r.get(&pcol))
            .map(val_to_string)
            .unwrap_or_default();
        let new_val = event.new.as_ref()
            .and_then(|r| r.get(&pcol))
            .map(val_to_string)
            .unwrap_or_default();
        RepairScenario::PredicateBoundaryCrossing { pred_col: pcol, old_val, new_val }
    } else if aggregate_changed {
        let acol = shape.aggregate_cols.iter().find(|c| event.changed_cols.contains(*c)).unwrap().clone();
        if shape.aggregate_function == AggregateFunction::Count {
            RepairScenario::CountAdjust { direction: 0 } // COUNT unaffected by value change
        } else {
            let old_val = event.old.as_ref()
                .and_then(|r| r.get(&acol))
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let new_val = event.new.as_ref()
                .and_then(|r| r.get(&acol))
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            RepairScenario::AggregateDelta {
                aggr_col: acol,
                old_val,
                new_val,
                delta: new_val - old_val,
            }
        }
    } else {
        RepairScenario::PredicateBoundaryCrossing {
            pred_col: "unknown".into(),
            old_val: String::new(),
            new_val: String::new(),
        }
    }
}

fn val_to_string(v: &Value) -> String {
    match v {
        Value::Int(i) => i.to_string(),
        Value::Float(f) => f.to_string(),
        Value::Text(s) => s.clone(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "NULL".into(),
    }
}

// ─── Tests ─────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use smpl_cert::*;
    use smpl_evidence::*;
    use std::collections::BTreeSet;

    fn test_shape() -> QueryShape {
        QueryShape {
            name: "revenue".into(),
            relation: "orders".into(),
            predicate_cols: BTreeSet::from(["status".into()]),
            aggregate_cols: BTreeSet::from(["amount".into()]),
            group_cols: BTreeSet::from(["customer_id".into()]),
            projection_cols: BTreeSet::new(),
            join_cols: BTreeSet::new(),
            security_cols: BTreeSet::new(),
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::SingleTableGroupSum,
            aggregate_function: AggregateFunction::Sum,
        }
    }

    fn repair_packet() -> DecisionPacket {
        DecisionPacket {
            decision: Decision::Repair,
            authority: Authority::Certificate,
            shape_id: "revenue".into(),
            event_relation: "orders".into(),
            evidence_level: EvidenceLevel::E3,
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::SingleTableGroupSum,
            operator: "aggregate_delta".into(),
            reason: "repairable via SINGLE_TABLE_GROUP_SUM".into(),
            proof_tags: vec!["old_new_values_present".into()],
            fallback: Decision::Invalidate,
        }
    }

    #[test]
    fn test_aggregate_delta_sqlserver() {
        let shape = test_shape();
        let event = BoundaryEvent {
            relation: "orders".into(),
            op: Operation::Update,
            changed_cols: BTreeSet::from(["amount".into()]),
            old: Some(RowImage::new().with_col("amount", Value::Int(100))),
            new: Some(RowImage::new().with_col("amount", Value::Int(150))),
            commit_lsn: None,
            evidence_level: EvidenceLevel::E2,
        };

        let plan = build_plan(repair_packet(), &shape, &event, "q_hash_rev");
        assert_eq!(
            plan.scenario,
            RepairScenario::AggregateDelta {
                aggr_col: "amount".into(),
                old_val: 100,
                new_val: 150,
                delta: 50
            }
        );

        let sql = SqlServerEmitter.emit(&plan, &shape);
        assert_eq!(sql.len(), 1);
        assert!(sql[0].contains("value + (50)"));
        assert!(sql[0].contains("q_hash_rev"));
    }

    #[test]
    fn test_invalidation_sqlserver() {
        let shape = test_shape();
        let event = BoundaryEvent {
            relation: "orders".into(),
            op: Operation::Update,
            changed_cols: BTreeSet::from(["amount".into()]),
            old: None,
            new: None,
            commit_lsn: None,
            evidence_level: EvidenceLevel::E0,
        };

        let packet = DecisionPacket {
            decision: Decision::Invalidate,
            authority: Authority::Certificate,
            shape_id: "revenue".into(),
            event_relation: "orders".into(),
            evidence_level: EvidenceLevel::E0,
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::SingleTableGroupSum,
            operator: "evidence_insufficient".into(),
            reason: "insufficient evidence".into(),
            proof_tags: vec![],
            fallback: Decision::Invalidate,
        };

        let plan = build_plan(packet, &shape, &event, "q_hash_rev");
        let sql = SqlServerEmitter.emit(&plan, &shape);
        assert_eq!(sql.len(), 1);
        assert!(sql[0].contains("DELETE"));
        assert!(sql[0].contains("q_hash_rev"));
    }

    #[test]
    fn test_group_key_movement_postgres() {
        let shape = test_shape();
        let event = BoundaryEvent {
            relation: "orders".into(),
            op: Operation::Update,
            changed_cols: BTreeSet::from(["customer_id".into()]),
            old: Some(RowImage::new().with_col("customer_id", Value::Text("c1".into()))),
            new: Some(RowImage::new().with_col("customer_id", Value::Text("c3".into()))),
            commit_lsn: None,
            evidence_level: EvidenceLevel::E2,
        };

        let plan = build_plan(repair_packet(), &shape, &event, "q_hash_rev");
        let sql = PostgresEmitter.emit(&plan, &shape);
        assert_eq!(sql.len(), 2);
        assert!(sql[0].contains("Step 1"));
        assert!(sql[0].contains("old_customer_id"));
        assert!(sql[1].contains("Step 2"));
        assert!(sql[1].contains("new_customer_id"));
    }

    #[test]
    fn test_preserve_emits_nothing() {
        let shape = test_shape();
        let event = BoundaryEvent {
            relation: "orders".into(),
            op: Operation::Update,
            changed_cols: BTreeSet::from(["shipping_address".into()]),
            old: None,
            new: None,
            commit_lsn: None,
            evidence_level: EvidenceLevel::E0,
        };

        let packet = DecisionPacket {
            decision: Decision::Preserve,
            authority: Authority::Certificate,
            shape_id: "revenue".into(),
            event_relation: "orders".into(),
            evidence_level: EvidenceLevel::E0,
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::SingleTableGroupSum,
            operator: "fingerprint_miss".into(),
            reason: "no intersection".into(),
            proof_tags: vec![],
            fallback: Decision::Preserve,
        };

        let plan = build_plan(packet, &shape, &event, "q_hash_rev");
        let sql = SqlServerEmitter.emit(&plan, &shape);
        assert!(sql.is_empty());
    }
}
