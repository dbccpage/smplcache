import type {
  BoundaryTrace,
  CumulativeCounters,
  RunTraceResponse,
  SampleOrderRow,
  ScenarioId,
  ScenariosMetadataResponse,
} from "./types";

interface AppState {
  metadata: ScenariosMetadataResponse | null;
  run: RunTraceResponse | null;
  scenarioId: ScenarioId;
  seed: number;
  datasetSize: number;
  auxMaxTotalRows: number | null;
  currentStepIndex: number; // 0 = initial state, 1..N = boundaries[index - 1]
  isPlaying: boolean;
  inspectorTab: "certificate" | "sql" | "architecture";
  loading: boolean;
  error: string | null;
  copiedReplayNotice: boolean;
}

const state: AppState = {
  metadata: null,
  run: null,
  scenarioId: "live_revenue",
  seed: 42,
  datasetSize: 1000,
  auxMaxTotalRows: null,
  currentStepIndex: 1,
  isPlaying: false,
  inspectorTab: "certificate",
  loading: true,
  error: null,
  copiedReplayNotice: false,
};

let playbackTimer: number | null = null;
const STEP_INTERVAL_MS = 7200; // ~50 seconds across 7 steps for the 60-second demo

function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) {
    return "Withheld (Stale)";
  }
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(Math.trunc(cents));
  const dollars = Math.floor(abs / 100);
  const rem = abs % 100;
  return `${sign}$${dollars.toLocaleString("en-US")}.${rem.toString().padStart(2, "0")}`;
}

function escapeHtml(raw: unknown): string {
  const s = String(raw ?? "");
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function parseUrlParams(): void {
  const params = new URLSearchParams(window.location.search);
  const scen = params.get("scenario");
  if (
    scen === "live_revenue" ||
    scen === "customers_and_regions" ||
    scen === "missing_evidence"
  ) {
    state.scenarioId = scen;
  }
  const seedRaw = Number(params.get("seed"));
  if (Number.isInteger(seedRaw) && seedRaw >= 1 && seedRaw <= 999999) {
    state.seed = seedRaw;
  }
  const sizeRaw = Number(params.get("size"));
  if ([100, 500, 1000, 2500].includes(sizeRaw)) {
    state.datasetSize = sizeRaw;
  }
  const capParam = params.get("cap");
  if (capParam !== null && capParam !== "" && capParam !== "none") {
    const capNum = Number(capParam);
    if ([60, 120, 250, 500, 1500, 3000].includes(capNum)) {
      state.auxMaxTotalRows = capNum;
    }
  }
  const stepParam = params.get("step");
  if (stepParam !== null) {
    const stepNum = Number(stepParam);
    if (Number.isInteger(stepNum) && stepNum >= 0 && stepNum <= 10) {
      state.currentStepIndex = stepNum;
    }
  }
}

function syncUrlParams(): void {
  const params = new URLSearchParams();
  params.set("scenario", state.scenarioId);
  params.set("seed", String(state.seed));
  params.set("size", String(state.datasetSize));
  if (state.scenarioId === "customers_and_regions" && state.auxMaxTotalRows !== null) {
    params.set("cap", String(state.auxMaxTotalRows));
  }
  params.set("step", String(state.currentStepIndex));
  const newUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, "", newUrl);
}

function stopPlayback(): void {
  state.isPlaying = false;
  if (playbackTimer !== null) {
    window.clearInterval(playbackTimer);
    playbackTimer = null;
  }
}

function startPlayback(): void {
  if (!state.run) return;
  stopPlayback();
  const maxStep = state.run.boundaries.length;
  if (state.currentStepIndex >= maxStep) {
    state.currentStepIndex = 1;
  } else if (state.currentStepIndex === 0) {
    state.currentStepIndex = 1;
  }
  state.isPlaying = true;
  syncUrlParams();
  renderApp();

  playbackTimer = window.setInterval(() => {
    if (!state.run) {
      stopPlayback();
      return;
    }
    const totalSteps = state.run.boundaries.length;
    const currentBoundary =
      state.currentStepIndex > 0
        ? state.run.boundaries[state.currentStepIndex - 1]
        : null;
    if (currentBoundary && !currentBoundary.oracle_match) {
      stopPlayback();
      renderApp();
      return;
    }
    if (state.currentStepIndex < totalSteps) {
      state.currentStepIndex += 1;
      syncUrlParams();
      renderApp();
    } else {
      stopPlayback();
      renderApp();
    }
  }, STEP_INTERVAL_MS);
}

