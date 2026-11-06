import {
  buildBoundaryChangeSql,
  buildScreenUrlSearch,
  classifyChangeMove,
  decisionKindToMove,
  formatCentsUsd,
  formatFixInPlaceArithmetic,
  formatPlainTextRecord,
  formatSignedCentsUsd,
  parseScreenUrlState,
  syncScreenUrlState,
  type MoveCode,
  type ReplayRecordInput,
  type ScreenUrlState,
} from "./moves";
import type { RunTraceResponse } from "./types";

const QUERY_SQL =
  "SELECT r.name, SUM(o.amount_cents) FROM orders o JOIN customers c ON o.customer_id = c.id JOIN regions r ON c.region_id = r.id WHERE o.status = 'paid' GROUP BY r.name";

const REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America"];
const DEFAULT_BASE: Record<string, number> = {
  "North America": 2651700,
  Europe: 2160200,
  "Asia Pacific": 1641200,
  "Latin America": 1303700,
};
let baseTotals: Record<string, number> = { ...DEFAULT_BASE };
const ACME_BASE = 57500;

interface FrontStepDef {
  step: number;
  sql: string;
  table: string;
  operation: "UPDATE";
  beforeImage: Record<string, unknown>;
  afterImage: Record<string, unknown>;
  changedColumns?: string[];
}

const FRONT_STEPS: Record<number, FrontStepDef> = {
  1: {
    step: 1,
    sql: "UPDATE orders SET notes = 'Loading bay 4' WHERE id = 101",
    table: "orders",
    operation: "UPDATE",
    beforeImage: {
      id: 101,
      customer_id: 1,
      status: "paid",
      amount_cents: 10000,
      notes: "Leave at front desk",
    },
    afterImage: {
      id: 101,
      customer_id: 1,
      status: "paid",
      amount_cents: 10000,
      notes: "Loading bay 4",
    },
  },
  2: {
    step: 2,
    sql: "UPDATE orders SET amount_cents = 12500 WHERE id = 101",
    table: "orders",
    operation: "UPDATE",
    beforeImage: {
      id: 101,
      customer_id: 1,
      status: "paid",
      amount_cents: 10000,
      notes: "Leave at front desk",
    },
    afterImage: {
      id: 101,
      customer_id: 1,
      status: "paid",
      amount_cents: 12500,
      notes: "Leave at front desk",
    },
  },
  3: {
    step: 3,
    sql: "UPDATE customers SET region_id = 2 WHERE id = 1",
    table: "customers",
    operation: "UPDATE",
    beforeImage: {
      id: 1,
      name: "Acme Industrial",
      region_id: 1,
    },
    afterImage: {
      id: 1,
      name: "Acme Industrial",
      region_id: 2,
    },
  },
  4: {
    step: 4,
    sql: "UPDATE orders SET amount_cents = 24000 WHERE id = 106",
    table: "orders",
    operation: "UPDATE",
    beforeImage: {
      id: 106,
      customer_id: 1,
      status: "paid",
    },
    afterImage: {
      id: 106,
      customer_id: 1,
      status: "paid",
      amount_cents: 24000,
      notes: "Annual renewal invoice",
    },
    changedColumns: ["amount_cents"],
  },
};

interface LocalState {
  done: Record<number, boolean>;
  last: number;
  hasChange: boolean;
  redis: number;
  mv: number;
  ours: number;
}

const urlState: ScreenUrlState = parseScreenUrlState(window.location.search, {
  scenario: "customers_and_regions",
  seed: 42,
  size: 1000,
  step: 0,
  maxStep: 7,
});

let s: LocalState = {
  done: {},
  last: 0,
  hasChange: false,
  redis: 0,
  mv: 0,
  ours: 0,
};
let cached: Record<string, number> = {};
let timer: number | undefined;
let copyTimer: number | undefined;
let activeRecordInput: ReplayRecordInput = {
  scenario: urlState.scenario,
  seed: urlState.seed,
  size: urlState.size,
  step: 0,
  changeSql: "(no change yet)",
  move: "INITIAL",
  reasonCode: "initial_snapshot",
  beforeTotals: null,
  afterTotals: null,
  groupOrder: REGIONS,
};

