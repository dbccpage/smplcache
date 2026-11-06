# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
FastAPI application exposing bounded, self-contained SmplCache Live scenario runs
and serving the compiled frontend bundle.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
from pathlib import Path
import threading
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from demo.scenarios.runner import (
    ALLOWED_AUX_CAPACITIES,
    ALLOWED_DATASET_SIZES,
    SCENARIO_CATALOG,
    ScenarioConfig,
    execute_scenario_run,
    get_scenarios_metadata,
    get_version_metadata,
)
from smplcache.cli import _inspect_sql_ast_doctor, _parse_fds_from_raw, _shape_from_dict
from smplcache.normal_forms import (
    RelationalSchema,
    candidate_keys,
    classify_normal_form,
    decompose_bcnf,
    detect_fd_redundant_group_cols,
    generate_sql_ddl,
    minimal_cover,
    synthesize_3nf,
    tableau_chase,
)
from smplcache.smplcache import normalize_relation

MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_CONCURRENT_RUNS = 8
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 120

NORMALIZE_PRESETS: dict[str, dict[str, Any]] = {
    "orders_2nf_lift": {
        "preset_id": "orders_2nf_lift",
        "label": "2NF Transitive Dependency (Orders -> Customers -> Regions)",
        "relation_name": "orders_denorm",
        "attributes": [
            "order_id",
            "customer_id",
            "customer_tier",
            "region_id",
            "region_name",
            "status",
            "amount_cents",
        ],
        "functional_dependencies": [
            "order_id -> customer_id, status, amount_cents",
            "customer_id -> customer_tier, region_id",
            "region_id -> region_name",
        ],
        "query_sql": (
            "SELECT customer_id, customer_tier, SUM(amount_cents) "
            "FROM orders_denorm WHERE LOWER(status) = 'paid' "
            "GROUP BY customer_id, customer_tier"
        ),
        "lossy_split": [
            ["order_id", "region_name", "amount_cents"],
            ["customer_id", "customer_tier", "region_id", "region_name", "status"],
        ],
    },
    "supplier_bcnf_split": {
        "preset_id": "supplier_bcnf_split",
        "label": "3NF vs. BCNF Overlapping Candidate Keys (Supplier Parts)",
        "relation_name": "supplier_parts",
        "attributes": ["supplier_id", "supplier_name", "part_id", "quantity"],
        "functional_dependencies": [
            "supplier_id, part_id -> quantity, supplier_name",
            "supplier_name, part_id -> quantity, supplier_id",
            "supplier_name -> supplier_id",
        ],
        "query_sql": (
            "SELECT supplier_name, supplier_id, SUM(quantity) "
            "FROM supplier_parts GROUP BY supplier_name, supplier_id"
        ),
        "lossy_split": [
            ["supplier_id", "quantity"],
            ["supplier_name", "part_id", "quantity"],
        ],
    },
}


class NormalizeRequestPayload(BaseModel):
    """
    Bounded request payload for `POST /api/normalize`.
    """

    model_config = ConfigDict(extra="forbid")

    preset_id: Literal["orders_2nf_lift", "supplier_bcnf_split", "custom"] = (
        "orders_2nf_lift"
    )
    dialect: Literal["postgres", "mssql"] = "postgres"
    relation_name: str = Field(default="orders_denorm", min_length=1, max_length=64)
    attributes: list[str] = Field(default_factory=list, max_length=24)
    functional_dependencies: list[str] = Field(default_factory=list, max_length=32)
    query_sql: str | None = Field(default=None, max_length=1024)
    lossy_split: list[list[str]] | None = Field(default=None, max_length=8)


