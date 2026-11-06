# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""Run the self-tuning experiments against the baselines and write the numbers.

    python -m smplcache.tuning.experiments --out docs/experiments

Every experiment states its hypothesis and metric in `docs/EXPERIMENTS.md`;
this module produces the results tables and the receipts behind them. All
reported runs use the oracle (``fast=False``); evaluators inside the loop use
fast rollouts, and the configuration they recommend is then measured with the
oracle on.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Callable

from smplcache.tuning._engine import Loop, Policy, PolicyAuthorizer, ReceiptLog
from smplcache.tuning.bandit import (
    AdmissionPriorProposer,
    AdmissionReplayEvaluator,
    ThompsonAdmission,
    lfu_admission,
    lru_admission,
    run_capped,
)
from smplcache.tuning.counterfactual import estimate_capped_from_receipts
from smplcache.tuning.mistakes import CounterfactualEvaluator, RereadSuggestionProposer
from smplcache.tuning.replay import (
    CacheConfig,
    CacheSim,
    Day,
    ReplayResult,
    benchmark_shapes,
    generate_day,
    run_plain,
    run_ttl,
    run_uncached,
)
from smplcache.tuning.search import (
    HeldOutEvaluator,
    Rollout,
    SearchProposer,
    greedy_drop,
    reward,
)
from smplcache.tuning.spectral import (
    HAS_NUMPY,
    EntropyDriftDetector,
    PartitionReplayEvaluator,
    RepartitionProposer,
    density_matrix,
    eigenmode_partition,
    random_partition,
    relation_partition,
    von_neumann_entropy,
)


@dataclass(frozen=True)
class TuningFlags:
    """Every adaptive behaviour is off by default; the limits page lists each flag."""

    spectral_partitioning: bool = False
    entropy_drift_detector: bool = False
    config_search: bool = False
    bandit_admission: bool = False
    reread_suggestions: bool = False
    counterfactual_estimates: bool = False


ALL_ON = TuningFlags(True, True, True, True, True, True)


def _rows(results: list[ReplayResult]) -> list[dict[str, Any]]:
    return [r.row() for r in results]


def run_baselines(day: Day) -> dict[str, Any]:
    t0 = time.time()
    uncached = run_uncached(day)
    plain = run_plain(day)
    ttl = run_ttl(day, 200)
    return {
        "rows": _rows([uncached, plain, ttl]),
        "seconds": round(time.time() - t0, 1),
        "_plain": plain,
    }


def run_e1(day: Day, plain: ReplayResult, window_ops: int = 1000) -> dict[str, Any]:
    names = [s.name for s in benchmark_shapes()]
    mat = frozenset(names)
    t0 = time.time()
    rho_touched = density_matrix([t for _, t in plain.co_invalidation], names)
    rho_inval = density_matrix([t for _, t in plain.invalidated_sets], names)
    static: list[ReplayResult] = []
    partitions = {
        "relation-k3": relation_partition(names),
        "spectral-touched-k3": eigenmode_partition(rho_touched, names, 3),
        "spectral-invalidated-k3": eigenmode_partition(rho_inval, names, 3),
        "random-k3": random_partition(names, 3, day.seed),
        "spectral-touched-k6": eigenmode_partition(rho_touched, names, 6),
        "random-k6": random_partition(names, 6, day.seed),
    }
    for label, part in partitions.items():
        static.append(
            CacheSim(day, CacheConfig(mat, part, "partition", label=label)).run()
        )
    static.append(plain)

    # drift detector + loop over windows
    detector = EntropyDriftDetector(names, window=400, threshold_bits=0.3)
    proposer = RepartitionProposer(k=3)
    loop = Loop(
        proposer,
        PartitionReplayEvaluator(),
        PolicyAuthorizer(),
        Policy(
            "e1/repartition",
            auto_approve_kinds=frozenset({"repartition"}),
            min_improvement=0.05,
        ),
    )
    current = relation_partition(names)
    windows: list[dict[str, Any]] = []
    online_rereads = 0
    static_relation_rereads = 0
    prev_window: Day | None = None
    for start in range(0, len(day.stream), window_ops):
        window = day.slice(start, start + window_ops)
        res = CacheSim(
            window, CacheConfig(mat, current, "partition", label="online"), fast=True
        ).run()
        online_rereads += res.rereads
        static_relation_rereads += (
            CacheSim(
                window,
                CacheConfig(mat, relation_partition(names), "partition", label="rel"),
                fast=True,
            )
            .run()
            .rereads
        )
        for _, touched in res.co_invalidation:
            detector.observe(touched)
        drifted, entropy = detector.check()
        row = {
            "window_start": start,
            "entropy_bits": round(entropy, 3),
            "drifted": drifted,
            "proposed": 0,
            "verdict": None,
            "authorization": None,
        }
        if drifted and prev_window is not None:
            context = {
                "names": names,
                "window": list(detector.buffer),
                "drifted": True,
                "current_partition": current,
                "entropy": round(entropy, 3),
                "reference_entropy": detector.reference,
            }
            history = {
                "day": prev_window,
                "current_partition": current,
                "materialized": mat,
            }

            def apply(c: Any) -> dict[str, Any]:
                nonlocal current
                current = dict(c.payload["partition"])
                return {"partition": current}

            summary = loop.run_once(
                context, history, apply=apply, run_label=f"run-{start}"
            )
            row["proposed"] = summary.proposed
            for r in list(loop.log)[-6:]:
                if r.step == "evaluated":
                    row["verdict"] = (
                        r.body["verdict"]["status"] + ": " + r.body["verdict"]["reason"]
                    )
                if r.step == "authorized":
                    row["authorization"] = r.body["authorization"]["status"]
            detector.accept(entropy)
        elif detector.reference is None and len(detector.buffer) >= 50:
            detector.accept(entropy)
        windows.append(row)
        prev_window = window
    return {
        "static_rows": _rows(static),
        "partitions": partitions,
        "entropy_full_day_bits": round(von_neumann_entropy(rho_touched), 3),
        "windows": windows,
        "online_loop_rereads": online_rereads,
        "static_relation_rereads": static_relation_rereads,
        "receipts": loop.log.to_json(),
        "receipt_chain_intact": loop.log.verify() == -1,
        "seconds": round(time.time() - t0, 1),
    }