function truth(st: LocalState): Record<string, number> {
  const t: Record<string, number> = { ...baseTotals };
  const acme = ACME_BASE + (st.done[2] ? 2500 : 0) + (st.done[4] ? 4000 : 0);
  t[st.done[3] ? "Europe" : "North America"] += acme;
  return t;
}

function updateLabLinks(): void {
  const search = buildScreenUrlSearch(urlState);
  document.querySelectorAll<HTMLAnchorElement>('a[href^="/lab"]').forEach((a) => {
    a.href = `/lab/${search}`;
  });
}

function refreshRecordState(): void {
  const text = formatPlainTextRecord(activeRecordInput);
  const copyBtn = document.getElementById("copy-record");
  if (copyBtn) {
    copyBtn.setAttribute("data-record", text);
  }
}

function renderQueryCells(): void {
  const redisEl = document.getElementById("redis");
  const mvEl = document.getElementById("mv");
  const oursEl = document.getElementById("ours");
  if (!redisEl || !mvEl || !oursEl) return;

  if (!s.hasChange) {
    redisEl.textContent = "no change yet";
    mvEl.textContent = "no change yet";
    oursEl.textContent = "no change yet";
    redisEl.classList.add("idle");
    mvEl.classList.add("idle");
    oursEl.classList.add("idle");
  } else {
    redisEl.textContent = String(s.redis);
    mvEl.textContent = String(s.mv);
    oursEl.textContent = String(s.ours);
    redisEl.classList.remove("idle");
    mvEl.classList.remove("idle");
    oursEl.classList.remove("idle");
  }
}

function render(
  deltas: Record<string, number>,
  withheld: string | null
): void {
  const db = truth(s);
  const rowsEl = document.getElementById("rows");
  if (rowsEl) {
    rowsEl.innerHTML = REGIONS.map((r, idx) => {
      const d = deltas[r];
      const cls =
        withheld === r ? "num withheld" : d ? "num hit" : "num";
      const val = withheld === r ? "withheld" : formatCentsUsd(cached[r]);
      const chip = d
        ? `<span class="delta${d < 0 ? " neg" : ""}">${
            d > 0 ? "+" : "−"
          }${formatCentsUsd(Math.abs(d))}</span>`
        : "";
      const ok = withheld === r ? "" : cached[r] === db[r] ? "✓" : "✗";
      return `<tr><td class="id">${
        idx + 1
      }</td><td>${r}</td><td class="${cls}">${chip}${val}</td><td class="num">${formatCentsUsd(
        db[r]
      )}</td><td class="chk">${ok}</td></tr>`;
    }).join("");
  }

  const cell = (
    step: number,
    oldV: string,
    newV: string,
    extra = ""
  ): string => {
    if (!s.done[step]) return `<td${extra}>${oldV}</td>`;
    if (s.last !== step) return `<td${extra}>${newV}</td>`;
    if (step === 4) {
      return `<td class="num lost"><s>?</s><span class="arrow">→</span>${newV}<span class="tag">log has no old value</span></td>`;
    }
    return `<td class="${extra.includes("num") ? "num " : ""}${
      extra.includes("wrap") ? "wrap " : ""
    }chg"><s>${oldV}</s><span class="arrow">→</span>${newV}</td>`;
  };

  const ordersEl = document.getElementById("orders");
  if (ordersEl) {
    ordersEl.innerHTML =
      `<tr><td class="id">101</td><td>1</td><td>paid</td>${cell(
        2,
        "10000",
        "12500",
        ' class="num"'
      )}${cell(
        1,
        "Leave at front desk",
        "Loading bay 4",
        ' class="wrap"'
      )}</tr>` +
      `<tr><td class="id">106</td><td>1</td><td>paid</td>${cell(
        4,
        "20000",
        "24000",
        ' class="num"'
      )}<td class="wrap">Annual renewal invoice</td></tr>`;
  }

  const customersEl = document.getElementById("customers");
  if (customersEl) {
    customersEl.innerHTML = `<tr><td class="id">1</td><td>Acme Industrial</td>${cell(
      3,
      "1",
      "2",
      ' class="num"'
    )}</tr>`;
  }

  renderQueryCells();
}

