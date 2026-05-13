// License: Apache-2.0
// Copyright: Copyright 2026 Jeremy Carroll
//! smpl_geometry: Workload structure discovery and lift proposals.
//!
//! This module discovers persistent coupling structure across shapes and events,
//! identifies cache hotspots, and proposes schema/query lifts to reduce invalidation.
//!
//! Public names:
//!   Invalidation Graph, Cache Hotspot, Invalidation Cycles,
//!   Invalidation Skew, Schema Lift Proposal
//!
//! Internal math:
//!   coupling graph, connected components (union-find), cycle counting (Euler),
//!   entropy, eigenvector centrality (power iteration), lift witness

use serde::{Deserialize, Serialize};
use smpl_cert::QueryShape;
use smpl_evidence::EvidencePacket;
use std::collections::{BTreeMap, HashMap};

// ─── Coupling Graph ────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CouplingEdge {
    pub left: usize,
    pub right: usize,
    pub weight: f64,
    pub event_count: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CouplingGraph {
    pub nodes: Vec<String>,
    pub edges: Vec<CouplingEdge>,
}

// ─── Cache Hotspot ─────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CacheHotspot {
    pub shape_id: String,
    pub invalidation_count: usize,
    pub repair_count: usize,
    pub hotspot_score: f64,
}

// ─── Invalidation Graph Report ─────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InvalidationGraphReport {
    pub components: Vec<Vec<String>>,
    pub cycles: usize,
    pub coupling_score: f64,
    pub invalidation_skew: f64,
    pub hotspots: Vec<CacheHotspot>,
    pub graph: CouplingGraph,
}