def _execute_normalize(payload: NormalizeRequestPayload) -> dict[str, Any]:
    preset = NORMALIZE_PRESETS.get(payload.preset_id)
    if payload.preset_id != "custom" and preset is not None and not payload.attributes:
        rel_name = normalize_relation(preset["relation_name"])
        raw_attrs = list(preset["attributes"])
        raw_fds = list(preset["functional_dependencies"])
        query_sql = payload.query_sql or preset.get("query_sql")
        lossy_split = payload.lossy_split or preset.get("lossy_split")
    else:
        rel_name = normalize_relation(payload.relation_name)
        raw_attrs = [
            str(a).strip().lower() for a in payload.attributes if str(a).strip()
        ]
        raw_fds = list(payload.functional_dependencies)
        query_sql = payload.query_sql
        lossy_split = payload.lossy_split

    fds = _parse_fds_from_raw(raw_fds)
    attr_set = {a.strip().lower() for a in raw_attrs if a.strip()}
    for fd in fds:
        attr_set.update(fd.determinant | fd.dependent)

    if not attr_set:
        raise ValueError("At least one attribute is required for normalization.")

    schema = RelationalSchema(
        name=rel_name or "relation",
        attributes=frozenset(attr_set),
        functional_dependencies=tuple(fds),
    )

    min_cov = minimal_cover(schema.functional_dependencies)
    cks = candidate_keys(schema)
    nf_rep = classify_normal_form(schema)
    tables_3nf = synthesize_3nf(schema)
    bcnf_res = decompose_bcnf(schema)
    chase_3nf = tableau_chase(schema, [t.attributes for t in tables_3nf])
    chase_bcnf = tableau_chase(schema, [t.attributes for t in bcnf_res.tables])
    ddl_3nf = generate_sql_ddl(tables_3nf, dialect=payload.dialect).ddl_script
    ddl_bcnf = generate_sql_ddl(bcnf_res.tables, dialect=payload.dialect).ddl_script

    lossy_chase_info: dict[str, Any] | None = None
    if lossy_split:
        lossy_sets = [
            frozenset(str(c).strip().lower() for c in sub if str(c).strip())
            for sub in lossy_split
            if sub
        ]
        if lossy_sets and frozenset().union(*lossy_sets) == schema.attributes:
            lossy_chase = tableau_chase(schema, lossy_sets)
            lossy_chase_info = {
                "sub_relations": [sorted(s) for s in lossy_sets],
                "is_lossless": lossy_chase.is_lossless,
                "counterexample": lossy_chase.counterexample,
            }

    doctor_info: dict[str, Any] = {
        "query_sql": query_sql,
        "non_sargable": [],
        "implicit_conversions": [],
        "fd_redundant_group_cols": [],
        "reduced_group_cols": [],
    }
    if isinstance(query_sql, str) and query_sql.strip():
        shape_obj = _shape_from_dict(
            {"name": "inspector_query", "relation": schema.name, "query": query_sql}
        )
        ns_list, ic_list = _inspect_sql_ast_doctor(
            query_sql, "inspector_query", schema.name, {}
        )
        doctor_info["non_sargable"] = ns_list
        doctor_info["implicit_conversions"] = ic_list
        if shape_obj.group_cols and schema.functional_dependencies:
            red_rep = detect_fd_redundant_group_cols(
                shape_obj, schema.functional_dependencies
            )
            doctor_info["fd_redundant_group_cols"] = list(red_rep.redundant_group_cols)
            doctor_info["reduced_group_cols"] = list(red_rep.minimal_group_cols)

    return {
        "preset_id": payload.preset_id,
        "dialect": payload.dialect,
        "relation": schema.name,
        "attributes": sorted(schema.attributes),
        "minimal_cover": [fd.format() for fd in min_cov],
        "candidate_keys": [sorted(k) for k in cks],
        "highest_normal_form": nf_rep.highest_normal_form,
        "is_1nf": nf_rep.is_1nf,
        "is_2nf": nf_rep.is_2nf,
        "is_3nf": nf_rep.is_3nf,
        "is_eknf": nf_rep.is_eknf,
        "is_bcnf": nf_rep.is_bcnf,
        "violations": list(nf_rep.violations),
        "synthesis_3nf": {
            "tables": [
                {
                    "name": t.name,
                    "attributes": sorted(t.attributes),
                    "candidate_keys": [sorted(k) for k in candidate_keys(t)],
                }
                for t in tables_3nf
            ],
            "is_lossless": chase_3nf.is_lossless,
            "chase_steps": len(chase_3nf.steps),
            "counterexample": chase_3nf.counterexample,
            "ddl": ddl_3nf,
        },
        "decomposition_bcnf": {
            "tables": [
                {
                    "name": t.name,
                    "attributes": sorted(t.attributes),
                    "candidate_keys": [sorted(k) for k in candidate_keys(t)],
                }
                for t in bcnf_res.tables
            ],
            "is_lossless": chase_bcnf.is_lossless,
            "is_dependency_preserving": bcnf_res.is_dependency_preserving,
            "lost_dependencies": [fd.format() for fd in bcnf_res.lost_dependencies],
            "chase_steps": len(chase_bcnf.steps),
            "counterexample": chase_bcnf.counterexample,
            "ddl": ddl_bcnf,
        },
        "lossy_comparison": lossy_chase_info,
        "doctor": doctor_info,
    }


