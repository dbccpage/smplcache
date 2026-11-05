import type { ScenarioId } from "./types";

export type MoveCode = "KEEP" | "FIX IN PLACE" | "REREAD";

export interface TableDependencyColumns {
  readCols: string[];
  joinKeys: string[];
  filterCols: string[];
  groupCols: string[];
  allCols: string[];
}

export interface QueryDependencySet {
  tables: string[];
  baseTable: string;
  aliasToTable: Record<string, string>;
  columnsByTable: Record<string, TableDependencyColumns>;
}

export interface ClassifyChangeInput {
  sql: string;
  table: string;
  operation: "INSERT" | "UPDATE" | "DELETE" | "ROLLBACK";
  beforeImage: Record<string, unknown> | null;
  afterImage: Record<string, unknown> | null;
  changedColumns?: string[];
  evictedKeys?: Array<string | number>;
  hasMatchedJoinRows?: boolean;
  primaryKeyCol?: string;
}

export interface ClassifiedChangeMove {
  move: MoveCode;
  decisionKind: "preserve" | "repair" | "invalidate";
  reasonCode: string;
}

export interface GroupArithmeticEntry {
  group: string;
  oldValueCents: number;
  newValueCents: number;
  diffCents: number;
  beforeAnswerCents: number;
  afterAnswerCents: number;
}

export interface ReplayRecordInput {
  scenario: ScenarioId;
  seed: number;
  size: number;
  step: number;
  changeSql: string;
  move: MoveCode | "INITIAL";
  reasonCode: string;
  arithmeticSummary?: string;
  beforeTotals: Record<string, number> | null;
  afterTotals: Record<string, number> | null;
  groupOrder?: string[];
}

export interface ScreenUrlState {
  scenario: ScenarioId;
  seed: number;
  size: number;
  step: number;
  cap: number | null;
}

const VALID_SCENARIOS: readonly ScenarioId[] = [
  "live_revenue",
  "customers_and_regions",
  "missing_evidence",
];

const VALID_SIZES = [100, 500, 1000, 2500];
const VALID_CAPS = [60, 120, 250, 500, 1500, 3000];

export function decisionKindToMove(
  kind: "preserve" | "repair" | "invalidate" | "unsupported"
): MoveCode {
  if (kind === "preserve") return "KEEP";
  if (kind === "repair") return "FIX IN PLACE";
  return "REREAD";
}

export function formatCentsUsd(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) {
    return "withheld";
  }
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(Math.trunc(cents));
  const dollars = Math.floor(abs / 100);
  const rem = abs % 100;
  return `${sign}$${dollars.toLocaleString("en-US")}.${rem
    .toString()
    .padStart(2, "0")}`;
}

export function formatSignedCentsUsd(cents: number): string {
  const sign = cents >= 0 ? "+" : "-";
  const abs = Math.abs(Math.trunc(cents));
  const dollars = Math.floor(abs / 100);
  const rem = abs % 100;
  return `${sign}$${dollars.toLocaleString("en-US")}.${rem
    .toString()
    .padStart(2, "0")}`;
}

function normalizeTableName(raw: string): string {
  const trimmed = raw.trim().toLowerCase();
  const parts = trimmed.split(".");
  return parts[parts.length - 1] || trimmed;
}

function pushUnique(arr: string[], item: string): void {
  const norm = item.trim().toLowerCase();
  if (norm && !arr.includes(norm)) {
    arr.push(norm);
  }
}

/**
 * Derive each participating table's dependency set (read columns, join keys,
 * filter columns, grouping columns) directly from the SQL query text.
 */
