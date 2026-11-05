# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Acceptance and adversarial tests for the SmplCache Live MVP (`demo/`).

Verifies real engine execution, independent SQLite oracle agreement, stale-value
withholding, latched invalidation, join fanout and bounded eviction fallback,
multi-table commit/rollback, counter reconciliation, fault-injection mismatch
halting, concurrent request isolation, and API input bounds.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from demo.api.app import create_app
from demo.scenarios.runner import ScenarioConfig, execute_scenario_run

client = TestClient(create_app())


def test_chunked_body_cannot_bypass_size_limit() -> None:
    response = client.post(
        "/api/runs",
        content=iter([b"x" * 9000, b"x" * 9000]),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_spa_does_not_serve_sibling_with_matching_prefix(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("shell", encoding="utf-8")
    sibling = tmp_path / "dist-private"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("private-data", encoding="utf-8")
    test_client = TestClient(create_app(dist))
    response = test_client.get("/..%2Fdist-private%2Fsecret.txt")
    assert "private-data" not in response.text


def test_healthz_and_version_endpoints() -> None:
    r_health = client.get("/healthz")
    assert r_health.status_code == 200
    body = r_health.json()
    assert body["status"] == "ok"
    assert body["service"] == "smplcache-live"
    assert body["app_commit"]
    assert body["engine_commit"]

    r_ver = client.get("/api/version")
    assert r_ver.status_code == 200
    vbody = r_ver.json()
    assert vbody["api_version"] == "1.0.0"
    assert vbody["engine_commit"] == body["engine_commit"]


def test_scenarios_metadata_and_live_rejected_sql_examples() -> None:
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    scenario_ids = [s["scenario_id"] for s in data["scenarios"]]
    assert scenario_ids == [
        "live_revenue",
        "customers_and_regions",
        "missing_evidence",
    ]
    rejected = data["rejected_sql_examples"]
    assert len(rejected) >= 5
    for item in rejected:
        assert item["is_supported"] is False
        assert item["rejection_reason"]


def test_live_revenue_walkthrough_decisions_and_oracle_parity() -> None:
    resp = client.post(
        "/api/runs",
        json={"scenario_id": "live_revenue", "seed": 42, "dataset_size": 1000},
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["summary"]["status"] == "verified"
    assert run["summary"]["mismatch_count"] == 0
    assert run["initial_state"]["oracle_match"] is True
    assert "Pilot Sandbox" in run["initial_state"]["smplcache_result_cents"]

    boundaries = run["boundaries"]
    assert len(boundaries) == 7

    # Step 1: Disjoint delivery note -> preserve, 0 SmplCache queries, 1 baseline query
    b1 = boundaries[0]
    assert b1["decision_kind"] == "preserve"
    assert b1["reason_code"] == "disjoint_columns"
    assert b1["boundary_counters"]["baseline_source_queries"] == 1
    assert b1["boundary_counters"]["smplcache_source_queries"] == 0
    assert b1["oracle_match"] is True

    # Step 2: Paid order amount $100 -> $125 (+2500 cents in North America)
    b2 = boundaries[1]
    assert b2["decision_kind"] == "repair"
    assert b2["reason_code"] == "value_change"
    assert (
        b2["smplcache_result_cents"]["North America"]
        == b1["smplcache_result_cents"]["North America"] + 2500
    )
    assert b2["oracle_match"] is True

    # Step 3: Predicate entry (pending -> paid, +8000 cents in Europe)
    b3 = boundaries[2]
    assert b3["decision_kind"] == "repair"
    assert b3["reason_code"] == "predicate_entry"
    assert (
        b3["smplcache_result_cents"]["Europe"]
        == b2["smplcache_result_cents"]["Europe"] + 8000
    )

    # Step 4: Group move (North America -> Europe, 15000 cents)
    b4 = boundaries[3]
    assert b4["decision_kind"] == "repair"
    assert b4["reason_code"] == "group_move"
    assert (
        b4["smplcache_result_cents"]["North America"]
        == b3["smplcache_result_cents"]["North America"] - 15000
    )
    assert (
        b4["smplcache_result_cents"]["Europe"]
        == b3["smplcache_result_cents"]["Europe"] + 15000
    )

    # Step 5: Delete sole member of 'Pilot Sandbox' group -> group key removed cleanly
    b5 = boundaries[4]
    assert b5["decision_kind"] == "repair"
    assert b5["reason_code"] == "delete"
    assert "Pilot Sandbox" not in b5["smplcache_result_cents"]
    assert "Pilot Sandbox" not in b5["oracle_result_cents"]

    # Step 6: Missing evidence -> invalidate, withhold stale value, rebuild from SQLite
    b6 = boundaries[5]
    assert b6["decision_kind"] == "invalidate"
    assert b6["reason_code"] == "missing_evidence_for_repair"
    assert b6["stale_value_withheld"] is True
    assert b6["smplcache_pre_rebuild_result"] is None
    assert b6["freshness_state"] == "rebuilt_verified"
    assert b6["boundary_counters"]["smplcache_rebuilds"] == 1
    assert b6["boundary_counters"]["smplcache_source_queries"] == 1
    assert b6["oracle_match"] is True

    # Step 7: Post-rebuild complete event -> clean delta repair (+3000 cents in Europe)
    b7 = boundaries[6]
    assert b7["decision_kind"] == "repair"
    assert b7["reason_code"] == "value_change"
    assert (
        b7["smplcache_result_cents"]["Europe"]
        == b6["smplcache_result_cents"]["Europe"] + 3000
    )
    assert b7["oracle_match"] is True


def test_latched_invalidation_blocks_further_repair_when_rebuild_deferred() -> None:
    run = execute_scenario_run(
        ScenarioConfig(
            scenario_id="live_revenue",
            seed=42,
            dataset_size=100,
            defer_rebuild=True,
        )
    )
    b6 = run["boundaries"][5]
    b7 = run["boundaries"][6]
    assert b6["decision_kind"] == "invalidate"
    assert b6["reason_code"] == "missing_evidence_for_repair"
    assert b6["freshness_state"] == "invalidated_withheld"
    assert b6["smplcache_result_cents"] is None

    # Step 7 has complete before/after evidence, but because rebuild was deferred,
    # the cache remains latched invalid and refuses repair!
    assert b7["decision_kind"] == "invalidate"
    assert b7["reason_code"] == "cache_requires_rebuild"
    assert b7["freshness_state"] == "invalidated_withheld"
    assert b7["smplcache_result_cents"] is None


def test_customers_and_regions_join_fanout_and_transactions() -> None:
    resp = client.post(
        "/api/runs",
        json={
            "scenario_id": "customers_and_regions",
            "seed": 42,
            "dataset_size": 1000,
            "aux_max_total_rows": None,
        },
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["summary"]["status"] == "verified"
    assert run["summary"]["mismatch_count"] == 0

    b_move = run["boundaries"][2]
    assert b_move["boundary_id"] == "join_step_3_move_customer_region"
    assert b_move["decision_kind"] == "repair"
    assert b_move["reason_code"] == "dimension_update"
    assert b_move["aux_telemetry"]["affected_fanout_count"] >= 4
    assert b_move["oracle_match"] is True

    # Step 5: Multi-table transaction COMMIT
    b_commit = run["boundaries"][4]
    assert b_commit["boundary_id"] == "join_step_5_multi_table_tx_commit"
    assert b_commit["decision_kind"] == "repair"
    assert b_commit["oracle_match"] is True

    # Step 6: Multi-table transaction ROLLBACK
    b_rollback = run["boundaries"][5]
    assert b_rollback["boundary_id"] == "join_step_6_tx_rollback"
    assert b_rollback["decision_kind"] == "preserve"
    assert b_rollback["reason_code"] == "transaction_rollback"
    assert b_rollback["boundary_counters"]["baseline_source_queries"] == 0
    assert b_rollback["boundary_counters"]["smplcache_source_queries"] == 0

    # Step 7: Join rebuild reads 3 source tables (3 source queries vs. 1 baseline query)
    b_rebuild = run["boundaries"][6]
    assert b_rebuild["decision_kind"] == "invalidate"
    assert b_rebuild["boundary_counters"]["smplcache_rebuilds"] == 1
    assert b_rebuild["boundary_counters"]["smplcache_source_queries"] == 3
    assert b_rebuild["boundary_counters"]["baseline_source_queries"] == 1


def test_customers_and_regions_lowered_capacity_eviction_and_honest_counters() -> None:
    resp = client.post(
        "/api/runs",
        json={
            "scenario_id": "customers_and_regions",
            "seed": 42,
            "dataset_size": 1000,
            "aux_max_total_rows": 60,
        },
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["summary"]["status"] == "verified"
    assert run["summary"]["mismatch_count"] == 0

    eviction_boundaries = [
        b for b in run["boundaries"] if b["reason_code"] == "evicted_join_auxiliary_key"
    ]
    assert len(eviction_boundaries) >= 1
    for eb in eviction_boundaries:
        assert eb["decision_kind"] == "invalidate"
        assert eb["boundary_counters"]["smplcache_rebuilds"] == 1
        assert eb["boundary_counters"]["smplcache_source_queries"] == 3
        assert eb["aux_telemetry"]["resident_aux_rows"] <= 60
        assert eb["oracle_match"] is True

    # Because each 3-table join rebuild costs 3 source queries vs. 1 baseline query,
    # multiple eviction rebuilds make source_queries_avoided negative. Verify it is
    # reported honestly without clamping to 0.
    assert run["summary"]["source_queries_avoided"] < 0
    assert (
        run["summary"]["source_queries_avoided"]
        == run["summary"]["baseline_source_queries"]
        - run["summary"]["smplcache_source_queries"]
    )


def test_missing_evidence_wal_scenario_and_cross_schema_isolation() -> None:
    resp = client.post(
        "/api/runs",
        json={"scenario_id": "missing_evidence", "seed": 42, "dataset_size": 500},
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["summary"]["status"] == "verified"
    assert run["summary"]["mismatch_count"] == 0

    b_missing = run["boundaries"][1]
    assert b_missing["reason_code"] == "missing_evidence_for_repair"
    assert b_missing["stale_value_withheld"] is True

    b_toast = run["boundaries"][2]
    assert b_toast["reason_code"] == "missing_evidence_for_repair"
    assert b_toast["stale_value_withheld"] is True

    b_schema = run["boundaries"][3]
    assert b_schema["boundary_id"] == "wal_step_4_cross_schema_isolation"
    assert b_schema["decision_kind"] == "preserve"
    assert b_schema["reason_code"] == "unrelated_relation"
    assert b_schema["boundary_counters"]["baseline_source_queries"] == 0
    assert b_schema["boundary_counters"]["smplcache_source_queries"] == 0
    assert b_schema["smplcache_result_cents"] == b_toast["smplcache_result_cents"]

    b_post = run["boundaries"][5]
    assert b_post["decision_kind"] == "repair"
    assert b_post["reason_code"] == "value_change"
    assert b_post["oracle_match"] is True


def test_fault_injection_corrupt_value_triggers_mismatch_and_halts_run() -> None:
    run = execute_scenario_run(
        ScenarioConfig(
            scenario_id="live_revenue",
            seed=42,
            dataset_size=100,
            _fault_inject_corrupt_step=2,
        )
    )
    assert run["summary"]["status"] == "mismatch"
    assert run["summary"]["halted_on_mismatch"] is True
    assert run["summary"]["mismatch_count"] == 1
    assert run["summary"]["first_failing_boundary"] == 2
    # Playback must stop at boundary 2 rather than continuing through boundaries 3..7
    assert len(run["boundaries"]) == 2
    assert run["boundaries"][1]["freshness_state"] == "mismatch"
    assert run["boundaries"][1]["ui_badge"] == "Mismatch"
    assert run["boundaries"][1]["oracle_match"] is False


def test_deterministic_seed_replay_and_counter_reconciliation() -> None:
    r1 = client.post(
        "/api/runs",
        json={"scenario_id": "live_revenue", "seed": 777, "dataset_size": 500},
    ).json()
    r2 = client.post(
        "/api/runs",
        json={"scenario_id": "live_revenue", "seed": 777, "dataset_size": 500},
    ).json()
    assert r1["canonical_hash"] == r2["canonical_hash"]
    assert r1["boundaries"] == r2["boundaries"]
    assert r1["summary"] == r2["summary"]

    # Reconcile query_log with summary counters
    qlog = r1["query_log"]
    s = r1["summary"]
    assert (
        sum(1 for q in qlog if q["category"] == "initialization")
        == s["initialization_queries"]
    )
    assert (
        sum(1 for q in qlog if q["category"] == "baseline")
        == s["baseline_source_queries"]
    )
    assert (
        sum(1 for q in qlog if q["category"] == "smplcache_rebuild")
        == s["smplcache_source_queries"]
    )
    assert (
        sum(1 for q in qlog if q["category"] == "oracle_verification")
        == s["oracle_queries"]
    )


def test_concurrent_runs_do_not_share_mutable_state() -> None:
    configs = [
        ScenarioConfig("live_revenue", seed=11, dataset_size=100),
        ScenarioConfig(
            "customers_and_regions", seed=22, dataset_size=500, aux_max_total_rows=60
        ),
        ScenarioConfig("missing_evidence", seed=33, dataset_size=1000),
        ScenarioConfig(
            "live_revenue",
            seed=44,
            dataset_size=500,
            evidence_mode="remove_required_evidence",
        ),
        ScenarioConfig(
            "customers_and_regions", seed=55, dataset_size=1000, aux_max_total_rows=1500
        ),
        ScenarioConfig("live_revenue", seed=11, dataset_size=100),
    ]
    expected_hashes = [execute_scenario_run(c)["canonical_hash"] for c in configs]
    with ThreadPoolExecutor(max_workers=6) as pool:
        concurrent_runs = list(pool.map(execute_scenario_run, configs))
    actual_hashes = [r["canonical_hash"] for r in concurrent_runs]
    assert actual_hashes == expected_hashes
    assert all(r["summary"]["mismatch_count"] == 0 for r in concurrent_runs)


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"scenario_id": "arbitrary_sql", "seed": 42},
        {"scenario_id": "live_revenue", "sql": "SELECT * FROM users"},
        {"scenario_id": "live_revenue", "seed": 0},
        {"scenario_id": "live_revenue", "dataset_size": 999999},
        {"scenario_id": "customers_and_regions", "aux_max_total_rows": 7},
    ],
)
def test_invalid_run_requests_rejected_with_422(bad_payload: dict[str, Any]) -> None:
    resp = client.post("/api/runs", json=bad_payload)
    assert resp.status_code == 422


def test_unknown_api_route_returns_404_json_never_spa_200() -> None:
    r_get = client.get("/api/nonexistent_endpoint")
    assert r_get.status_code == 404
    assert "detail" in r_get.json()

    r_post = client.post("/api/nonexistent_endpoint", json={})
    assert r_post.status_code == 404
    assert "detail" in r_post.json()


def test_oversized_request_body_returns_413() -> None:
    huge = "x" * (20 * 1024)
    resp = client.post(
        "/api/runs",
        content=huge,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_spa_root_and_static_assets_served() -> None:
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "SmplCache Live" in r_root.text
    assert "/assets/anomalon-" in r_root.text
    assert 'href="/lab/"' in r_root.text

    for path in ("/lab/", "/lab"):
        r_lab = client.get(path)
        assert r_lab.status_code == 200
        assert "/assets/app-" in r_lab.text
        assert "/assets/styles-" in r_lab.text


def test_ambiguous_unqualified_binding_invalidates_rather_than_guessing() -> None:
    from smplcache import DecisionKind, WriteEvent, certify_event, parse_sql

    qualified_shape = parse_sql(
        "SELECT region, SUM(amount_cents) FROM main.orders WHERE status = 'paid' GROUP BY region"
    ).shape
    assert qualified_shape is not None
    unqualified_evt = WriteEvent(
        relation="orders",
        old={
            "id": 101,
            "region": "North America",
            "status": "paid",
            "amount_cents": 10000,
        },
        new={
            "id": 101,
            "region": "North America",
            "status": "paid",
            "amount_cents": 12500,
        },
    )
    dec = certify_event(qualified_shape, unqualified_evt)
    assert dec.kind == DecisionKind.INVALIDATE
    assert dec.reason == "ambiguous_relation"


def test_one_way_dependency_boundary_core_never_imports_demo_or_web() -> None:
    core_dir = Path(__file__).resolve().parent.parent.parent / "smplcache"
    forbidden = {"demo", "fastapi", "uvicorn", "starlette", "pydantic", "httpx"}
    for py_file in core_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert (
                        root_pkg not in forbidden
                    ), f"{py_file.name} imports {root_pkg}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in forbidden, f"{py_file.name} imports {root_pkg}"
