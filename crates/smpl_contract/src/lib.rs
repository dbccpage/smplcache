// License: Apache-2.0
// Copyright: Copyright 2026 Jeremy Carroll
//! smpl_contract: Public numeric response contracts for smplcache.
//!
//! Contract rule:
//! Raw numerics may exist inside algorithms.
//! Raw numerics may not be emitted as public conclusions.
//! Every public numeric result must carry:
//! - unit
//! - method
//! - evidence level
//! - tolerance/error bound where applicable
//! - authority
//! - explanation/proof tags

use serde::{Deserialize, Serialize};
use smpl_evidence::EvidenceLevel;

// Note: smpl_contract cannot depend on smpl_cert because smpl_cert depends on smpl_evidence
// and we want smpl_cert to use these types. Actually, to break cyclic dependencies, Authority 
// might need to move to smpl_contract, or we define it here, or smpl_contract defines types 
// that smpl_cert consumes. Wait, the user's example imports `smpl_cert::Authority`.
// Let's redefine Authority here or move it. The user said: "add a small crate before importing certified linear algebra"
// If `smpl_cert` also needs to use `smpl_contract`, they can't have a circular dependency.
// But the user didn't explicitly say `smpl_cert` uses `smpl_contract`, they said `smpl_dual` does.
// Let's just create `Authority` here if we move it, or import from `smpl_cert`.
// Wait, `DualDecision` is in `smpl_dual` and uses `CertifiedScalar`, so `smpl_dual` depends on `smpl_contract`.
// Does `smpl_contract` need `Authority`? Yes, `pub authority: Authority`.
// Does `smpl_contract` depend on `smpl_cert`? If yes, `smpl_cert` cannot depend on `smpl_contract`.
// If `smpl_cert` doesn't depend on `smpl_contract`, that's fine.

use smpl_cert::Authority;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum NumericUnit {
    Count,
    Ratio,
    Probability,
    Milliseconds,
    Bytes,
    CpuCost,
    IoCost,
    MemoryMb,
    Dimensionless,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum NumericMethod {
    ExactInteger,
    ExactSetCardinality,
    FloatingEstimate,
    EigenApproximation,
    HomologyRank,
    CycleRank,
    EntropyEstimate,
    DualScore,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NumericTolerance {
    pub absolute: Option<f64>,
    pub relative: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CertifiedScalar {
    pub value: f64,
    pub unit: NumericUnit,
    pub method: NumericMethod,
    pub evidence_level: EvidenceLevel,
    pub authority: Authority,
    pub tolerance: Option<NumericTolerance>,
    pub proof_tags: Vec<String>,
    pub explanation: String,
}

impl CertifiedScalar {
    pub fn certificate(
        value: f64,
        unit: NumericUnit,
        method: NumericMethod,
        evidence_level: EvidenceLevel,
        explanation: impl Into<String>,
    ) -> Self {
        Self {
            value,
            unit,
            method,
            evidence_level,
            authority: Authority::Certificate,
            tolerance: None,
            proof_tags: Vec::new(),
            explanation: explanation.into(),
        }
    }

    pub fn diagnostic(
        value: f64,
        unit: NumericUnit,
        method: NumericMethod,
        evidence_level: EvidenceLevel,
        explanation: impl Into<String>,
    ) -> Self {
        Self {
            value,
            unit,
            method,
            evidence_level,
            authority: Authority::Diagnostic,
            tolerance: None,
            proof_tags: Vec::new(),
            explanation: explanation.into(),
        }
    }

    pub fn proposal(
        value: f64,
        unit: NumericUnit,
        method: NumericMethod,
        evidence_level: EvidenceLevel,
        explanation: impl Into<String>,
    ) -> Self {
        Self {
            value,
            unit,
            method,
            evidence_level,
            authority: Authority::Proposal,
            tolerance: None,
            proof_tags: Vec::new(),
            explanation: explanation.into(),
        }
    }
}
