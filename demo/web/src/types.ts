export type ScenarioId =
  | "live_revenue"
  | "customers_and_regions"
  | "missing_evidence";

export interface ScenarioCatalogEntry {
  scenario_id: ScenarioId;
  title: string;
  short_label: string;
  category: string;
  sql: string;
  description: string;
  engine_components: string[];
  participating_tables: string[];
  default_seed: number;
  default_dataset_size: number;
  default_aux_max_total_rows: number | null;
  adverse_aux_max_total_rows?: number;
  supports_aux_capacity: boolean;
  capacity_disclosure?: string;
  step_count: number;
}

export interface RejectedSqlExample {
  id: string;
  label: string;
  parser: string;
  sql: string;
  is_supported: boolean;
  rejection_reason: string;
  explanation: string;
}

export interface ScenariosMetadataResponse {
  api_version: string;
  scenario_version: string;
  default_scenario_id: ScenarioId;
  allowed_dataset_sizes: number[];
  allowed_aux_capacities: Array<number | null>;
  seed_bounds: { min: number; max: number; default: number };
  max_steps_bounds: { min: number; max: number };
  scenarios: ScenarioCatalogEntry[];
  rejected_sql_examples: RejectedSqlExample[];
}

export interface CertificateRecord {
  shape: string;
  event_id: string;
  relation: string;
  decision_kind: "preserve" | "repair" | "invalidate" | "unsupported";
  reason_code: string;
  required_evidence: string[];
  available_evidence: string[];
  repair_program: string | null;
  boundary_clock: string;
}

export interface SampleOrderRow {
  id: number;
  customer_id: number;
  customer_name: string | null;
  region: string;
  joined_region_name: string | null;
  status: string;
  amount_cents: number;
  notes: string;
}

export interface CumulativeCounters {
  baseline_recomputations: number;
  baseline_source_queries: number;
  smplcache_rebuilds: number;
  smplcache_source_queries: number;
  full_result_recomputations_avoided: number;
  full_result_recomputations_avoided_pct: number | null;
  source_queries_avoided: number;
  source_queries_avoided_pct: number | null;
  preserve_count: number;
  repair_count: number;
  invalidate_count: number;
  unsupported_count: number;
  oracle_checks: number;
  oracle_queries: number;
  mismatch_count: number;
}

export interface AuxTelemetry {
  resident_aux_rows: number;
  aux_max_total_rows: number | null;
  rows_by_table?: Record<string, number>;
  evicted_aux_keys_count?: number;
  eviction_policy?: string;
  affected_fanout_count?: number | null;
}

export interface BoundaryTrace {
  boundary_index: number;
  step_number: number;
  boundary_id: string;
  title: string;
  action_label: string;
  narration: string;
  table_written: string;
  operation: string;
  change_sql?: string;
  highlighted_order_id: number | null;
  delta_summary: string;
  before_image: Record<string, unknown> | null;
  after_image: Record<string, unknown> | null;
  cache_before?: Record<string, number> | null;
  cache_after?: Record<string, number> | null;
  decision_kind: "preserve" | "repair" | "invalidate" | "unsupported";
  ui_badge:
    | "Preserve"
    | "Repair"
    | "Invalidate / Recompute"
    | "Unsupported"
    | "Mismatch";
  reason_code: string;
  plain_language_reason: string;
  certificate: CertificateRecord;
  all_certificates: CertificateRecord[];
  stale_value_withheld: boolean;
  smplcache_pre_rebuild_result: Record<string, number> | null;
  freshness_state:
    | "preserved_verified"
    | "repaired_verified"
    | "rebuilt_verified"
    | "invalidated_withheld"
    | "mismatch";
  baseline_result_cents: Record<string, number>;
  smplcache_result_cents: Record<string, number> | null;
  oracle_result_cents: Record<string, number>;
  total_revenue_cents: number;
  total_revenue_formatted: string;
  oracle_match: boolean;
  boundary_counters: {
    baseline_recomputations: number;
    baseline_source_queries: number;
    smplcache_rebuilds: number;
    smplcache_source_queries: number;
    oracle_queries: number;
  };
  cumulative_counters: CumulativeCounters;
  aux_telemetry: AuxTelemetry | null;
  sample_orders: SampleOrderRow[];
}

export interface RunTraceResponse {
  api_version: string;
  scenario_id: ScenarioId;
  scenario_version: string;
  title: string;
  description: string;
  seed: number;
  config: {
    scenario_id: ScenarioId;
    seed: number;
    dataset_size: number;
    aux_max_total_rows: number | null;
    max_steps: number;
    evidence_mode: "standard" | "remove_required_evidence";
    defer_rebuild: boolean;
  };
  app_commit: string;
  engine_commit: string;
  sql: string;
  shape: Record<string, unknown>;
  initial_state: {
    dataset_size_orders: number;
    dataset_size_customers: number;
    dataset_size_regions: number;
    initialization_queries: number;
    baseline_result_cents: Record<string, number>;
    smplcache_result_cents: Record<string, number>;
    oracle_result_cents: Record<string, number>;
    total_revenue_cents: number;
    total_revenue_formatted: string;
    oracle_match: boolean;
    sample_orders: SampleOrderRow[];
    aux_telemetry: AuxTelemetry | null;
  };
  boundaries: BoundaryTrace[];
  summary: CumulativeCounters & {
    status: "verified" | "mismatch";
    halted_on_mismatch: boolean;
    first_failing_boundary: number | null;
    boundaries_executed: number;
    initialization_queries: number;
    correctness_headline: string;
  };
  counter_definitions: Record<string, string>;
  claim_hygiene: {
    what_it_supports: string;
    what_it_does_not_support: string;
    narrowest_defensible_claim: string;
    next_test_needed: string;
  };
  query_log: Array<{
    boundary_index: number;
    category: string;
    sql: string;
  }>;
  canonical_hash: string;
  timings_ms: {
    seed_and_init_ms: number;
    policy_execution_ms: number;
    oracle_verification_ms: number;
    total_ms: number;
    workload_note: string;
  };
}
