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
use smpl_cert::{AggregateFunction, Authority, DecisionPacket, Decision, QueryShape};
use smpl_evidence::{BoundaryEvent, Value};
use std::collections::BTreeMap;

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

// ─── SQL Statement ─────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SqlStatement {
    pub sql: String,
    pub params: BTreeMap<String, Value>,
}

// ─── Repair Plan ───────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RepairPlan {
    pub packet: DecisionPacket,
    pub scenario: RepairScenario,
    pub statements: Vec<SqlStatement>,
    pub shape_hash: String,
}

// ─── SQL Emitters ──────────────────────────────────────────────

pub trait RepairEmitter {
    fn emit(&self, plan: &RepairPlan, shape: &QueryShape) -> Vec<SqlStatement>;
}

pub struct SqlServerEmitter;
pub struct PostgresEmitter;

impl SqlServerEmitter {
    fn merge_stmt(shape_hash: &str, group_col: &str, delta_expr: &str, params: BTreeMap<String, Value>) -> SqlStatement {
        let mut p = params;
        p.insert("shape_hash".into(), Value::Text(shape_hash.into()));
        SqlStatement {
            sql: format!(
                "MERGE dbo.cached_aggregates AS target\n\
                 USING (SELECT @shape_hash AS shape_hash, @{group_col} AS group_key, {delta_expr} AS delta_value) AS src\n\
                 ON target.shape_hash = src.shape_hash\n\
                 AND target.group_key = src.group_key\n\
                 WHEN MATCHED THEN\n\
                 \x20   UPDATE SET value = value + src.delta_value\n\
                 WHEN NOT MATCHED THEN\n\
                 \x20   INSERT (shape_hash, group_key, value)\n\
                 \x20   VALUES (src.shape_hash, src.group_key, src.delta_value);"
            ),
            params: p,
        }
    }

    fn update_stmt(shape_hash: &str, group_col: &str, delta_expr: &str, params: BTreeMap<String, Value>) -> SqlStatement {
        let mut p = params;
        p.insert("shape_hash".into(), Value::Text(shape_hash.into()));
        SqlStatement {
            sql: format!(
                "UPDATE dbo.cached_aggregates\n\
                 SET value = value + ({delta_expr})\n\
                 WHERE shape_hash = @shape_hash\n\
                 \x20 AND group_key = @{group_col};"
            ),
            params: p,
        }
    }

    fn delete_stmt(shape_hash: &str) -> SqlStatement {
        let mut p = BTreeMap::new();
        p.insert("shape_hash".into(), Value::Text(shape_hash.into()));
        SqlStatement {
            sql: "DELETE FROM dbo.cached_aggregates WHERE shape_hash = @shape_hash;".into(),
            params: p,
        }
    }
}

impl RepairEmitter for SqlServerEmitter {
    fn emit(&self, plan: &RepairPlan, shape: &QueryShape) -> Vec<SqlStatement> {
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
                let mut p = BTreeMap::new();
                p.insert("delta".into(), Value::Int(*delta));
                vec![Self::update_stmt(hash, group_col, "@delta", p)]
            }
            RepairScenario::CountAdjust { direction } => {
                let mut p = BTreeMap::new();
                p.insert("delta".into(), Value::Int(*direction as i64));
                vec![Self::update_stmt(hash, group_col, "@delta", p)]
            }
            RepairScenario::PredicateBoundaryCrossing { .. } => {
                let delta_expr = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{}", shape.aggregate_cols.iter().next().unwrap_or(&"value".to_string())),
                };
                vec![Self::merge_stmt(hash, group_col, &delta_expr, BTreeMap::new())]
            }
            RepairScenario::GroupKeyMovement {
                group_col: gcol,
                ..
            } => {
                let aggr = shape.aggregate_cols.iter().next().map(|s| s.as_str()).unwrap_or("value");
                let delta_expr = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{aggr}"),
                };
                let mut p = BTreeMap::new();
                p.insert("shape_hash".into(), Value::Text(hash.into()));
                let mut step2 = Self::merge_stmt(hash, &format!("new_{gcol}"), &delta_expr, BTreeMap::new());
                step2.sql = format!("-- Step 2: add to new group key\n{}", step2.sql);
                vec![
                    SqlStatement {
                        sql: format!(
                            "-- Step 1: subtract from old group key\n\
                             UPDATE dbo.cached_aggregates\n\
                             SET value = value - {delta_expr}\n\
                             WHERE shape_hash = @shape_hash\n\
                             \x20 AND group_key = @old_{gcol};"
                        ),
                        params: p,
                    },
                    step2,
                ]
            }
        }
    }
}