function setVerdict(kind: string, word: string, text: string): void {
  const el = document.getElementById("w-us");
  if (!el) return;
  el.innerHTML =
    (word ? `<span class="word ${kind}">${word}</span>` : "") + text;
}

function others(redis: string, mv: string): void {
  const rEl = document.getElementById("w-redis");
  const mEl = document.getElementById("w-mv");
  if (rEl) rEl.textContent = redis;
  if (mEl) mEl.textContent = mv;
}

function diff(
  before: Record<string, number>,
  after: Record<string, number>
): Record<string, number> {
  const d: Record<string, number> = {};
  for (const r of REGIONS) {
    if (after[r] !== before[r]) {
      d[r] = after[r] - before[r];
    }
  }
  return d;
}

function stripLeadingMovePrefix(reasonText: string): string {
  return reasonText.replace(/^(?:KEEP|FIX IN PLACE|REREAD)\s*[—-]\s*/i, "");
}

function reset(updateUrl = true): void {
  window.clearTimeout(timer);
  s = { done: {}, last: 0, hasChange: false, redis: 0, mv: 0, ours: 0 };
  cached = truth(s);
  render({}, null);
  setVerdict(
    "",
    "",
    "Pick a change on the left. Every result is checked against the database."
  );
  others(
    "Drops the key on every write. The next read reruns the whole query.",
    "Keeps the old totals until someone runs REFRESH, which reruns the whole query."
  );
  document.querySelectorAll<HTMLButtonElement>("button.change").forEach((b) => {
    b.classList.remove("done");
    b.setAttribute("aria-pressed", "false");
    b.disabled = false;
  });

  activeRecordInput = {
    scenario: urlState.scenario,
    seed: urlState.seed,
    size: urlState.size,
    step: 0,
    changeSql: "(no change yet)",
    move: "INITIAL",
    reasonCode: "initial_snapshot",
    beforeTotals: { ...cached },
    afterTotals: { ...cached },
    groupOrder: REGIONS,
  };
  refreshRecordState();

  if (updateUrl) {
    urlState.step = 0;
    syncScreenUrlState(urlState);
    updateLabLinks();
  }
}