async function fetchInitialAndRun(preserveStep = false): Promise<void> {
  stopPlayback();
  state.loading = true;
  state.error = null;
  renderApp();

  try {
    if (!state.metadata) {
      const metaResp = await fetch("/api/scenarios");
      if (!metaResp.ok) {
        throw new Error(`Failed to load /api/scenarios (${metaResp.status})`);
      }
      state.metadata = (await metaResp.json()) as ScenariosMetadataResponse;
    }

    const runResp = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_id: state.scenarioId,
        seed: state.seed,
        dataset_size: state.datasetSize,
        aux_max_total_rows:
          state.scenarioId === "customers_and_regions"
            ? state.auxMaxTotalRows
            : null,
      }),
    });

    if (!runResp.ok) {
      const errBody = await runResp.json().catch(() => ({}));
      throw new Error(
        errBody.detail || `Run request failed with HTTP ${runResp.status}`
      );
    }

    const trace = (await runResp.json()) as RunTraceResponse;
    state.run = trace;
    if (!preserveStep) {
      state.currentStepIndex = 1;
    } else {
      state.currentStepIndex = Math.min(
        state.currentStepIndex,
        trace.boundaries.length
      );
    }
    state.loading = false;
    syncUrlParams();
    renderApp();
  } catch (err) {
    state.loading = false;
    state.error = err instanceof Error ? err.message : String(err);
    renderApp();
  }
}

function getActiveBoundary(): BoundaryTrace | null {
  if (!state.run || state.currentStepIndex <= 0) {
    return null;
  }
  return (
    state.run.boundaries[
      Math.min(state.currentStepIndex - 1, state.run.boundaries.length - 1)
    ] ?? null
  );
}

function getActiveCounters(): CumulativeCounters {
  const b = getActiveBoundary();
  if (b) {
    return b.cumulative_counters;
  }
  return {
    baseline_recomputations: 0,
    baseline_source_queries: 0,
    smplcache_rebuilds: 0,
    smplcache_source_queries: 0,
    full_result_recomputations_avoided: 0,
    full_result_recomputations_avoided_pct: null,
    source_queries_avoided: 0,
    source_queries_avoided_pct: null,
    preserve_count: 0,
    repair_count: 0,
    invalidate_count: 0,
    unsupported_count: 0,
    oracle_checks: 1,
    oracle_queries: 1,
    mismatch_count: 0,
  };
}

function renderDecisionBadge(boundary: BoundaryTrace | null): string {
  if (!boundary) {
    return `<span class="decision-badge badge-preserve">● Initial Seeded Snapshot</span>`;
  }
  switch (boundary.ui_badge) {
    case "Preserve":
      return `<span class="decision-badge badge-preserve">✓ Preserve (${escapeHtml(boundary.reason_code)})</span>`;
    case "Repair":
      return `<span class="decision-badge badge-repair">⚡ Repair (${escapeHtml(boundary.reason_code)})</span>`;
    case "Invalidate / Recompute":
      return `<span class="decision-badge badge-invalidate">↻ Invalidate / Recompute (${escapeHtml(boundary.reason_code)})</span>`;
    case "Unsupported":
      return `<span class="decision-badge badge-unsupported">⊘ Unsupported (${escapeHtml(boundary.reason_code)})</span>`;
    case "Mismatch":
      return `<span class="decision-badge badge-mismatch">✕ Mismatch Detected</span>`;
  }
}

