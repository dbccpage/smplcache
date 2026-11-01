# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
FastAPI application exposing bounded, self-contained SmplCache Live scenario runs
and serving the compiled frontend bundle.
"""

from __future__ import annotations

import asyncio
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

MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_CONCURRENT_RUNS = 8
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 120


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


def create_app(web_dist_dir: Path | None = None) -> FastAPI:
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
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request payload exceeds 16 KB limit."},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
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

    @application.post("/api/runs")
    async def api_runs(payload: RunRequestPayload, request: Request) -> dict[str, Any]:
        client_host = request.client.host if request.client else "local"
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
                if (
                    str(candidate).startswith(str(resolved_dist.resolve()))
                    and candidate.is_file()
                ):
                    return FileResponse(str(candidate))
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
