// License: Apache-2.0
// Copyright: Copyright 2026 Jeremy Carroll
//! smpl_dual: Runtime budget control via online dual prices.
//!
//! Decides whether to spend repair budget now or invalidate,
//! using cheap online dual prices inspired by mirror descent.
//!
//! The key insight: repair may be safe but expensive. If CPU or I/O
//! shadow prices are high, certified invalidation is the correct choice
//! even when repair is technically possible.
//!
//! Public UX:
//!   "Repair is safe, but CPU is expensive right now. Invalidating instead."
//!   "Repair cost justified: avoided 50ms recompute."

use serde::{Deserialize, Serialize};
use smpl_cert::Decision;

// ─── Resource Budget ───────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ResourceBudget {
    pub cpu_repair_per_sec: f64,
    pub io_lookup_per_sec: f64,
    pub memory_mb: f64,
}

impl Default for ResourceBudget {
    fn default() -> Self {
        Self {
            cpu_repair_per_sec: 1000.0,
            io_lookup_per_sec: 500.0,
            memory_mb: 256.0,
        }
    }
}

// ─── Dual Prices ───────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DualPrices {
    pub cpu: f64,
    pub io: f64,
    pub memory: f64,
    pub risk: f64,
}

impl Default for DualPrices {
    fn default() -> Self {
        Self {
            cpu: 1.0,
            io: 1.0,
            memory: 1.0,
            risk: 1.0,
        }
    }
}

// ─── Action Cost & Reward ──────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActionCost {
    pub cpu: f64,
    pub io: f64,
    pub memory: f64,
    pub risk: f64,
}

impl Default for ActionCost {
    fn default() -> Self {
        Self {
            cpu: 0.0,
            io: 0.0,
            memory: 0.0,
            risk: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActionReward {
    pub avoided_recompute_ms: f64,
    pub avoided_invalidations: f64,
}

impl Default for ActionReward {
    fn default() -> Self {
        Self {
            avoided_recompute_ms: 0.0,
            avoided_invalidations: 0.0,
        }
    }
}

// ─── Dual Decision ─────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DualDecision {
    pub action: Decision,
    pub repair_score: f64,
    pub invalidate_score: f64,
    pub reason: String,
}

// ─── Dual Controller Trait ─────────────────────────────────────

pub trait DualController {
    fn choose_action(
        &self,
        cost: &ActionCost,
        reward: &ActionReward,
    ) -> DualDecision;

    fn update_prices(&mut self, observed: &ActionCost);
}

// ─── Online Mirror Descent ─────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OnlineMirrorDescent {
    pub prices: DualPrices,
    pub learning_rate: f64,
    pub budget: ResourceBudget,
}

impl OnlineMirrorDescent {
    pub fn new(budget: ResourceBudget, learning_rate: f64) -> Self {
        Self {
            prices: DualPrices::default(),
            learning_rate,
            budget,
        }
    }

    /// Compute the net score of a repair action.
    /// score = reward_value - prices · cost
    fn repair_score(&self, cost: &ActionCost, reward: &ActionReward) -> f64 {
        let reward_value = reward.avoided_recompute_ms + reward.avoided_invalidations * 10.0;

        let penalty = self.prices.cpu * cost.cpu
            + self.prices.io * cost.io
            + self.prices.memory * cost.memory
            + self.prices.risk * cost.risk;

        reward_value - penalty
    }
}

impl DualController for OnlineMirrorDescent {
    fn choose_action(
        &self,
        cost: &ActionCost,
        reward: &ActionReward,
    ) -> DualDecision {
        let repair_score = self.repair_score(cost, reward);
        let invalidate_score = 0.0; // baseline

        if repair_score > invalidate_score {
            DualDecision {
                action: Decision::Repair,
                repair_score,
                invalidate_score,
                reason: format!(
                    "repair justified: score {:.2} > baseline {:.2}",
                    repair_score, invalidate_score
                ),
            }
        } else {
            DualDecision {
                action: Decision::Invalidate,
                repair_score,
                invalidate_score,
                reason: format!(
                    "repair too expensive at current prices: score {:.2} <= baseline {:.2}",
                    repair_score, invalidate_score
                ),
            }
        }
    }