export function extractQueryDependencies(sql: string): QueryDependencySet {
  const clean = sql.replace(/\s+/g, " ").trim();
  const aliasToTable: Record<string, string> = {};
  const tables: string[] = [];
  const columnsByTable: Record<string, TableDependencyColumns> = {};

  function ensureTable(t: string): TableDependencyColumns {
    const norm = normalizeTableName(t);
    if (!tables.includes(norm)) {
      tables.push(norm);
    }
    if (!columnsByTable[norm]) {
      columnsByTable[norm] = {
        readCols: [],
        joinKeys: [],
        filterCols: [],
        groupCols: [],
        allCols: [],
      };
    }
    return columnsByTable[norm];
  }

  const fromMatch = clean.match(
    /\bFROM\s+([a-zA-Z0-9_.]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_]+))?/i
  );
  let baseTable = "orders";
  if (fromMatch) {
    baseTable = normalizeTableName(fromMatch[1]);
    ensureTable(baseTable);
    aliasToTable[baseTable] = baseTable;
    const maybeAlias = fromMatch[2]?.toLowerCase();
    if (
      maybeAlias &&
      !["join", "inner", "left", "where", "group", "order", "limit"].includes(
        maybeAlias
      )
    ) {
      aliasToTable[maybeAlias] = baseTable;
    }
  }

  const joinRegex =
    /\bJOIN\s+([a-zA-Z0-9_.]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_]+))?\s+ON\s+([a-zA-Z0-9_.]+)\s*=\s*([a-zA-Z0-9_.]+)/gi;
  let jm: RegExpExecArray | null;
  while ((jm = joinRegex.exec(clean)) !== null) {
    const joinTable = normalizeTableName(jm[1]);
    ensureTable(joinTable);
    aliasToTable[joinTable] = joinTable;
    if (jm[2] && jm[2].toLowerCase() !== "on") {
      aliasToTable[jm[2].toLowerCase()] = joinTable;
    }
    for (const ref of [jm[3], jm[4]]) {
      const resolved = resolveColumnRef(ref, aliasToTable, baseTable);
      if (resolved) {
        const entry = ensureTable(resolved.table);
        pushUnique(entry.joinKeys, resolved.column);
      }
    }
  }

  const selectMatch = clean.match(/^SELECT\s+(.+?)\s+FROM\b/i);
  if (selectMatch) {
    const selectClause = selectMatch[1];
    const aggRegex = /\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(\s*([a-zA-Z0-9_.*]+)\s*\)/gi;
    let am: RegExpExecArray | null;
    while ((am = aggRegex.exec(selectClause)) !== null) {
      const inner = am[1].trim();
      if (inner !== "*") {
        const resolved = resolveColumnRef(inner, aliasToTable, baseTable);
        if (resolved) {
          pushUnique(ensureTable(resolved.table).readCols, resolved.column);
        }
      }
    }
  }

  const whereMatch = clean.match(/\bWHERE\s+(.+?)(?:\bGROUP\s+BY\b|$)/i);
  if (whereMatch) {
    const whereClause = whereMatch[1].replace(/'[^']*'/g, "''");
    const predRegex =
      /([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:=|!=|<>|<=|>=|<|>|\bIN\b|\bIS\b)/gi;
    let pm: RegExpExecArray | null;
    while ((pm = predRegex.exec(whereClause)) !== null) {
      const token = pm[1];
      if (!["and", "or", "not"].includes(token.toLowerCase())) {
        const resolved = resolveColumnRef(token, aliasToTable, baseTable);
        if (resolved) {
          pushUnique(ensureTable(resolved.table).filterCols, resolved.column);
        }
      }
    }
  }

  const groupMatch = clean.match(/\bGROUP\s+BY\s+(.+)$/i);
  if (groupMatch) {
    const groupParts = groupMatch[1].split(",");
    for (const part of groupParts) {
      const resolved = resolveColumnRef(part.trim(), aliasToTable, baseTable);
      if (resolved) {
        pushUnique(ensureTable(resolved.table).groupCols, resolved.column);
      }
    }
  }

  for (const tbl of tables) {
    const c = columnsByTable[tbl];
    for (const col of [
      ...c.readCols,
      ...c.joinKeys,
      ...c.filterCols,
      ...c.groupCols,
    ]) {
      pushUnique(c.allCols, col);
    }
  }

  return { tables, baseTable, aliasToTable, columnsByTable };
}

function resolveColumnRef(
  ref: string,
  aliasToTable: Record<string, string>,
  defaultTable: string
): { table: string; column: string } | null {
  const trimmed = ref.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(".");
  if (parts.length >= 2) {
    const alias = parts[parts.length - 2].toLowerCase();
    const col = parts[parts.length - 1].toLowerCase();
    const table = aliasToTable[alias] ?? normalizeTableName(alias);
    return { table, column: col };
  }
  return { table: defaultTable, column: parts[0].toLowerCase() };
}

/**
 * Classify a table mutation into one of the three moves (KEEP, FIX IN PLACE, REREAD)
 * from the query's extracted dependency set and the event's before/after row evidence.
 */