function apply(
  step: number,
  options: { immediate?: boolean; syncUrl?: boolean } = {}
): void {
  const stepDef = FRONT_STEPS[step];
  if (!stepDef || s.done[step]) return;

  window.clearTimeout(timer);
  document.querySelectorAll<HTMLButtonElement>("button.change").forEach((b) => {
    b.setAttribute("aria-pressed", "false");
  });
  const btn = document.getElementById("c" + step) as HTMLButtonElement | null;
  btn?.setAttribute("aria-pressed", "true");

  const acmeRegion = s.done[3] ? "Europe" : "North America";
  const before = { ...cached };
  s.done[step] = true;
  s.last = step;
  s.hasChange = true;
  s.redis += 1;
  s.mv += 1;
  const after = truth(s);

  const classified = classifyChangeMove({
    sql: QUERY_SQL,
    table: stepDef.table,
    operation: stepDef.operation,
    beforeImage: stepDef.beforeImage,
    afterImage: stepDef.afterImage,
    changedColumns: stepDef.changedColumns,
    hasMatchedJoinRows: true,
  });

  if (classified.move === "KEEP") {
    render({}, null);
    setVerdict(
      "kept",
      "KEEP",
      `(${classified.reasonCode}) — notes isn't in the query dependency set, so no total can have changed. Nothing to do, no query.`
    );
    others(
      "Drops the key anyway. It can't tell that notes isn't in the query. The next read reruns the whole join.",
      "Nothing changed, but it can't tell. The next REFRESH reruns the whole join anyway."
    );
    activeRecordInput = {
      scenario: urlState.scenario,
      seed: urlState.seed,
      size: urlState.size,
      step,
      changeSql: stepDef.sql,
      move: classified.move,
      reasonCode: classified.reasonCode,
      beforeTotals: before,
      afterTotals: after,
      groupOrder: REGIONS,
    };
    refreshRecordState();
  } else if (classified.move === "FIX IN PLACE") {
    cached = { ...after };
    render(diff(before, after), null);
    const arith = formatFixInPlaceArithmetic({
      beforeTotals: before,
      afterTotals: after,
      beforeImage: stepDef.beforeImage,
      afterImage: stepDef.afterImage,
      groupOrder: REGIONS,
    });
    setVerdict(
      "fixed",
      "FIX IN PLACE",
      `(${classified.reasonCode}) — ${arith}. No query.`
    );
    if (step === 2) {
      others(
        "Drops the key. The next read reruns the whole join to pick up a $25 change.",
        `Shows ${acmeRegion} $25 short until REFRESH reruns the whole join.`
      );
    } else {
      const moved = Math.abs(
        (after["Europe"] ?? 0) - (before["Europe"] ?? 0)
      );
      others(
        "Drops the key only because the app's code says to. Redis itself doesn't know the customers row feeds this answer. The next read reruns the whole join.",
        `Shows ${formatCentsUsd(
          moved
        )} in the wrong region until REFRESH reruns the whole join.`
      );
    }
    activeRecordInput = {
      scenario: urlState.scenario,
      seed: urlState.seed,
      size: urlState.size,
      step,
      changeSql: stepDef.sql,
      move: classified.move,
      reasonCode: classified.reasonCode,
      arithmeticSummary: arith,
      beforeTotals: before,
      afterTotals: after,
      groupOrder: REGIONS,
    };
    refreshRecordState();
  } else {
    // REREAD
    render({}, acmeRegion);
    setVerdict(
      "refused",
      "REREAD",
      `(${classified.reasonCode}) — Log row has 24000 after but no old value, so the difference can't be computed. Stops serving ${acmeRegion} and rereads.`
    );
    others(
      "Drops the key. The next read reruns the whole join.",
      `Shows ${acmeRegion} $40 short until REFRESH reruns the whole join.`
    );
    activeRecordInput = {
      scenario: urlState.scenario,
      seed: urlState.seed,
      size: urlState.size,
      step,
      changeSql: stepDef.sql,
      move: "REREAD",
      reasonCode: classified.reasonCode,
      beforeTotals: before,
      afterTotals: after,
      groupOrder: REGIONS,
    };
    refreshRecordState();

    const finish = (): void => {
      s.ours += 3;
      cached = { ...after };
      render(diff(before, after), null);
      const deltaCents = (after[acmeRegion] ?? 0) - (before[acmeRegion] ?? 0);
      setVerdict(
        "rebuilt",
        "REREAD",
        `(${classified.reasonCode}) — Read the 3 tables again and matched the database (${acmeRegion}: ${formatCentsUsd(
          before[acmeRegion]
        )} → ${formatCentsUsd(after[acmeRegion])}, ${formatSignedCentsUsd(
          deltaCents
        )}). Cost: 3 reads vs 1.`
      );
      refreshRecordState();
    };

    if (
      options.immediate ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      finish();
    } else {
      timer = window.setTimeout(finish, 1400);
    }
  }

  btn?.classList.add("done");
  if (options.syncUrl !== false) {
    urlState.step = step;
    syncScreenUrlState(urlState);
    updateLabLinks();
  }
}

