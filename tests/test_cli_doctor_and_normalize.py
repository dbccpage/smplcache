# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import json
from pathlib import Path
from contextlib import redirect_stdout

from smplcache.cli import (
    analyze_workload,
    calculate_topomap,
    compare_workloads,
    main,
    run_doctor,
    run_normalize,
)


def test_calculate_topomap_reports_isolated_singletons_and_preserves_beta1() -> None:
    shapes = ["s_a", "s_b", "s_c", "s_isolated_1", "s_isolated_2"]
    correlations = [
        ("s_a <-> s_b", 4),
        ("s_b <-> s_c", 3),
        ("s_c <-> s_a", 2),
    ]
    topo = calculate_topomap(shapes, correlations)
    assert topo["total_nodes"] == 5
    assert topo["active_nodes"] == 3
    assert topo["isolated_nodes"] == 2
    assert topo["isolated_shapes"] == ["s_isolated_1", "s_isolated_2"]
    assert topo["components"] == 1
    assert topo["total_components"] == 3
    assert topo["beta_1"] == 1


def test_analyze_workload_tracks_repair_vs_invalidate_coinvalidations(
    tmp_path: Path,
) -> None:
    workload = {
        "schema": {
            "orders": {
                "columns": {
                    "id": {"type": "INTEGER"},
                    "region": {"type": "VARCHAR"},
                    "amount": {"type": "NUMERIC"},
                    "notes": {"type": "TEXT"},
                }
            }
        },
        "shapes": [
            {
                "name": "sum_by_region",
                "relation": "orders",
                "query": "SELECT region, SUM(amount) FROM orders GROUP BY region",
            },
            {
                "name": "count_by_region",
                "relation": "orders",
                "query": "SELECT region, COUNT(amount) FROM orders GROUP BY region",
            },
            {
                "name": "raw_orders_projection",
                "relation": "orders",
                "predicate_cols": ["region"],
                "projection_cols": ["id", "region", "amount"],
            },
            {
                "name": "isolated_notes_shape",
                "relation": "orders",
                "predicate_cols": ["notes"],
                "projection_cols": ["id", "notes"],
            },
        ],
        "events": [
            {
                "relation": "orders",
                "operation": "UPDATE",
                "changed_cols": ["amount"],
                "old": {"id": 1, "region": "us", "amount": 10},
                "new": {"id": 1, "region": "us", "amount": 25},
            },
            {
                "relation": "orders",
                "operation": "UPDATE",
                "changed_cols": ["region"],
                "old": {"id": 2, "region": "us"},
                "new": {"id": 2, "region": "eu"},
            },
        ],
    }
    w_file = tmp_path / "workload.json"
    w_file.write_text(json.dumps(workload), encoding="utf-8")

    buf_md = io.StringIO()
    with redirect_stdout(buf_md):
        analyze_workload(str(w_file), format_md="markdown", topomap=True)
    md_out = buf_md.getvalue()

    assert "Both Repair" in md_out
    assert "Both Invalidate" in md_out
    assert "Isolated Singleton Shapes" in md_out
    assert "isolated_notes_shape" in md_out