export function classifyChangeMove(
  input: ClassifyChangeInput
): ClassifiedChangeMove {
  if (input.operation === "ROLLBACK") {
    return {
      move: "KEEP",
      decisionKind: "preserve",
      reasonCode: "transaction_rollback",
    };
  }

  const deps = extractQueryDependencies(input.sql);
  const table = normalizeTableName(input.table);
  const tableDeps = deps.columnsByTable[table];
  if (!tableDeps) {
    return {
      move: "KEEP",
      decisionKind: "preserve",
      reasonCode: "unrelated_relation",
    };
  }

  const before = input.beforeImage ?? {};
  const after = input.afterImage ?? {};
  const pkCol = (input.primaryKeyCol ?? "id").toLowerCase();

  const changed: string[] = [];
  if (input.changedColumns && input.changedColumns.length > 0) {
    for (const c of input.changedColumns) {
      pushUnique(changed, c);
    }
  } else if (input.operation === "UPDATE") {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const k of keys) {
      if (before[k] !== after[k]) {
        pushUnique(changed, k);
      }
    }
  } else {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const k of keys) {
      pushUnique(changed, k);
    }
  }

  if (input.operation === "UPDATE") {
    const intersects = changed.some((c) => tableDeps.allCols.includes(c));
    if (!intersects) {
      return {
        move: "KEEP",
        decisionKind: "preserve",
        reasonCode: "disjoint_columns",
      };
    }
  }

  const requiredCols = [...tableDeps.allCols];
  if (deps.tables.length > 1) {
    pushUnique(requiredCols, pkCol);
  }

  const hasAllRequired = (img: Record<string, unknown>): boolean =>
    requiredCols.every(
      (col) =>
        Object.prototype.hasOwnProperty.call(img, col) &&
        img[col] !== undefined &&
        img[col] !== "__debezium_unavailable_value"
    );

  if (input.operation === "UPDATE") {
    if (!hasAllRequired(before) || !hasAllRequired(after)) {
      return {
        move: "REREAD",
        decisionKind: "invalidate",
        reasonCode: "missing_evidence_for_repair",
      };
    }
  } else if (input.operation === "INSERT") {
    if (!hasAllRequired(after)) {
      return {
        move: "REREAD",
        decisionKind: "invalidate",
        reasonCode: "missing_evidence_for_repair",
      };
    }
  } else if (input.operation === "DELETE") {
    if (!hasAllRequired(before)) {
      return {
        move: "REREAD",
        decisionKind: "invalidate",
        reasonCode: "missing_evidence_for_repair",
      };
    }
  }

  if (input.evictedKeys && input.evictedKeys.length > 0) {
    const targetPk = (before[pkCol] ?? after[pkCol]) as
      | string
      | number
      | undefined;
    if (targetPk !== undefined && input.evictedKeys.includes(targetPk)) {
      return {
        move: "REREAD",
        decisionKind: "invalidate",
        reasonCode: "evicted_join_auxiliary_key",
      };
    }
  }

  const joinKeyChanged =
    input.operation === "UPDATE" &&
    changed.some((c) => tableDeps.joinKeys.includes(c));

  if (joinKeyChanged) {
    if (table === deps.baseTable) {
      return {
        move: "FIX IN PLACE",
        decisionKind: "repair",
        reasonCode: "foreign_key_reparent",
      };
    }
    if (input.hasMatchedJoinRows !== false) {
      return {
        move: "FIX IN PLACE",
        decisionKind: "repair",
        reasonCode: "dimension_update",
      };
    }
    return {
      move: "KEEP",
      decisionKind: "preserve",
      reasonCode: "zero_join_multiplicity",
    };
  }

  if (table !== deps.baseTable) {
    if (input.hasMatchedJoinRows === false) {
      return {
        move: "KEEP",
        decisionKind: "preserve",
        reasonCode: "zero_join_multiplicity",
      };
    }
    return {
      move: "FIX IN PLACE",
      decisionKind: "repair",
      reasonCode: "dimension_update",
    };
  }

  if (input.operation === "INSERT") {
    return {
      move: "FIX IN PLACE",
      decisionKind: "repair",
      reasonCode: "insert",
    };
  }
  if (input.operation === "DELETE") {
    return {
      move: "FIX IN PLACE",
      decisionKind: "repair",
      reasonCode: "delete",
    };
  }
  return {
    move: "FIX IN PLACE",
    decisionKind: "repair",
    reasonCode: "value_change",
  };
}

/**
 * Compute per-group arithmetic for a FIX IN PLACE move:
 * group, old value, new value, difference, before answer, and after answer.
 */
