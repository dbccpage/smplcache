# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""Self-tuning: invariants, the engine in SmplCache, the store port, and the mutation protocol."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
import unittest
from unittest import mock

from smplcache.smplcache import (
    Certificate,
    Decision,
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
)
from smplcache.tuning import (
    VENDORED_ENGINE_SHA256,
    Candidate,
    Loop,
    MistakeMemory,
    Policy,
    PolicyAuthorizer,
    ReceiptChainBroken,
    ReceiptLog,
    RoleConflict,
    SelfAuthorization,
    Verdict,
    authorize,
    replay,
    vendored_engine_sha256,
)
import smplcache.tuning.replay as rp
from smplcache.tuning.bandit import (
    ThompsonAdmission,
    lfu_admission,
    lru_admission,
    run_capped,
)
from smplcache.tuning.counterfactual import estimate_capped_from_receipts
from smplcache.tuning.mistakes import (
    CounterfactualEvaluator,
    RereadSuggestionProposer,
    cluster_rereads,
    with_full_before_images,
)
from smplcache.tuning.replay import (
    CacheConfig,
    CacheSim,
    benchmark_shapes,
    generate_day,
    run_plain,
    run_ttl,
)
from smplcache.tuning.search import (
    HeldOutEvaluator,
    Rollout,
    audit_config_receipts,
    greedy_drop,
    uct_search,
)
from smplcache.tuning.store import (
    InMemoryStoreAdapter,
    RedisStoreAdapter,
    StoreApplicationError,
)

try:
    import numpy  # noqa: F401

    HAS_NUMPY = True
except ImportError:  # pragma: no cover
    HAS_NUMPY = False

SMALL = dict(seed=3, writes=600)
NAMES = [s.name for s in benchmark_shapes()]
CACHEABLE = frozenset(s.name for s in benchmark_shapes() if s.agg_fn is not None)


class _NullMemory(MistakeMemory):
    def remember(self, mistake: Any) -> None:
        return None


class _Proposer:
    name = "p"

    def __init__(self, cands: Sequence[Candidate]) -> None:
        self.cands = list(cands)

    def propose(self, context: Mapping[str, Any]) -> Sequence[Candidate]:
        return self.cands


class _Evaluator:
    name = "e"

    def __init__(self, status: str) -> None:
        self.status = status

    def evaluate(self, candidate: Candidate, history: Any) -> Verdict:
        return Verdict(self.status, self.name, "fixed", {"improvement": 1.0})  # type: ignore[arg-type]


class TestVendoredEngine(unittest.TestCase):
    def test_vendored_engine_matches_recorded_hash(self) -> None:
        self.assertEqual(vendored_engine_sha256(), VENDORED_ENGINE_SHA256)

    def test_engine_separation_rules_hold_in_smplcache(self) -> None:
        cand = Candidate("tweak", {"x": 1}, "p")
        policy = Policy("t", auto_approve_kinds=frozenset({"tweak"}))

        class SelfStamp:
            name = "p"  # same name as the proposer

            def authorize(self, c: Candidate, v: Verdict, p: Policy) -> Any:
                raise AssertionError("never reached")

        with self.assertRaises(RoleConflict):
            Loop(_Proposer([cand]), _Evaluator("supported"), SelfStamp(), policy)
        with self.assertRaises(SelfAuthorization):
            authorize(
                SelfStamp(),
                cand,
                Verdict("supported", "e", "ok", {"improvement": 1.0}),
                policy,
            )
        denied = authorize(
            PolicyAuthorizer(), cand, Verdict("refuted", "e", "no"), policy
        )
        self.assertEqual(denied.status, "denied")


