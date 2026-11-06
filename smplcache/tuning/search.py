# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""E2: tree search (UCT) over which shapes to materialize.

State: the set of materialized shapes. Actions: add or drop one shape. A
rollout replays a recorded segment of CDC and reads against the configuration
(`CacheSim`, fast mode) and scores it as

    reward = database reads saved against serving everything from the database
             - memory_cost * mean number of cached groups.

The search only explores; the configuration it recommends is a candidate
(`materialize_set`) that the tuner loop evaluates on a *held-out* segment
before a policy or a human decides. Partition "split" actions were dropped
after E1 showed per-shape store granularity is never beaten on this workload;
the state therefore carries no partition map.

Greedy single-drop search is the comparison class: starting from "everything
materialized", drop each shape once if that raises the reward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Mapping, Sequence

from smplcache.tuning._engine import Candidate, ReceiptLog, Verdict
from smplcache.tuning.replay import CacheConfig, CacheSim, Day, ReplayResult


def reward(result: ReplayResult, uncached_db_reads: int, memory_cost: float) -> float:
    """Database reads saved against the uncached baseline minus the memory cost of resident groups."""
    return (
        uncached_db_reads - result.db_reads
    ) - memory_cost * result.mean_cached_groups


@dataclass
class Rollout:
    day: Day
    memory_cost: float
    cache: dict[frozenset[str], float] = field(default_factory=dict)
    evaluations: int = 0

    def score(self, materialized: frozenset[str]) -> float:
        if materialized in self.cache:
            return self.cache[materialized]
        self.evaluations += 1
        res = CacheSim(
            self.day, CacheConfig(materialized, label="rollout"), fast=True
        ).run()
        value = reward(res, self.day.reads, self.memory_cost)
        self.cache[materialized] = value
        return value


@dataclass
class Node:
    state: frozenset[str]
    parent: "Node | None" = None
    children: dict[str, "Node"] = field(default_factory=dict)
    visits: int = 0
    total: float = 0.0

    @property
    def mean(self) -> float:
        return self.total / self.visits if self.visits else 0.0


def actions(state: frozenset[str], universe: Sequence[str]) -> list[str]:
    return [f"drop:{n}" for n in universe if n in state] + [
        f"add:{n}" for n in universe if n not in state
    ]


def step(state: frozenset[str], action: str) -> frozenset[str]:
    kind, name = action.split(":", 1)
    return state - {name} if kind == "drop" else state | {name}


def uct_search(
    start: frozenset[str],
    universe: Sequence[str],
    rollout: Rollout,
    budget: int = 40,
    depth: int = 3,
    c: float = 1.0,
    seed: int = 0,
) -> tuple[frozenset[str], float, dict[str, Any]]:
    """Return (best state found, its rollout reward, search statistics)."""
    rng = random.Random(seed)
    root = Node(start)
    best_state, best_value = start, rollout.score(start)
    scale = max(1.0, abs(best_value))
    for _ in range(budget):
        node = root
        d = 0
        while d < depth:
            acts = actions(node.state, universe)
            untried = [a for a in acts if a not in node.children]
            if untried:
                a = rng.choice(untried)
                child = Node(step(node.state, a), node)
                node.children[a] = child
                node = child
                d += 1
                break
            a = max(
                node.children,
                key=lambda a: node.children[a].mean / scale
                + c
                * math.sqrt(
                    math.log(node.visits + 1) / (node.children[a].visits + 1e-9)
                ),
            )
            node = node.children[a]
            d += 1
        value = rollout.score(node.state)
        if value > best_value:
            best_state, best_value = node.state, value
        walk: Node | None = node
        while walk is not None:
            walk.visits += 1
            walk.total += value
            walk = walk.parent
    stats = {
        "budget": budget,
        "rollouts": rollout.evaluations,
        "root_children": len(root.children),
        "depth": depth,
    }
    return best_state, best_value, stats


def greedy_drop(
    start: frozenset[str], universe: Sequence[str], rollout: Rollout
) -> tuple[frozenset[str], float]:
    state, value = start, rollout.score(start)
    for name in universe:
        if name not in state:
            continue
        trial = state - {name}
        v = rollout.score(trial)
        if v > value:
            state, value = trial, v
    return state, value