async function loadSeededFrontStep(state: ScreenUrlState): Promise<void> {
  try {
    const resp = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_id: "customers_and_regions",
        seed: state.seed,
        dataset_size: state.size,
        aux_max_total_rows: null,
      }),
    });
    if (resp.ok) {
      const run = (await resp.json()) as RunTraceResponse;
      const initTotals = run.initial_state.oracle_result_cents;
      baseTotals = {
        "North America": (initTotals["North America"] ?? 0) - ACME_BASE,
        Europe: initTotals["Europe"] ?? 0,
        "Asia Pacific": initTotals["Asia Pacific"] ?? 0,
        "Latin America": initTotals["Latin America"] ?? 0,
      };
    }
  } catch {
    // Keep DEFAULT_BASE if offline
  }
  reset(false);
  if (state.step >= 1 && state.step <= 4) {
    apply(state.step, { immediate: true, syncUrl: true });
  } else {
    syncScreenUrlState(state);
    updateLabLinks();
  }
}

async function applyRemoteScenarioStep(state: ScreenUrlState): Promise<void> {
  try {
    const resp = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_id: state.scenario,
        seed: state.seed,
        dataset_size: state.size,
        aux_max_total_rows:
          state.scenario === "customers_and_regions" ? state.cap : null,
      }),
    });
    if (!resp.ok) return;
    const run = (await resp.json()) as RunTraceResponse;
    const initTotals = run.initial_state.oracle_result_cents;
    if (
      "North America" in initTotals &&
      "Europe" in initTotals &&
      "Asia Pacific" in initTotals &&
      "Latin America" in initTotals
    ) {
      baseTotals = {
        "North America": (initTotals["North America"] ?? 0) - ACME_BASE,
        Europe: initTotals["Europe"] ?? 0,
        "Asia Pacific": initTotals["Asia Pacific"] ?? 0,
        "Latin America": initTotals["Latin America"] ?? 0,
      };
      cached = truth(s);
    }

    const queryEl = document.querySelector(".query");
    if (queryEl) {
      queryEl.innerHTML = `<b>Cached query</b>${run.sql}`;
    }

    const boundedStep = Math.max(
      0,
      Math.min(state.step, run.boundaries.length)
    );
    state.step = boundedStep;
    syncScreenUrlState(state);
    updateLabLinks();

    const boundary =
      boundedStep > 0 ? run.boundaries[boundedStep - 1] ?? null : null;
    const prevBoundary =
      boundedStep > 1 ? run.boundaries[boundedStep - 2] ?? null : null;

    const beforeTotals = prevBoundary
      ? prevBoundary.oracle_result_cents
      : run.initial_state.oracle_result_cents;
    const afterTotals = boundary
      ? boundary.oracle_result_cents
      : run.initial_state.oracle_result_cents;

    const groupNames = Object.keys(afterTotals);
    const rowsEl = document.getElementById("rows");
    if (rowsEl) {
      rowsEl.innerHTML = groupNames
        .map((r, idx) => {
          const bVal = beforeTotals[r] ?? 0;
          const aVal = afterTotals[r] ?? 0;
          const d = boundary ? aVal - bVal : 0;
          const chip = d
            ? `<span class="delta${d < 0 ? " neg" : ""}">${
                d > 0 ? "+" : "−"
              }${formatCentsUsd(Math.abs(d))}</span>`
            : "";
          return `<tr><td class="id">${
            idx + 1
          }</td><td>${r}</td><td class="num${
            d ? " hit" : ""
          }">${chip}${formatCentsUsd(aVal)}</td><td class="num">${formatCentsUsd(
            aVal
          )}</td><td class="chk">✓</td></tr>`;
        })
        .join("");
    }

    if (!boundary) {
      s.hasChange = false;
      renderQueryCells();
      setVerdict(
        "",
        "",
        `Loaded ${run.scenario_id} (seed ${run.seed}, ${run.config.dataset_size} rows, step 0).`
      );
      activeRecordInput = {
        scenario: state.scenario,
        seed: state.seed,
        size: state.size,
        step: 0,
        changeSql: "(no change yet)",
        move: "INITIAL",
        reasonCode: "initial_snapshot",
        beforeTotals,
        afterTotals,
        groupOrder: groupNames,
      };
      refreshRecordState();
      return;
    }

    s.hasChange = true;
    s.redis = boundary.cumulative_counters.baseline_source_queries;
    s.mv = boundary.cumulative_counters.baseline_recomputations;
    s.ours = boundary.cumulative_counters.smplcache_source_queries;
    renderQueryCells();

    const move: MoveCode = decisionKindToMove(boundary.decision_kind);
    const arith =
      move === "FIX IN PLACE"
        ? formatFixInPlaceArithmetic({
            beforeTotals,
            afterTotals,
            beforeImage: boundary.before_image,
            afterImage: boundary.after_image,
            groupOrder: groupNames,
          })
        : undefined;

    const wordClass =
      move === "KEEP" ? "kept" : move === "FIX IN PLACE" ? "fixed" : "rebuilt";
    const cleanReason = stripLeadingMovePrefix(boundary.plain_language_reason);
    const detail =
      move === "FIX IN PLACE" && arith
        ? `(${boundary.reason_code}) — ${arith}.`
        : `(${boundary.reason_code}) — ${cleanReason}`;
    setVerdict(wordClass, move, detail);
    others(
      boundary.boundary_counters.baseline_source_queries > 0
        ? "Drops the cached key on table write; the next read reruns the full SQL query."
        : "No committed write on a participating table.",
      boundary.boundary_counters.baseline_recomputations > 0
        ? "Serves old totals until REFRESH reruns the full SQL query."
        : "No committed write on a participating table."
    );

    const stepSql = buildBoundaryChangeSql(boundary);

    activeRecordInput = {
      scenario: state.scenario,
      seed: state.seed,
      size: state.size,
      step: boundedStep,
      changeSql: stepSql,
      move,
      reasonCode: boundary.reason_code,
      arithmeticSummary: arith,
      beforeTotals,
      afterTotals,
      groupOrder: groupNames,
    };
    refreshRecordState();
  } catch {
    // Keep pre-rendered default state on network error
  }
}

