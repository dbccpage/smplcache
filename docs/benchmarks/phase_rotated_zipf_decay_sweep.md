# Phase-Rotated Zipfian Access Shift & Windowed Frequency Decay (`w_lfu`) Evaluation

> Historical benchmark: these tables were recorded before the PR #7 review repairs
> at commit `d8467e7`. Certification now leaves access telemetry unchanged, which
> changes eviction trajectories. Rerun the documented sweep commands before using
> these figures for the current revision or a resume claim.

Empirical evaluation of dynamic access pattern shifts (phase-rotated Zipfian distributions)
across `FIFO`, `LRU`, static `LFU`, and windowed frequency-decay `W-LFU` (`w_lfu`) in `JoinAuxState`
at fixed memory capacity (`max_total_rows = 50`).

## 1. Phase-Rotated Zipfian Skew Sweep ($s \in [0.0, 2.0]$)

| skew_s | policy | decay_win | total_decs | p1_evict_inv | post_shift_evict_inv | post_shift_inv_rate | stale_p1_retained | hot_p2_retained | decay_epochs | oracle_match |
|---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 0.00 | fifo | none | 153 | 0 | 15 | 17.65% | 24 | 14 | 0 | yes |
| 0.00 | lru | none | 153 | 0 | 2 | 2.35% | 7 | 28 | 0 | yes |
| 0.00 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 0.00 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 50 | yes |
| 0.25 | fifo | none | 153 | 0 | 19 | 22.35% | 17 | 22 | 0 | yes |
| 0.25 | lru | none | 153 | 0 | 3 | 3.53% | 20 | 13 | 0 | yes |
| 0.25 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 0.25 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 48 | yes |
| 0.50 | fifo | none | 153 | 0 | 16 | 18.82% | 25 | 13 | 0 | yes |
| 0.50 | lru | none | 153 | 0 | 5 | 5.88% | 10 | 27 | 0 | yes |
| 0.50 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 0.50 | w_lfu | 32 | 153 | 0 | 2 | 2.35% | 0 | 28 | 49 | yes |
| 0.75 | fifo | none | 153 | 0 | 12 | 14.12% | 21 | 21 | 0 | yes |
| 0.75 | lru | none | 153 | 0 | 1 | 1.18% | 3 | 31 | 0 | yes |
| 0.75 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 0.75 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 50 | yes |
| 1.00 | fifo | none | 153 | 0 | 9 | 10.59% | 27 | 11 | 0 | yes |
| 1.00 | lru | none | 153 | 0 | 2 | 2.35% | 6 | 31 | 0 | yes |
| 1.00 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 1.00 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 49 | yes |
| 1.25 | fifo | none | 153 | 0 | 9 | 10.59% | 27 | 11 | 0 | yes |
| 1.25 | lru | none | 153 | 0 | 2 | 2.35% | 7 | 30 | 0 | yes |
| 1.25 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 1.25 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 46 | yes |
| 1.50 | fifo | none | 153 | 0 | 8 | 9.41% | 18 | 22 | 0 | yes |
| 1.50 | lru | none | 153 | 0 | 2 | 2.35% | 7 | 31 | 0 | yes |
| 1.50 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 1.50 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 45 | yes |
| 1.75 | fifo | none | 153 | 0 | 8 | 9.41% | 18 | 22 | 0 | yes |
| 1.75 | lru | none | 153 | 0 | 2 | 2.35% | 2 | 31 | 0 | yes |
| 1.75 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 1.75 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 51 | yes |
| 2.00 | fifo | none | 153 | 0 | 8 | 9.41% | 18 | 22 | 0 | yes |
| 2.00 | lru | none | 153 | 0 | 2 | 2.35% | 4 | 30 | 0 | yes |
| 2.00 | lfu | none | 153 | 0 | 53 | 62.35% | 28 | 0 | 0 | yes |
| 2.00 | w_lfu | 32 | 153 | 0 | 0 | 0.00% | 0 | 28 | 51 | yes |

## 2. Post-Shift Cache Pollution Comparison (`Static LFU` vs. `Windowed W-LFU`)

