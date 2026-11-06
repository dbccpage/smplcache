# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""Self-tuning for SmplCache: proposals tested on recorded traffic, shown before they apply.

SmplCache tunes itself from its own receipts. Every change it proposes is
tested on your recorded traffic and shown to you before it takes effect.
Learned policies change only how much work SmplCache does (what to cache, how
to partition, what to evict, what to precompute). They never decide whether a
served value is correct; that stays with the certifier (`smplcache.certify_event`).

Layout:

- `_engine.py`: the five-verb loop (propose / evaluate / authorize / record /
  learn) with hash-chained receipts and a mistake memory. It is a verbatim
  copy of `src/anomalon_workbench/tuner.py` from `dbccpage/owed_ai` at
  commit `5c03b43486d883144f2f082c037629dcda2c5117` (sha256 recorded in
  `VENDORED_ENGINE_SHA256`). SmplCache depends on no other repository; the
  owed_ai module is the reference implementation and this copy is checked
  against its hash in `tests/test_tuning.py`.
- `replay.py`: the benchmark replay (a write-heavy day with Zipfian skew),
  the SQLite oracle, and the plain / TTL baselines.
- `spectral.py`: E1, eigenmode partitioning and the entropy drift detector.
- `search.py`: E2, tree search over which shapes to materialize.
- `bandit.py`: E3, Thompson-sampling admission under a shape cap.
- `mistakes.py`: E4, clustering of REREAD causes into reviewable suggestions.
- `experiments.py`: runs every experiment against the baselines and writes
  the numbers behind `docs/EXPERIMENTS.md`.

Every adaptive behaviour is off unless a `TuningFlags` field enables it, and
every enabled behaviour appears on the limits page in `README.md`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from smplcache.tuning._engine import (
    Authorization,
    Candidate,
    Loop,
    MistakeMemory,
    Policy,
    PolicyAuthorizer,
    Receipt,
    ReceiptChainBroken,
    ReceiptLog,
    RoleConflict,
    RunSummary,
    SelfAuthorization,
    Verdict,
    authorize,
    evaluate,
    learn,
    propose,
    record,
    replay,
)

VENDORED_ENGINE_SOURCE = "dbccpage/owed_ai@5c03b43486d883144f2f082c037629dcda2c5117:src/anomalon_workbench/tuner.py"
VENDORED_ENGINE_SHA256 = "7b22d9e085e20bc09d3a941a242a3d6179727ba45ea95d5b8805a67c25157455"


def vendored_engine_sha256() -> str:
    return hashlib.sha256((Path(__file__).parent / "_engine.py").read_bytes()).hexdigest()


__all__ = [
    "Authorization",
    "Candidate",
    "Loop",
    "MistakeMemory",
    "Policy",
    "PolicyAuthorizer",
    "Receipt",
    "ReceiptChainBroken",
    "ReceiptLog",
    "RoleConflict",
    "RunSummary",
    "SelfAuthorization",
    "VENDORED_ENGINE_SHA256",
    "VENDORED_ENGINE_SOURCE",
    "Verdict",
    "authorize",
    "evaluate",
    "learn",
    "propose",
    "record",
    "replay",
    "vendored_engine_sha256",
]