export function computeFixInPlaceArithmetic(params: {
  beforeTotals: Record<string, number>;
  afterTotals: Record<string, number>;
  beforeImage?: Record<string, unknown> | null;
  afterImage?: Record<string, unknown> | null;
  groupOrder?: string[];
}): GroupArithmeticEntry[] {
  const { beforeTotals, afterTotals, beforeImage, afterImage, groupOrder } =
    params;
  const allGroups = groupOrder
    ? [
        ...groupOrder,
        ...Object.keys({ ...beforeTotals, ...afterTotals }).filter(
          (g) => !groupOrder.includes(g)
        ),
      ]
    : Object.keys({ ...beforeTotals, ...afterTotals });

  const changedGroups = allGroups.filter(
    (g) => (beforeTotals[g] ?? 0) !== (afterTotals[g] ?? 0)
  );

  const oldAmt =
    beforeImage && typeof beforeImage.amount_cents === "number"
      ? beforeImage.amount_cents
      : null;
  const newAmt =
    afterImage && typeof afterImage.amount_cents === "number"
      ? afterImage.amount_cents
      : null;
  const oldPaid =
    beforeImage && beforeImage.status !== undefined
      ? beforeImage.status === "paid"
      : true;
  const newPaid =
    afterImage && afterImage.status !== undefined
      ? afterImage.status === "paid"
      : true;

  return changedGroups.map((group) => {
    const beforeAns = beforeTotals[group] ?? 0;
    const afterAns = afterTotals[group] ?? 0;
    const diff = afterAns - beforeAns;

    let oldVal: number;
    let newVal: number;

    if (
      changedGroups.length === 1 &&
      oldAmt !== null &&
      newAmt !== null &&
      oldPaid &&
      newPaid &&
      newAmt - oldAmt === diff
    ) {
      oldVal = oldAmt;
      newVal = newAmt;
    } else if (diff >= 0) {
      oldVal = 0;
      newVal = diff;
    } else {
      oldVal = Math.abs(diff);
      newVal = 0;
    }

    return {
      group,
      oldValueCents: oldVal,
      newValueCents: newVal,
      diffCents: diff,
      beforeAnswerCents: beforeAns,
      afterAnswerCents: afterAns,
    };
  });
}

/**
 * Format the arithmetic string shown on every FIX IN PLACE move:
 * "<group>: old $<old> -> new $<new> (diff ±$<diff>), answer $<before> -> $<after>"
 */
export function formatFixInPlaceArithmetic(params: {
  beforeTotals: Record<string, number>;
  afterTotals: Record<string, number>;
  beforeImage?: Record<string, unknown> | null;
  afterImage?: Record<string, unknown> | null;
  groupOrder?: string[];
}): string {
  const entries = computeFixInPlaceArithmetic(params);
  if (entries.length === 0) {
    return "No group total changed (diff $0.00).";
  }
  return entries
    .map(
      (e) =>
        `${e.group}: old ${formatCentsUsd(e.oldValueCents)} → new ${formatCentsUsd(
          e.newValueCents
        )} (diff ${formatSignedCentsUsd(e.diffCents)}), answer ${formatCentsUsd(
          e.beforeAnswerCents
        )} → ${formatCentsUsd(e.afterAnswerCents)}`
    )
    .join("; ");
}

export function formatAnswersSummary(
  totals: Record<string, number> | null,
  groupOrder?: string[]
): string {
  if (!totals) {
    return "withheld (stale answer withheld until reread completes)";
  }
  const keys = groupOrder
    ? [
        ...groupOrder.filter((k) => k in totals),
        ...Object.keys(totals).filter((k) => !groupOrder.includes(k)),
      ]
    : Object.keys(totals);
  if (keys.length === 0) {
    return "(none)";
  }
  return keys.map((k) => `${k}=${formatCentsUsd(totals[k])}`).join(", ");
}

/**
 * Format the plain-text replayable record copied by "Copy record" on both `/` and `/lab/`.
 * Includes: change SQL, move, reason, before and after answers, scenario, seed, size, and step.
 */
export function formatPlainTextRecord(input: ReplayRecordInput): string {
  const reasonLine =
    input.move === "FIX IN PLACE" && input.arithmeticSummary
      ? `${input.reasonCode} (${input.arithmeticSummary})`
      : input.reasonCode;
  return [
    `scenario: ${input.scenario}`,
    `seed: ${input.seed}`,
    `size: ${input.size}`,
    `step: ${input.step}`,
    `change_sql: ${input.changeSql}`,
    `move: ${input.move}`,
    `reason: ${reasonLine}`,
    `before_answer: ${formatAnswersSummary(input.beforeTotals, input.groupOrder)}`,
    `after_answer: ${formatAnswersSummary(input.afterTotals, input.groupOrder)}`,
  ].join("\n");
}

