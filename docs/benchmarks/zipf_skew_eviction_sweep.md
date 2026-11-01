# Zipfian Skew Eviction Policy Sweep (`max_total_rows = 50`)

Empirical evaluation of `FIFO`, `LRU`, and frequency-biased `LFU` eviction strategies
in `JoinAuxState` across Zipfian foreign-key skew parameters $s \in [0.0, 2.0]$.

## Summary Results Table

| skew_s | policy | max_total_rows | total_decisions | repair_count | invalidate_count | eviction_invalidate_count | evict_inv_rate | oracle_match |
|---:|:---:|---:|---:|---:|---:|---:|---:|:---:|
| 0.00 | fifo | 50 | 88 | 0 | 60 | 45 | 51.14% | yes |
| 0.00 | lru | 50 | 88 | 0 | 60 | 45 | 51.14% | yes |
| 0.00 | lfu | 50 | 88 | 14 | 36 | 26 | 29.55% | yes |
| 0.25 | fifo | 50 | 88 | 0 | 54 | 42 | 47.73% | yes |
| 0.25 | lru | 50 | 88 | 0 | 54 | 42 | 47.73% | yes |
| 0.25 | lfu | 50 | 88 | 17 | 32 | 24 | 27.27% | yes |
| 0.50 | fifo | 50 | 88 | 0 | 59 | 45 | 51.14% | yes |
| 0.50 | lru | 50 | 88 | 0 | 59 | 45 | 51.14% | yes |
| 0.50 | lfu | 50 | 88 | 17 | 37 | 28 | 31.82% | yes |
| 0.75 | fifo | 50 | 88 | 0 | 60 | 44 | 50.00% | yes |
| 0.75 | lru | 50 | 88 | 0 | 60 | 44 | 50.00% | yes |
| 0.75 | lfu | 50 | 88 | 13 | 43 | 30 | 34.09% | yes |
| 1.00 | fifo | 50 | 88 | 0 | 58 | 44 | 50.00% | yes |
| 1.00 | lru | 50 | 88 | 0 | 58 | 44 | 50.00% | yes |
| 1.00 | lfu | 50 | 88 | 11 | 44 | 33 | 37.50% | yes |
| 1.25 | fifo | 50 | 88 | 0 | 60 | 46 | 52.27% | yes |
| 1.25 | lru | 50 | 88 | 0 | 60 | 46 | 52.27% | yes |
| 1.25 | lfu | 50 | 88 | 15 | 40 | 29 | 32.95% | yes |
| 1.50 | fifo | 50 | 88 | 0 | 60 | 44 | 50.00% | yes |
| 1.50 | lru | 50 | 88 | 0 | 60 | 44 | 50.00% | yes |
| 1.50 | lfu | 50 | 88 | 15 | 40 | 27 | 30.68% | yes |
| 1.75 | fifo | 50 | 88 | 0 | 60 | 44 | 50.00% | yes |
| 1.75 | lru | 50 | 88 | 0 | 60 | 44 | 50.00% | yes |
| 1.75 | lfu | 50 | 88 | 13 | 43 | 30 | 34.09% | yes |
| 2.00 | fifo | 50 | 88 | 0 | 57 | 42 | 47.73% | yes |
| 2.00 | lru | 50 | 88 | 0 | 57 | 42 | 47.73% | yes |
| 2.00 | lfu | 50 | 88 | 13 | 42 | 28 | 31.82% | yes |

## Comparative Policy Analysis

| skew_s | FIFO Evict Inval | LRU Evict Inval | LFU Evict Inval | LFU Reduction vs FIFO |
|---:|---:|---:|---:|---:|
| 0.00 | 45 | 45 | 26 | 42.2% |
| 0.25 | 42 | 42 | 24 | 42.9% |
| 0.50 | 45 | 45 | 28 | 37.8% |
| 0.75 | 44 | 44 | 30 | 31.8% |
| 1.00 | 44 | 44 | 33 | 25.0% |
| 1.25 | 46 | 46 | 29 | 37.0% |
| 1.50 | 44 | 44 | 27 | 38.6% |
| 1.75 | 44 | 44 | 30 | 31.8% |
| 2.00 | 42 | 42 | 28 | 33.3% |

## Claim Hygiene Summary

1. **What this artifact supports:**
   - Supports the observation that in this synthetic 3-table snowflake schema under fixed memory capacity (`max_total_rows = 50`), retaining high-multiplicity dimension rows (such as regions and active customers) via frequency-biased LFU eviction reduces `eviction_invalidate_count` by 25.0% to 42.9% relative to FIFO eviction across all tested skew values ($s \in [0.0, 2.0]$).
   - Demonstrates that FIFO and LRU experience higher invalidation rates due to the eviction of structural dimension tables.
   - Verifies numerical agreement with the SQLite oracle across all tested skew levels and eviction policies.

2. **What this artifact does not support:**
   - Does not support the proposition that LFU eliminates invalidations when unique foreign keys exceed cache capacity.
   - Does not support claims regarding performance under dynamic skew drift where key popularity changes over time.
   - Does not evaluate multi-threaded concurrency or lock contention.

3. **Narrowest defensible claim:**
   - Under the declared simulation parameters (seed=42, 60 transactions, fixed `max_total_rows = 50`), frequency-biased LFU reduced `evicted_join_auxiliary_key` invalidation events compared to FIFO across the tested range $s \in [0.0, 2.0]$.

4. **Next test needed:**
   - Evaluate dynamic access pattern shifts (phase-rotated Zipfian distributions) to observe whether static LFU experiences cache pollution and test frequency decay or windowed frequency strategies.
