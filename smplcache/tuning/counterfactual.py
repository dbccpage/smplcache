# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""E5: counterfactual replay from receipts (off-policy estimation).

A plain replay leaves a receipt per read and per certified invalidation
(`ReplayResult.event_log`). Because admission and eviction only decide *which*
shapes are resident, never what the certifier says, the outcome of any
admission policy under a shape cap can be estimated from those receipts alone,
without re-running the certifier or the database: walk the log, keep the
resident set, and count a read as a hit when its shape is resident and valid.

The estimate is exact for policies whose decisions depend only on reads and
invalidations (LRU, LFU); it is compared with the actual capped replay in E5.
"""

from __future__ import annotations

import time
from typing import Any, Iterable


def estimate_capped_from_receipts(event_log: Iterable[tuple[int, str, str]], materialized: frozenset[str], cap: int, policy: str) -> dict[str, Any]:
    t0 = time.time()
    resident: dict[str, bool] = {}  # shape -> valid
    last_read: dict[str, int] = {}
    read_count: dict[str, int] = {}
    hits = db_reads = reads = rereads = 0
    for clock, kind, shape in event_log:
        if kind == "invalidate":
            if shape in resident:
                resident[shape] = False
            continue
        reads += 1
        last_read[shape] = clock
        read_count[shape] = read_count.get(shape, 0) + 1
        if shape not in materialized:
            db_reads += 1
            continue
        if shape in resident:
            if resident[shape]:
                hits += 1
            else:
                rereads += 1
                db_reads += 1
                resident[shape] = True
            continue
        if len(resident) >= cap:
            if policy == "lru":
                victim = min(resident, key=lambda n: last_read.get(n, -1))
            else:
                victim = min(resident, key=lambda n: (read_count.get(n, 0), last_read.get(n, -1)))
            del resident[victim]
        resident[shape] = True
        db_reads += 1
    return {"reads": reads, "hits": hits, "db_reads": db_reads, "rereads": rereads, "seconds": round(time.time() - t0, 3)}