impl PostgresEmitter {
    fn upsert_stmt(shape_hash: &str, group_col: &str, delta_expr: &str, params: BTreeMap<String, Value>) -> SqlStatement {
        let mut p = params;
        p.insert("shape_hash".into(), Value::Text(shape_hash.into()));
        SqlStatement {
            sql: format!(
                "INSERT INTO cached_aggregates (shape_hash, group_key, value)\n\
                 VALUES (@shape_hash, @{group_col}, {delta_expr})\n\
                 ON CONFLICT (shape_hash, group_key)\n\
                 DO UPDATE SET value = cached_aggregates.value + EXCLUDED.value;"
            ),
            params: p,
        }
    }

    fn update_stmt(shape_hash: &str, group_col: &str, delta_expr: &str, params: BTreeMap<String, Value>) -> SqlStatement {
        let mut p = params;
        p.insert("shape_hash".into(), Value::Text(shape_hash.into()));
        SqlStatement {
            sql: format!(
                "UPDATE cached_aggregates\n\
                 SET value = value + ({delta_expr})\n\
                 WHERE shape_hash = @shape_hash\n\
                 \x20 AND group_key = @{group_col};"
            ),
            params: p,
        }
    }

    fn delete_stmt(shape_hash: &str) -> SqlStatement {
        let mut p = BTreeMap::new();
        p.insert("shape_hash".into(), Value::Text(shape_hash.into()));
        SqlStatement {
            sql: "DELETE FROM cached_aggregates WHERE shape_hash = @shape_hash;".into(),
            params: p,
        }
    }
}

impl RepairEmitter for PostgresEmitter {
    fn emit(&self, plan: &RepairPlan, shape: &QueryShape) -> Vec<SqlStatement> {
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
                let mut p = BTreeMap::new();
                p.insert("delta".into(), Value::Int(*delta));
                vec![Self::update_stmt(hash, group_col, "@delta", p)]
            }
            RepairScenario::CountAdjust { direction } => {
                let mut p = BTreeMap::new();
                p.insert("delta".into(), Value::Int(*direction as i64));
                vec![Self::update_stmt(hash, group_col, "@delta", p)]
            }
            RepairScenario::PredicateBoundaryCrossing { .. } => {
                let delta_expr = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{}", shape.aggregate_cols.iter().next().unwrap_or(&"value".to_string())),
                };
                vec![Self::upsert_stmt(hash, group_col, &delta_expr, BTreeMap::new())]
            }
            RepairScenario::GroupKeyMovement {
                group_col: gcol,
                ..
            } => {
                let aggr = shape.aggregate_cols.iter().next().map(|s| s.as_str()).unwrap_or("value");
                let delta_expr = match shape.aggregate_function {
                    AggregateFunction::Count => "1".to_string(),
                    _ => format!("@{aggr}"),
                };
                let mut p = BTreeMap::new();
                p.insert("shape_hash".into(), Value::Text(hash.into()));
                let mut step2 = Self::upsert_stmt(hash, &format!("new_{gcol}"), &delta_expr, BTreeMap::new());
                step2.sql = format!("-- Step 2: add to new group key\n{}", step2.sql);
                vec![
                    SqlStatement {
                        sql: format!(
                            "-- Step 1: subtract from old group key\n\
                             UPDATE cached_aggregates\n\
                             SET value = value - {delta_expr}\n\
                             WHERE shape_hash = @shape_hash\n\
                             \x20 AND group_key = @old_{gcol};"
                        ),
                        params: p,
                    },
                    step2,
                ]
            }
        }
    }
}

// ─── Plan Builder ──────────────────────────────────────────────

enum RowSide {
    Old,
    New,
}

fn required_value<'a>(
    event: &'a BoundaryEvent,
    old_or_new: RowSide,
    col: &str,
) -> Option<&'a Value> {
    match old_or_new {
        RowSide::Old => event.old.as_ref()?.get(col),
        RowSide::New => event.new.as_ref()?.get(col),
    }
}