| skew_s | FIFO Post-Shift Inv | LRU Post-Shift Inv | Static LFU Post-Shift Inv (Stale Retained) | W-LFU Post-Shift Inv (Stale Retained) | W-LFU Reduction vs Static LFU |
|---:|---:|---:|---:|---:|---:|
| 0.00 | 15 | 2 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 0.25 | 19 | 3 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 0.50 | 16 | 5 | 53 (28 stale) | 2 (0 stale) | 96.2% |
| 0.75 | 12 | 1 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 1.00 | 9 | 2 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 1.25 | 9 | 2 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 1.50 | 8 | 2 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 1.75 | 8 | 2 | 53 (28 stale) | 0 (0 stale) | 100.0% |
| 2.00 | 8 | 2 | 53 (28 stale) | 0 (0 stale) | 100.0% |

## 3. Decay Window Sensitivity Sweep ($s = 1.50$, `lfu_decay_factor = 0.5`)

| policy | lfu_decay_window | decay_epochs | post_shift_evict_inv | stale_p1_retained | hot_p2_retained | repair_count | oracle_match |
|:---:|:---:|---:|---:|---:|---:|---:|:---:|
| w_lfu | 8 | 182 | 0 | 0 | 28 | 99 | yes |
| w_lfu | 16 | 91 | 0 | 0 | 28 | 99 | yes |
| w_lfu | 24 | 60 | 0 | 0 | 28 | 99 | yes |
| w_lfu | 32 | 45 | 0 | 0 | 28 | 99 | yes |
| w_lfu | 48 | 30 | 0 | 0 | 28 | 99 | yes |
| w_lfu | 64 | 22 | 0 | 0 | 28 | 99 | yes |
| w_lfu | 128 | 11 | 0 | 0 | 30 | 99 | yes |
| w_lfu | 256 | 5 | 10 | 6 | 22 | 93 | yes |
| w_lfu | 512 | 2 | 20 | 25 | 3 | 89 | yes |
| lfu | unbounded (static) | 0 | 53 | 28 | 0 | 77 | yes |

## 4. Claim Hygiene Summary

1. **What this artifact supports:**
   - Demonstrates in this synthetic 3-table snowflake benchmark (`max_total_rows = 50`, 80 transactions across 2 rotated Zipfian phases) that static `LFU` experiences cache pollution after an access pattern shift: Cohort 1 fact rows (`stale_p1_retained = 28`) retain inflated historical frequency scores (`freq >= 4`) and displace newly inserted Cohort 2 fact rows (`freq = 1`), causing repeated `evicted_join_auxiliary_key` invalidations in Phase 2.
   - Supports the observation that windowed frequency decay (`w_lfu`, halving accumulated access frequencies every `lfu_decay_window = 32` logical accesses while preserving structural resident join multiplicity) ages out stale Cohort 1 fact rows and reduces post-shift eviction invalidations relative to static `LFU`, `LRU`, and `FIFO` in this test configuration.
   - Checked against `rebuild_join_cache` with 100% agreement across the recorded cases. That rebuild shares engine code and is not an independent oracle.

2. **What this artifact does not support:**
   - Does not support a claim that a fixed `lfu_decay_window = 32` is optimal for all workload durations, table cardinalities, or oscillation frequencies.
   - Does not support eliminating eviction invalidations when the active post-shift hot working set itself exceeds `max_total_rows`.
   - Does not model network latency, disk I/O rebuild cost, or concurrent multi-threaded WAL ingestion.

3. **Narrowest defensible claim:**
   - Under the declared phase-rotated 3-table snowflake simulation (`max_total_rows = 50`, `seed = 42`, 80 transactions across 2 disjoint hot cohorts), static `LFU` retains stale Phase 1 fact rows and incurs post-shift `evicted_join_auxiliary_key` invalidations on Phase 2 updates, whereas windowed `W-LFU` (`lfu_decay_window = 32`, `lfu_decay_factor = 0.5`) decays stale Phase 1 frequencies and reduces post-shift eviction invalidations while maintaining oracle agreement.

4. **Next test needed:**
   - Evaluate adaptive decay window sizing (e.g., admission-controlled Count-Min Sketch / W-TinyLFU with hill-climbing window adaptation) under rapid multi-phase oscillations and concurrent multi-query shared `JoinAuxState` pools.