def test_run_doctor_sqlglot_ast_and_fd_redundant_groups(tmp_path: Path) -> None:
    workload = {
        "schema": {
            "orders": {
                "columns": {
                    "order_id": {"type": "INTEGER"},
                    "customer_id": {"type": "INTEGER"},
                    "customer_tier": {"type": "VARCHAR"},
                    "email": {"type": "VARCHAR"},
                    "amount": {"type": "NUMERIC"},
                },
                "functional_dependencies": [
                    {
                        "determinant": ["customer_id"],
                        "dependent": ["customer_tier"],
                    }
                ],
            }
        },
        "shapes": [
            {
                "name": "s_ast_issues",
                "relation": "orders",
                "query": (
                    "SELECT customer_id, customer_tier, SUM(amount) "
                    "FROM orders "
                    "WHERE LOWER(email) = 'alice@example.com' "
                    "AND amount + 10 > 100 "
                    "AND email LIKE '%@example.com' "
                    "AND customer_id = '42' "
                    "GROUP BY customer_id, customer_tier"
                ),
            }
        ],
    }
    w_file = tmp_path / "doctor_workload.json"
    w_file.write_text(json.dumps(workload), encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        findings = run_doctor(str(w_file), format_md=True)
    out = buf.getvalue()

    reasons = {ns["reason"] for ns in findings["non_sargable"]}
    assert "function_on_column" in reasons
    assert "arithmetic_on_column" in reasons
    assert "leading_wildcard_like" in reasons

    assert len(findings["implicit_conversions"]) >= 1
    assert any(
        ic["col"] == "orders.customer_id" for ic in findings["implicit_conversions"]
    )

    assert len(findings["fd_redundant_groups"]) == 1
    fg = findings["fd_redundant_groups"][0]
    assert fg["redundant_cols"] == ["customer_tier"]
    assert fg["reduced_group_cols"] == ["customer_id"]
    assert "FD-Redundant GROUP BY Columns" in out


def test_compare_workloads_dynamic_obstruction_and_normal_forms(
    tmp_path: Path,
) -> None:
    workload_a = {
        "schema": {
            "shipments": {
                "columns": {
                    "shipment_id": {"type": "INTEGER"},
                    "carrier_id": {"type": "INTEGER"},
                    "carrier_status": {"type": "VARCHAR"},
                    "cost": {"type": "NUMERIC"},
                },
                "functional_dependencies": [
                    {
                        "determinant": ["shipment_id"],
                        "dependent": ["carrier_id", "cost"],
                    },
                    {
                        "determinant": ["carrier_id"],
                        "dependent": ["carrier_status"],
                    },
                ],
            }
        },
        "shapes": [
            {
                "name": "s1",
                "relation": "shipments",
                "predicate_cols": ["carrier_status"],
                "aggregate_cols": ["cost"],
            },
            {
                "name": "s2",
                "relation": "shipments",
                "predicate_cols": ["carrier_status"],
                "projection_cols": ["shipment_id"],
            },
            {
                "name": "s3",
                "relation": "shipments",
                "predicate_cols": ["carrier_status"],
                "projection_cols": ["carrier_id"],
            },
        ],
        "events": [
            {
                "relation": "shipments",
                "changed_cols": ["carrier_status"],
                "old": {"shipment_id": 1, "carrier_status": "A"},
                "new": {"shipment_id": 1, "carrier_status": "B"},
            }
        ],
    }
    workload_b = {
        "schema": workload_a["schema"],
        "shapes": [
            {
                "name": "s1",
                "relation": "shipments",
                "predicate_cols": ["dispatch_state"],
                "aggregate_cols": ["cost"],
            },
            {
                "name": "s2",
                "relation": "shipments",
                "predicate_cols": ["customs_state"],
                "projection_cols": ["shipment_id"],
            },
            {
                "name": "s3",
                "relation": "shipments",
                "predicate_cols": ["delivery_state"],
                "projection_cols": ["carrier_id"],
            },
        ],
        "events": [
            {
                "relation": "shipments",
                "changed_cols": ["dispatch_state"],
                "old": {"shipment_id": 1, "dispatch_state": "A"},
                "new": {"shipment_id": 1, "dispatch_state": "B"},
            }
        ],
    }
    file_a = tmp_path / "wa.json"
    file_b = tmp_path / "wb.json"
    file_a.write_text(json.dumps(workload_a), encoding="utf-8")
    file_b.write_text(json.dumps(workload_b), encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        res = compare_workloads(str(file_a), str(file_b), format_md=True)
    out = buf.getvalue()

    assert res["primary_obstruction"] == "shipments.carrier_status"
    assert set(res["lifted_columns"]) == {
        "customs_state",
        "delivery_state",
        "dispatch_state",
    }
    assert len(res["normal_form_summaries"]) == 1
    assert res["normal_form_summaries"][0]["highest_normal_form"] == "2NF"
    assert res["normal_form_summaries"][0]["chase_3nf_lossless"] is True
    assert "shipments.carrier_status" in out
    assert "Normal-Form Decomposition & Tableau Chase Verification" in out


def test_cli_normalize_command_postgres_and_mssql(tmp_path: Path) -> None:
    schema_payload = {
        "name": "orders_denorm",
        "attributes": [
            "order_id",
            "customer_id",
            "customer_email",
            "region_id",
            "region_name",
            "amount",
        ],
        "functional_dependencies": [
            {
                "determinant": ["order_id"],
                "dependent": ["customer_id", "amount"],
            },
            {
                "determinant": ["customer_id"],
                "dependent": ["customer_email", "region_id"],
            },
            {
                "determinant": ["region_id"],
                "dependent": ["region_name"],
            },
        ],
        "column_types": {
            "order_id": "INTEGER",
            "customer_id": "INTEGER",
            "customer_email": "VARCHAR(255)",
            "region_id": "INTEGER",
            "region_name": "VARCHAR(100)",
            "amount": "NUMERIC(12,2)",
        },
    }
    schema_file = tmp_path / "orders_denorm.json"
    schema_file.write_text(json.dumps(schema_payload), encoding="utf-8")

    buf_json = io.StringIO()
    with redirect_stdout(buf_json):
        main(
            [
                "normalize",
                str(schema_file),
                "--dialect",
                "postgres",
                "--format",
                "json",
            ]
        )
    parsed = json.loads(buf_json.getvalue())
    assert parsed["dialect"] == "postgres"
    assert len(parsed["relations"]) == 1
    rel_rep = parsed["relations"][0]
    assert rel_rep["highest_normal_form"] == "2NF"
    assert rel_rep["synthesis_3nf"]["is_lossless"] is True
    assert rel_rep["decomposition_bcnf"]["is_lossless"] is True
    assert "CREATE TABLE" in rel_rep["synthesis_3nf"]["ddl"]

    buf_md = io.StringIO()
    with redirect_stdout(buf_md):
        run_normalize(str(schema_file), dialect="mssql", output_format="markdown")
    md_text = buf_md.getvalue()
    assert "CREATE TABLE orders_denorm_3nf_" in md_text
    assert "Tableau Chase Lossless" in md_text