function copyRecord(): void {
  const text = formatPlainTextRecord(activeRecordInput);
  const btn = document.getElementById("copy-record");
  if (btn) {
    btn.setAttribute("data-record", text);
    btn.textContent = "Copied record";
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      btn.textContent = "Copy record";
    }, 2000);
  }
  navigator.clipboard?.writeText(text).catch(() => {});
}

document.querySelectorAll<HTMLButtonElement>("button.change").forEach((b) => {
  b.addEventListener("click", () => {
    const step = Number(b.dataset.step);
    if (Number.isInteger(step)) {
      apply(step);
    }
  });
});

document.getElementById("reset")?.addEventListener("click", () => {
  urlState.scenario = "customers_and_regions";
  urlState.seed = 42;
  urlState.size = 1000;
  baseTotals = { ...DEFAULT_BASE };
  reset(true);
});

document.getElementById("copy-record")?.addEventListener("click", () => {
  copyRecord();
});

// Initialize screen from URL parameters (?scenario=&seed=&size=&step=)
reset(false);
updateLabLinks();

const isFrontStepScenario =
  urlState.scenario === "customers_and_regions" &&
  urlState.cap === null &&
  urlState.step <= 4;

if (isFrontStepScenario && urlState.seed === 42 && urlState.size === 1000) {
  if (urlState.step >= 1 && urlState.step <= 4) {
    apply(urlState.step, { immediate: true, syncUrl: true });
  } else if (window.location.search) {
    syncScreenUrlState(urlState);
  }
} else if (isFrontStepScenario) {
  void loadSeededFrontStep(urlState);
} else {
  void applyRemoteScenarioStep(urlState);
}
