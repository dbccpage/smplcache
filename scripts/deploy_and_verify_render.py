# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Provision, inspect, and smoke-test the SmplCache Live Render service and
`smplcache.anomalon.com` custom domain.

Reads `RENDER_API_KEY` from `~/.env` (or environment) without printing secret
values. Can also run in `--verify-url <url>` mode without an API key once the
Render service is live.

Provisioning requires explicit --owner-id and --environment-id. Existing
services must match the requested owner, environment, repository and branch.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import time
from typing import Any

import httpx

RENDER_API_BASE = "https://api.render.com/v1"
REPO_URL = "https://github.com/dbccpage/smplcache"
SERVICE_NAME = "smplcache-live"
CUSTOM_DOMAIN = "smplcache.anomalon.com"


def _load_env_key(var_name: str) -> str | None:
    import os

    if os.environ.get(var_name):
        return os.environ[var_name].strip()
    for env_path in (Path.home() / ".env", Path(".env")):
        if env_path.is_file():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == var_name:
                    return v.strip().strip('"').strip("'")
    return None


def check_dns_cname(hostname: str) -> dict[str, Any]:
    try:
        cname, aliases, ips = socket.gethostbyname_ex(hostname)
        return {
            "resolved": True,
            "canonical_name": cname,
            "aliases": aliases,
            "ips": ips,
        }
    except socket.gaierror as exc:
        return {
            "resolved": False,
            "error": str(exc),
        }


def run_https_smoke_checks(base_url: str) -> dict[str, Any]:
    url = base_url.rstrip("/")
    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        r_health = client.get(f"{url}/healthz")
        r_health.raise_for_status()
        health = r_health.json()
        if health.get("status") != "ok":
            raise RuntimeError(f"/healthz returned non-ok payload: {health}")

        r_ver = client.get(f"{url}/api/version")
        r_ver.raise_for_status()
        ver = r_ver.json()

        r_scen = client.get(f"{url}/api/scenarios")
        r_scen.raise_for_status()
        scen = r_scen.json()

        r_run = client.post(
            f"{url}/api/runs",
            json={"scenario_id": "live_revenue", "seed": 42, "dataset_size": 1000},
        )
        r_run.raise_for_status()
        run_data = r_run.json()
        summary = run_data.get("summary", {})
        if summary.get("status") != "verified" or summary.get("mismatch_count") != 0:
            raise RuntimeError(f"Smoke run failed verification: {summary}")

        r_root = client.get(f"{url}/")
        r_root.raise_for_status()
        if "SmplCache Live" not in r_root.text:
            raise RuntimeError("Root HTML shell did not contain 'SmplCache Live'")

        r_404 = client.get(f"{url}/api/nonexistent_smoke_check")
        if r_404.status_code != 404:
            raise RuntimeError(
                f"Expected 404 on unknown /api route, got {r_404.status_code}"
            )

    return {
        "base_url": url,
        "healthz": health,
        "version": ver,
        "scenarios_count": len(scen.get("scenarios", [])),
        "live_revenue_summary": {
            "status": summary.get("status"),
            "oracle_checks": summary.get("oracle_checks"),
            "mismatch_count": summary.get("mismatch_count"),
            "baseline_recomputations": summary.get("baseline_recomputations"),
            "smplcache_rebuilds": summary.get("smplcache_rebuilds"),
            "full_result_recomputations_avoided": summary.get(
                "full_result_recomputations_avoided"
            ),
            "canonical_hash": run_data.get("canonical_hash"),
        },
    }