/// Build a RepairPlan from a DecisionPacket and event data.
pub fn build_plan(
    packet: DecisionPacket,
    shape: &QueryShape,
    event: &BoundaryEvent,
    shape_hash: &str,
) -> RepairPlan {
    if packet.authority != Authority::Certificate {
        return RepairPlan {
            packet,
            scenario: RepairScenario::Invalidation {
                reason: "repair refused: decision packet is not certificate-authority".into(),
            },
            statements: vec![],
            shape_hash: shape_hash.to_string(),
        };
    }

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
        
        let old_key = required_value(event, RowSide::Old, &gcol).map(val_to_string);
        let new_key = required_value(event, RowSide::New, &gcol).map(val_to_string);

        if let (Some(old_key), Some(new_key)) = (old_key, new_key) {
            RepairScenario::GroupKeyMovement { group_col: gcol, old_key, new_key }
        } else {
            RepairScenario::Invalidation { reason: format!("missing old or new group key value for {gcol}") }
        }
    } else if predicate_changed {
        let pcol = shape.predicate_cols.iter().find(|c| event.changed_cols.contains(*c)).unwrap().clone();
        let old_val = required_value(event, RowSide::Old, &pcol).map(val_to_string);
        let new_val = required_value(event, RowSide::New, &pcol).map(val_to_string);
        
        if let (Some(old_val), Some(new_val)) = (old_val, new_val) {
            RepairScenario::PredicateBoundaryCrossing { pred_col: pcol, old_val, new_val }
        } else {
            RepairScenario::Invalidation { reason: format!("missing old or new predicate value for {pcol}") }
        }
    } else if aggregate_changed {
        let acol = shape.aggregate_cols.iter().find(|c| event.changed_cols.contains(*c)).unwrap().clone();
        if shape.aggregate_function == AggregateFunction::Count {
            RepairScenario::CountAdjust { direction: 0 } // COUNT unaffected by value change
        } else {
            let old_val = required_value(event, RowSide::Old, &acol).and_then(|v| v.as_i64());
            let new_val = required_value(event, RowSide::New, &acol).and_then(|v| v.as_i64());
            
            if let (Some(old_val), Some(new_val)) = (old_val, new_val) {
                RepairScenario::AggregateDelta {
                    aggr_col: acol,
                    old_val,
                    new_val,
                    delta: new_val - old_val,
                }
            } else {
                RepairScenario::Invalidation { reason: format!("missing old or new aggregate value for {acol}") }
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
            operator: RepairOperator::AggregateDelta,
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

        let sql_stmts = SqlServerEmitter.emit(&plan, &shape);
        assert_eq!(sql_stmts.len(), 1);
        let stmt = &sql_stmts[0];
        assert!(stmt.sql.contains("value + (@delta)"));
        assert!(stmt.sql.contains("@shape_hash"));
        assert_eq!(stmt.params.get("shape_hash"), Some(&Value::Text("q_hash_rev".into())));
        assert_eq!(stmt.params.get("delta"), Some(&Value::Int(50)));
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
            operator: RepairOperator::EvidenceInsufficient,
            reason: "insufficient evidence".into(),
            proof_tags: vec![],
            fallback: Decision::Invalidate,
        };

        let plan = build_plan(packet, &shape, &event, "q_hash_rev");
        let sql_stmts = SqlServerEmitter.emit(&plan, &shape);
        assert_eq!(sql_stmts.len(), 1);
        assert!(sql_stmts[0].sql.contains("DELETE"));
        assert!(sql_stmts[0].sql.contains("@shape_hash"));
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
        let sql_stmts = PostgresEmitter.emit(&plan, &shape);
        assert_eq!(sql_stmts.len(), 2);
        assert!(sql_stmts[0].sql.contains("Step 1"));
        assert!(sql_stmts[0].sql.contains("old_customer_id"));
        assert!(sql_stmts[1].sql.contains("Step 2"));
        assert!(sql_stmts[1].sql.contains("new_customer_id"));
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
            operator: RepairOperator::FingerprintMiss,
            reason: "no intersection".into(),
            proof_tags: vec![],
            fallback: Decision::Preserve,
        };

        let plan = build_plan(packet, &shape, &event, "q_hash_rev");
        let sql_stmts = SqlServerEmitter.emit(&plan, &shape);
        assert!(sql_stmts.is_empty());
    }

    #[test]
    fn test_non_certificate_refused() {
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

        let mut packet = repair_packet();
        packet.authority = Authority::Diagnostic; // Intentionally lower authority

        let plan = build_plan(packet, &shape, &event, "q_hash_rev");
        assert!(matches!(plan.scenario, RepairScenario::Invalidation { .. }));
    }

    #[test]
    fn test_missing_value_downgrades_to_invalidation() {
        let shape = test_shape();
        let event = BoundaryEvent {
            relation: "orders".into(),
            op: Operation::Update,
            changed_cols: BTreeSet::from(["amount".into()]),
            old: None, // Missing old value!
            new: Some(RowImage::new().with_col("amount", Value::Int(150))),
            commit_lsn: None,
            evidence_level: EvidenceLevel::E2,
        };

        let plan = build_plan(repair_packet(), &shape, &event, "q_hash_rev");
        assert!(matches!(plan.scenario, RepairScenario::Invalidation { .. }));
    }
}