class SearchProposer:
    """Run UCT on the recorded segment and propose its best configuration."""

    name = "uct-config-proposer"

    def __init__(
        self, budget: int = 40, memory_cost: float = 0.5, seed: int = 0
    ) -> None:
        self.budget = budget
        self.memory_cost = memory_cost
        self.seed = seed
        self.last_stats: dict[str, Any] = {}

    def propose(self, context: Mapping[str, Any]) -> Sequence[Candidate]:
        day: Day = context["search_day"]
        universe: Sequence[str] = context["universe"]
        current: frozenset[str] = frozenset(context["materialized"])
        rollout = Rollout(day, self.memory_cost)
        best, value, stats = uct_search(
            current, universe, rollout, self.budget, seed=self.seed
        )
        self.last_stats = {
            **stats,
            "search_reward": value,
            "start_reward": rollout.score(current),
        }
        if best == current:
            return []
        return [
            Candidate(
                "materialize_set",
                {"materialized": sorted(best), "memory_cost": self.memory_cost},
                self.name,
                f"search reward {value:.1f} vs {rollout.score(current):.1f} on the search segment",
            )
        ]


@dataclass
class HeldOutEvaluator:
    """Replay a held-out segment under the current and proposed materialized sets."""

    min_improvement: float = 0.02
    name: str = "held-out-config-evaluator"

    def evaluate(self, candidate: Candidate, history: Any) -> Verdict:
        day: Day = history["day"]
        current = frozenset(history["materialized"])
        proposed = frozenset(candidate.payload["materialized"])
        memory_cost = float(candidate.payload["memory_cost"])
        if day.reads < 100:
            return Verdict(
                "underdetermined",
                self.name,
                f"only {day.reads} recorded reads",
                {"reads": day.reads},
            )
        before = CacheSim(day, CacheConfig(current, label="current"), fast=True).run()
        after = CacheSim(day, CacheConfig(proposed, label="proposed"), fast=True).run()
        r_before = reward(before, day.reads, memory_cost)
        r_after = reward(after, day.reads, memory_cost)
        improvement = (r_after - r_before) / max(abs(r_before), 1.0)
        evidence = {
            "reads": day.reads,
            "reward_before": r_before,
            "reward_after": r_after,
            "db_reads_before": before.db_reads,
            "db_reads_after": after.db_reads,
            "mean_groups_before": before.row()["mean_cached_groups"],
            "mean_groups_after": after.row()["mean_cached_groups"],
            "improvement": improvement,
        }
        if improvement >= self.min_improvement:
            return Verdict(
                "supported",
                self.name,
                f"held-out reward {r_before:.1f} -> {r_after:.1f}",
                evidence,
            )
        if improvement < 0:
            return Verdict(
                "refuted",
                self.name,
                f"held-out reward falls {r_before:.1f} -> {r_after:.1f}",
                evidence,
            )
        return Verdict(
            "underdetermined",
            self.name,
            f"gain {improvement:.1%} below {self.min_improvement:.0%} resolution",
            evidence,
        )


def audit_config_receipts(
    log: ReceiptLog, held_out: Day, current: frozenset[str]
) -> list[dict[str, Any]]:
    """Recount every ``evaluated`` materialize_set receipt directly with CacheSim.

    Independent of `HeldOutEvaluator`: it re-derives database reads before and
    after from the recorded segment and compares them, and the verdict they
    imply, with what the receipt claims. Returns one entry per disagreement.
    """
    log.require_intact()
    problems: list[dict[str, Any]] = []
    for receipt in log:
        if (
            receipt.step != "evaluated"
            or receipt.body.get("candidate", {}).get("kind") != "materialize_set"
        ):
            continue
        verdict = receipt.body["verdict"]
        evidence = verdict.get("evidence", {})
        proposed = frozenset(receipt.body["candidate"]["payload"]["materialized"])
        memory_cost = float(
            receipt.body["candidate"]["payload"].get("memory_cost", 0.0)
        )
        before = CacheSim(
            held_out, CacheConfig(current, label="audit-before"), fast=True
        ).run()
        after = CacheSim(
            held_out, CacheConfig(proposed, label="audit-after"), fast=True
        ).run()
        r_before = reward(before, held_out.reads, memory_cost)
        r_after = reward(after, held_out.reads, memory_cost)
        improvement = (r_after - r_before) / max(abs(r_before), 1.0)
        expected = (
            "supported"
            if improvement >= HeldOutEvaluator.min_improvement
            else ("refuted" if improvement < 0 else "underdetermined")
        )
        claimed = (evidence.get("db_reads_before"), evidence.get("db_reads_after"))
        if (
            claimed != (before.db_reads, after.db_reads)
            or expected != verdict["status"]
        ):
            problems.append(
                {
                    "seq": receipt.seq,
                    "claimed_db_reads": claimed,
                    "recount_db_reads": (before.db_reads, after.db_reads),
                    "claimed_status": verdict["status"],
                    "recount_status": expected,
                }
            )
    return problems