def provision_or_find_render_service(
    api_key: str, branch: str, owner_id: str, environment_id: str
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    with httpx.Client(
        base_url=RENDER_API_BASE, headers=headers, timeout=30.0
    ) as client:
        r_svcs = client.get(
            "/services", params={"name": SERVICE_NAME, "ownerId": owner_id, "limit": 20}
        )
        r_svcs.raise_for_status()
        existing = r_svcs.json()

        svc_obj: dict[str, Any] | None = None
        for item in existing:
            candidate = item.get("service", item)
            if candidate.get("name") == SERVICE_NAME:
                if (
                    candidate.get("ownerId") != owner_id
                    or candidate.get("environmentId") != environment_id
                    or candidate.get("repo") != REPO_URL
                    or candidate.get("branch") != branch
                ):
                    raise RuntimeError(
                        "Existing service does not match the requested owner, environment, repository and branch"
                    )
                svc_obj = candidate
                break

        if svc_obj is None:
            create_body = {
                "type": "web_service",
                "name": SERVICE_NAME,
                "ownerId": owner_id,
                "environmentId": environment_id,
                "repo": REPO_URL,
                "branch": branch,
                "autoDeploy": "yes",
                "envVars": [{"key": "PYTHON_VERSION", "value": "3.12.7"}],
                "serviceDetails": {
                    "runtime": "python",
                    "plan": "free",
                    "region": "oregon",
                    "healthCheckPath": "/healthz",
                    "envSpecificDetails": {
                        "buildCommand": 'pip install --upgrade pip && pip install ".[demo]"',
                        "startCommand": "uvicorn demo.api.app:app --host 0.0.0.0 --port $PORT",
                    },
                },
            }
            r_create = client.post("/services", json=create_body)
            r_create.raise_for_status()
            created_raw = r_create.json()
            svc_obj = created_raw.get("service", created_raw)

        assert svc_obj is not None
        service_id = svc_obj["id"]
        service_url = svc_obj.get("serviceDetails", {}).get("url")
        if not service_url:
            raise RuntimeError("Render did not return a service URL")
        onrender_host = (
            service_url.replace("https://", "").replace("http://", "").strip("/")
        )

        # Ensure custom domain smplcache.anomalon.com is attached
        r_domains = client.get(f"/services/{service_id}/custom-domains")
        r_domains.raise_for_status()
        dom_list = [d.get("customDomain", d).get("name") for d in r_domains.json()]
        if CUSTOM_DOMAIN not in dom_list:
            result = client.post(
                f"/services/{service_id}/custom-domains",
                json={"name": CUSTOM_DOMAIN},
            )
            result.raise_for_status()

        return {
            "service_id": service_id,
            "service_name": svc_obj.get("name", SERVICE_NAME),
            "service_url": service_url,
            "onrender_cname_target": onrender_host,
            "custom_domain": CUSTOM_DOMAIN,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Provision and verify SmplCache Live on Render."
    )
    parser.add_argument(
        "--branch",
        default="feature/upgrades-1-3-join-wtinylfu-shared-pool",
        help="Git branch to deploy on Render.",
    )
    parser.add_argument(
        "--provision",
        action="store_true",
        help="Create or find the smplcache-live Render service using RENDER_API_KEY.",
    )
    parser.add_argument("--owner-id", help="Explicit Render workspace owner ID")
    parser.add_argument("--environment-id", help="Explicit Render environment ID")
    parser.add_argument(
        "--verify-url",
        default=None,
        help="Run HTTPS /healthz and /api/runs smoke checks against the given URL.",
    )
    args = parser.parse_args(argv)

    import json

    if args.provision:
        if not args.owner_id or not args.environment_id:
            parser.error("--provision requires --owner-id and --environment-id")
        api_key = _load_env_key("RENDER_API_KEY")
        if not api_key:
            print(
                "ERROR: RENDER_API_KEY not found in ~/.env or environment.",
                file=sys.stderr,
            )
            return 2
        info = provision_or_find_render_service(
            api_key, args.branch, args.owner_id, args.environment_id
        )
        print(json.dumps({"render_service": info}, indent=2))
        if not args.verify_url:
            args.verify_url = info["service_url"]

    if args.verify_url:
        for attempt in range(1, 25):
            try:
                report = run_https_smoke_checks(args.verify_url)
                print(json.dumps({"smoke_verification": report}, indent=2))
                return 0
            except Exception as exc:
                if attempt == 24:
                    print(f"Smoke verification failed: {exc}", file=sys.stderr)
                    return 1
                time.sleep(10.0)

    dns_status = check_dns_cname(CUSTOM_DOMAIN)
    print(json.dumps({"dns_check": dns_status}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
