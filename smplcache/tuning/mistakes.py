# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""E4: mistake memory from REREADs.

Every certified invalidate or unsupported decision carries a reason and the
evidence that was required and available. This module clusters those
certificates by (relation, reason, missing evidence) and turns each cluster
into a human-reviewable suggestion:

- ``missing_evidence_for_repair`` with columns the CDC stream did not carry
  -> "set REPLICA IDENTITY FULL on <table>" (or the equivalent for the
  connector), evaluated by a counterfactual replay in which the same events
  carry full before-images;
- ``min_max_requires_auxiliary_state`` -> "admit this shape with a bounded
  extremum cache", which cannot be replayed without that state and is left
  underdetermined with the number of reads it would touch.

Suggestions are never auto-approved: the policy lists both kinds under human
review, so the loop records them as ``needs_human`` with their evidence. The
loop's mistake memory keeps refuted suggestions from being proposed twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from smplcache.smplcache import WriteEvent
from smplcache.tuning._engine import Candidate, Verdict
from smplcache.tuning.replay import (
    CacheConfig,
    CacheSim,
    Day,
    ReadOp,
    ReplayResult,
    SqlDatabase,
)


@dataclass
class RereadCluster:
    relation: str
    reason: str
    missing: tuple[str, ...]
    shapes: set[str] = field(default_factory=set)
    certificates: int = 0
    rereads: int = 0
    db_reads: int = 0

    @property
    def key(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.relation, self.reason, self.missing)


def cluster_rereads(result: ReplayResult) -> list[RereadCluster]:
    clusters: dict[tuple[str, str, tuple[str, ...]], RereadCluster] = {}
    for cert in result.certificates:
        missing = tuple(
            sorted(set(cert["required"]) - set(cert["available"]))
        ) or tuple(cert["unavailable"])
        key = (cert["relation"], cert["reason"], missing)
        c = clusters.setdefault(
            key, RereadCluster(cert["relation"], cert["reason"], missing)
        )
        c.shapes.add(cert["shape"])
        c.certificates += 1
    for name, stats in result.per_shape.items():
        shape_clusters = [c for c in clusters.values() if name in c.shapes]
        for c in shape_clusters:
            c.rereads += stats.rereads
            c.db_reads += stats.db_reads
        if (
            not shape_clusters
            and stats.db_reads
            and stats.hits == 0
            and stats.invalidations == 0
            and stats.rereads == 0
        ):
            # never cached at all: outside the admitted fragment
            c = clusters.setdefault(
                (name, "unsupported_shape", ()),
                RereadCluster(name, "unsupported_shape", ()),
            )
            c.shapes.add(name)
            c.db_reads += stats.db_reads
    return sorted(clusters.values(), key=lambda c: (-c.db_reads, c.relation, c.reason))


def with_full_before_images(day: Day, table: str) -> Day:
    """Counterfactual stream: the same events on ``table`` with full old rows and no unavailable columns."""
    db = SqlDatabase(day.seed_rows)
    stream: list[WriteEvent | ReadOp] = []
    for op in day.stream:
        if isinstance(op, WriteEvent):
            if (
                op.relation == table
                and op.operation == "UPDATE"
                and op.unavailable_cols
            ):
                rid = op.new["id"]
                old_rows = [r for r in db.rows(table) if r["id"] == rid]
                old = old_rows[0] if old_rows else dict(op.old)
                op = WriteEvent(
                    op.relation,
                    set(),
                    old,
                    dict(op.new),
                    op.event_id,
                    "UPDATE",
                    boundary_clock=op.boundary_clock,
                )
            db.apply(op)
        stream.append(op)
    return Day(day.seed_rows, stream, day.seed, day.phase_change_at)


class RereadSuggestionProposer:
    """Turn reread clusters into reviewable suggestions."""

    name = "reread-suggestion-proposer"

    def propose(self, context: Mapping[str, Any]) -> Sequence[Candidate]:
        result: ReplayResult = context["result"]
        out: list[Candidate] = []
        by_table: dict[str, list[RereadCluster]] = {}
        for c in cluster_rereads(result):
            if c.reason == "missing_evidence_for_repair" and c.missing:
                by_table.setdefault(c.relation, []).append(
                    c
                )  # one suggestion per table, not per column set
            elif c.reason in ("unsupported_shape", "min_max_requires_auxiliary_state"):
                out.append(
                    Candidate(
                        "admit_shape",
                        {
                            "shape": sorted(c.shapes)[0],
                            "mechanism": "SortedMultisetExtremumCache",
                        },
                        self.name,
                        f"{c.db_reads} reads of {sorted(c.shapes)} went to the database because MIN/MAX is outside the admitted fragment",
                    )
                )
        for table, clusters in sorted(by_table.items()):
            missing = sorted(set().union(*(set(c.missing) for c in clusters)))
            shapes = sorted(set().union(*(c.shapes for c in clusters)))
            certificates = sum(c.certificates for c in clusters)
            out.insert(
                0,
                Candidate(
                    "replica_identity_full",
                    {"table": table, "missing_columns": missing},
                    self.name,
                    f"{certificates} invalidations on {shapes} lacked before-images of {missing}; "
                    f"ALTER TABLE {table} REPLICA IDENTITY FULL (Postgres) or enable before-images in the connector",
                ),
            )
        return out


@dataclass
class CounterfactualEvaluator:
    """Replay the window with the suggested fix simulated, where that is possible."""

    min_improvement: float = 0.05
    name: str = "counterfactual-reread-evaluator"

    def evaluate(self, candidate: Candidate, history: Any) -> Verdict:
        day: Day = history["day"]
        materialized: frozenset[str] = history["materialized"]
        if candidate.kind == "replica_identity_full":
            table = str(candidate.payload["table"])
            before = CacheSim(
                day, CacheConfig(materialized, label="as recorded"), fast=True
            ).run()
            after = CacheSim(
                with_full_before_images(day, table),
                CacheConfig(materialized, label="with before-images"),
                fast=True,
            ).run()
            improvement = (before.rereads - after.rereads) / max(before.rereads, 1)
            evidence = {
                "table": table,
                "rereads_before": before.rereads,
                "rereads_after": after.rereads,
                "db_reads_before": before.db_reads,
                "db_reads_after": after.db_reads,
                "improvement": improvement,
            }
            if improvement >= self.min_improvement:
                return Verdict(
                    "supported",
                    self.name,
                    f"rereads {before.rereads} -> {after.rereads} with before-images on {table}",
                    evidence,
                )
            if improvement < 0:
                return Verdict(
                    "refuted",
                    self.name,
                    f"rereads rise {before.rereads} -> {after.rereads}",
                    evidence,
                )
            return Verdict(
                "underdetermined",
                self.name,
                f"gain {improvement:.1%} below resolution",
                evidence,
            )
        if candidate.kind == "admit_shape":
            shape = str(candidate.payload["shape"])
            stats = (
                CacheSim(day, CacheConfig(materialized, label="as recorded"), fast=True)
                .run()
                .per_shape[shape]
            )
            return Verdict(
                "underdetermined",
                self.name,
                f"{stats.db_reads} database reads of {shape} could become cache hits only with auxiliary extremum state; not replayable here",
                {"shape": shape, "db_reads": stats.db_reads},
            )
        return Verdict(
            "underdetermined",
            self.name,
            f"no counterfactual for kind {candidate.kind!r}",
            {},
        )