export function parseScreenUrlState(
  search: string,
  defaults: {
    scenario: ScenarioId;
    seed: number;
    size: number;
    step: number;
    maxStep?: number;
  }
): ScreenUrlState {
  const params = new URLSearchParams(search);
  let scenario = defaults.scenario;
  const rawScen = params.get("scenario");
  if (rawScen && (VALID_SCENARIOS as readonly string[]).includes(rawScen)) {
    scenario = rawScen as ScenarioId;
  }

  let seed = defaults.seed;
  const rawSeed = Number(params.get("seed"));
  if (Number.isInteger(rawSeed) && rawSeed >= 1 && rawSeed <= 999999) {
    seed = rawSeed;
  }

  let size = defaults.size;
  const rawSize = Number(params.get("size"));
  if (VALID_SIZES.includes(rawSize)) {
    size = rawSize;
  }

  let step = defaults.step;
  const rawStep = params.get("step");
  if (rawStep !== null && rawStep !== "") {
    const stepNum = Number(rawStep);
    const maxStep = defaults.maxStep ?? 10;
    if (Number.isInteger(stepNum) && stepNum >= 0 && stepNum <= maxStep) {
      step = stepNum;
    }
  }

  let cap: number | null = null;
  const rawCap = params.get("cap");
  if (rawCap !== null && rawCap !== "" && rawCap !== "none") {
    const capNum = Number(rawCap);
    if (VALID_CAPS.includes(capNum)) {
      cap = capNum;
    }
  }

  return { scenario, seed, size, step, cap };
}

export function buildScreenUrlSearch(state: ScreenUrlState): string {
  const params = new URLSearchParams();
  params.set("scenario", state.scenario);
  params.set("seed", String(state.seed));
  params.set("size", String(state.size));
  if (state.scenario === "customers_and_regions" && state.cap !== null) {
    params.set("cap", String(state.cap));
  }
  params.set("step", String(state.step));
  return `?${params.toString()}`;
}

export function syncScreenUrlState(state: ScreenUrlState): void {
  const search = buildScreenUrlSearch(state);
  const newUrl = `${window.location.pathname}${search}`;
  window.history.replaceState({}, "", newUrl);
}

function sqlLiteral(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return `'${String(v).replace(/'/g, "''")}'`;
}

export function buildBoundaryChangeSql(boundary: {
  operation: string;
  table_written: string;
  before_image: Record<string, unknown> | null;
  after_image: Record<string, unknown> | null;
} | null): string {
  if (!boundary) return "(no change yet)";
  const op = boundary.operation.toUpperCase();
  const tbl = boundary.table_written;
  const before = boundary.before_image;
  const after = boundary.after_image;

  if (op.includes("ROLLBACK")) {
    return "BEGIN; DELETE FROM orders WHERE id = 101; ROLLBACK";
  }
  if (op.includes("BEGIN") && after) {
    const id = after.id ?? 9001;
    const amt = after.amount_cents ?? 9500;
    return `BEGIN; INSERT INTO customers (id, name, region_id, tier) VALUES (${sqlLiteral(
      id
    )}, 'Sao Paulo Solar', 4, 'enterprise'); INSERT INTO orders (id, customer_id, status, amount_cents) VALUES (${sqlLiteral(
      id
    )}, ${sqlLiteral(id)}, 'paid', ${sqlLiteral(amt)}); COMMIT`;
  }
  if (op.startsWith("INSERT") && after) {
    const cols = Object.keys(after);
    const vals = cols.map((c) => sqlLiteral(after[c]));
    return `INSERT INTO ${tbl} (${cols.join(", ")}) VALUES (${vals.join(", ")})`;
  }
  if (op.startsWith("DELETE") && before) {
    const id = before.id ?? "?";
    return `DELETE FROM ${tbl} WHERE id = ${sqlLiteral(id)}`;
  }
  if (after) {
    const id = after.id ?? before?.id ?? "?";
    const changedCols = Object.keys(after).filter((k) => {
      if (k === "id") return false;
      if (!before) return true;
      return before[k] !== after[k];
    });
    const setCols = changedCols.length > 0 ? changedCols : ["amount_cents"];
    const assignments = setCols
      .filter((k) => k in after)
      .map((k) => `${k} = ${sqlLiteral(after[k])}`);
    if (assignments.length > 0) {
      return `UPDATE ${tbl} SET ${assignments.join(", ")} WHERE id = ${sqlLiteral(
        id
      )}`;
    }
  }
  return `${boundary.operation} ${boundary.table_written}`;
}

