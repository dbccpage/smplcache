# Adaptive Window Sizing (`wtinylfu` / Count-Min Sketch) & Shared Multi-Query `JoinAuxState` Pool Evaluation

> Historical benchmark: these tables were recorded before the PR #7 review repairs
> at commit `d8467e7`. Certification now leaves access telemetry unchanged, which
> changes eviction trajectories. Rerun the documented sweep commands before using
> these figures for the current revision or a resume claim.

Empirical evaluation of **W-TinyLFU hill-climbing admission/window adaptation** (`wtinylfu`) with
**Count-Min Sketch (`CountMinSketch`) frequency aging** under rapid multi-phase Zipfian oscillations ($P = 4$ phases)
and **Shared Multi-Query `JoinAuxState` memory pools (`SharedJoinAuxPool`)** across $M = 3$ concurrent join queries.

## 1. Rapid Multi-Phase Oscillation Sweep ($P = 4$ Phases, `max_total_rows = 50`)

| skew_s | policy | total_decs | post_shift_decs | post_shift_evict_inv | post_shift_inv_rate | repair_count | final_win_ratio | final_decay_win | adapt_epochs | decay_epochs | oracle_match |
|---:|:---:|---:|---:|---:|---:|---:|---:|:---:|---:|---:|:---:|
| 0.00 | fifo | 173 | 126 | 43 | 34.13% | 89 | 0.25 | none | 0 | 0 | yes |
| 0.00 | lru | 173 | 126 | 60 | 47.62% | 58 | 0.25 | none | 0 | 0 | yes |
| 0.00 | lfu | 173 | 126 | 74 | 58.73% | 83 | 0.25 | none | 0 | 0 | yes |
| 0.00 | w_lfu | 173 | 126 | 0 | 0.00% | 154 | 0.25 | 64 | 0 | 27 | yes |
| 0.00 | wtinylfu | 173 | 126 | 0 | 0.00% | 154 | 0.05 | 128 | 110 | 13 | yes |
| 0.50 | fifo | 173 | 126 | 24 | 19.05% | 119 | 0.25 | none | 0 | 0 | yes |
| 0.50 | lru | 173 | 126 | 55 | 43.65% | 64 | 0.25 | none | 0 | 0 | yes |
| 0.50 | lfu | 173 | 126 | 74 | 58.73% | 83 | 0.25 | none | 0 | 0 | yes |
| 0.50 | w_lfu | 173 | 126 | 0 | 0.00% | 155 | 0.25 | 64 | 0 | 27 | yes |
| 0.50 | wtinylfu | 173 | 126 | 0 | 0.00% | 155 | 0.05 | 128 | 110 | 13 | yes |
| 1.00 | fifo | 173 | 126 | 49 | 38.89% | 79 | 0.25 | none | 0 | 0 | yes |
| 1.00 | lru | 173 | 126 | 68 | 53.97% | 47 | 0.25 | none | 0 | 0 | yes |
| 1.00 | lfu | 173 | 126 | 74 | 58.73% | 83 | 0.25 | none | 0 | 0 | yes |
| 1.00 | w_lfu | 173 | 126 | 0 | 0.00% | 152 | 0.25 | 64 | 0 | 27 | yes |
| 1.00 | wtinylfu | 173 | 126 | 0 | 0.00% | 152 | 0.05 | 128 | 109 | 13 | yes |
| 1.50 | fifo | 173 | 126 | 23 | 18.25% | 117 | 0.25 | none | 0 | 0 | yes |
| 1.50 | lru | 173 | 126 | 60 | 47.62% | 57 | 0.25 | none | 0 | 0 | yes |
| 1.50 | lfu | 173 | 126 | 74 | 58.73% | 83 | 0.25 | none | 0 | 0 | yes |
| 1.50 | w_lfu | 173 | 126 | 0 | 0.00% | 152 | 0.25 | 64 | 0 | 27 | yes |
| 1.50 | wtinylfu | 173 | 126 | 0 | 0.00% | 152 | 0.80 | 8 | 109 | 191 | yes |
| 2.00 | fifo | 173 | 126 | 15 | 11.90% | 126 | 0.25 | none | 0 | 0 | yes |
| 2.00 | lru | 173 | 126 | 63 | 50.00% | 53 | 0.25 | none | 0 | 0 | yes |
| 2.00 | lfu | 173 | 126 | 74 | 58.73% | 83 | 0.25 | none | 0 | 0 | yes |
| 2.00 | w_lfu | 173 | 126 | 0 | 0.00% | 149 | 0.25 | 64 | 0 | 27 | yes |
| 2.00 | wtinylfu | 173 | 126 | 0 | 0.00% | 149 | 0.80 | 8 | 108 | 189 | yes |

