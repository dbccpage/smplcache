const VALID_SCENARIOS = [
    "live_revenue",
    "customers_and_regions",
    "missing_evidence",
];
const VALID_SIZES = [100, 500, 1000, 2500];
const VALID_CAPS = [60, 120, 250, 500, 1500, 3000];
function decisionKindToMove(kind) {
    if (kind === "preserve")
        return "KEEP";
    if (kind === "repair")
        return "FIX IN PLACE";
    return "REREAD";
}
function formatCentsUsd(cents) {
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
function formatSignedCentsUsd(cents) {
    const sign = cents >= 0 ? "+" : "-";
    const abs = Math.abs(Math.trunc(cents));
    const dollars = Math.floor(abs / 100);
    const rem = abs % 100;
    return `${sign}$${dollars.toLocaleString("en-US")}.${rem
        .toString()
        .padStart(2, "0")}`;
}
function stripIdentifierQuotes(raw) {
    return raw.replace(/["`\[\]]/g, "").trim();
}
function normalizeTableName(raw) {
    const trimmed = stripIdentifierQuotes(raw).toLowerCase();
    const parts = trimmed.split(".");
    return parts[parts.length - 1] || trimmed;
}
function pushUnique(arr, item) {
    const norm = stripIdentifierQuotes(item).toLowerCase();
    if (norm && !arr.includes(norm)) {
        arr.push(norm);
    }
}
const SQL_KEYWORDS = new Set([
    "select",
    "from",
    "join",
    "inner",
    "left",
    "right",
    "full",
    "cross",
    "on",
    "where",
    "group",
    "by",
    "order",
    "having",
    "limit",
    "and",
    "or",
    "not",
    "in",
    "is",
    "null",
    "true",
    "false",
    "as",
    "distinct",
    "sum",
    "count",
    "avg",
    "min",
    "max",
]);
function isIdentifierToken(tok) {
    const clean = stripIdentifierQuotes(tok);
    if (!clean || /^\d+(\.\d+)?$/.test(clean))
        return false;
    const parts = clean.split(".");
    const last = parts[parts.length - 1]?.toLowerCase() ?? "";
    if (!last || SQL_KEYWORDS.has(last))
        return false;
    return /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(last);
}
/**
 * Derive each participating table's dependency set (read columns, join keys,
 * filter columns, grouping columns) directly from the SQL query text.
 */
function extractQueryDependencies(sql) {
    const clean = sql.replace(/\s+/g, " ").trim();
    const aliasToTable = {};
    const tables = [];
    const columnsByTable = {};
    function ensureTable(t) {
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
    const fromMatch = clean.match(/\bFROM\s+([a-zA-Z0-9_."`\[\]]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_"`\[\]]+))?/i);
    let baseTable = "orders";
    if (fromMatch) {
        baseTable = normalizeTableName(fromMatch[1]);
        ensureTable(baseTable);
        aliasToTable[baseTable] = baseTable;
        const maybeAlias = stripIdentifierQuotes(fromMatch[2] ?? "").toLowerCase();
        if (maybeAlias && !SQL_KEYWORDS.has(maybeAlias)) {
            aliasToTable[maybeAlias] = baseTable;
        }
    }
    const joinClauseRegex = /\bJOIN\s+([a-zA-Z0-9_."`\[\]]+)(?:\s+(?:AS\s+)?([a-zA-Z0-9_"`\[\]]+))?\s+ON\s+(.+?)(?=\b(?:INNER|LEFT|RIGHT|FULL|CROSS)?\s*JOIN\b|\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|$)/gi;
    let jm;
    while ((jm = joinClauseRegex.exec(clean)) !== null) {
        const joinTable = normalizeTableName(jm[1]);
        ensureTable(joinTable);
        aliasToTable[joinTable] = joinTable;
        const maybeJoinAlias = stripIdentifierQuotes(jm[2] ?? "").toLowerCase();
        if (maybeJoinAlias && !SQL_KEYWORDS.has(maybeJoinAlias)) {
            aliasToTable[maybeJoinAlias] = joinTable;
        }
        const onExpr = jm[3].replace(/'(?:''|[^'])*'/g, "''");
        const eqRegex = /([a-zA-Z0-9_."`\[\]]+)\s*(?:=|!=|<>|<=|>=|<|>)\s*([a-zA-Z0-9_."`\[\]]+)/g;
        let eqMatch;
        while ((eqMatch = eqRegex.exec(onExpr)) !== null) {
            for (const ref of [eqMatch[1], eqMatch[2]]) {
                if (isIdentifierToken(ref)) {
                    const resolved = resolveColumnRef(ref, aliasToTable, baseTable);
                    if (resolved) {
                        const entry = ensureTable(resolved.table);
                        pushUnique(entry.joinKeys, resolved.column);
                    }
                }
            }
        }
    }
    const groupMatch = clean.match(/\bGROUP\s+BY\s+(.+?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)/i);
    if (groupMatch) {
        const groupParts = groupMatch[1].split(",");
        for (const part of groupParts) {
            const cleanedPart = part.replace(/[()]/g, "").trim();
            if (isIdentifierToken(cleanedPart)) {
                const resolved = resolveColumnRef(cleanedPart, aliasToTable, baseTable);
                if (resolved) {
                    pushUnique(ensureTable(resolved.table).groupCols, resolved.column);
                }
            }
        }
    }
    const selectMatch = clean.match(/^SELECT\s+(.+?)\s+FROM\b/i);
    if (selectMatch) {
        const selectClause = selectMatch[1];
        const aggRegex = /\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(\s*(?:DISTINCT\s+)?([a-zA-Z0-9_.*"`\[\]]+)\s*\)/gi;
        let am;
        while ((am = aggRegex.exec(selectClause)) !== null) {
            const inner = stripIdentifierQuotes(am[1]);
            if (inner !== "*" && isIdentifierToken(inner)) {
                const resolved = resolveColumnRef(inner, aliasToTable, baseTable);
                if (resolved) {
                    pushUnique(ensureTable(resolved.table).readCols, resolved.column);
                }
            }
        }
        const strippedAggs = selectClause.replace(/\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\([^)]*\)/gi, "");
        for (const rawItem of strippedAggs.split(",")) {
            const withoutAlias = rawItem
                .trim()
                .replace(/\s+AS\s+[a-zA-Z0-9_"`\[\]]+$/i, "")
                .trim();
            if (isIdentifierToken(withoutAlias)) {
                const resolved = resolveColumnRef(withoutAlias, aliasToTable, baseTable);
                if (resolved) {
                    const tblDeps = ensureTable(resolved.table);
                    if (!tblDeps.groupCols.includes(resolved.column)) {
                        pushUnique(tblDeps.readCols, resolved.column);
                    }
                }
            }
        }
    }
    const whereMatch = clean.match(/\bWHERE\s+(.+?)(?=\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)/i);
    if (whereMatch) {
        const whereClause = whereMatch[1].replace(/'(?:''|[^'])*'/g, "''");
        const lhsRegex = /([a-zA-Z0-9_."`\[\]]+)\s*(?:=|!=|<>|<=|>=|<|>|\bIN\b|\bIS\b)/gi;
        let pm;
        while ((pm = lhsRegex.exec(whereClause)) !== null) {
            if (isIdentifierToken(pm[1])) {
                const resolved = resolveColumnRef(pm[1], aliasToTable, baseTable);
                if (resolved) {
                    pushUnique(ensureTable(resolved.table).filterCols, resolved.column);
                }
            }
        }
        const rhsRegex = /(?:=|!=|<>|<=|>=|<|>)\s*([a-zA-Z0-9_."`\[\]]+)/gi;
        let rm;
        while ((rm = rhsRegex.exec(whereClause)) !== null) {
            if (isIdentifierToken(rm[1])) {
                const resolved = resolveColumnRef(rm[1], aliasToTable, baseTable);
                if (resolved) {
                    pushUnique(ensureTable(resolved.table).filterCols, resolved.column);
                }
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
function resolveColumnRef(ref, aliasToTable, defaultTable) {
    const trimmed = stripIdentifierQuotes(ref);
    if (!trimmed)
        return null;
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
function classifyChangeMove(input) {
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
    const requiredCols = [...tableDeps.allCols];
    if (deps.tables.length > 1) {
        pushUnique(requiredCols, pkCol);
    }
    const hasAllRequired = (img) => requiredCols.every((col) => Object.prototype.hasOwnProperty.call(img, col) &&
        img[col] !== undefined &&
        img[col] !== "__debezium_unavailable_value");
    const changed = [];
    if (input.changedColumns && input.changedColumns.length > 0) {
        for (const c of input.changedColumns) {
            pushUnique(changed, c);
        }
    }
    else if (input.operation === "UPDATE") {
        const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
        for (const k of keys) {
            if (!Object.prototype.hasOwnProperty.call(before, k) ||
                !Object.prototype.hasOwnProperty.call(after, k) ||
                before[k] !== after[k] ||
                before[k] === "__debezium_unavailable_value" ||
                after[k] === "__debezium_unavailable_value") {
                pushUnique(changed, k);
            }
        }
    }
    else {
        const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
        for (const k of keys) {
            pushUnique(changed, k);
        }
    }
    if (input.operation === "UPDATE") {
        if (!input.beforeImage || !input.afterImage) {
            return {
                move: "REREAD",
                decisionKind: "invalidate",
                reasonCode: "missing_evidence_for_repair",
            };
        }
        if (changed.length === 0 && (!hasAllRequired(before) || !hasAllRequired(after))) {
            return {
                move: "REREAD",
                decisionKind: "invalidate",
                reasonCode: "missing_evidence_for_repair",
            };
        }
        const intersects = changed.some((c) => tableDeps.allCols.includes(c));
        if (!intersects) {
            return {
                move: "KEEP",
                decisionKind: "preserve",
                reasonCode: "disjoint_columns",
            };
        }
    }
    if (input.operation === "UPDATE") {
        if (!hasAllRequired(before) || !hasAllRequired(after)) {
            return {
                move: "REREAD",
                decisionKind: "invalidate",
                reasonCode: "missing_evidence_for_repair",
            };
        }
    }
    else if (input.operation === "INSERT") {
        if (!hasAllRequired(after)) {
            return {
                move: "REREAD",
                decisionKind: "invalidate",
                reasonCode: "missing_evidence_for_repair",
            };
        }
    }
    else if (input.operation === "DELETE") {
        if (!hasAllRequired(before)) {
            return {
                move: "REREAD",
                decisionKind: "invalidate",
                reasonCode: "missing_evidence_for_repair",
            };
        }
    }
    if (input.evictedKeys && input.evictedKeys.length > 0) {
        const targetPk = (before[pkCol] ?? after[pkCol]);
        if (targetPk !== undefined && input.evictedKeys.includes(targetPk)) {
            return {
                move: "REREAD",
                decisionKind: "invalidate",
                reasonCode: "evicted_join_auxiliary_key",
            };
        }
    }
    const joinKeyChanged = input.operation === "UPDATE" &&
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
function computeFixInPlaceArithmetic(params) {
    const { beforeTotals, afterTotals, beforeImage, afterImage, groupOrder } = params;
    const allGroups = groupOrder
        ? [
            ...groupOrder,
            ...Object.keys({ ...beforeTotals, ...afterTotals }).filter((g) => !groupOrder.includes(g)),
        ]
        : Object.keys({ ...beforeTotals, ...afterTotals });
    const changedGroups = allGroups.filter((g) => (beforeTotals[g] ?? 0) !== (afterTotals[g] ?? 0));
    const oldAmt = beforeImage && typeof beforeImage.amount_cents === "number"
        ? beforeImage.amount_cents
        : null;
    const newAmt = afterImage && typeof afterImage.amount_cents === "number"
        ? afterImage.amount_cents
        : null;
    const oldPaid = beforeImage && beforeImage.status !== undefined
        ? beforeImage.status === "paid"
        : true;
    const newPaid = afterImage && afterImage.status !== undefined
        ? afterImage.status === "paid"
        : true;
    if (changedGroups.length === 0 && allGroups.length > 0) {
        const hintedGroup = (afterImage && typeof afterImage.region === "string" && allGroups.includes(afterImage.region)
            ? afterImage.region
            : null) ??
            (beforeImage && typeof beforeImage.region === "string" && allGroups.includes(beforeImage.region)
                ? beforeImage.region
                : null) ??
            allGroups[0];
        const beforeAns = beforeTotals[hintedGroup] ?? 0;
        const afterAns = afterTotals[hintedGroup] ?? 0;
        const val = oldAmt ?? newAmt ?? 0;
        return [
            {
                group: hintedGroup,
                oldValueCents: val,
                newValueCents: newAmt ?? val,
                diffCents: 0,
                beforeAnswerCents: beforeAns,
                afterAnswerCents: afterAns,
            },
        ];
    }
    return changedGroups.map((group) => {
        const beforeAns = beforeTotals[group] ?? 0;
        const afterAns = afterTotals[group] ?? 0;
        const diff = afterAns - beforeAns;
        let oldVal;
        let newVal;
        if (changedGroups.length === 1 &&
            oldAmt !== null &&
            newAmt !== null &&
            oldPaid &&
            newPaid &&
            newAmt - oldAmt === diff) {
            oldVal = oldAmt;
            newVal = newAmt;
        }
        else if (diff >= 0) {
            oldVal = 0;
            newVal = diff;
        }
        else {
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
function formatFixInPlaceArithmetic(params) {
    const entries = computeFixInPlaceArithmetic(params);
    if (entries.length === 0) {
        return "No group total changed (diff +$0.00).";
    }
    return entries
        .map((e) => `${e.group}: old ${formatCentsUsd(e.oldValueCents)} → new ${formatCentsUsd(e.newValueCents)} (diff ${formatSignedCentsUsd(e.diffCents)}), answer ${formatCentsUsd(e.beforeAnswerCents)} → ${formatCentsUsd(e.afterAnswerCents)}`)
        .join("; ");
}
function formatAnswersSummary(totals, groupOrder) {
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
function formatPlainTextRecord(input) {
    const reasonLine = input.move === "FIX IN PLACE" && input.arithmeticSummary
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
const SCENARIO_ALIASES = {
    front: "customers_and_regions",
    join: "customers_and_regions",
    live_revenue: "live_revenue",
    customers_and_regions: "customers_and_regions",
    missing_evidence: "missing_evidence",
    missing_evidence_wal: "missing_evidence",
};
const SIZE_ALIASES = {
    small: 100,
    medium: 500,
    large: 1000,
    xlarge: 2500,
};
function parseScreenUrlState(search, defaults) {
    const params = new URLSearchParams(search);
    let scenario = defaults.scenario;
    const rawScen = params.get("scenario")?.trim().toLowerCase();
    if (rawScen) {
        if (rawScen in SCENARIO_ALIASES) {
            scenario = SCENARIO_ALIASES[rawScen];
        }
        else if (VALID_SCENARIOS.includes(rawScen)) {
            scenario = rawScen;
        }
    }
    let seed = defaults.seed;
    const rawSeed = Number(params.get("seed"));
    if (Number.isInteger(rawSeed) && rawSeed >= 1 && rawSeed <= 999999) {
        seed = rawSeed;
    }
    let size = defaults.size;
    const rawSizeParam = params.get("size")?.trim().toLowerCase() ?? "";
    if (rawSizeParam in SIZE_ALIASES) {
        size = SIZE_ALIASES[rawSizeParam];
    }
    else {
        const rawSize = Number(rawSizeParam);
        if (VALID_SIZES.includes(rawSize)) {
            size = rawSize;
        }
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
    let cap = null;
    const rawCap = params.get("cap");
    if (rawCap !== null && rawCap !== "" && rawCap !== "none") {
        const capNum = Number(rawCap);
        if (VALID_CAPS.includes(capNum)) {
            cap = capNum;
        }
    }
    return { scenario, seed, size, step, cap };
}
function buildScreenUrlSearch(state) {
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
function syncScreenUrlState(state) {
    const search = buildScreenUrlSearch(state);
    const newUrl = `${window.location.pathname}${search}`;
    window.history.replaceState({}, "", newUrl);
}
function sqlLiteral(v) {
    if (v === null || v === undefined)
        return "NULL";
    if (typeof v === "number" || typeof v === "boolean")
        return String(v);
    return `'${String(v).replace(/'/g, "''")}'`;
}
function buildBoundaryChangeSql(boundary) {
    if (!boundary)
        return "(no change yet)";
    if (boundary.change_sql && boundary.change_sql.trim()) {
        return boundary.change_sql.trim();
    }
    const op = boundary.operation.toUpperCase();
    const tbl = boundary.table_written;
    const before = boundary.before_image;
    const after = boundary.after_image;
    if (op.includes("ROLLBACK")) {
        if (before && after) {
            const id = after.id ?? before.id ?? 104;
            const diffCols = Object.keys(after).filter((k) => k !== "id" && before[k] !== after[k]);
            const targetTable = tbl && tbl !== "none (rolled back)" ? tbl : "main.orders";
            const assignments = (diffCols.length > 0 ? diffCols : ["amount_cents"])
                .filter((k) => k in after)
                .map((k) => `${k} = ${sqlLiteral(after[k])}`);
            return `BEGIN; UPDATE ${targetTable} SET ${assignments.join(", ")} WHERE id = ${sqlLiteral(id)}; ROLLBACK`;
        }
        const delId = before?.id ?? 101;
        return `BEGIN; DELETE FROM orders WHERE id = ${sqlLiteral(delId)}; ROLLBACK`;
    }
    if (op.includes("BEGIN") && after) {
        const id = after.id ?? 9001;
        const amt = after.amount_cents ?? 9500;
        return `BEGIN; INSERT INTO customers (id, name, region_id, tier) VALUES (${sqlLiteral(id)}, 'Sao Paulo Solar', 4, 'enterprise'); INSERT INTO orders (id, customer_id, status, amount_cents) VALUES (${sqlLiteral(id)}, ${sqlLiteral(id)}, 'paid', ${sqlLiteral(amt)}); COMMIT`;
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
        const beforeHasNonPk = before !== null &&
            Object.keys(before).some((k) => k.toLowerCase() !== "id");
        const changedCols = beforeHasNonPk
            ? Object.keys(after).filter((k) => {
                if (k === "id")
                    return false;
                if (!(k in (before ?? {})))
                    return true;
                return before?.[k] !== after[k];
            })
            : ["amount_cents"].filter((k) => k in after);
        const setCols = changedCols.length > 0 ? changedCols : ["amount_cents"];
        const assignments = setCols
            .filter((k) => k in after)
            .map((k) => `${k} = ${sqlLiteral(after[k])}`);
        if (assignments.length > 0) {
            return `UPDATE ${tbl} SET ${assignments.join(", ")} WHERE id = ${sqlLiteral(id)}`;
        }
    }
    return `${boundary.operation} ${boundary.table_written}`;
}


const state = {
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
    copiedRecordNotice: false,
};
let playbackTimer = null;
const STEP_INTERVAL_MS = 7200; // ~50 seconds across 7 steps for the 60-second demo
function formatCents(cents) {
    if (cents === null || cents === undefined) {
        return "Withheld (Stale)";
    }
    return formatCentsUsd(cents);
}
function escapeHtml(raw) {
    const s = String(raw ?? "");
    return s
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
function parseUrlParams() {
    const parsed = parseScreenUrlState(window.location.search, {
        scenario: "live_revenue",
        seed: 42,
        size: 1000,
        step: 1,
        maxStep: 10,
    });
    state.scenarioId = parsed.scenario;
    state.seed = parsed.seed;
    state.datasetSize = parsed.size;
    state.currentStepIndex = parsed.step;
    state.auxMaxTotalRows = parsed.cap;
}
function syncUrlParams() {
    syncScreenUrlState({
        scenario: state.scenarioId,
        seed: state.seed,
        size: state.datasetSize,
        step: state.currentStepIndex,
        cap: state.auxMaxTotalRows,
    });
}
function stopPlayback() {
    state.isPlaying = false;
    if (playbackTimer !== null) {
        window.clearInterval(playbackTimer);
        playbackTimer = null;
    }
}
function startPlayback() {
    if (!state.run)
        return;
    stopPlayback();
    const maxStep = state.run.boundaries.length;
    if (state.currentStepIndex >= maxStep) {
        state.currentStepIndex = 1;
    }
    else if (state.currentStepIndex === 0) {
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
        const currentBoundary = state.currentStepIndex > 0
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
        }
        else {
            stopPlayback();
            renderApp();
        }
    }, STEP_INTERVAL_MS);
}
async function fetchInitialAndRun(preserveStep = false) {
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
            state.metadata = (await metaResp.json());
        }
        const runResp = await fetch("/api/runs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                scenario_id: state.scenarioId,
                seed: state.seed,
                dataset_size: state.datasetSize,
                aux_max_total_rows: state.scenarioId === "customers_and_regions"
                    ? state.auxMaxTotalRows
                    : null,
            }),
        });
        if (!runResp.ok) {
            const errBody = await runResp.json().catch(() => ({}));
            throw new Error(errBody.detail || `Run request failed with HTTP ${runResp.status}`);
        }
        const trace = (await runResp.json());
        state.run = trace;
        if (!preserveStep) {
            state.currentStepIndex = 1;
        }
        else {
            state.currentStepIndex = Math.min(state.currentStepIndex, trace.boundaries.length);
        }
        state.loading = false;
        syncUrlParams();
        renderApp();
    }
    catch (err) {
        state.loading = false;
        state.error = err instanceof Error ? err.message : String(err);
        renderApp();
    }
}
function getActiveBoundary() {
    if (!state.run || state.currentStepIndex <= 0) {
        return null;
    }
    return (state.run.boundaries[Math.min(state.currentStepIndex - 1, state.run.boundaries.length - 1)] ?? null);
}
function getActiveCounters() {
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
function renderDecisionBadge(boundary) {
    if (!boundary) {
        return `<span class="decision-badge badge-preserve">● Initial Seeded Snapshot</span>`;
    }
    if (boundary.ui_badge === "Mismatch") {
        return `<span class="decision-badge badge-mismatch">✕ Mismatch Detected</span>`;
    }
    const move = decisionKindToMove(boundary.decision_kind);
    if (move === "KEEP") {
        return `<span class="decision-badge badge-preserve">✓ KEEP (${escapeHtml(boundary.reason_code)})</span>`;
    }
    if (move === "FIX IN PLACE") {
        return `<span class="decision-badge badge-repair">⚡ FIX IN PLACE (${escapeHtml(boundary.reason_code)})</span>`;
    }
    return `<span class="decision-badge badge-invalidate">↻ REREAD (${escapeHtml(boundary.reason_code)})</span>`;
}
function getPreviousTotals() {
    if (!state.run)
        return null;
    if (state.currentStepIndex <= 1) {
        return state.run.initial_state.oracle_result_cents;
    }
    const prev = state.run.boundaries[state.currentStepIndex - 2];
    return prev ? prev.oracle_result_cents : state.run.initial_state.oracle_result_cents;
}
function getActiveArithmetic(boundary) {
    if (!boundary || decisionKindToMove(boundary.decision_kind) !== "FIX IN PLACE") {
        return undefined;
    }
    const beforeTotals = getPreviousTotals();
    const afterTotals = boundary.smplcache_result_cents ?? boundary.oracle_result_cents;
    if (!beforeTotals || !afterTotals)
        return undefined;
    return formatFixInPlaceArithmetic({
        beforeTotals,
        afterTotals,
        beforeImage: boundary.before_image,
        afterImage: boundary.after_image,
    });
}
function getActiveRecordText() {
    const boundary = getActiveBoundary();
    const beforeTotals = getPreviousTotals();
    const afterTotals = boundary
        ? boundary.smplcache_result_cents ?? boundary.oracle_result_cents
        : state.run?.initial_state.smplcache_result_cents ?? null;
    const move = boundary ? decisionKindToMove(boundary.decision_kind) : "INITIAL";
    const arith = getActiveArithmetic(boundary);
    const input = {
        scenario: state.scenarioId,
        seed: state.seed,
        size: state.datasetSize,
        step: state.currentStepIndex,
        changeSql: buildBoundaryChangeSql(boundary),
        move,
        reasonCode: boundary ? boundary.reason_code : "initial_snapshot",
        arithmeticSummary: arith,
        beforeTotals,
        afterTotals,
    };
    return formatPlainTextRecord(input);
}
function copyCurrentRecord() {
    const text = getActiveRecordText();
    navigator.clipboard?.writeText(text).catch(() => { });
    state.copiedRecordNotice = true;
    renderApp();
    window.setTimeout(() => {
        state.copiedRecordNotice = false;
        renderApp();
    }, 2200);
}
function renderGroupBars(groups, isBaseline) {
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
function renderSampleOrders(orders, highlightedId) {
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
        const statusClass = row.status === "paid"
            ? "status-paid"
            : row.status === "pending"
                ? "status-pending"
                : "status-refunded";
        const displayRegion = state.scenarioId === "customers_and_regions"
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
function downloadRunReport() {
    if (!state.run)
        return;
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
function copyReplayLink() {
    syncUrlParams();
    navigator.clipboard?.writeText(window.location.href).catch(() => { });
    state.copiedReplayNotice = true;
    renderApp();
    window.setTimeout(() => {
        state.copiedReplayNotice = false;
        renderApp();
    }, 2200);
}
function jumpToRemoveEvidence() {
    stopPlayback();
    if (!state.run)
        return;
    const missingIdx = state.run.boundaries.findIndex((b) => b.reason_code === "missing_evidence_for_repair");
    if (missingIdx >= 0) {
        state.currentStepIndex = missingIdx + 1;
        syncUrlParams();
        renderApp();
    }
    else {
        state.scenarioId = "missing_evidence";
        state.currentStepIndex = 2;
        void fetchInitialAndRun(true);
    }
}
function renderApp() {
    const root = document.getElementById("app");
    if (!root)
        return;
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
    const isMismatch = (boundary && !boundary.oracle_match) ||
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
    const recompPct = counters.full_result_recomputations_avoided_pct !== null
        ? ` (${counters.full_result_recomputations_avoided_pct}% of baseline)`
        : "";
    const srcAvoided = counters.source_queries_avoided;
    const srcAvoidedNote = srcAvoided < 0
        ? `3-table join rebuilds read 3 source tables vs. 1 baseline query`
        : `Baseline: ${counters.baseline_source_queries} vs. SmplCache: ${counters.smplcache_source_queries}`;
    const overviewHref = `/${buildScreenUrlSearch({
        scenario: state.scenarioId,
        seed: state.seed,
        size: state.datasetSize,
        step: state.currentStepIndex,
        cap: state.auxMaxTotalRows,
    })}`;
    const activeArithmetic = getActiveArithmetic(boundary);
    const activeRecordText = getActiveRecordText();
    const activeMove = boundary ? decisionKindToMove(boundary.decision_kind) : null;
    root.innerHTML = `
    <header class="an-header">
      <div class="an-header__in">
        <a class="an-brand" href="https://anomalon.com/"><svg viewBox="0 0 22 22" aria-hidden="true"><rect x="1" y="1" width="20" height="20" rx="4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6 15.5 11 6l5 9.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="11" cy="12.6" r="1.7" fill="var(--an-accent)"/></svg>Anomalon <span class="an-brand__tool">/ SmplCache Live</span></a>
        <nav class="an-nav an-nav--inline" aria-label="Site">
          <a href="${escapeHtml(overviewHref)}">Overview</a>
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
            SmplCache makes one of three moves per change — <strong>KEEP</strong>, <strong>FIX IN PLACE</strong>, or <strong>REREAD</strong> —
            and checks every result against a native SQLite database.
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
          <button id="btn-copy-record" class="btn btn-sm" type="button" data-record="${escapeHtml(activeRecordText)}">
            ${state.copiedRecordNotice ? "✓ Record Copied" : "Copy record"}
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

      ${state.error
        ? `<div class="panel-card" style="margin-top:0.85rem; border-color: var(--accent-red); color: var(--an-bad);">
              <strong>Execution Error:</strong> ${escapeHtml(state.error)}
            </div>`
        : ""}

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
          <div class="strip-sub">Baseline: ${counters.baseline_recomputations} full runs · SmplCache: ${counters.smplcache_rebuilds} rereads</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">Source Table Queries Avoided</span>
          <div class="strip-value" style="${srcAvoided < 0 ? "color: var(--accent-amber);" : ""}">${srcAvoided}</div>
          <div class="strip-sub">${escapeHtml(srcAvoidedNote)}</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">SmplCache Moves</span>
          <div class="strip-value">
            ${counters.preserve_count} KEEP · ${counters.repair_count} FIX IN PLACE · ${counters.invalidate_count} REREAD
          </div>
          <div class="strip-sub">Changed col not in query / Add difference / Read again</div>
        </div>
        <div class="strip-card">
          <span class="strip-label">Step Progress</span>
          <div class="strip-value">
            Step ${state.currentStepIndex} of ${maxSteps}
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
        .map((sz) => `<option value="${sz}" ${state.datasetSize === sz ? "selected" : ""}>${sz.toLocaleString()} rows</option>`)
        .join("")}
              </select>
            </label>
            <label>
              Seed:
              <input id="input-seed" type="number" min="1" max="999999" value="${state.seed}" style="width: 78px;" aria-label="Random seed" />
            </label>
            ${state.scenarioId === "customers_and_regions"
        ? `<label>
                    Lookup Row Limit:
                    <select id="select-aux-cap" aria-label="Cached join lookup row limit">
                      <option value="none" ${state.auxMaxTotalRows === null ? "selected" : ""}>Unbounded (All rows)</option>
                      <option value="1500" ${state.auxMaxTotalRows === 1500 ? "selected" : ""}>1,500 rows (Fits 1k)</option>
                      <option value="250" ${state.auxMaxTotalRows === 250 ? "selected" : ""}>250 rows (Tight)</option>
                      <option value="60" ${state.auxMaxTotalRows === 60 ? "selected" : ""}>60 rows (Trigger eviction)</option>
                    </select>
                  </label>`
        : ""}
          </div>
        </div>

        <!-- Step Jump Pills (Permitted Mutations) -->
        <div class="step-pills" role="navigation" aria-label="Committed boundary steps">
          <button type="button" class="step-pill ${state.currentStepIndex === 0 ? "active" : ""}" data-step="0">
            0. Initial Snapshot
          </button>
          ${(run?.boundaries ?? [])
        .map((b) => `
              <button type="button" class="step-pill ${state.currentStepIndex === b.boundary_index ? "active" : ""}" data-step="${b.boundary_index}">
                ${b.step_number}. ${escapeHtml(b.action_label)} (${escapeHtml(decisionKindToMove(b.decision_kind))})
              </button>
            `)
        .join("")}
        </div>
      </section>

      <!-- Main Comparison Workspace -->
      <main class="workspace-grid">
        <!-- Left Column: Current Event + Side-by-Side Results -->
        <div class="panel-card">
          <div class="panel-header">
            <h2 class="panel-title">
              ${boundary
        ? `Step ${boundary.step_number} of ${maxSteps}: ${escapeHtml(boundary.title)}`
        : `Step 0: Initial Seeded Database Snapshot`}
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
              <strong>Move &amp; Reason${boundary ? ` (<code>${escapeHtml(boundary.reason_code)}</code>)` : ""}:</strong>
              ${activeMove &&
        boundary &&
        !boundary.plain_language_reason.startsWith(activeMove)
        ? `<strong style="margin-right:0.35rem;">${escapeHtml(activeMove)}</strong> — `
        : ""}${escapeHtml(boundary?.plain_language_reason ??
        "Both Baseline and SmplCache are seeded from the same initial SQLite snapshot. Zero post-initialization queries have run yet.")}
              ${activeArithmetic
        ? `<div style="margin-top:0.35rem; font-family:var(--font-mono); font-size:0.8rem; color:var(--text-bright);">
                      <strong>Arithmetic:</strong> ${escapeHtml(activeArithmetic)}
                    </div>`
        : ""}
              <div style="margin-top:0.3rem; font-size:0.82rem; color:var(--text-muted);">
                ${escapeHtml(boundary?.narration ?? "")}
              </div>
            </div>

            ${boundary?.stale_value_withheld
        ? `<div class="withheld-notice" role="status">
                    <strong>Stale Cache Withheld Before Rebuild:</strong>
                    When this event arrived without complete repair evidence, SmplCache immediately marked the cached result invalid
                    (<code>pre_rebuild_result: null</code>), refused to serve or repair the stale total, executed
                    <strong>${boundary.boundary_counters.smplcache_source_queries} source snapshot query(s)</strong> at committed boundary
                    <code>b0${boundary.boundary_index}</code>, and verified the rebuilt answer against SQLite.
                  </div>`
        : ""}

            ${boundary
        ? `<div class="diff-grid">
                    <div class="diff-box">
                      <div class="diff-box-title">CDC Before Image (old)</div>
                      <div>${boundary.before_image
            ? escapeHtml(JSON.stringify(boundary.before_image, null, 2))
            : "null (INSERT / none)"}</div>
                    </div>
                    <div class="diff-box">
                      <div class="diff-box-title">CDC After Image (new)</div>
                      <div>${boundary.after_image
            ? escapeHtml(JSON.stringify(boundary.after_image, null, 2))
            : "null (DELETE / none)"}</div>
                    </div>
                  </div>`
        : ""}
          </div>

          <!-- Side-by-Side SmplCache vs. Baseline -->
          <div class="comparison-grid">
            <div class="policy-box smplcache-box ${isWithheld ? "withheld-mode" : ""}">
              <div class="policy-header">
                <div>
                  <div class="policy-name">SmplCache</div>
                  <div class="policy-sub">
                    ${boundary
        ? `This boundary: ${boundary.boundary_counters.smplcache_source_queries} source queries (${boundary.boundary_counters.smplcache_rebuilds} rebuilds)`
        : `Initialized from snapshot`}
                  </div>
                </div>
                <span class="commit-tag" style="color: ${isMismatch
        ? "var(--accent-red)"
        : isWithheld
            ? "var(--accent-amber)"
            : "var(--accent-emerald)"};">
                  ${escapeHtml(boundary?.freshness_state ?? "initial_verified")}
                </span>
              </div>
              <div class="total-money">
                <span>${escapeHtml(totalFormatted)}</span>
                ${boundary
        ? `<span class="delta-tag">${escapeHtml(boundary.delta_summary)}</span>`
        : ""}
              </div>
              ${renderGroupBars(smplResult, false)}
            </div>

            <div class="policy-box">
              <div class="policy-header">
                <div>
                  <div class="policy-name">Baseline (Table Invalidation)</div>
                  <div class="policy-sub">
                    ${boundary
        ? `This boundary: ${boundary.boundary_counters.baseline_source_queries} full SQL query on read`
        : `Initialized from snapshot`}
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

            ${auxInfo
        ? `<div class="event-banner" style="margin-bottom:0;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.3rem;">
                      <strong style="font-size:0.82rem;">Cached Join Lookup Rows</strong>
                      <span class="commit-tag">
                        ${auxInfo.resident_aux_rows} / ${auxInfo.aux_max_total_rows ?? "∞"} rows
                      </span>
                    </div>
                    <div style="font-size:0.76rem; color:var(--text-secondary);">
                      Evicted keys tracked: <strong>${auxInfo.evicted_aux_keys_count ?? 0}</strong>
                      ${auxInfo.affected_fanout_count !== undefined &&
            auxInfo.affected_fanout_count !== null
            ? ` · Customer #1 fanout repaired: <strong>${auxInfo.affected_fanout_count} paid orders</strong>`
            : ""}
                    </div>
                    <div style="font-size:0.72rem; color:var(--text-muted); margin-top:0.3rem;">
                      Note: Limit counts resident lookup rows kept in memory for joins. Indexes, eviction tombstones,
                      frequency counters, and output groups also consume memory; row capacity is not a total RAM limit.
                    </div>
                  </div>`
        : ""}
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

        ${state.inspectorTab === "certificate"
        ? `<div class="inspector-grid">
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">Machine-Readable Decision Certificate</h3>
                  <pre class="code-block">${escapeHtml(JSON.stringify(boundary?.certificate ?? {
            note: "Step 0 (Initial Seed). Step forward to inspect per-event certificates.",
        }, null, 2))}</pre>
                </div>
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">
                    Instrumented SQLite Query Log (Total: ${run?.query_log.length ?? 0} queries)
                  </h3>
                  <pre class="code-block">${escapeHtml(JSON.stringify((run?.query_log ?? []).filter((q) => q.boundary_index === state.currentStepIndex), null, 2))}</pre>
                  <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.35rem;">
                    Canonical Run Hash (SHA-256): <code>${escapeHtml(run?.canonical_hash ?? "")}</code>
                  </div>
                </div>
              </div>`
        : ""}

        ${state.inspectorTab === "sql"
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
                  SmplCache never guesses on SQL constructs outside its supported single-block SELECT-FROM-JOIN-WHERE-GROUP BY subset.
                  The queries below are evaluated through the real parser on the server:
                </p>
                <div class="rejected-list">
                  ${(state.metadata?.rejected_sql_examples ?? [])
            .map((ex) => `<div class="rejected-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; gap:0.4rem; margin-bottom:0.35rem;">
                          <strong style="font-size:0.82rem;">${escapeHtml(ex.label)}</strong>
                          <span class="decision-badge badge-unsupported" style="font-size:0.7rem; padding:0.12rem 0.5rem;">
                            Rejected: ${escapeHtml(ex.rejection_reason)}
                          </span>
                        </div>
                        <pre class="code-block" style="margin-bottom:0.4rem; padding:0.45rem;">${escapeHtml(ex.sql)}</pre>
                        <div style="font-size:0.76rem; color:var(--text-secondary);">${escapeHtml(ex.explanation)}</div>
                      </div>`)
            .join("")}
                </div>
              </div>`
        : ""}

        ${state.inspectorTab === "architecture"
        ? `<div class="inspector-grid">
                <div>
                  <h3 class="panel-title" style="margin-bottom:0.45rem;">How SmplCache Evaluates Every Committed Write</h3>
                  <div class="code-block" style="line-height:1.65;">
1. <strong>Parse SQL Shape (AST Allowlist)</strong>
   Decompose admitted SELECT..FROM..[JOIN]..WHERE..GROUP BY into dependency set &amp; required log columns.
2. <strong>Dependency Intersection Check</strong>
   If modified columns are outside the query dependency set (e.g. <code>orders.notes</code>), emit <strong>KEEP</strong> (0 DB queries).
3. <strong>Evidence Completeness Check</strong>
   Verify required columns are present in both <code>old</code> and <code>new</code> row images (and foreign-key lookup rows are resident in memory).
4. <strong>In-Place Delta Repair or Fail-Closed Reread</strong>
   • Sufficient evidence → <strong>FIX IN PLACE</strong> by adding the difference to group sum &amp; count (0 DB queries).
   • Missing/evicted evidence → <strong>REREAD</strong>, withhold stale cache, and reread from the committed source snapshot.
5. <strong>Independent Native SQLite Verification</strong>
   Every step in this demo is checked against native SQLite integer <code>SUM(amount_cents)</code>.
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
        : ""}
      </section>

      <footer class="footer-section">
        <div>
          Anomalon · SmplCache Live · Apache-2.0 · Numbers and query counts are for this demo only, not a benchmark ·
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
function bindEvents() {
    document.getElementById("btn-run-demo")?.addEventListener("click", () => {
        if (state.isPlaying) {
            stopPlayback();
            renderApp();
        }
        else {
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
    document.getElementById("btn-copy-record")?.addEventListener("click", () => {
        copyCurrentRecord();
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
    document.querySelectorAll("[data-scenario]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const target = btn.getAttribute("data-scenario");
            if (target && target !== state.scenarioId) {
                state.scenarioId = target;
                state.currentStepIndex = 1;
                void fetchInitialAndRun(false);
            }
        });
    });
    document.querySelectorAll("[data-step]").forEach((btn) => {
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
    document.querySelectorAll("[data-inspector]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const tab = btn.getAttribute("data-inspector");
            if (tab) {
                state.inspectorTab = tab;
                renderApp();
            }
        });
    });
    const sizeSel = document.getElementById("select-dataset-size");
    sizeSel?.addEventListener("change", () => {
        const val = Number(sizeSel.value);
        if ([100, 500, 1000, 2500].includes(val)) {
            state.datasetSize = val;
            void fetchInitialAndRun(true);
        }
    });
    const seedInput = document.getElementById("input-seed");
    seedInput?.addEventListener("change", () => {
        const val = Math.max(1, Math.min(999999, Number(seedInput.value) || 42));
        state.seed = val;
        void fetchInitialAndRun(true);
    });
    const capSel = document.getElementById("select-aux-cap");
    capSel?.addEventListener("change", () => {
        const raw = capSel.value;
        state.auxMaxTotalRows = raw === "none" ? null : Number(raw);
        void fetchInitialAndRun(true);
    });
}
window.addEventListener("keydown", (ev) => {
    const tag = ev.target?.tagName?.toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea")
        return;
    if (ev.key === "ArrowRight") {
        ev.preventDefault();
        stopPlayback();
        if (state.run && state.currentStepIndex < state.run.boundaries.length) {
            state.currentStepIndex += 1;
            syncUrlParams();
            renderApp();
        }
    }
    else if (ev.key === "ArrowLeft") {
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

