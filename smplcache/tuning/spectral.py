# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""E1: spectral partitioning of cached shapes and an entropy drift detector.

The co-invalidation matrix M sums the outer products of indicator vectors (one
per write: which cached shapes that write touched). rho = M / tr M is a density
matrix: positive semidefinite, unit trace. Its von Neumann entropy
S = -sum(lambda log2 lambda) measures how spread out invalidation is over
independent modes; its eigenvectors are those modes. Partitioning shapes by
eigenmode groups together shapes that invalidate together, so that a store
which can only flush whole partitions flushes fewer bystanders.

Nothing here is a quantum computation: rho is a normalised Gram matrix and
the entropy is Shannon entropy of its spectrum.

Requires numpy. The drift detector compares the entropy of a sliding window of
writes with the entropy at the last accepted partition and proposes a
repartition when the difference exceeds a threshold. Proposals go through the
tuner loop; they change only how the store groups keys, never a certification.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import random
from typing import Any, Iterable, Mapping, Sequence

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:  # pragma: no cover - exercised only without the optional extra
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False

from smplcache.tuning._engine import Candidate, Verdict
from smplcache.tuning.replay import CacheConfig, CacheSim, Day, benchmark_shapes


def _require_numpy() -> None:
    if not HAS_NUMPY:
        raise RuntimeError(
            "smplcache.tuning.spectral needs numpy: pip install 'smplcache[matrix]'"
        )


def density_matrix(vectors: Iterable[frozenset[str]], names: Sequence[str]) -> Any:
    """rho = M / tr M over the co-invalidation indicator vectors; zero matrix when empty."""
    _require_numpy()
    index = {n: i for i, n in enumerate(names)}
    m = np.zeros((len(names), len(names)))
    for touched in vectors:
        v = np.zeros(len(names))
        for n in touched:
            i = index.get(n)
            if i is not None:
                v[i] = 1.0
        m += np.outer(v, v)
    tr = np.trace(m)
    return m / tr if tr > 0 else m


def von_neumann_entropy(rho: Any) -> float:
    """-sum lambda log2 lambda over the eigenvalues of rho (0 for the zero matrix)."""
    _require_numpy()
    if not np.any(rho):
        return 0.0
    vals = np.linalg.eigvalsh(rho)
    vals = vals[vals > 1e-12]
    return float(-np.sum(vals * np.log2(vals)))


def eigenmode_partition(rho: Any, names: Sequence[str], k: int) -> dict[str, str]:
    """Assign each shape to one of k partitions by clustering rows of the top-k eigenvectors.

    Deterministic: k-means with farthest-point seeding on the row-normalised
    spectral embedding. Shapes that never co-invalidate with anything (zero
    rows) are each given their own partition when k allows, else the last one.
    """
    _require_numpy()
    n = len(names)
    k = max(1, min(k, n))
    if not np.any(rho):
        return {name: f"p{i % k}" for i, name in enumerate(names)}
    vals, vecs = np.linalg.eigh(rho)
    order = np.argsort(vals)[::-1][:k]
    emb = vecs[:, order]
    norms = np.linalg.norm(emb, axis=1)
    active = [i for i in range(n) if norms[i] > 1e-9]
    inactive = [i for i in range(n) if norms[i] <= 1e-9]
    labels = [-1] * n
    if active:
        pts = emb[active] / norms[active][:, None]
        kk = min(k, len(active))
        centres = [pts[int(np.argmax(np.linalg.norm(pts, axis=1)))]]
        while len(centres) < kk:
            dists = np.min([np.linalg.norm(pts - c, axis=1) for c in centres], axis=0)
            centres.append(pts[int(np.argmax(dists))])
        c_arr = np.array(centres)
        assign = np.zeros(len(active), dtype=int)
        for _ in range(50):
            d = np.linalg.norm(pts[:, None, :] - c_arr[None, :, :], axis=2)
            new_assign = np.argmin(d, axis=1)
            if np.array_equal(new_assign, assign) and _ > 0:
                break
            assign = new_assign
            for j in range(kk):
                members = pts[assign == j]
                if len(members):
                    c_arr[j] = members.mean(axis=0)
        for idx, lab in zip(active, assign):
            labels[idx] = int(lab)
    used = max(labels) + 1 if active else 0
    for idx in inactive:
        labels[idx] = used if used < k else k - 1
        used = min(used + 1, k)
    return {names[i]: f"p{labels[i]}" for i in range(n)}


