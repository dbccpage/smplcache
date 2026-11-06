# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""E3: Thompson-sampling admission and eviction under a shape cap.

When the store can hold at most ``shape_cap`` materialized shapes, a read of a
non-resident shape must either evict a resident one or go to the database.
Each shape keeps a Beta(alpha, beta) posterior over "an epoch of this shape
(materialize ... invalidate) serves at least one hit". ``alpha`` counts epochs
with a hit, ``beta`` epochs without. Admission samples one value per shape
and evicts the weakest resident only when the newcomer's sample is higher.

The posteriors are learned from receipts: `ThompsonAdmission.from_result`
seeds them from the per-shape epoch counts of a previous replay window, and
the policy keeps updating from the live window's counts. A learned prior is a
candidate (`admission_prior`) that the tuner loop evaluates by replaying the
previous window with and without it before it is applied.

Baselines at the same cap: LRU (evict the least recently read shape) and LFU
(evict the least read shape). Admission never decides correctness: a resident
shape is still certified on every write and invalidated by the certifier.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Mapping, Sequence

from smplcache.tuning._engine import Candidate, Verdict
from smplcache.tuning.replay import CacheConfig, CacheSim, Day, ReplayResult


class ThompsonAdmission:
    """Admission policy: ``policy(sim, newcomer) -> victim or None``."""

    name = "thompson-admission"

    def __init__(self, prior: Mapping[str, tuple[float, float]] | None = None, seed: int = 0) -> None:
        self.prior: dict[str, tuple[float, float]] = {k: (float(a), float(b)) for k, (a, b) in (prior or {}).items()}
        self.rng = random.Random(seed)
        self.samples = 0

    @classmethod
    def from_result(cls, result: ReplayResult, seed: int = 0) -> "ThompsonAdmission":
        return cls({n: (1.0 + s.epochs_with_hit, 1.0 + s.epochs_without_hit) for n, s in result.per_shape.items()}, seed)

    def posterior(self, sim: CacheSim, name: str) -> tuple[float, float]:
        a0, b0 = self.prior.get(name, (1.0, 1.0))
        stats = sim.result.per_shape[name]
        return (a0 + stats.epochs_with_hit, b0 + stats.epochs_without_hit)

    def __call__(self, sim: CacheSim, newcomer: str) -> str | None:
        self.samples += 1
        a, b = self.posterior(sim, newcomer)
        theta_new = self.rng.betavariate(a, b)
        weakest, weakest_theta = None, 2.0
        for resident in sim.resident():
            ra, rb = self.posterior(sim, resident)
            theta = self.rng.betavariate(ra, rb)
            if theta < weakest_theta:
                weakest, weakest_theta = resident, theta
        if weakest is not None and theta_new > weakest_theta:
            return weakest
        return None


def lru_admission(sim: CacheSim, newcomer: str) -> str | None:
    residents = sim.resident()
    return min(residents, key=lambda n: sim.last_read.get(n, -1)) if residents else None


def lfu_admission(sim: CacheSim, newcomer: str) -> str | None:
    residents = sim.resident()
    return min(residents, key=lambda n: (sim.read_count.get(n, 0), sim.last_read.get(n, -1))) if residents else None


def run_capped(day: Day, materialized: frozenset[str], cap: int, admission: Any, label: str, fast: bool = False) -> ReplayResult:
    return CacheSim(day, CacheConfig(materialized, None, "shape", cap, admission, label=label), fast=fast).run()


class AdmissionPriorProposer:
    """Propose the posterior learned from the last window as the prior for the next."""

    name = "admission-prior-proposer"

    def propose(self, context: Mapping[str, Any]) -> Sequence[Candidate]:
        last: ReplayResult | None = context.get("last_result")
        if last is None:
            return []
        prior = {n: [1.0 + s.epochs_with_hit, 1.0 + s.epochs_without_hit] for n, s in last.per_shape.items()}
        return [Candidate("admission_prior", {"prior": prior, "cap": context["cap"]}, self.name, "posterior from the previous window")]


@dataclass
class AdmissionReplayEvaluator:
    """Replay the previous window at the cap with the proposed prior versus a flat prior."""

    min_improvement: float = 0.02
    seed: int = 0
    name: str = "admission-replay-evaluator"

    def evaluate(self, candidate: Candidate, history: Any) -> Verdict:
        day: Day = history["day"]
        materialized: frozenset[str] = history["materialized"]
        cap = int(candidate.payload["cap"])
        prior = {k: (float(v[0]), float(v[1])) for k, v in candidate.payload["prior"].items()}
        if day.reads < 100:
            return Verdict("underdetermined", self.name, f"only {day.reads} recorded reads", {"reads": day.reads})
        flat = run_capped(day, materialized, cap, ThompsonAdmission(None, self.seed), "flat", fast=True)
        learned = run_capped(day, materialized, cap, ThompsonAdmission(prior, self.seed), "learned", fast=True)
        improvement = (flat.db_reads - learned.db_reads) / max(flat.db_reads, 1)
        evidence = {"reads": day.reads, "db_reads_flat": flat.db_reads, "db_reads_learned": learned.db_reads, "improvement": improvement}
        if improvement >= self.min_improvement:
            return Verdict("supported", self.name, f"db reads {flat.db_reads} -> {learned.db_reads}", evidence)
        if improvement < 0:
            return Verdict("refuted", self.name, f"db reads rise {flat.db_reads} -> {learned.db_reads}", evidence)
        return Verdict("underdetermined", self.name, f"gain {improvement:.1%} below {self.min_improvement:.0%} resolution", evidence)