def run_e2(day: Day, budget: int = 30, memory_cost: float = 0.5) -> dict[str, Any]:
    names = [s.name for s in benchmark_shapes() if s.agg_fn is not None]
    t0 = time.time()
    n = len(day.stream)
    search_day, held_out, next_phase = (
        day.slice(0, n // 4),
        day.slice(n // 4, n // 2),
        day.slice(n // 2, n),
    )
    rollout = Rollout(search_day, memory_cost)
    start = frozenset(names)
    greedy_state, greedy_value = greedy_drop(start, names, rollout)
    proposer = SearchProposer(budget=budget, memory_cost=memory_cost, seed=day.seed)
    loop = Loop(
        proposer,
        HeldOutEvaluator(),
        PolicyAuthorizer(),
        Policy(
            "e2/materialize",
            auto_approve_kinds=frozenset({"materialize_set"}),
            min_improvement=0.02,
        ),
    )
    applied: dict[str, Any] = {}

    def apply(c: Any) -> dict[str, Any]:
        applied["materialized"] = sorted(c.payload["materialized"])
        return dict(applied)

    summary = loop.run_once(
        {"search_day": search_day, "universe": names, "materialized": start},
        {"day": held_out, "materialized": start},
        apply=apply,
        run_label="run-1",
    )
    search_state = frozenset(applied.get("materialized", [])) if applied else None
    uct_best = None
    for r in loop.log:
        if r.step == "proposed":
            uct_best = frozenset(r.body["candidate"]["payload"]["materialized"])

    def measure(label: str, state: frozenset[str], segment: Day) -> dict[str, Any]:
        res = CacheSim(segment, CacheConfig(state, label=label)).run()
        row = res.row()
        row["reward"] = round(reward(res, segment.reads, memory_cost), 1)
        row["materialized"] = len(state)
        return row

    rows = []
    for seg_label, seg in (
        ("held-out (same phase)", held_out),
        ("next phase", next_phase),
    ):
        rows.append({"segment": seg_label, **measure("plain (all shapes)", start, seg)})
        rows.append({"segment": seg_label, **measure("greedy-drop", greedy_state, seg)})
        if uct_best is not None:
            rows.append({"segment": seg_label, **measure("uct-search", uct_best, seg)})
    return {
        "rows": rows,
        "search_stats": proposer.last_stats,
        "greedy": {
            "materialized": sorted(greedy_state),
            "search_reward": round(greedy_value, 1),
        },
        "uct": {"materialized": sorted(uct_best) if uct_best else None},
        "loop_summary": summary.to_json(),
        "applied": applied,
        "receipts": loop.log.to_json(),
        "receipt_chain_intact": loop.log.verify() == -1,
        "seconds": round(time.time() - t0, 1),
    }


def run_e3(day: Day, cap: int = 5) -> dict[str, Any]:
    names = frozenset(s.name for s in benchmark_shapes() if s.agg_fn is not None)
    t0 = time.time()
    n = len(day.stream)
    learn_seg, eval_seg, apply_seg = (
        day.slice(0, n // 4),
        day.slice(n // 4, n // 2),
        day.slice(n // 2, n),
    )
    learned_from = run_capped(
        learn_seg, names, cap, ThompsonAdmission(None, day.seed), "learn", fast=True
    )
    proposer = AdmissionPriorProposer()
    loop = Loop(
        proposer,
        AdmissionReplayEvaluator(seed=day.seed),
        PolicyAuthorizer(),
        Policy(
            "e3/admission",
            auto_approve_kinds=frozenset({"admission_prior"}),
            min_improvement=0.02,
        ),
    )
    applied: dict[str, Any] = {}

    def apply(c: Any) -> dict[str, Any]:
        applied["prior"] = {
            k: (float(v[0]), float(v[1])) for k, v in c.payload["prior"].items()
        }
        return {"shapes": len(applied["prior"])}

    summary = loop.run_once(
        {"last_result": learned_from, "cap": cap},
        {"day": eval_seg, "materialized": names},
        apply=apply,
        run_label="run-1",
    )
    rows = []
    rows.append(run_plain(apply_seg).row() | {"cap": "none"})
    rows.append(
        run_capped(apply_seg, names, cap, lru_admission, f"lru-cap{cap}").row()
        | {"cap": cap}
    )
    rows.append(
        run_capped(apply_seg, names, cap, lfu_admission, f"lfu-cap{cap}").row()
        | {"cap": cap}
    )
    rows.append(
        run_capped(
            apply_seg,
            names,
            cap,
            ThompsonAdmission(None, day.seed),
            f"thompson-flat-cap{cap}",
        ).row()
        | {"cap": cap}
    )
    if applied:
        rows.append(
            run_capped(
                apply_seg,
                names,
                cap,
                ThompsonAdmission(applied["prior"], day.seed),
                f"thompson-learned-cap{cap}",
            ).row()
            | {"cap": cap}
        )
    return {
        "rows": rows,
        "loop_summary": summary.to_json(),
        "applied": bool(applied),
        "receipts": loop.log.to_json(),
        "receipt_chain_intact": loop.log.verify() == -1,
        "seconds": round(time.time() - t0, 1),
    }


def run_e4(day: Day, plain: ReplayResult) -> dict[str, Any]:
    names = frozenset(s.name for s in benchmark_shapes())
    t0 = time.time()
    loop = Loop(
        RereadSuggestionProposer(),
        CounterfactualEvaluator(),
        PolicyAuthorizer(),
        Policy(
            "e4/suggestions",
            human_review_kinds=frozenset({"replica_identity_full", "admit_shape"}),
        ),
    )
    summary = loop.run_once(
        {"result": plain},
        {"day": day, "materialized": names},
        apply=lambda c: {"applied": "never"},
        run_label="run-1",
    )
    suggestions = []
    for cand, verdict, auth in summary.pending_human:
        suggestions.append(
            {
                "kind": cand.kind,
                "payload": dict(cand.payload),
                "rationale": cand.rationale,
                "verdict": verdict.to_json(),
                "authorization": auth.to_json(),
            }
        )
    return {
        "suggestions": suggestions,
        "loop_summary": summary.to_json(),
        "receipts": loop.log.to_json(),
        "receipt_chain_intact": loop.log.verify() == -1,
        "seconds": round(time.time() - t0, 1),
    }


def run_e5(day: Day, plain: ReplayResult, cap: int = 5) -> dict[str, Any]:
    names = frozenset(s.name for s in benchmark_shapes() if s.agg_fn is not None)
    t0 = time.time()
    rows = []
    for label, policy in (("lru", "lru"), ("lfu", "lfu")):
        est = estimate_capped_from_receipts(plain.event_log, names, cap, policy)
        actual = run_capped(
            day,
            names,
            cap,
            lru_admission if policy == "lru" else lfu_admission,
            f"{label}-cap{cap}",
            fast=True,
        )
        rows.append(
            {
                "policy": f"{label}-cap{cap}",
                "estimated_db_reads": est["db_reads"],
                "actual_db_reads": actual.db_reads,
                "estimated_hits": est["hits"],
                "actual_hits": actual.hits,
                "estimate_seconds": est["seconds"],
            }
        )
    return {"rows": rows, "seconds": round(time.time() - t0, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("docs/experiments"))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--writes", type=int, default=6000)
    parser.add_argument(
        "--only", nargs="*", default=None, help="subset of: baselines e1 e2 e3 e4 e5"
    )
    args = parser.parse_args(argv)
    if not HAS_NUMPY:
        print("numpy is required: pip install 'smplcache[matrix]'")
        return 2
    day = generate_day(seed=args.seed, writes=args.writes)
    out: dict[str, Any] = {
        "seed": args.seed,
        "writes": day.writes,
        "reads": day.reads,
        "phase_change_at_write": day.phase_change_at,
    }
    wanted = set(args.only or ["baselines", "e1", "e2", "e3", "e4", "e5"])
    base = run_baselines(day)
    plain = base.pop("_plain")
    out["baselines"] = base
    print("baselines", json.dumps(base["rows"], indent=0))
    runners: dict[str, Callable[[], dict[str, Any]]] = {
        "e1": lambda: run_e1(day, plain),
        "e2": lambda: run_e2(day),
        "e3": lambda: run_e3(day),
        "e4": lambda: run_e4(day, plain),
        "e5": lambda: run_e5(day, plain),
    }
    for key, fn in runners.items():
        if key in wanted:
            out[key] = fn()
            print(key, "done in", out[key]["seconds"], "s")
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "results.json"
    if args.only and target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if (previous.get("seed"), previous.get("writes")) == (
            out["seed"],
            out["writes"],
        ):
            previous.update(out)  # a partial rerun replaces only the experiments it ran
            out = previous
    target.write_text(
        json.dumps(out, indent=1, sort_keys=True, default=str), encoding="utf-8"
    )
    print("wrote", args.out / "results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