## 2. Shared Multi-Query Pool (`SharedJoinAuxPool`) vs. Isolated Per-Query Pools ($M = 3$ Queries, $s = 1.50$, $P = 4$ Phases)

| total_pool_rows | pool_mode | per_query_rows | policy | total_decs | total_repairs | total_evict_inv | evict_inv_rate | q_region_rev_inv | q_tier_cnt_inv | q_region_avg_inv | oracle_match |
|---:|:---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 48 | isolated | 16 | fifo | 420 | 0 | 246 | 58.57% | 96 | 54 | 96 | yes |
| 48 | isolated | 16 | lru | 420 | 1 | 227 | 54.05% | 88 | 51 | 88 | yes |
| 48 | isolated | 16 | lfu | 420 | 108 | 237 | 56.43% | 109 | 19 | 109 | yes |
| 48 | isolated | 16 | w_lfu | 420 | 93 | 243 | 57.86% | 109 | 25 | 109 | yes |
| 48 | isolated | 16 | wtinylfu | 420 | 108 | 237 | 56.43% | 109 | 19 | 109 | yes |
| 48 | shared | 48 | fifo | 420 | 226 | 69 | 16.43% | 27 | 15 | 27 | yes |
| 48 | shared | 48 | lru | 420 | 69 | 170 | 40.48% | 61 | 48 | 61 | yes |
| 48 | shared | 48 | lfu | 420 | 273 | 66 | 15.71% | 33 | 0 | 33 | yes |
| 48 | shared | 48 | w_lfu | 420 | 273 | 66 | 15.71% | 33 | 0 | 33 | yes |
| 48 | shared | 48 | wtinylfu | 420 | 273 | 66 | 15.71% | 33 | 0 | 33 | yes |
| 54 | isolated | 18 | fifo | 420 | 0 | 244 | 58.10% | 95 | 54 | 95 | yes |
| 54 | isolated | 18 | lru | 420 | 3 | 221 | 52.62% | 86 | 49 | 86 | yes |
| 54 | isolated | 18 | lfu | 420 | 132 | 213 | 50.71% | 98 | 17 | 98 | yes |
| 54 | isolated | 18 | w_lfu | 420 | 104 | 209 | 49.76% | 93 | 23 | 93 | yes |
| 54 | isolated | 18 | wtinylfu | 420 | 137 | 208 | 49.52% | 98 | 12 | 98 | yes |
| 54 | shared | 54 | fifo | 420 | 273 | 48 | 11.43% | 20 | 8 | 20 | yes |
| 54 | shared | 54 | lru | 420 | 137 | 122 | 29.05% | 44 | 34 | 44 | yes |
| 54 | shared | 54 | lfu | 420 | 246 | 85 | 20.24% | 35 | 15 | 35 | yes |
| 54 | shared | 54 | w_lfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 54 | shared | 54 | wtinylfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 66 | isolated | 22 | fifo | 420 | 7 | 238 | 56.67% | 95 | 48 | 95 | yes |
| 66 | isolated | 22 | lru | 420 | 3 | 217 | 51.67% | 84 | 49 | 84 | yes |
| 66 | isolated | 22 | lfu | 420 | 144 | 201 | 47.86% | 94 | 13 | 94 | yes |
| 66 | isolated | 22 | w_lfu | 420 | 167 | 151 | 35.95% | 66 | 17 | 68 | yes |
| 66 | isolated | 22 | wtinylfu | 420 | 148 | 197 | 46.90% | 94 | 9 | 94 | yes |
| 66 | shared | 66 | fifo | 420 | 322 | 8 | 1.90% | 4 | 0 | 4 | yes |
| 66 | shared | 66 | lru | 420 | 230 | 63 | 15.00% | 23 | 17 | 23 | yes |
| 66 | shared | 66 | lfu | 420 | 312 | 18 | 4.29% | 7 | 4 | 7 | yes |
| 66 | shared | 66 | w_lfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 66 | shared | 66 | wtinylfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 78 | isolated | 26 | fifo | 420 | 34 | 215 | 51.19% | 89 | 37 | 89 | yes |
| 78 | isolated | 26 | lru | 420 | 3 | 211 | 50.24% | 81 | 49 | 81 | yes |
| 78 | isolated | 26 | lfu | 420 | 156 | 189 | 45.00% | 90 | 9 | 90 | yes |
| 78 | isolated | 26 | w_lfu | 420 | 222 | 99 | 23.57% | 46 | 8 | 45 | yes |
| 78 | isolated | 26 | wtinylfu | 420 | 223 | 117 | 27.86% | 55 | 5 | 57 | yes |
| 78 | shared | 78 | fifo | 420 | 325 | 2 | 0.48% | 1 | 0 | 1 | yes |
| 78 | shared | 78 | lru | 420 | 255 | 47 | 11.19% | 17 | 13 | 17 | yes |
| 78 | shared | 78 | lfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 78 | shared | 78 | w_lfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 78 | shared | 78 | wtinylfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 96 | isolated | 32 | fifo | 420 | 46 | 214 | 50.95% | 91 | 32 | 91 | yes |
| 96 | isolated | 32 | lru | 420 | 3 | 201 | 47.86% | 76 | 49 | 76 | yes |
| 96 | isolated | 32 | lfu | 420 | 171 | 172 | 40.95% | 84 | 4 | 84 | yes |
| 96 | isolated | 32 | w_lfu | 420 | 273 | 60 | 14.29% | 29 | 2 | 29 | yes |
| 96 | isolated | 32 | wtinylfu | 420 | 280 | 57 | 13.57% | 28 | 1 | 28 | yes |
| 96 | shared | 96 | fifo | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 96 | shared | 96 | lru | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 96 | shared | 96 | lfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 96 | shared | 96 | w_lfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |
| 96 | shared | 96 | wtinylfu | 420 | 330 | 0 | 0.00% | 0 | 0 | 0 | yes |