// ─── Lift Actions ──────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum LiftAction {
    SplitColumn {
        relation: String,
        column: String,
        into: Vec<String>,
    },
    SplitShape {
        shape_id: String,
        regimes: Vec<String>,
    },
    AddProjection {
        relation: String,
        keys: Vec<String>,
        includes: Vec<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LiftProposal {
    pub action: LiftAction,
    pub witness: Vec<String>,
    pub expected_reduction_pct: f64,
}

// ─── Union-Find ────────────────────────────────────────────────

struct UnionFind {
    parent: Vec<usize>,
    rank: Vec<usize>,
}

impl UnionFind {
    fn new(n: usize) -> Self {
        Self {
            parent: (0..n).collect(),
            rank: vec![0; n],
        }
    }

    fn find(&mut self, mut x: usize) -> usize {
        while self.parent[x] != x {
            self.parent[x] = self.parent[self.parent[x]];
            x = self.parent[x];
        }
        x
    }

    fn union(&mut self, a: usize, b: usize) -> bool {
        let ra = self.find(a);
        let rb = self.find(b);
        if ra == rb {
            return false;
        }
        if self.rank[ra] < self.rank[rb] {
            self.parent[ra] = rb;
        } else if self.rank[ra] > self.rank[rb] {
            self.parent[rb] = ra;
        } else {
            self.parent[rb] = ra;
            self.rank[ra] += 1;
        }
        true
    }

    fn components(&mut self, n: usize) -> Vec<Vec<usize>> {
        let mut comp_map: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for i in 0..n {
            let root = self.find(i);
            comp_map.entry(root).or_default().push(i);
        }
        comp_map.into_values().collect()
    }
}

// ─── Entropy ───────────────────────────────────────────────────

fn entropy(counts: &[usize]) -> f64 {
    let total: usize = counts.iter().sum();
    if total == 0 {
        return 0.0;
    }
    let mut ent = 0.0f64;
    for &c in counts {
        if c > 0 {
            let p = c as f64 / total as f64;
            ent -= p * p.log2();
        }
    }
    ent
}

// ─── Analyze Coupling ──────────────────────────────────────────

/// Build the coupling graph and report from shapes and events.
pub fn analyze_coupling(
    shapes: &[QueryShape],
    events: &[EvidencePacket],
) -> InvalidationGraphReport {
    let n = shapes.len();
    let shape_names: Vec<String> = shapes.iter().map(|s| s.name.clone()).collect();
    let _shape_idx: HashMap<&str, usize> = shapes
        .iter()
        .enumerate()
        .map(|(i, s)| (s.name.as_str(), i))
        .collect();

    // Count invalidations per shape
    let mut inv_counts: Vec<usize> = vec![0; n];

    // Track which shapes are co-invalidated per event
    let mut edge_counts: BTreeMap<(usize, usize), usize> = BTreeMap::new();

    for event in events {
        let mut affected: Vec<usize> = Vec::new();
        for (i, shape) in shapes.iter().enumerate() {
            if shape.relation == event.event.relation {
                let fp = shape.fingerprint();
                if fp.intersection(&event.event.changed_cols).next().is_some() {
                    affected.push(i);
                    inv_counts[i] += 1;
                }
            }
        }
        // Build edges between co-invalidated shapes
        for a in 0..affected.len() {
            for b in (a + 1)..affected.len() {
                let key = (affected[a], affected[b]);
                *edge_counts.entry(key).or_insert(0) += 1;
            }
        }
    }

    // Build coupling graph
    let edges: Vec<CouplingEdge> = edge_counts
        .iter()
        .map(|(&(l, r), &count)| CouplingEdge {
            left: l,
            right: r,
            weight: count as f64,
            event_count: count,
        })
        .collect();

    let graph = CouplingGraph {
        nodes: shape_names.clone(),
        edges: edges.clone(),
    };

    // Connected components via union-find
    let mut uf = UnionFind::new(n);
    for e in &edges {
        uf.union(e.left, e.right);
    }
    let components: Vec<Vec<String>> = uf
        .components(n)
        .into_iter()
        .map(|comp| comp.into_iter().map(|i| shape_names[i].clone()).collect())
        .collect();

    let num_components = components.len();

    // Cycle count: E - V + C (Euler characteristic for graphs)
    let active_nodes = shape_names
        .iter()
        .enumerate()
        .filter(|(i, _)| inv_counts[*i] > 0)
        .count();
    let e_count = edges.len();
    let cycles = if e_count >= active_nodes {
        e_count - active_nodes + num_components
    } else {
        0
    };

    // Coupling score: density relative to complete graph
    let max_edges = if n > 1 { n * (n - 1) / 2 } else { 1 };
    let coupling_score = (e_count as f64 / max_edges as f64) * 100.0;

    // Invalidation skew (entropy)
    let invalidation_skew = entropy(&inv_counts);

    // Hotspots: shapes sorted by invalidation count (descending)
    let total_inv: usize = inv_counts.iter().sum();
    let mut hotspots: Vec<CacheHotspot> = shapes
        .iter()
        .enumerate()
        .filter(|(i, _)| inv_counts[*i] > 0)
        .map(|(i, s)| {
            let score = if total_inv > 0 {
                inv_counts[i] as f64 / total_inv as f64
            } else {
                0.0
            };
            CacheHotspot {
                shape_id: s.name.clone(),
                invalidation_count: inv_counts[i],
                repair_count: 0, // enriched by caller if needed
                hotspot_score: score,
            }
        })
        .collect();
    hotspots.sort_by(|a, b| b.hotspot_score.partial_cmp(&a.hotspot_score).unwrap());

    InvalidationGraphReport {
        components,
        cycles,
        coupling_score,
        invalidation_skew,
        hotspots,
        graph,
    }
}

// ─── Lift Proposals ────────────────────────────────────────────

/// Propose schema/query lifts based on coupling analysis.
pub fn propose_lifts(
    report: &InvalidationGraphReport,
    shapes: &[QueryShape],
) -> Vec<LiftProposal> {
    let mut proposals = Vec::new();

    // Proposal 1: Columns that appear in multiple shapes' predicate sets
    // and drive high coupling are candidates for column splits.
    let mut col_shapes: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for shape in shapes {
        for col in &shape.predicate_cols {
            col_shapes
                .entry(col.clone())
                .or_default()
                .push(shape.name.clone());
        }
    }

    for (col, shape_names) in &col_shapes {
        if shape_names.len() >= 2 {
            // Check if these shapes are in the same coupling component
            let in_same_component = report.components.iter().any(|comp| {
                shape_names.iter().filter(|s| comp.contains(s)).count() >= 2
            });

            if in_same_component {
                proposals.push(LiftProposal {
                    action: LiftAction::SplitColumn {
                        relation: shapes
                            .iter()
                            .find(|s| s.predicate_cols.contains(col))
                            .map(|s| s.relation.clone())
                            .unwrap_or_default(),
                        column: col.clone(),
                        into: shape_names
                            .iter()
                            .map(|s| format!("{}_{}", col, s))
                            .collect(),
                    },
                    witness: shape_names.clone(),
                    expected_reduction_pct: (shape_names.len() as f64 - 1.0)
                        / shape_names.len() as f64
                        * 100.0,
                });
            }
        }
    }

    // Proposal 2: Hotspot shapes with high invalidation score
    // may benefit from projection narrowing.
    for hotspot in &report.hotspots {
        if hotspot.hotspot_score > 0.3 {
            if let Some(shape) = shapes.iter().find(|s| s.name == hotspot.shape_id) {
                if !shape.projection_cols.is_empty() {
                    let keys: Vec<String> = shape.predicate_cols.iter().cloned().collect();
                    let includes: Vec<String> = shape.projection_cols.iter().cloned().collect();
                    if !keys.is_empty() {
                        proposals.push(LiftProposal {
                            action: LiftAction::AddProjection {
                                relation: shape.relation.clone(),
                                keys,
                                includes,
                            },
                            witness: vec![shape.name.clone()],
                            expected_reduction_pct: hotspot.hotspot_score * 50.0,
                        });
                    }
                }
            }
        }
    }

    proposals
}

// ─── Tests ─────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use smpl_cert::*;
    use smpl_evidence::*;
    use std::collections::BTreeSet;

    fn make_shape(name: &str, relation: &str, pred_cols: &[&str], agg_cols: &[&str], group_cols: &[&str]) -> QueryShape {
        QueryShape {
            name: name.into(),
            relation: relation.into(),
            predicate_cols: pred_cols.iter().map(|s| s.to_string()).collect(),
            aggregate_cols: agg_cols.iter().map(|s| s.to_string()).collect(),
            group_cols: group_cols.iter().map(|s| s.to_string()).collect(),
            projection_cols: BTreeSet::new(),
            join_cols: BTreeSet::new(),
            security_cols: BTreeSet::new(),
            required_evidence: EvidenceLevel::E2,
            repair_class: RepairClass::SingleTableGroupSum,
            aggregate_function: AggregateFunction::Sum,
        }
    }

    fn make_event(relation: &str, changed: &[&str]) -> EvidencePacket {
        EvidencePacket {
            event: BoundaryEvent {
                relation: relation.into(),
                op: Operation::Update,
                changed_cols: changed.iter().map(|s| s.to_string()).collect(),
                old: Some(RowImage::new().with_col("dummy", Value::Int(0))),
                new: Some(RowImage::new().with_col("dummy", Value::Int(1))),
                commit_lsn: None,
                evidence_level: EvidenceLevel::E2,
            },
            normalized_at_epoch_ms: 1000,
            source: EvidenceSource::WorkloadFixture,
        }
    }

    #[test]
    fn test_coupling_graph_basic() {
        let shapes = vec![
            make_shape("revenue", "orders", &["status"], &["amount"], &["customer_id"]),
            make_shape("dashboard", "orders", &["status"], &[], &[]),
            make_shape("inventory", "inventory", &[], &["quantity"], &["item_id"]),
        ];

        let events = vec![
            make_event("orders", &["status"]),
            make_event("orders", &["status"]),
            make_event("inventory", &["quantity"]),
        ];

        let report = analyze_coupling(&shapes, &events);

        // revenue + dashboard co-invalidated by "status" changes
        assert!(report.graph.edges.len() >= 1);
        // inventory is in a separate component
        assert!(report.components.len() >= 2);
    }

    #[test]
    fn test_cycle_detection() {
        let shapes = vec![
            make_shape("a", "t", &["x"], &[], &[]),
            make_shape("b", "t", &["x"], &[], &[]),
            make_shape("c", "t", &["x"], &[], &[]),
        ];

        // All three shapes co-invalidated → triangle → 1 cycle
        let events = vec![
            make_event("t", &["x"]),
        ];

        let report = analyze_coupling(&shapes, &events);
        // 3 edges (a-b, a-c, b-c), 3 active nodes, 1 component
        // cycles = E - V + C = 3 - 3 + 1 = 1
        assert_eq!(report.graph.edges.len(), 3);
        assert_eq!(report.cycles, 1);
    }

    #[test]
    fn test_no_coupling_independent_shapes() {
        let shapes = vec![
            make_shape("a", "orders", &["status"], &[], &[]),
            make_shape("b", "inventory", &["quantity"], &[], &[]),
        ];

        let events = vec![
            make_event("orders", &["status"]),
            make_event("inventory", &["quantity"]),
        ];

        let report = analyze_coupling(&shapes, &events);
        assert_eq!(report.graph.edges.len(), 0);
        assert_eq!(report.cycles, 0);
        assert!(report.components.len() >= 2);
    }

    #[test]
    fn test_hotspot_ranking() {
        let shapes = vec![
            make_shape("hot", "t", &["x"], &[], &[]),
            make_shape("cold", "t", &["y"], &[], &[]),
        ];

        let events = vec![
            make_event("t", &["x"]),
            make_event("t", &["x"]),
            make_event("t", &["x"]),
            make_event("t", &["y"]),
        ];

        let report = analyze_coupling(&shapes, &events);
        assert!(!report.hotspots.is_empty());
        assert_eq!(report.hotspots[0].shape_id, "hot");
        assert!(report.hotspots[0].hotspot_score > report.hotspots[1].hotspot_score);
    }

    #[test]
    fn test_lift_proposal_split_column() {
        let shapes = vec![
            make_shape("a", "orders", &["status"], &["amount"], &["customer_id"]),
            make_shape("b", "orders", &["status"], &[], &[]),
        ];

        let events = vec![
            make_event("orders", &["status"]),
        ];

        let report = analyze_coupling(&shapes, &events);
        let proposals = propose_lifts(&report, &shapes);

        // "status" column shared across coupled shapes → split proposal
        assert!(!proposals.is_empty());
        assert!(proposals.iter().any(|p| matches!(&p.action, LiftAction::SplitColumn { column, .. } if column == "status")));
    }

    #[test]
    fn test_entropy_uniform() {
        let counts = vec![10, 10, 10];
        let e = entropy(&counts);
        // log2(3) ≈ 1.585
        assert!((e - 1.585).abs() < 0.01);
    }

    #[test]
    fn test_entropy_single() {
        let counts = vec![10, 0, 0];
        assert_eq!(entropy(&counts), 0.0);
    }
}
