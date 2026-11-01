# SmplCache Live: Architecture, Verification & Operations Runbook

## 1. Purpose and Public Promise

**SmplCache Live** (`https://smplcache.anomalon.com`) is an interactive engineering demonstration of SmplCache built by Jeremy H. Carroll.

> **Public promise:** Keep dashboards correct without rerunning every query after every write.

Every run executes the real Python `smplcache` certification and incremental maintenance engine (`parse_sql`, `parse_join_sql`, `AggregateCache.from_rows`, `JoinAuxState.from_tables`, `process_event`, `process_join_event`, `rebuild_join_cache`, `WalStreamProcessor`) and checks every committed boundary against an independent native `sqlite3` database.

---

## 2. Package Layout and Dependency Direction

```text
demo/
  api/
    app.py                  # FastAPI service (/api/scenarios, /api/runs, /api/version, /healthz)
  scenarios/
    runner.py               # Seeded fixtures, policy harness, and independent SQLite verifier
  web/
    src/                    # TypeScript + CSS frontend source
    dist/                   # Compiled production static bundle served at /
    build.mjs               # Zero-native-binary TypeScript + asset hash builder
  tests/
    test_demo_acceptance.py # 20 acceptance, adversarial, concurrency, and boundary tests
```

- **One-way dependency rule:** `demo` imports `smplcache`; no module in `smplcache/` imports `demo`, `fastapi`, `uvicorn`, `starlette`, or `pydantic`. Enforced by `test_one_way_dependency_boundary_core_never_imports_demo_or_web`.

---

## 3. Fair Baseline, Counter Definitions, and Independent Oracle

Both policies receive the identical initial snapshot, SQL query, ordered committed mutations, and read schedule (1 read after each committed boundary):

| Counter | Definition |
| --- | --- |
| `initialization_queries` | Queries executed at Boundary 0 to seed the baseline and SmplCache (`2` for single-table scenarios; `4` for the 3-table join scenario). Excluded from post-initialization policy comparisons. |
| `baseline_recomputations` | Full SQL query executions by the table-invalidation baseline after initialization (`1` when a participating table was modified in a committed transaction; `0` on unrelated schema writes or rolled-back transactions). |
| `baseline_source_queries` | SQL queries executed by the baseline after initialization (`== baseline_recomputations`). |
| `smplcache_rebuilds` | Full result rebuilds performed by SmplCache after initialization when an event produces `invalidate` or `unsupported`. |
| `smplcache_source_queries` | Source table snapshot queries executed by SmplCache during rebuilds (`1` query for single-table `orders`; `3` queries for the 3-table `orders -> customers -> regions` join). |
| `full_result_recomputations_avoided` | `baseline_recomputations - smplcache_rebuilds`. |
| `source_queries_avoided` | `baseline_source_queries - smplcache_source_queries`. Reported without clamping; becomes negative when multiple 3-table join rebuilds each read 3 source tables while the baseline executes 1 joined query. |
| `oracle_queries` | Independent native `sqlite3` verification queries executed by the harness at Boundary 0 and after every committed boundary. Excluded from both policies' query counts and disclosed separately. |

---

## 4. Local Development and Verification Commands

```bash
# 1. Install package with demo and dev extras in virtual environment
.venv/Scripts/pip install -e ".[dev,demo]"

# 2. Rebuild frontend static bundle (TypeScript typecheck + asset hashing)
cd demo/web && npm run build && cd ../..

# 3. Run acceptance and full regression suite (149 tests total)
.venv/Scripts/python -m pytest -q

# 4. Run type checking and code formatting checks
.venv/Scripts/python -m mypy smplcache demo
.venv/Scripts/python -m black --check smplcache tests demo

# 5. Start production server locally
.venv/Scripts/uvicorn demo.api.app:app --host 0.0.0.0 --port 8000
```

---

## 5. Render Deployment and Custom Subdomain Handoff (`smplcache.anomalon.com`)

The repository includes both a native Python Render Blueprint ([`render.yaml`](../../render.yaml)) and a multi-stage container build ([`Dockerfile`](../../Dockerfile)).

### Step 1: Connect Blueprint in Render
1. In the Render Dashboard, select **New -> Blueprint** and connect `https://github.com/dbccpage/smplcache`.
2. Select branch `feature/upgrades-1-3-join-wtinylfu-shared-pool` (or `main` after merging PR #7).
3. Render reads `render.yaml` and provisions the `smplcache-live` web service with:
   - **Build Command:** `pip install --upgrade pip && pip install ".[demo]"`
   - **Start Command:** `uvicorn demo.api.app:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path:** `/healthz`

### Step 2: Configure DNS CNAME for `smplcache.anomalon.com`
Add the following DNS record at the `anomalon.com` DNS provider:

| Type | Host / Name | Target / Value | TTL | Proxy Status |
| --- | --- | --- | --- | --- |
| `CNAME` | `smplcache` | `smplcache-live.onrender.com` *(or the exact `.onrender.com` hostname assigned by Render)* | `Auto` / `300` | DNS-only (unproxied) during TLS certificate issuance |

Once DNS propagates, click **Verify** next to `smplcache.anomalon.com` under **Settings -> Custom Domains** in Render to provision the managed Let's Encrypt TLS certificate.

### Step 3: Post-Deploy Smoke Verification
```bash
curl -fsS https://smplcache.anomalon.com/healthz
curl -fsS https://smplcache.anomalon.com/api/version
curl -fsS -X POST https://smplcache.anomalon.com/api/runs \
  -H "Content-Type: application/json" \
  -d '{"scenario_id":"live_revenue","seed":42,"dataset_size":1000}'
```

### Step 4: Rollback Procedure
If a deployed revision fails health or oracle checks:
1. Open the `smplcache-live` service in the Render Dashboard -> **Events / Deploys**.
2. Locate the previous healthy deploy (matching the known-good `engine_commit` from `/api/version`).
3. Click **Rollback to this deploy** to restore the previous build image immediately without DNS changes.

---

## 6. Claim Hygiene Summary

1. **What it supports:** Demonstrates in a deterministic, seeded SQLite setting that SmplCache preserves or incrementally repairs admitted SPJA query results when complete before/after row evidence is present, and falls back to snapshot recomputation when evidence is omitted or resident auxiliary join keys are evicted.
2. **What it does not support:** Does not establish arbitrary-SQL support, external RDBMS collation/floating-point parity, total process RAM bounds from auxiliary row limits, or durable live PostgreSQL replication delivery.
3. **Narrowest defensible claim:** Across all checked boundaries in the three shipped scenarios (`live_revenue`, `customers_and_regions`, `missing_evidence`), every preserved, repaired, and rebuilt state matches independent native SQLite integer recomputation (`0` mismatches), and injected cache corruption halts the run immediately.
4. **Next test needed:** End-to-end live PostgreSQL logical replication testing under concurrent write connections with LSN snapshot alignment.