class RunRequestPayload(BaseModel):
    """
    Strictly allowlisted request payload for `POST /api/runs`.
    Rejects unknown fields, arbitrary SQL, or out-of-range parameters.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: Literal[
        "live_revenue", "customers_and_regions", "missing_evidence"
    ] = "live_revenue"
    seed: int = Field(default=42, ge=1, le=999999)
    dataset_size: int = Field(default=1000)
    aux_max_total_rows: int | None = Field(default=None)
    max_steps: int | None = Field(default=None, ge=1, le=10)
    evidence_mode: Literal["standard", "remove_required_evidence"] = "standard"
    defer_rebuild: bool = False

    @field_validator("dataset_size")
    @classmethod
    def _validate_dataset_size(cls, v: int) -> int:
        if v not in ALLOWED_DATASET_SIZES:
            raise ValueError(
                f"dataset_size must be one of {list(ALLOWED_DATASET_SIZES)}, got {v}"
            )
        return v

    @field_validator("aux_max_total_rows")
    @classmethod
    def _validate_aux_cap(cls, v: int | None) -> int | None:
        if v not in ALLOWED_AUX_CAPACITIES:
            raise ValueError(
                f"aux_max_total_rows must be one of {list(ALLOWED_AUX_CAPACITIES)}, got {v}"
            )
        return v


class _ProcessRateLimiter:
    """
    Lightweight process-local rate limiter per client IP.
    """

    def __init__(self, window_seconds: float, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, client_key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = [t for t in self._hits.get(client_key, []) if t >= cutoff]
            if len(bucket) >= self.max_requests:
                self._hits[client_key] = bucket
                return False
            bucket.append(now)
            self._hits[client_key] = bucket
            return True


def create_app(
    web_dist_dir: Path | None = None, *, trust_render_edge: bool | None = None
) -> FastAPI:
    if trust_render_edge is None:
        trust_render_edge = os.environ.get("RENDER") == "true"
    application = FastAPI(
        title="SmplCache Live API",
        description=(
            "Keep dashboards correct without rerunning every query after every write. "
            "Self-contained deterministic execution and independent SQLite verification."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    run_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    rate_limiter = _ProcessRateLimiter(
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        max_requests=RATE_LIMIT_MAX_REQUESTS,
    )

    resolved_dist = (
        web_dist_dir
        if web_dist_dir is not None
        else Path(__file__).resolve().parent.parent / "web" / "dist"
    )

    @application.middleware("http")
    async def _security_and_size_guard(request: Request, call_next: Any) -> Any:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
                if length < 0:
                    raise ValueError("negative content length")
                if length > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request payload exceeds 16 KB limit."},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request payload exceeds 16 KB limit."},
                )
            chunks.append(chunk)
        # Starlette's cached request replays this bounded body to downstream code.
        request._body = b"".join(chunks)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.get("/healthz")
    async def healthz() -> dict[str, Any]:
        ver = get_version_metadata()
        return {
            "status": "ok",
            "service": "smplcache-live",
            "api_version": ver["api_version"],
            "app_commit": ver["app_commit"],
            "engine_commit": ver["engine_commit"],
        }

    @application.get("/api/version")
    async def api_version() -> dict[str, Any]:
        return get_version_metadata()

    @application.get("/api/scenarios")
    async def api_scenarios() -> dict[str, Any]:
        return get_scenarios_metadata()

    @application.get("/api/normalize/presets")
    async def api_normalize_presets() -> dict[str, Any]:
        return {"presets": list(NORMALIZE_PRESETS.values())}

    @application.post("/api/normalize")
    async def api_normalize(
        payload: NormalizeRequestPayload, request: Request
    ) -> dict[str, Any]:
        client_host = request.client.host if request.client else "local"
        if not rate_limiter.allow(client_host):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded for /api/normalize; please wait briefly.",
            )
        try:
            async with run_semaphore:
                return await asyncio.to_thread(_execute_normalize, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/api/runs")
    async def api_runs(payload: RunRequestPayload, request: Request) -> dict[str, Any]:
        client_host = request.client.host if request.client else "local"
        if trust_render_edge:
            # Render's public Cloudflare edge replaces this header. Never use
            # X-Forwarded-For: it can contain caller-supplied addresses.
            candidate = request.headers.get("cf-connecting-ip", "")
            try:
                client_host = str(ipaddress.ip_address(candidate))
            except ValueError:
                client_host = "render-unknown-client"
        if not rate_limiter.allow(client_host):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded for /api/runs; please wait briefly.",
            )

        if payload.scenario_id not in SCENARIO_CATALOG:
            raise HTTPException(status_code=422, detail="Unsupported scenario_id")

        cfg = ScenarioConfig(
            scenario_id=payload.scenario_id,
            seed=payload.seed,
            dataset_size=payload.dataset_size,
            aux_max_total_rows=payload.aux_max_total_rows,
            max_steps=payload.max_steps,
            evidence_mode=payload.evidence_mode,
            defer_rebuild=payload.defer_rebuild,
        )
        async with run_semaphore:
            result = await asyncio.to_thread(execute_scenario_run, cfg)
        return result

    @application.api_route(
        "/api/{unmatched_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        include_in_schema=False,
    )
    async def api_not_found(unmatched_path: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"API endpoint '/api/{unmatched_path}' not found."},
        )

    assets_dir = resolved_dist / "assets"
    if assets_dir.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="static_assets",
        )

    @application.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> Any:
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": f"API endpoint '/{full_path}' not found."},
            )
        if resolved_dist.is_dir():
            if full_path:
                candidate = (resolved_dist / full_path).resolve()
                if candidate.is_relative_to(resolved_dist.resolve()):
                    if candidate.is_file():
                        return FileResponse(str(candidate))
                    if (candidate / "index.html").is_file():
                        return FileResponse(str(candidate / "index.html"))
            index_file = resolved_dist / "index.html"
            if index_file.is_file():
                return FileResponse(str(index_file))

        return HTMLResponse(
            content=(
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                "<title>SmplCache Live</title></head><body>"
                "<h1>SmplCache Live</h1>"
                "<p>Keep dashboards correct without rerunning every query after every write.</p>"
                "<p>API is active at <a href='/api/scenarios'>/api/scenarios</a> "
                "and <a href='/healthz'>/healthz</a>.</p>"
                "</body></html>"
            ),
            status_code=200,
        )

    return application


app = create_app()