function renderGroupBars(
  groups: Record<string, number> | null,
  isBaseline: boolean
): string {
  if (!groups) {
    return `<div class="withheld-notice">Stale cached value withheld until snapshot rebuild completes.</div>`;
  }
  const entries = Object.entries(groups);
  const maxVal = Math.max(1, ...entries.map(([, v]) => v));
  return `<div class="group-rows">
    ${entries
      .map(([name, cents]) => {
        const pct = Math.max(4, Math.min(100, Math.round((cents / maxVal) * 100)));
        return `<div class="group-row">
          <div class="group-row-top">
            <span class="group-name">${escapeHtml(name)}</span>
            <span class="group-amount">${formatCents(cents)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill ${isBaseline ? "baseline-fill" : ""}" style="width: ${pct}%"></div>
          </div>
        </div>`;
      })
      .join("")}
  </div>`;
}

function renderSampleOrders(
  orders: SampleOrderRow[],
  highlightedId: number | null
): string {
  return `<div class="orders-table-wrap">
    <table class="orders-table" aria-label="Sample committed orders in SQLite">
      <thead>
        <tr>
          <th>Order</th>
          <th>Region</th>
          <th>Status</th>
          <th>Amount</th>
          <th>Delivery Note</th>
        </tr>
      </thead>
      <tbody>
        ${orders
          .map((row) => {
            const isHighlighted = highlightedId === row.id;
            const statusClass =
              row.status === "paid"
                ? "status-paid"
                : row.status === "pending"
                  ? "status-pending"
                  : "status-refunded";
            const displayRegion =
              state.scenarioId === "customers_and_regions"
                ? row.joined_region_name || row.region
                : row.region;
            return `<tr class="${isHighlighted ? "highlighted-row" : ""}">
              <td><strong>#${row.id}</strong></td>
              <td>${escapeHtml(displayRegion)}</td>
              <td><span class="status-pill ${statusClass}">${escapeHtml(row.status)}</span></td>
              <td style="font-family: var(--font-mono); font-weight: 600;">${formatCents(row.amount_cents)}</td>
              <td style="color: var(--text-secondary);">${escapeHtml(row.notes)}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table>
  </div>`;
}

function downloadRunReport(): void {
  if (!state.run) return;
  const blob = new Blob([JSON.stringify(state.run, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `smplcache-run-${state.run.scenario_id}-seed${state.run.seed}-${state.run.engine_commit}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function copyReplayLink(): void {
  syncUrlParams();
  navigator.clipboard?.writeText(window.location.href).catch(() => {});
  state.copiedReplayNotice = true;
  renderApp();
  window.setTimeout(() => {
    state.copiedReplayNotice = false;
    renderApp();
  }, 2200);
}

function jumpToRemoveEvidence(): void {
  stopPlayback();
  if (!state.run) return;
  const missingIdx = state.run.boundaries.findIndex(
    (b) => b.reason_code === "missing_evidence_for_repair"
  );
  if (missingIdx >= 0) {
    state.currentStepIndex = missingIdx + 1;
    syncUrlParams();
    renderApp();
  } else {
    state.scenarioId = "missing_evidence";
    state.currentStepIndex = 2;
    void fetchInitialAndRun(true);
  }
}

function renderApp(): void {
  const root = document.getElementById("app");
  if (!root) return;

  const run = state.run;
  const boundary = getActiveBoundary();
  const counters = getActiveCounters();
  const maxSteps = run ? run.boundaries.length : 7;

  const smplResult = boundary
    ? boundary.smplcache_result_cents
    : run?.initial_state.smplcache_result_cents ?? null;
  const baseResult = boundary
    ? boundary.baseline_result_cents
    : run?.initial_state.baseline_result_cents ?? null;
  const totalFormatted = boundary
    ? boundary.total_revenue_formatted
    : run?.initial_state.total_revenue_formatted ?? "$0.00";
  const sampleOrders = boundary
    ? boundary.sample_orders
    : run?.initial_state.sample_orders ?? [];
  const auxInfo = boundary
    ? boundary.aux_telemetry
    : run?.initial_state.aux_telemetry ?? null;

  const isMismatch =
    (boundary && !boundary.oracle_match) ||
    run?.summary.status === "mismatch";
  const isWithheld = boundary?.freshness_state === "invalidated_withheld";

  const statusCardClass = isMismatch
    ? "status-mismatch"
    : isWithheld
      ? "status-withheld"
      : "status-verified";

  const statusHeadline = isMismatch
    ? `✕ Mismatch detected at boundary ${run?.summary.first_failing_boundary}`
    : isWithheld
      ? `↻ Cache invalidated — stale answer withheld`
      : `✓ 0 mismatches in ${counters.oracle_checks} checked boundaries`;

  const recompPct =
    counters.full_result_recomputations_avoided_pct !== null
      ? ` (${counters.full_result_recomputations_avoided_pct}% of baseline)`
      : "";

  const srcAvoided = counters.source_queries_avoided;
  const srcAvoidedNote =
    srcAvoided < 0
      ? `3-table join rebuilds read 3 source tables vs. 1 baseline query`
      : `Baseline: ${counters.baseline_source_queries} vs. SmplCache: ${counters.smplcache_source_queries}`;

  root.innerHTML = `
    <header class="an-header">
      <div class="an-header__in">
        <a class="an-brand" href="https://anomalon.com/"><svg viewBox="0 0 22 22" aria-hidden="true"><rect x="1" y="1" width="20" height="20" rx="4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6 15.5 11 6l5 9.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="11" cy="12.6" r="1.7" fill="var(--an-accent)"/></svg>Anomalon <span class="an-brand__tool">/ SmplCache Live</span></a>
        <nav class="an-nav an-nav--inline" aria-label="Site">
          <a href="/">Overview</a>
          <a href="https://github.com/dbccpage/smplcache" target="_blank" rel="noopener noreferrer">Source</a>
          <a href="https://anomalon.com/">All tools</a>
        </nav>
      </div>
    </header>
    <div class="app-shell">

      <section class="hero-banner" aria-labelledby="hero-heading">
        <div class="hero-copy">
          <h1 id="hero-heading">Change the data. Keep the answer correct.</h1>
          <p>
            SmplCache preserves or repairs supported SQL results when the evidence is sufficient,
            and recomputes when it is not. Watch every decision checked against a native SQLite database.
          </p>
        </div>
        <div class="hero-actions">
          <button id="btn-run-demo" class="btn btn-primary" type="button">
            ${state.isPlaying ? "⏸ Pause 60s Demo" : "▶ Run the 60-second demo"}
          </button>
          <button id="btn-step-next" class="btn" type="button" ${state.currentStepIndex >= maxSteps ? "disabled" : ""}>
            Step through ▶
          </button>
          <button id="btn-remove-evidence" class="btn btn-warning" type="button">
            Remove required evidence
          </button>
          <button id="btn-inspect-sql" class="btn btn-sm" type="button">
            Inspect the SQL
          </button>
          <button id="btn-download-json" class="btn btn-sm" type="button">
            Download JSON Report
          </button>
          <button id="btn-copy-replay" class="btn btn-sm" type="button">
            ${state.copiedReplayNotice ? "✓ Replay Link Copied" : "Copy Replay URL"}
          </button>
        </div>
      </section>

      ${
        state.error
          ? `<div class="panel-card" style="margin-top:0.85rem; border-color: var(--accent-red); color: var(--an-bad);">
              <strong>Execution Error:</strong> ${escapeHtml(state.error)}
            </div>`
          : ""
      }

      <!-- Evidence & Correctness Strip -->
      <section class="evidence-strip" aria-label="Live verification and work counters">
        <div class="strip-card ${statusCardClass}">
          <span class="strip-label">Independent Native SQLite Check</span>
          <div class="strip-value">${escapeHtml(statusHeadline)}</div>
          <div class="strip-sub">Scoped to this seeded synthetic run · ${counters.oracle_queries} verification queries</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">Full Recomputations Avoided</span>
          <div class="strip-value">${counters.full_result_recomputations_avoided}${escapeHtml(recompPct)}</div>
          <div class="strip-sub">Baseline: ${counters.baseline_recomputations} full runs · SmplCache: ${counters.smplcache_rebuilds} rebuilds</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">Source Table Queries Avoided</span>
          <div class="strip-value" style="${srcAvoided < 0 ? "color: var(--accent-amber);" : ""}">${srcAvoided}</div>
          <div class="strip-sub">${escapeHtml(srcAvoidedNote)}</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">SmplCache Decisions</span>
          <div class="strip-value">
            ${counters.preserve_count}P · ${counters.repair_count}R · ${counters.invalidate_count}I
          </div>
          <div class="strip-sub">Preserve / Repair / Invalidate</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">Step Progress</span>
          <div class="strip-value">
            Boundary ${state.currentStepIndex} of ${maxSteps}
          </div>
          <div class="strip-sub">${escapeHtml(run?.config.dataset_size ?? 1000)} seeded orders (seed ${escapeHtml(state.seed)})</div>
        </div>
      </section>

      <!-- Scenario & Playback Controls -->
      <section class="controls-bar" aria-label="Scenario and step controls">
        <div class="controls-row">
          <div class="scenario-tabs" role="tablist" aria-label="Scenario selection">
            <button type="button" role="tab" aria-selected="${state.scenarioId === "live_revenue"}" class="scenario-tab ${state.scenarioId === "live_revenue" ? "active" : ""}" data-scenario="live_revenue">
              1. Live revenue (Single-table SUM)
            </button>
            <button type="button" role="tab" aria-selected="${state.scenarioId === "customers_and_regions"}" class="scenario-tab ${state.scenarioId === "customers_and_regions" ? "active" : ""}" data-scenario="customers_and_regions">
              2. Customers &amp; regions (3-table JOIN &amp; capacity)
            </button>
            <button type="button" role="tab" aria-selected="${state.scenarioId === "missing_evidence"}" class="scenario-tab ${state.scenarioId === "missing_evidence" ? "active" : ""}" data-scenario="missing_evidence">
              3. Missing evidence (WAL latching &amp; recovery)
            </button>
          </div>

          <div class="playback-group">
            <button id="btn-prev" class="btn btn-sm" type="button" ${state.currentStepIndex <= 0 ? "disabled" : ""}>
              ◀ Prev
            </button>
            <button id="btn-next" class="btn btn-sm" type="button" ${state.currentStepIndex >= maxSteps ? "disabled" : ""}>
              Next ▶
            </button>
            <button id="btn-reset" class="btn btn-sm" type="button">
              ↺ Reset
            </button>
          </div>

          <div class="param-controls">
            <label>
              Orders:
              <select id="select-dataset-size" aria-label="Dataset size">
                ${[100, 500, 1000, 2500]
                  .map(
                    (sz) =>
                      `<option value="${sz}" ${state.datasetSize === sz ? "selected" : ""}>${sz.toLocaleString()} rows</option>`
                  )
                  .join("")}
              </select>
            </label>
            <label>
              Seed:
              <input id="input-seed" type="number" min="1" max="999999" value="${state.seed}" style="width: 78px;" aria-label="Random seed" />
            </label>
            ${
              state.scenarioId === "customers_and_regions"
                ? `<label>
                    Aux Row Limit:
                    <select id="select-aux-cap" aria-label="Auxiliary resident row limit">
                      <option value="none" ${state.auxMaxTotalRows === null ? "selected" : ""}>Unbounded (All rows)</option>
                      <option value="1500" ${state.auxMaxTotalRows === 1500 ? "selected" : ""}>1,500 rows (Fits 1k)</option>
                      <option value="250" ${state.auxMaxTotalRows === 250 ? "selected" : ""}>250 rows (Tight)</option>
                      <option value="60" ${state.auxMaxTotalRows === 60 ? "selected" : ""}>60 rows (Trigger eviction)</option>
                    </select>
                  </label>`
                : ""
            }
          </div>
        </div>

        <!-- Step Jump Pills (Permitted Mutations) -->
        <div class="step-pills" role="navigation" aria-label="Committed boundary steps">
          <button type="button" class="step-pill ${state.currentStepIndex === 0 ? "active" : ""}" data-step="0">
            0. Initial Snapshot
          </button>
          ${(run?.boundaries ?? [])
            .map(
              (b) => `
              <button type="button" class="step-pill ${state.currentStepIndex === b.boundary_index ? "active" : ""}" data-step="${b.boundary_index}">
                ${b.step_number}. ${escapeHtml(b.action_label)} (${escapeHtml(b.ui_badge)})
              </button>
            `
            )
            .join("")}
        </div>
      </section>

      <!-- Main Comparison Workspace -->
      <main class="workspace-grid">
        <!-- Left Column: Current Event + Side-by-Side Results -->
        <div class="panel-card">
          <div class="panel-header">
            <h2 class="panel-title">
              ${
                boundary
                  ? `Step ${boundary.step_number} of ${maxSteps}: ${escapeHtml(boundary.title)}`
                  : `Boundary 0: Initial Seeded Database Snapshot`
              }
            </h2>
            ${renderDecisionBadge(boundary)}
          </div>

          <div class="event-banner">
            <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:0.5rem; font-size:0.78rem; color:var(--text-muted);">
              <span>Table written: <strong style="color:var(--text-bright); font-family:var(--font-mono);">${escapeHtml(boundary?.table_written ?? "orders (seed)")}</strong></span>
              <span>Operation: <strong style="color:var(--text-bright); font-family:var(--font-mono);">${escapeHtml(boundary?.operation ?? "INITIAL_SEED")}</strong></span>
              <span>Delta impact: <strong style="color:var(--accent-emerald);">${escapeHtml(boundary?.delta_summary ?? "Initial parity verified")}</strong></span>
            </div>

            <div class="event-reason-box">
              <strong>Decision Reason:</strong>
              ${escapeHtml(
                boundary?.plain_language_reason ??
                  "Both Baseline and SmplCache are seeded from the same initial SQLite snapshot. Zero post-initialization queries have run yet."
              )}
              <div style="margin-top:0.3rem; font-size:0.82rem; color:var(--text-muted);">
                ${escapeHtml(boundary?.narration ?? "")}
              </div>
            </div>

            ${
              boundary?.stale_value_withheld
                ? `<div class="withheld-notice" role="status">
                    <strong>Stale Cache Withheld Before Rebuild:</strong>
                    When this event arrived without complete repair evidence, SmplCache immediately marked the cached result invalid
                    (<code>pre_rebuild_result: null</code>), refused to serve or repair the stale total, executed
                    <strong>${boundary.boundary_counters.smplcache_source_queries} source snapshot query(s)</strong> at committed boundary
                    <code>b0${boundary.boundary_index}</code>, and verified the rebuilt answer against SQLite.
                  </div>`
                : ""
            }

            ${
              boundary
                ? `<div class="diff-grid">
                    <div class="diff-box">
                      <div class="diff-box-title">CDC Before Image (old)</div>
                      <div>${
                        boundary.before_image
                          ? escapeHtml(JSON.stringify(boundary.before_image, null, 2))
                          : "null (INSERT / none)"
                      }</div>
                    </div>
                    <div class="diff-box">
                      <div class="diff-box-title">CDC After Image (new)</div>
                      <div>${
                        boundary.after_image
                          ? escapeHtml(JSON.stringify(boundary.after_image, null, 2))
                          : "null (DELETE / none)"
                      }</div>
                    </div>
                  </div>`
                : ""
            }
          </div>

          <!-- Side-by-Side SmplCache vs. Baseline -->
          <div class="comparison-grid">
            <div class="policy-box smplcache-box ${isWithheld ? "withheld-mode" : ""}">
              <div class="policy-header">
                <div>
                  <div class="policy-name">SmplCache (Certified Incremental)</div>
                  <div class="policy-sub">
                    ${
                      boundary
                        ? `This boundary: ${boundary.boundary_counters.smplcache_source_queries} source queries (${boundary.boundary_counters.smplcache_rebuilds} rebuilds)`
                        : `Initialized from snapshot`
                    }
                  </div>
                </div>
                <span class="commit-tag" style="color: ${
                  isMismatch
                    ? "var(--accent-red)"
                    : isWithheld
                      ? "var(--accent-amber)"
                      : "var(--accent-emerald)"
                };">
                  ${escapeHtml(boundary?.freshness_state ?? "initial_verified")}
                </span>
              </div>
              <div class="total-money">
                <span>${escapeHtml(totalFormatted)}</span>
                ${
                  boundary
                    ? `<span class="delta-tag">${escapeHtml(boundary.delta_summary)}</span>`
                    : ""
                }
              </div>
              ${renderGroupBars(smplResult, false)}
            </div>

            <div class="policy-box">
              <div class="policy-header">
                <div>
                  <div class="policy-name">Baseline (Table Invalidation)</div>
                  <div class="policy-sub">
                    ${
                      boundary
                        ? `This boundary: ${boundary.boundary_counters.baseline_source_queries} full SQL query on read`
                        : `Initialized from snapshot`
                    }
                  </div>
                </div>
                <span class="commit-tag">table-flush policy</span>
              </div>
              <div class="total-money">
                <span>${escapeHtml(totalFormatted)}</span>
              </div>
              ${renderGroupBars(baseResult, true)}
            </div>
          </div>
        </div>

        <!-- Right Column: Measured Work Counters + Live Sample Orders -->
        <div style="display:flex; flex-direction:column; gap:0.9rem;">
          <div class="panel-card">
            <div class="panel-header">
              <h2 class="panel-title">Measured Work &amp; Verification Counters</h2>
              <span class="commit-tag">Seed ${escapeHtml(state.seed)}</span>
            </div>
            <div class="metrics-list">
              <div class="metric-tile" title="${escapeHtml(run?.counter_definitions.baseline_recomputations ?? "")}">
                <div class="metric-tile-label">Baseline Recomputations</div>
                <div class="metric-tile-val">${counters.baseline_recomputations}</div>
                <div class="metric-tile-note">${counters.baseline_source_queries} SQL queries after init</div>
              </div>
              <div class="metric-tile" title="${escapeHtml(run?.counter_definitions.smplcache_rebuilds ?? "")}">
                <div class="metric-tile-label">SmplCache Rebuilds</div>
                <div class="metric-tile-val">${counters.smplcache_rebuilds}</div>
                <div class="metric-tile-note">${counters.smplcache_source_queries} source table reads</div>
              </div>
              <div class="metric-tile" title="${escapeHtml(run?.counter_definitions.oracle_queries ?? "")}">
                <div class="metric-tile-label">Independent SQLite Checks</div>
                <div class="metric-tile-val">${counters.oracle_checks} / ${counters.oracle_checks}</div>
                <div class="metric-tile-note">${counters.mismatch_count} mismatches (excluded from policy counts)</div>
              </div>
              <div class="metric-tile" title="${escapeHtml(run?.counter_definitions.initialization_queries ?? "")}">
                <div class="metric-tile-label">Initialization Queries</div>
                <div class="metric-tile-val">${run?.initial_state.initialization_queries ?? 2}</div>
                <div class="metric-tile-note">Boundary 0 setup reads</div>
              </div>
            </div>

            ${
              auxInfo
                ? `<div class="event-banner" style="margin-bottom:0;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.3rem;">
                      <strong style="font-size:0.82rem;">JoinAuxState Resident Row Telemetry</strong>
                      <span class="commit-tag">
                        ${auxInfo.resident_aux_rows} / ${auxInfo.aux_max_total_rows ?? "∞"} rows
                      </span>
                    </div>
                    <div style="font-size:0.76rem; color:var(--text-secondary);">
                      Evicted keys tracked: <strong>${auxInfo.evicted_aux_keys_count ?? 0}</strong>
                      ${
                        auxInfo.affected_fanout_count !== undefined &&
                        auxInfo.affected_fanout_count !== null
                          ? ` · Customer #1 fanout repaired: <strong>${auxInfo.affected_fanout_count} paid orders</strong>`
                          : ""
                      }
                    </div>
                    <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.3rem;">
                      Note: Limit counts resident auxiliary rows in JoinAuxState. Indexes, eviction tombstones,
                      frequency counters, and output groups also consume memory; row capacity is not a total RAM limit.
                    </div>
                  </div>`
                : ""
            }
          </div>

          <div class="panel-card">
            <div class="panel-header">
              <h2 class="panel-title">Committed SQLite Sample Rows (Orders #101–#107)</h2>
              <span class="commit-tag">Highlighted = active mutation</span>
            </div>
            ${renderSampleOrders(sampleOrders, boundary?.highlighted_order_id ?? null)}
          </div>
        </div>
      </main>

      <!-- Progressive Disclosure Inspector -->
      <section id="inspector-section" class="inspector-section panel-card" aria-label="Engineering evidence and SQL shape inspector">
        <div class="inspector-tabs" role="tablist">
          <button type="button" class="scenario-tab ${state.inspectorTab === "certificate" ? "active" : ""}" data-inspector="certificate">
            Decision Certificate &amp; Query Log
          </button>
          <button type="button" class="scenario-tab ${state.inspectorTab === "sql" ? "active" : ""}" data-inspector="sql">
            Inspect the SQL &amp; AST Allowlist Rejections
          </button>
          <button type="button" class="scenario-tab ${state.inspectorTab === "architecture" ? "active" : ""}" data-inspector="architecture">
            Architecture, Timings &amp; Scoped Claims
          </button>
        </div>

        ${
          state.inspectorTab === "certificate"
            ? `<div class="inspector-grid">
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">Machine-Readable Decision Certificate</h3>
                  <pre class="code-block">${escapeHtml(
                    JSON.stringify(
                      boundary?.certificate ?? {
                        note: "Boundary 0 (Initial Seed). Step forward to inspect per-event certificates.",
                      },
                      null,
                      2
                    )
                  )}</pre>
                </div>
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">
                    Instrumented SQLite Query Log (Total: ${run?.query_log.length ?? 0} queries)
                  </h3>
                  <pre class="code-block">${escapeHtml(
                    JSON.stringify(
                      (run?.query_log ?? []).filter(
                        (q) => q.boundary_index === state.currentStepIndex
                      ),
                      null,
                      2
                    )
                  )}</pre>
                  <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.35rem;">
                    Canonical Run Hash (SHA-256): <code>${escapeHtml(run?.canonical_hash ?? "")}</code>
                  </div>
                </div>
              </div>`
            : ""
        }

        ${
          state.inspectorTab === "sql"
            ? `<div>
                <div class="inspector-grid">
                  <div>
                    <h3 class="panel-title" style="margin-bottom:0.45rem;">Admitted Scenario SQL</h3>
                    <pre class="code-block">${escapeHtml(run?.sql ?? "")}</pre>
                  </div>
                  <div>
                    <h3 class="panel-title" style="margin-bottom:0.45rem;">Extracted Shape &amp; Required Evidence</h3>
                    <pre class="code-block">${escapeHtml(JSON.stringify(run?.shape ?? {}, null, 2))}</pre>
                  </div>
                </div>
                <h3 class="panel-title" style="margin-top:0.9rem;">
                  Fail-Closed SQL AST Allowlist: Live Rejection Examples (via <code>parse_sql</code> / <code>parse_join_sql</code>)
                </h3>
                <p style="font-size:0.82rem; color:var(--text-secondary); margin:0.2rem 0 0.5rem;">
                  SmplCache never guesses on SQL constructs outside its verified single-block SPJA fragment.
                  The queries below are evaluated through the real parser on the server:
                </p>
                <div class="rejected-list">
                  ${(state.metadata?.rejected_sql_examples ?? [])
                    .map(
                      (ex) => `<div class="rejected-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; gap:0.4rem; margin-bottom:0.35rem;">
                          <strong style="font-size:0.82rem;">${escapeHtml(ex.label)}</strong>
                          <span class="decision-badge badge-unsupported" style="font-size:0.7rem; padding:0.12rem 0.5rem;">
                            Rejected: ${escapeHtml(ex.rejection_reason)}
                          </span>
                        </div>
                        <pre class="code-block" style="margin-bottom:0.4rem; padding:0.45rem;">${escapeHtml(ex.sql)}</pre>
                        <div style="font-size:0.76rem; color:var(--text-secondary);">${escapeHtml(ex.explanation)}</div>
                      </div>`
                    )
                    .join("")}
                </div>
              </div>`
            : ""
        }

        ${
          state.inspectorTab === "architecture"
            ? `<div class="inspector-grid">
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">How SmplCache Evaluates Every Committed Write</h3>
                  <div class="code-block" style="line-height:1.65;">
1. <strong>Parse SQL Shape (AST Allowlist)</strong>
   Decompose admitted SELECT..FROM..[JOIN]..WHERE..GROUP BY into dependency fingerprint &amp; required CDC evidence columns.
2. <strong>Boundary Intersection Check</strong>
   If modified columns are disjoint from the shape fingerprint (e.g. <code>orders.notes</code>), emit <strong>Preserve</strong> (0 DB queries).
3. <strong>Evidence Completeness Check</strong>
   Verify required columns are present in both <code>old</code> and <code>new</code> row images (and foreign-key keys are resident in <code>JoinAuxState</code>).
4. <strong>In-Place Delta Repair or Fail-Closed Rebuild</strong>
   • Sufficient evidence → <strong>Repair</strong> group sum &amp; count in place (0 DB queries).
   • Missing/evicted evidence → <strong>Invalidate</strong>, withhold stale cache, and rebuild from the committed source snapshot.
5. <strong>Independent Native SQLite Verification</strong>
   Every boundary in this demo is checked against native SQLite integer <code>SUM(amount_cents)</code>.
                  </div>
                </div>
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">Scoped Engineering Claims &amp; Boundaries</h3>
                  <div class="code-block" style="line-height:1.55;">
<strong>1. What this demo supports:</strong>
${escapeHtml(run?.claim_hygiene.what_it_supports ?? "")}

<strong>2. What this demo does not support:</strong>
${escapeHtml(run?.claim_hygiene.what_it_does_not_support ?? "")}

<strong>3. Narrowest defensible claim:</strong>
${escapeHtml(run?.claim_hygiene.narrowest_defensible_claim ?? "")}

<strong>4. Next test needed:</strong>
${escapeHtml(run?.claim_hygiene.next_test_needed ?? "")}

<strong>Server Monotonic Timings (Separated):</strong>
• Seed &amp; Init: ${run?.timings_ms.seed_and_init_ms ?? 0} ms
• Policy Execution: ${run?.timings_ms.policy_execution_ms ?? 0} ms
• Oracle Verification Overhead: ${run?.timings_ms.oracle_verification_ms ?? 0} ms
• Note: ${escapeHtml(run?.timings_ms.workload_note ?? "")}
                  </div>
                </div>
              </div>`
            : ""
        }
      </section>

      <footer class="footer-section">
        <div>
          Anomalon · SmplCache Live · Apache-2.0 ·
          <span class="commit-tag" title="Engine & App Git Revision">commit: ${escapeHtml(run?.engine_commit ?? "unavailable")}</span>
        </div>
        <div style="display:flex; gap:1rem; flex-wrap:wrap;">
          <a href="https://github.com/dbccpage/smplcache" target="_blank" rel="noopener noreferrer">Source</a>
          <a href="https://github.com/dbccpage/smplcache/blob/main/docs/reviews/pr7_review.md" target="_blank" rel="noopener noreferrer">PR #7 review</a>
          <a href="https://github.com/dbccpage/smplcache/blob/main/docs/mvp_smplcache_anomalon.md" target="_blank" rel="noopener noreferrer">MVP specification</a>
          <a href="/api/scenarios" target="_blank" rel="noopener noreferrer">/api/scenarios</a>
          <a href="/healthz" target="_blank" rel="noopener noreferrer">/healthz</a>
          <a href="https://anomalon.com/about/">About</a>
          <a href="https://anomalon.com/contact/">Contact</a>
        </div>
      </footer>
    </div>
  `;

  bindEvents();
}

function bindEvents(): void {
  document.getElementById("btn-run-demo")?.addEventListener("click", () => {
    if (state.isPlaying) {
      stopPlayback();
      renderApp();
    } else {
      startPlayback();
    }
  });

  document.getElementById("btn-step-next")?.addEventListener("click", () => {
    stopPlayback();
    if (state.run && state.currentStepIndex < state.run.boundaries.length) {
      state.currentStepIndex += 1;
      syncUrlParams();
      renderApp();
    }
  });

  document.getElementById("btn-prev")?.addEventListener("click", () => {
    stopPlayback();
    if (state.currentStepIndex > 0) {
      state.currentStepIndex -= 1;
      syncUrlParams();
      renderApp();
    }
  });

  document.getElementById("btn-next")?.addEventListener("click", () => {
    stopPlayback();
    if (state.run && state.currentStepIndex < state.run.boundaries.length) {
      state.currentStepIndex += 1;
      syncUrlParams();
      renderApp();
    }
  });

  document.getElementById("btn-reset")?.addEventListener("click", () => {
    stopPlayback();
    state.currentStepIndex = 0;
    syncUrlParams();
    renderApp();
  });

  document
    .getElementById("btn-remove-evidence")
    ?.addEventListener("click", () => {
      jumpToRemoveEvidence();
    });

  document.getElementById("btn-inspect-sql")?.addEventListener("click", () => {
    state.inspectorTab = "sql";
    renderApp();
    document
      .getElementById("inspector-section")
      ?.scrollIntoView({ behavior: "smooth" });
  });

  document
    .getElementById("btn-download-json")
    ?.addEventListener("click", () => {
      downloadRunReport();
    });

  document.getElementById("btn-copy-replay")?.addEventListener("click", () => {
    copyReplayLink();
  });

  document.querySelectorAll<HTMLElement>("[data-scenario]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-scenario") as ScenarioId | null;
      if (target && target !== state.scenarioId) {
        state.scenarioId = target;
        state.currentStepIndex = 1;
        void fetchInitialAndRun(false);
      }
    });
  });

  document.querySelectorAll<HTMLElement>("[data-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      stopPlayback();
      const s = Number(btn.getAttribute("data-step"));
      if (Number.isInteger(s)) {
        state.currentStepIndex = s;
        syncUrlParams();
        renderApp();
      }
    });
  });

  document.querySelectorAll<HTMLElement>("[data-inspector]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-inspector") as
        | "certificate"
        | "sql"
        | "architecture"
        | null;
      if (tab) {
        state.inspectorTab = tab;
        renderApp();
      }
    });
  });

  const sizeSel = document.getElementById(
    "select-dataset-size"
  ) as HTMLSelectElement | null;
  sizeSel?.addEventListener("change", () => {
    const val = Number(sizeSel.value);
    if ([100, 500, 1000, 2500].includes(val)) {
      state.datasetSize = val;
      void fetchInitialAndRun(true);
    }
  });

  const seedInput = document.getElementById(
    "input-seed"
  ) as HTMLInputElement | null;
  seedInput?.addEventListener("change", () => {
    const val = Math.max(1, Math.min(999999, Number(seedInput.value) || 42));
    state.seed = val;
    void fetchInitialAndRun(true);
  });

  const capSel = document.getElementById(
    "select-aux-cap"
  ) as HTMLSelectElement | null;
  capSel?.addEventListener("change", () => {
    const raw = capSel.value;
    state.auxMaxTotalRows = raw === "none" ? null : Number(raw);
    void fetchInitialAndRun(true);
  });
}

window.addEventListener("keydown", (ev) => {
  const tag = (ev.target as HTMLElement | null)?.tagName?.toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return;
  if (ev.key === "ArrowRight") {
    ev.preventDefault();
    stopPlayback();
    if (state.run && state.currentStepIndex < state.run.boundaries.length) {
      state.currentStepIndex += 1;
      syncUrlParams();
      renderApp();
    }
  } else if (ev.key === "ArrowLeft") {
    ev.preventDefault();
    stopPlayback();
    if (state.currentStepIndex > 0) {
      state.currentStepIndex -= 1;
      syncUrlParams();
      renderApp();
    }
  }
});

parseUrlParams();
void fetchInitialAndRun(true);