    fn update_prices(&mut self, observed: &ActionCost) {
        // Mirror descent update: nudge prices toward resource scarcity.
        // price_new = price_old * exp(learning_rate * (observed / budget - 1))
        // Clamped to [0.01, 100.0] to prevent degenerate prices.

        let lr = self.learning_rate;

        self.prices.cpu = clamp_price(
            self.prices.cpu * (1.0 + lr * (observed.cpu / self.budget.cpu_repair_per_sec - 1.0)),
        );
        self.prices.io = clamp_price(
            self.prices.io * (1.0 + lr * (observed.io / self.budget.io_lookup_per_sec - 1.0)),
        );
        self.prices.memory = clamp_price(
            self.prices.memory * (1.0 + lr * (observed.memory / self.budget.memory_mb - 1.0)),
        );
        // Risk price decays slowly toward 1.0 unless explicitly raised
        self.prices.risk = clamp_price(
            self.prices.risk * (1.0 + lr * (observed.risk - 0.5)),
        );
    }
}

fn clamp_price(p: f64) -> f64 {
    p.max(0.01).min(100.0)
}

// ─── Tests ─────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_repair_chosen_when_cheap() {
        let controller = OnlineMirrorDescent::new(ResourceBudget::default(), 0.1);

        let cost = ActionCost {
            cpu: 0.1,
            io: 0.1,
            memory: 0.0,
            risk: 0.0,
        };
        let reward = ActionReward {
            avoided_recompute_ms: 50.0,
            avoided_invalidations: 1.0,
        };

        let decision = controller.choose_action(&cost, &reward);
        assert_eq!(decision.action, Decision::Repair);
        assert!(decision.repair_score > 0.0);
    }

    #[test]
    fn test_invalidate_when_expensive() {
        let mut controller = OnlineMirrorDescent::new(ResourceBudget::default(), 0.1);
        // Simulate high CPU prices
        controller.prices.cpu = 50.0;
        controller.prices.io = 50.0;

        let cost = ActionCost {
            cpu: 5.0,
            io: 5.0,
            memory: 0.0,
            risk: 0.0,
        };
        let reward = ActionReward {
            avoided_recompute_ms: 10.0,
            avoided_invalidations: 0.0,
        };

        let decision = controller.choose_action(&cost, &reward);
        assert_eq!(decision.action, Decision::Invalidate);
        assert!(decision.repair_score <= 0.0);
    }

    #[test]
    fn test_price_update_increases_under_load() {
        let mut controller = OnlineMirrorDescent::new(ResourceBudget::default(), 0.5);
        let initial_cpu = controller.prices.cpu;

        // Observed CPU usage exceeds budget
        controller.update_prices(&ActionCost {
            cpu: 2000.0, // 2x the budget
            io: 100.0,
            memory: 50.0,
            risk: 0.0,
        });

        assert!(controller.prices.cpu > initial_cpu);
    }

    #[test]
    fn test_price_update_decreases_under_slack() {
        let mut controller = OnlineMirrorDescent::new(ResourceBudget::default(), 0.5);
        controller.prices.cpu = 5.0; // start elevated
        let initial_cpu = controller.prices.cpu;

        // Observed CPU usage well below budget
        controller.update_prices(&ActionCost {
            cpu: 100.0, // 0.1x the budget
            io: 50.0,
            memory: 10.0,
            risk: 0.0,
        });

        assert!(controller.prices.cpu < initial_cpu);
    }

    #[test]
    fn test_prices_clamped() {
        let mut controller = OnlineMirrorDescent::new(ResourceBudget::default(), 10.0);

        // Extreme update
        controller.update_prices(&ActionCost {
            cpu: 100_000.0,
            io: 100_000.0,
            memory: 100_000.0,
            risk: 100.0,
        });

        assert!(controller.prices.cpu <= 100.0);
        assert!(controller.prices.io <= 100.0);
        assert!(controller.prices.memory <= 100.0);
        assert!(controller.prices.risk <= 100.0);
    }
}
