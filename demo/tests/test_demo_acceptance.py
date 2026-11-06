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


def test_front_page_prerendered_rows_and_no_change_yet_before_js() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Keep Source link and demo-only note
    assert "https://github.com/dbccpage/smplcache" in html
    assert "demo only, not a benchmark" in html
    assert "prefers-color-scheme" not in html

    # Pre-rendered rows in #orders, #customers, and #rows before JS runs
    assert "Leave at front desk" in html
    assert "Annual renewal invoice" in html
    assert "Acme Industrial" in html
    assert "$27,092.00" in html
    assert "$21,602.00" in html
    assert "$16,412.00" in html
    assert "$13,037.00" in html

    # Show "no change yet" instead of zeros in the three Queries cells before first click
    assert 'id="redis"' in html and "no change yet" in html
    assert '<td class="q" id="redis">0</td>' not in html
    assert '<td class="q" id="mv">0</td>' not in html
    assert '<td class="q" id="ours">0</td>' not in html

    # Copy record button is present on the front page
    assert "Copy record" in html


def test_shared_moves_module_and_copy_record_and_url_roundtrip_contracts() -> None:
    import json
    import shutil
    import subprocess

    web_src = Path(__file__).resolve().parent.parent / "web" / "src"
    moves_ts = web_src / "moves.ts"
    onepane_ts = web_src / "onepane.ts"
    main_ts = web_src / "main.ts"

    assert moves_ts.exists(), "Shared moves.ts module must exist"
    assert onepane_ts.exists(), "Front-page onepane.ts module must exist"

    moves_text = moves_ts.read_text(encoding="utf-8")
    onepane_text = onepane_ts.read_text(encoding="utf-8")
    main_text = main_ts.read_text(encoding="utf-8")

    # Both / and /lab/ import the shared three-move module
    assert 'from "./moves"' in onepane_text
    assert 'from "./moves"' in main_text

    # Shared module extracts query dependency set and formats arithmetic & plain-text record
    assert "extractQueryDependencies" in moves_text
    assert "classifyChangeMove" in moves_text
    assert "formatFixInPlaceArithmetic" in moves_text
    assert "formatPlainTextRecord" in moves_text
    assert "parseScreenUrlState" in moves_text

    # /lab/ uses plain labels KEEP, FIX IN PLACE, REREAD, has Copy record, and avoids unverified 'Certified' badge
    assert "FIX IN PLACE" in main_text or "decisionKindToMove" in main_text
    assert "Copy record" in main_text
    assert "SmplCache (Certified Incremental)" not in main_text
    assert "demo only, not a benchmark" in main_text

    # Verify /api/runs boundaries include exact change_sql, cache_before, and cache_after
    r_wal = client.post(
        "/api/runs",
        json={"scenario_id": "missing_evidence", "seed": 42, "dataset_size": 100},
    ).json()
    b_wal_step2 = r_wal["boundaries"][1]
    assert (
        b_wal_step2["change_sql"]
        == "UPDATE main.orders SET amount_cents = 24000 WHERE id = 106"
    )
    assert isinstance(b_wal_step2["cache_before"], dict)
    assert isinstance(b_wal_step2["cache_after"], dict)

    b_wal_step5 = r_wal["boundaries"][4]
    assert (
        b_wal_step5["change_sql"]
        == "BEGIN; UPDATE main.orders SET amount_cents = 99900 WHERE id = 104; ROLLBACK"
    )

    node_bin = shutil.which("node")
    if node_bin:
        node_script = """
import * as moves from "./demo/web/src/moves.ts";

const sql = "SELECT r.name, SUM(o.amount_cents) FROM orders o JOIN customers c ON (o.customer_id = c.id) JOIN regions r ON c.region_id = r.id WHERE 'paid' = o.status GROUP BY r.name";
const deps = moves.extractQueryDependencies(sql);

const moveCust = moves.classifyChangeMove({
  sql,
  table: "customers",
  operation: "UPDATE",
  beforeImage: { id: 1, name: "Acme", region_id: 1 },
  afterImage: { id: 1, name: "Acme", region_id: 2 },
  hasMatchedJoinRows: true,
});

const moveOrderFk = moves.classifyChangeMove({
  sql,
  table: "orders",
  operation: "UPDATE",
  beforeImage: { id: 101, customer_id: 1, status: "paid", amount_cents: 10000 },
  afterImage: { id: 101, customer_id: 5, status: "paid", amount_cents: 10000 },
});

const moveEmptyBefore = moves.classifyChangeMove({
  sql,
  table: "orders",
  operation: "UPDATE",
  beforeImage: { id: 106 },
  afterImage: { id: 106 },
});

const moveNullBefore = moves.classifyChangeMove({
  sql,
  table: "orders",
  operation: "UPDATE",
  beforeImage: null,
  afterImage: { id: 106, amount_cents: 24000 },
});

const zeroDeltaArith = moves.formatFixInPlaceArithmetic({
  beforeTotals: { "North America": 10000 },
  afterTotals: { "North America": 10000 },
  beforeImage: { id: 101, amount_cents: 10000 },
  afterImage: { id: 101, amount_cents: 10000 },
  groupOrder: ["North America"],
});

const parsedUrl = moves.parseScreenUrlState("?scenario=front&seed=1&size=small&step=3", {
  scenario: "live_revenue",
  seed: 42,
  size: 1000,
  step: 0,
});

const recordText = moves.formatPlainTextRecord({
  scenario: parsedUrl.scenario,
  seed: parsedUrl.seed,
  size: parsedUrl.size,
  step: parsedUrl.step,
  changeSql: "UPDATE customers SET region_id = 2 WHERE id = 1",
  move: moveCust.move,
  reasonCode: moveCust.reasonCode,
  arithmeticSummary: zeroDeltaArith,
  beforeTotals: { "North America": 10000 },
  afterTotals: { "North America": 10000 },
});

console.log(JSON.stringify({
  deps,
  moveCust,
  moveOrderFk,
  moveEmptyBefore,
  moveNullBefore,
  zeroDeltaArith,
  parsedUrl,
  recordText,
}));
"""
        proc = subprocess.run(
            [node_bin, "--input-type=module", "-e", node_script],
            cwd=Path(__file__).resolve().parent.parent.parent,
            capture_output=True,
            text=True,
            check=True,
        )
        out = json.loads(proc.stdout.strip())
        assert out["deps"]["columnsByTable"]["orders"]["joinKeys"] == ["customer_id"]
        assert out["deps"]["columnsByTable"]["orders"]["readCols"] == ["amount_cents"]
        assert out["deps"]["columnsByTable"]["orders"]["filterCols"] == ["status"]
        assert set(out["deps"]["columnsByTable"]["customers"]["joinKeys"]) == {
            "id",
            "region_id",
        }
        assert out["deps"]["columnsByTable"]["regions"]["groupCols"] == ["name"]
        assert out["moveCust"]["move"] == "FIX IN PLACE"
        assert out["moveOrderFk"]["move"] == "FIX IN PLACE"
        assert out["moveEmptyBefore"]["move"] == "REREAD"
        assert out["moveNullBefore"]["move"] == "REREAD"
        assert "North America: old $100.00" in out["zeroDeltaArith"]
        assert "diff +$0.00" in out["zeroDeltaArith"]
        assert out["parsedUrl"] == {
            "scenario": "customers_and_regions",
            "seed": 1,
            "size": 100,
            "step": 3,
            "cap": None,
        }
        assert "scenario: customers_and_regions" in out["recordText"]
        assert "seed: 1" in out["recordText"]
        assert "size: 100" in out["recordText"]
        assert "step: 3" in out["recordText"]
        assert "move: FIX IN PLACE" in out["recordText"]