class TestReplayInvariants(unittest.TestCase):
    def test_plain_replay_has_no_oracle_mismatch_and_ttl_does(self) -> None:
        day = generate_day(**SMALL)
        plain = run_plain(day)
        self.assertEqual(plain.oracle_mismatches, 0)
        self.assertGreater(plain.oracle_checks, 50)
        self.assertGreater(plain.hit_rate, 0.5)
        ttl = run_ttl(day, 200)
        self.assertGreater(
            ttl.oracle_mismatches, 0
        )  # the TTL baseline's defined failure, measured

    def test_window_slices_carry_database_state(self) -> None:
        day = generate_day(**SMALL)
        window = day.slice(len(day.stream) // 2, len(day.stream))
        self.assertEqual(run_plain(window).oracle_mismatches, 0)

    def test_partition_store_only_adds_collateral_never_mismatch(self) -> None:
        day = generate_day(**SMALL)
        part = {n: ("a" if i % 2 else "b") for i, n in enumerate(NAMES)}
        res = CacheSim(
            day, CacheConfig(frozenset(NAMES), part, "partition", label="p")
        ).run()
        self.assertEqual(res.oracle_mismatches, 0)
        self.assertGreaterEqual(res.rereads, run_plain(day).rereads)

    def test_capped_admission_policies_keep_oracle_clean(self) -> None:
        day = generate_day(**SMALL)
        for label, policy in (
            ("lru", lru_admission),
            ("lfu", lfu_admission),
            ("ts", ThompsonAdmission(None, 1)),
        ):
            res = run_capped(day, CACHEABLE, 4, policy, label)
            self.assertEqual(res.oracle_mismatches, 0, label)
            self.assertLessEqual(
                max(len(CacheSim(day, CacheConfig(CACHEABLE)).resident()), 0), 11
            )

    def test_counterfactual_estimate_matches_actual_capped_replay(self) -> None:
        day = generate_day(**SMALL)
        plain = run_plain(day)
        for policy, fn in (("lru", lru_admission), ("lfu", lfu_admission)):
            est = estimate_capped_from_receipts(plain.event_log, CACHEABLE, 4, policy)
            actual = run_capped(day, CACHEABLE, 4, fn, policy, fast=True)
            self.assertEqual(
                (est["db_reads"], est["hits"]), (actual.db_reads, actual.hits), policy
            )


@unittest.skipUnless(HAS_NUMPY, "numpy extra not installed")
class TestSpectral(unittest.TestCase):
    def test_density_matrix_entropy_and_partition(self) -> None:
        from smplcache.tuning.spectral import (
            EntropyDriftDetector,
            density_matrix,
            eigenmode_partition,
            von_neumann_entropy,
        )

        vecs = [frozenset({"a", "b"})] * 5 + [frozenset({"c"})] * 5
        rho = density_matrix(vecs, ["a", "b", "c", "d"])
        self.assertAlmostEqual(float(rho.trace()), 1.0)
        s = von_neumann_entropy(rho)
        self.assertGreater(s, 0.5)
        part = eigenmode_partition(rho, ["a", "b", "c", "d"], 2)
        self.assertEqual(part["a"], part["b"])
        self.assertNotEqual(part["a"], part["c"])
        det = EntropyDriftDetector(["a", "b", "c", "d"], window=10, threshold_bits=0.3)
        for v in vecs:
            det.observe(v)
        drifted, entropy = det.check()
        self.assertTrue(
            drifted
        )  # first window with enough data counts as drift from "no reference"
        det.accept(entropy)
        self.assertFalse(det.check()[0])

    def test_repartition_loop_declines_a_bad_partition(self) -> None:
        from smplcache.tuning.spectral import (
            PartitionReplayEvaluator,
            relation_partition,
        )

        day = generate_day(**SMALL)
        current = relation_partition(NAMES)
        bad = {n: "one" for n in NAMES}
        cand = Candidate(
            "repartition", {"k": 1, "partition": bad}, "spectral-repartition-proposer"
        )
        verdict = PartitionReplayEvaluator(min_writes=100).evaluate(
            cand,
            {
                "day": day,
                "current_partition": current,
                "materialized": frozenset(NAMES),
            },
        )
        self.assertEqual(verdict.status, "refuted")


class TestSearchAndBandit(unittest.TestCase):
    def test_uct_never_beats_its_own_rollout_scores_and_greedy_is_consistent(
        self,
    ) -> None:
        day = generate_day(**SMALL)
        rollout = Rollout(day, memory_cost=0.5)
        names = sorted(CACHEABLE)
        best, value, stats = uct_search(
            frozenset(names), names, rollout, budget=6, depth=2
        )
        self.assertEqual(value, rollout.score(best))
        self.assertLessEqual(stats["rollouts"], 7)
        g_state, g_value = greedy_drop(frozenset(names), names, rollout)
        self.assertGreaterEqual(g_value, rollout.score(frozenset(names)))

    def test_held_out_evaluator_and_audit_agree(self) -> None:
        day = generate_day(**SMALL)
        names = sorted(CACHEABLE)
        cand = Candidate(
            "materialize_set",
            {"materialized": names[:5], "memory_cost": 0.5},
            "uct-config-proposer",
        )
        loop = Loop(
            _Proposer([cand]),
            HeldOutEvaluator(),
            PolicyAuthorizer(),
            Policy("t", auto_approve_kinds=frozenset({"materialize_set"})),
        )
        loop.run_once(
            {}, {"day": day, "materialized": frozenset(names)}, run_label="run-1"
        )
        self.assertEqual(audit_config_receipts(loop.log, day, frozenset(names)), [])

    def test_admission_prior_learned_from_receipts(self) -> None:
        day = generate_day(**SMALL)
        first = run_capped(
            day, CACHEABLE, 4, ThompsonAdmission(None, 1), "learn", fast=True
        )
        prior = ThompsonAdmission.from_result(first)
        self.assertEqual(set(prior.prior), set(first.per_shape))
        for a, b in prior.prior.values():
            self.assertGreaterEqual(a, 1.0)
            self.assertGreaterEqual(b, 1.0)


class TestMistakes(unittest.TestCase):
    def test_reread_clusters_become_human_reviewed_suggestions(self) -> None:
        day = generate_day(**SMALL)
        plain = run_plain(day)
        clusters = cluster_rereads(plain)
        reasons = {c.reason for c in clusters}
        self.assertIn("missing_evidence_for_repair", reasons)
        self.assertIn("unsupported_shape", reasons)
        loop = Loop(
            RereadSuggestionProposer(),
            CounterfactualEvaluator(),
            PolicyAuthorizer(),
            Policy(
                "e4",
                human_review_kinds=frozenset({"replica_identity_full", "admit_shape"}),
            ),
        )
        applied: list[Candidate] = []
        summary = loop.run_once(
            {"result": plain},
            {"day": day, "materialized": frozenset(NAMES)},
            apply=lambda c: applied.append(c) or {},
            run_label="run-1",
        )
        self.assertGreaterEqual(summary.needs_human, 2)
        self.assertEqual(summary.applied, 0)
        self.assertEqual(applied, [])  # suggestions never self-apply
        kinds = {c.kind for c, _, _ in summary.pending_human}
        self.assertEqual(kinds, {"replica_identity_full", "admit_shape"})
        supported = [
            v for c, v, _ in summary.pending_human if c.kind == "replica_identity_full"
        ]
        self.assertTrue(all(v.status == "supported" for v in supported))
        self.assertEqual(loop.log.verify(), -1)

    def test_full_before_images_remove_missing_evidence_and_keep_oracle_clean(
        self,
    ) -> None:
        day = generate_day(**SMALL)
        fixed = with_full_before_images(day, "customers")
        res = run_plain(fixed)
        self.assertEqual(res.oracle_mismatches, 0)
        self.assertEqual(
            sum(
                s.reasons.get("missing_evidence_for_repair", 0)
                for s in res.per_shape.values()
            ),
            0,
        )


class TestStorePort(unittest.TestCase):
    def _decision(self, kind: DecisionKind) -> Decision:
        cert = Certificate("s", "evt-1", "orders", kind, "")
        if kind == DecisionKind.REPAIR:
            return Decision.repair("value_change", cert)
        if kind == DecisionKind.PRESERVE:
            return Decision.preserve("disjoint_columns", cert)
        return Decision.invalidate("missing_evidence_for_repair", cert)

    def test_in_memory_adapter_evicts_and_keeps_receipts(self) -> None:
        log = ReceiptLog()
        store = InMemoryStoreAdapter(max_certificates=2, receipt_log=log)
        store.set("k", {"v": 1})
        cert = store.apply_certified_decision("k", self._decision(DecisionKind.REPAIR))
        self.assertEqual(cert.decision_kind, DecisionKind.INVALIDATE)
        self.assertEqual(cert.reason_code, "repair_execution_unavailable")
        self.assertIsNone(store.get("k"))
        store.set("k", 2)
        store.apply_certified_decision("k", self._decision(DecisionKind.PRESERVE))
        self.assertEqual(store.get("k"), 2)
        with self.assertRaises(StoreApplicationError):
            store.apply_certified_decision(
                "k", self._decision(DecisionKind.INVALIDATE)
            )  # cap reached, refused before touching
        self.assertEqual(store.get("k"), 2)
        self.assertEqual(len(store.get_certificates()), 2)
        self.assertEqual(len(log), 2)
        self.assertEqual(log.verify(), -1)

    def test_redis_adapter_without_client_stores_receipt_next_to_key(self) -> None:
        store = RedisStoreAdapter()
        store.set("orders:sum", {"north": 5})
        cert = store.apply_certified_decision(
            "orders:sum", self._decision(DecisionKind.INVALIDATE)
        )
        self.assertIsNone(store.get("orders:sum"))
        receipt = store.get(store.receipt_key("orders:sum", cert.event_id))
        self.assertEqual(receipt["decision"], "invalidate")
        self.assertEqual(receipt["reason"], "missing_evidence_for_repair")

    def test_redis_adapter_with_unacknowledged_set_fails(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.d: dict[str, str] = {}

            def get(self, k: str) -> Any:
                return self.d.get(k)

            def set(self, k: str, v: str) -> Any:
                self.d[k] = v
                return None  # not acknowledged

            def delete(self, k: str) -> int:
                return int(self.d.pop(k, None) is not None)

        store = RedisStoreAdapter(Client())
        store.set("k", 1)
        with self.assertRaises(StoreApplicationError):
            store.apply_certified_decision("k", self._decision(DecisionKind.INVALIDATE))


# --- Part 3: the mutation protocol. One test per row of docs/MUTATIONS.md. ---------------------


class TestMutations(unittest.TestCase):
    def test_m1_keep_on_a_write_that_touches_the_query_is_caught_by_the_oracle(
        self,
    ) -> None:
        day = generate_day(**SMALL)
        real = rp.process_event

        def keep(shape: QueryShape, cache: Any, event: WriteEvent) -> Decision:
            d = real(shape, cache, event)
            if d.kind in (DecisionKind.INVALIDATE, DecisionKind.UNSUPPORTED):
                return Decision.preserve("mutated_keep", d.certificate)
            return d

        with mock.patch.object(rp, "process_event", keep):
            mutated = run_plain(day)
        self.assertGreater(mutated.oracle_mismatches, 0)
        self.assertEqual(run_plain(day).oracle_mismatches, 0)  # fix: real certifier
        with mock.patch.object(rp, "process_event", keep):
            self.assertGreater(
                run_plain(day).oracle_mismatches, 0
            )  # re-applied: caught again

    def test_m2_drift_threshold_zero_is_refused(self) -> None:
        if not HAS_NUMPY:
            self.skipTest("numpy extra not installed")
        from smplcache.tuning.spectral import EntropyDriftDetector

        with self.assertRaises(ValueError):
            EntropyDriftDetector(NAMES, threshold_bits=0.0)
        EntropyDriftDetector(NAMES, threshold_bits=0.3)

    def test_m3_proposer_authorizing_itself_is_refused(self) -> None:
        cand = Candidate("materialize_set", {"materialized": []}, "uct-config-proposer")

        class Forward:
            name = "forwarder"

            def authorize(self, c: Candidate, v: Verdict, p: Policy) -> Any:
                from smplcache.tuning import Authorization

                return Authorization("approved", c.origin, "self", p.policy_id)

        with self.assertRaises(SelfAuthorization):
            authorize(
                Forward(),
                cand,
                Verdict("supported", "e", "ok", {"improvement": 1.0}),
                Policy("t"),
            )

    def test_m4_one_corrupted_receipt_hash_is_caught(self) -> None:
        loop = Loop(
            _Proposer([Candidate("tweak", {}, "p")]),
            _Evaluator("supported"),
            PolicyAuthorizer(),
            Policy("t", auto_approve_kinds=frozenset({"tweak"})),
        )
        loop.run_once({}, None, apply=lambda c: {})
        receipts = loop.log.to_json()
        receipts[2]["hash"] = receipts[2]["hash"][::-1]
        bad = ReceiptLog.from_json(receipts)
        self.assertEqual(bad.verify(), 2)
        with self.assertRaises(ReceiptChainBroken):
            replay(bad)
        self.assertEqual(loop.log.verify(), -1)

    def test_m5_mistake_memory_removed_repeats_the_mistake(self) -> None:
        cand = Candidate("tweak", {"x": 1}, "p")
        policy = Policy("t", auto_approve_kinds=frozenset({"tweak"}))
        mutated = Loop(
            _Proposer([cand]),
            _Evaluator("refuted"),
            PolicyAuthorizer(),
            policy,
            memory=_NullMemory(),
        )
        mutated.run_once({}, None)
        self.assertEqual(mutated.run_once({}, None).refuted, 1)
        fixed = Loop(
            _Proposer([cand]), _Evaluator("refuted"), PolicyAuthorizer(), policy
        )
        fixed.run_once({}, None)
        self.assertEqual(fixed.run_once({}, None).skipped_known_mistakes, 1)

    def test_m6_store_that_ignores_invalidation_is_caught_by_the_oracle(self) -> None:
        day = generate_day(**SMALL)
        with mock.patch.object(
            CacheSim, "_drop", lambda self, name, reason, collateral=False: None
        ):
            mutated = run_plain(day)
        self.assertGreater(mutated.oracle_mismatches, 0)
        self.assertEqual(run_plain(day).oracle_mismatches, 0)

    def test_m7_window_slice_without_state_is_caught_by_the_oracle(self) -> None:
        day = generate_day(**SMALL)
        start = len(day.stream) // 2
        mutated = rp.Day(day.seed_rows, day.stream[start:], day.seed, 0)
        self.assertGreater(run_plain(mutated).oracle_mismatches, 0)
        self.assertEqual(
            run_plain(day.slice(start, len(day.stream))).oracle_mismatches, 0
        )

    def test_m8_lying_held_out_evaluator_is_caught_by_the_receipt_audit(self) -> None:
        day = generate_day(**SMALL)
        names = sorted(CACHEABLE)
        cand = Candidate(
            "materialize_set",
            {"materialized": names[:3], "memory_cost": 0.5},
            "uct-config-proposer",
        )

        class Liar:
            name = "held-out-config-evaluator"

            def evaluate(self, c: Candidate, h: Any) -> Verdict:
                return Verdict(
                    "supported",
                    self.name,
                    "mutated",
                    {"improvement": 1.0, "db_reads_before": 0, "db_reads_after": 0},
                )

        loop = Loop(
            _Proposer([cand]),
            Liar(),
            PolicyAuthorizer(),
            Policy("t", auto_approve_kinds=frozenset({"materialize_set"})),
        )
        loop.run_once(
            {}, {"day": day, "materialized": frozenset(names)}, run_label="run-1"
        )
        self.assertTrue(audit_config_receipts(loop.log, day, frozenset(names)))
        honest = Loop(
            _Proposer([cand]),
            HeldOutEvaluator(),
            PolicyAuthorizer(),
            Policy("t", auto_approve_kinds=frozenset({"materialize_set"})),
        )
        honest.run_once(
            {}, {"day": day, "materialized": frozenset(names)}, run_label="run-1"
        )
        self.assertEqual(audit_config_receipts(honest.log, day, frozenset(names)), [])

    def test_m9_store_adapter_whose_delete_fails_is_caught(self) -> None:
        store = InMemoryStoreAdapter()
        store.set("k", 1)
        cert = Certificate("s", "evt", "orders", DecisionKind.INVALIDATE, "")
        with mock.patch.object(InMemoryStoreAdapter, "delete", lambda self, key: False):
            with self.assertRaises(StoreApplicationError):
                store.apply_certified_decision("k", Decision.invalidate("x", cert))
        self.assertEqual(
            store.get_certificates(), []
        )  # no receipt for a failed application
        store.apply_certified_decision("k", Decision.invalidate("x", cert))
        self.assertIsNone(store.get("k"))

    def test_m10_vendored_engine_edit_is_caught_by_the_hash(self) -> None:
        with mock.patch("smplcache.tuning.Path.read_bytes", lambda self: b"mutated"):
            self.assertNotEqual(vendored_engine_sha256(), VENDORED_ENGINE_SHA256)
        self.assertEqual(vendored_engine_sha256(), VENDORED_ENGINE_SHA256)

    def test_m11_certifier_itself_refuses_repair_without_before_image(self) -> None:
        shape = QueryShape("customers", "sum", "credit", ("region",), name="s")
        event = WriteEvent(
            "customers",
            set(),
            {"id": 1},
            {"id": 1, "region": "north", "credit": 5},
            "e",
            "UPDATE",
            unavailable_cols={"region", "credit"},
        )
        self.assertEqual(certify_event(shape, event).kind, DecisionKind.INVALIDATE)
        full = WriteEvent(
            "customers",
            set(),
            {"id": 1, "region": "north", "credit": 4},
            {"id": 1, "region": "north", "credit": 5},
            "e",
            "UPDATE",
        )
        self.assertEqual(certify_event(shape, full).kind, DecisionKind.REPAIR)


if __name__ == "__main__":
    unittest.main()