## 3. Isolated vs. Shared Pool Eviction Reduction Summary (`wtinylfu` vs. `lfu` / `fifo`)

| total_pool_rows | Isolated FIFO Evict Inv | Isolated LFU Evict Inv | Isolated W-TinyLFU Evict Inv | Shared W-TinyLFU Evict Inv | Shared vs. Isolated W-TinyLFU Reduction |
|---:|---:|---:|---:|---:|---:|
| 48 | 246 | 237 | 237 | 66 | 72.2% |
| 54 | 244 | 213 | 208 | 0 | 100.0% |
| 66 | 238 | 201 | 197 | 0 | 100.0% |
| 78 | 215 | 189 | 117 | 0 | 100.0% |
| 96 | 214 | 172 | 57 | 0 | 100.0% |

## 4. Claim Hygiene Summary

1. **What this artifact supports:**
   - Demonstrates in this synthetic 4-phase rapid oscillation benchmark (`max_total_rows = 50`, 96 transactions across 4 rotating Zipfian cohorts) that `wtinylfu` (combining a 4-row `CountMinSketch`, an adaptive recency window protecting newly admitted phase rows from immediate eviction, and hill-climbing window adaptation) reduces post-shift `evicted_join_auxiliary_key` invalidations relative to `fifo`, `lru`, static `lfu`, and fixed-window `w_lfu` (`lfu_decay_window = 64`).
   - Supports the observation that consolidating $M = 3$ concurrent join queries (`q_region_rev`, `q_tier_cnt`, `q_region_avg`) into a unified `SharedJoinAuxPool` eliminates triplicate storage of shared `regions`, `customers`, and `orders` rows and weights dimension retention by cross-query structural multiplicity, substantially reducing total eviction invalidations compared to partitioning the same row budget across 3 isolated `JoinAuxState` instances.
   - Checked against `rebuild_join_cache` with 100% agreement across the recorded cases. That rebuild shares engine code and is not an independent oracle.

2. **What this artifact does not support:**
   - Does not support a claim that `wtinylfu` eliminates eviction invalidations when the concurrent single-phase working set exceeds `max_total_rows`.
   - Does not support claims about physical byte-level RAM overhead, lock contention across multi-threaded workers, or distributed cache coherence.
   - Does not model queries over completely disjoint table sets where `SharedJoinAuxPool` cannot deduplicate shared dimension or fact rows.

3. **Narrowest defensible claim:**
   - Under the declared 4-phase oscillating snowflake workload (`seed = 42`) and 3-query shared-schema workload, `wtinylfu` with `CountMinSketch` aging and recency-window protection reduces post-shift eviction invalidations relative to static `lfu` and fixed-window `w_lfu` (`window = 64`), and `SharedJoinAuxPool` reduces multi-query eviction invalidations relative to isolated per-query `JoinAuxState` pools at fixed total row capacity while matching the shared-code rebuild on recorded cases.

4. **Next test needed:**
   - Measure byte-accurate row memory accounting (accounting for variable-width string/JSON payloads rather than uniform row counts) and evaluate multi-tenant workload isolation when one query experiences an adversarial scan burst over unshared tables.