def relation_partition(names: Sequence[str] | None = None) -> dict[str, str]:
    shapes = {s.name: s for s in benchmark_shapes()}
    return {n: shapes[n].relation for n in (names or list(shapes))}


def random_partition(names: Sequence[str], k: int, seed: int) -> dict[str, str]:
    rng = random.Random(seed)
    return {n: f"r{rng.randrange(k)}" for n in names}


class EntropyDriftDetector:
    """Von Neumann entropy of the co-invalidation density matrix over a sliding window."""

    def __init__(
        self, names: Sequence[str], window: int = 400, threshold_bits: float = 0.3
    ) -> None:
        if threshold_bits <= 0:
            raise ValueError(
                "threshold_bits must be positive; 0 would propose a repartition on every window"
            )
        self.names = list(names)
        self.window = window
        self.threshold = threshold_bits
        self.buffer: deque[frozenset[str]] = deque(maxlen=window)
        self.reference: float | None = None
        self.history: list[float] = []

    def observe(self, touched: frozenset[str]) -> None:
        self.buffer.append(touched)

    def entropy(self) -> float:
        return von_neumann_entropy(density_matrix(self.buffer, self.names))

    def check(self) -> tuple[bool, float]:
        """Return (drifted, current entropy); records the entropy in ``history``."""
        s = self.entropy()
        self.history.append(s)
        if self.reference is None:
            return (len(self.buffer) >= min(self.window, 50), s)
        return (abs(s - self.reference) >= self.threshold, s)

    def accept(self, entropy: float) -> None:
        self.reference = entropy


class RepartitionProposer:
    """Propose a k-way eigenmode partition from the recent co-invalidation window."""

    name = "spectral-repartition-proposer"

    def __init__(self, k: int) -> None:
        self.k = k

    def propose(self, context: Mapping[str, Any]) -> Sequence[Candidate]:
        names: Sequence[str] = context["names"]
        window: Sequence[frozenset[str]] = context["window"]
        if not context.get("drifted", False):
            return []
        rho = density_matrix(window, names)
        partition = eigenmode_partition(rho, names, self.k)
        if partition == context.get("current_partition"):
            return []
        return [
            Candidate(
                "repartition",
                {"k": self.k, "partition": partition},
                self.name,
                f"entropy moved from {context.get('reference_entropy')} to {context.get('entropy')} bits",
            )
        ]


@dataclass
class PartitionReplayEvaluator:
    """Replay the recorded window under the current and the proposed partition."""

    min_improvement: float = 0.05
    min_writes: int = 200
    name: str = "partition-replay-evaluator"

    def evaluate(self, candidate: Candidate, history: Any) -> Verdict:
        day: Day = history["day"]
        current: Mapping[str, str] = history["current_partition"]
        materialized: frozenset[str] = history["materialized"]
        if day.writes < self.min_writes:
            return Verdict(
                "underdetermined",
                self.name,
                f"only {day.writes} recorded writes",
                {"writes": day.writes},
            )
        proposed = candidate.payload["partition"]
        before = CacheSim(
            day,
            CacheConfig(materialized, current, "partition", label="current"),
            fast=True,
        ).run()
        after = CacheSim(
            day,
            CacheConfig(materialized, proposed, "partition", label="proposed"),
            fast=True,
        ).run()
        improvement = (before.rereads - after.rereads) / max(before.rereads, 1)
        evidence = {
            "writes": day.writes,
            "reads": day.reads,
            "rereads_before": before.rereads,
            "rereads_after": after.rereads,
            "collateral_before": before.collateral_flushes,
            "collateral_after": after.collateral_flushes,
            "improvement": improvement,
        }
        if improvement >= self.min_improvement:
            return Verdict(
                "supported",
                self.name,
                f"rereads {before.rereads} -> {after.rereads}",
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
            f"gain {improvement:.1%} below {self.min_improvement:.0%} resolution",
            evidence,
        )
