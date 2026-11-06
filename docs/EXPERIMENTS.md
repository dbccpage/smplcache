# Self-tuning experiments

SmplCache tunes itself from its own receipts. Every change it proposes is
tested on recorded traffic and shown before it takes effect. This page records
what was tried, how it was measured, and what the numbers said, including the
experiments that lost. Code lives in `smplcache/tuning/`; the numbers come from
`python -m smplcache.tuning.experiments --out docs/experiments` and are in
[`experiments/results.json`](experiments/results.json) together with the
receipts of every loop run.

## The benchmark replay and the baselines

One recorded day (`replay.generate_day`, seed 1): 6,000 writes and 2,000 reads
interleaved (3 writes per read), three tables of 400 rows, Zipf skew (s = 1.1)
over rows and over which of the 12 cached shapes is read, a phase change at
write 3,000 (the write mix moves from `orders.amount/status` to
`orders.region` and `inventory`, and the read skew reverses), one table
(`customers`) whose UPDATE events carry no before-image (as Debezium does
under `REPLICA IDENTITY DEFAULT`), and one shape (`MAX`) outside the admitted
fragment. A SQLite database is the system of record; every value served from
cache is compared with a fresh SQL query against it. That comparison is the
**oracle mismatch** column and it must be 0.

Measures: database reads (materializations and uncached reads), rereads
(materializations that follow an invalidation), hit rate, p99 decision latency
(wall clock around `process_event`, on a shared machine; it varied between
0.6 ms and 2.7 ms across runs of identical code and is reported but not
compared), oracle mismatches, collateral flushes, resident groups.

| Baseline | db reads | rereads | hit rate | p99 decision (us) | oracle mismatches | mean resident groups |
| --- | --- | --- | --- | --- | --- | --- |
| uncached | 2000 | 0 | 0.000 | – | 0 | 0 |
| **plain SmplCache** (every shape materialized, per-shape store) | **378** | 293 | **0.811** | 811 | **0** | 120.5 |
| TTL cache-aside (200 events, no certification) | 392 | 307 | 0.804 | – | **1409** of 1608 checks | 120.5 |

Plain SmplCache's 293 rereads are all on the `customers` shapes (no
before-image, `missing_evidence_for_repair`); every `orders` and `inventory`
write that touched a shape was repaired in place. TTL serves a stale value on
88 % of its hits; the certifier never does.

## E1: spectral partitioning and the entropy drift detector

**Hypothesis.** For a store that can only flush whole partitions (tag-based
invalidation), grouping shapes by the eigenmodes of the co-invalidation density
matrix rho = M / tr M produces fewer collateral flushes than grouping by
relation or at random, at the same partition count k. The von Neumann entropy
of rho over a sliding window rises when the write mix changes and can trigger
a repartition proposal.

**Metric.** Rereads and database reads under a partition-granular store;
oracle mismatches must stay 0. Drift: entropy per 1,000-op window, whether the
detector fired, and the verdict of the proposal it triggered.

| Partition (store flushes whole partitions) | db reads | rereads | hit rate | collateral flushes | oracle mismatches |
| --- | --- | --- | --- | --- | --- |
| per relation, k = 3 | 378 | 293 | 0.811 | 0 | 0 |
| spectral (write-touch signal), k = 3 | 636 | 551 | 0.682 | 259 | 0 |
| spectral (invalidation signal), k = 3 | 786 | 701 | 0.607 | 414 | 0 |
| random, k = 3 | 543 | 458 | 0.729 | 168 | 0 |
| spectral (write-touch signal), k = 6 | 378 | 293 | 0.811 | 0 | 0 |
| random, k = 6 | 433 | 348 | 0.784 | 56 | 0 |
| plain (per-shape store) | 378 | 293 | 0.811 | 0 | 0 |

Drift detector (window 400 writes, threshold 0.3 bits, k = 3):

| window start | entropy (bits) | fired | proposal | verdict | authorization |
| --- | --- | --- | --- | --- | --- |
| 0 | 1.581 | reference set | – | – | – |
| 1000–3000 | 1.585–1.606 | no | – | – | – |
| 4000 (first window after the phase change) | 2.141 | **yes** | repartition k = 3 | **refuted**: rereads 17 -> 28 on the previous window | denied |
| 5000–7000 | 2.168–2.205 | no | – | – | – |

Online loop total rereads 272 = static per-relation 272 (the refused proposal
changed nothing). Receipt chain intact (4 receipts).

**Verdict: drop spectral partitioning; flag the drift detector.** On this
workload invalidation is a property of the table (missing before-images), so
grouping by relation is already the collateral-free partition at k = 3, and the
per-shape store that SmplCache uses by default beats every coarser store.
Eigenmodes of the write-touch matrix group shapes that are *repaired* together,
which is not what a flush-granular store needs; the invalidation-only signal is
worse because almost every row of it is zero. Spectral partitioning beat random
at both k and matched per-shape at k = 6, which is the most that can be said.
The entropy signal did move at the phase change (1.6 to 2.1 bits) and nowhere
else, and the loop correctly refused the proposal it produced. It stays as a
diagnostic; a threshold of 0 is rejected by the constructor.

## E2: tree search over the materialized set

**Hypothesis.** A UCT search over add/drop actions, scored by replaying a
recorded segment, finds a materialized set with higher reward than
"materialize everything" and than greedy single-drop, where
reward = database reads saved against uncached - memory_cost x mean resident
groups (memory_cost 0.5).

**Metric.** Reward, database reads and oracle mismatches on a held-out segment
(ops 2,000–4,000, same phase as the search segment 0–2,000) and on the next
phase (ops 4,000–8,000). Search budget 30 rollouts, depth 3.

| segment | configuration | shapes | db reads | hit rate | mean groups | reward | oracle |
| --- | --- | --- | --- | --- | --- | --- | --- |
| held-out | plain (all 11 cacheable) | 11 | 75 | 0.850 | 115.8 | 367.1 | 0 |
| held-out | greedy single-drop | 8 | 113 | 0.774 | 14.5 | **379.8** | 0 |
| held-out | UCT (applied by the loop) | 10 | 111 | 0.778 | 21.4 | 378.3 | 0 |
| next phase | plain | 11 | 261 | 0.739 | 119.8 | 679.1 | 0 |
| next phase | greedy single-drop | 8 | 332 | 0.668 | 14.8 | 660.6 | 0 |
| next phase | UCT | 10 | 301 | 0.699 | 21.6 | **688.2** | 0 |

Loop: 1 proposal, verdict supported (held-out reward 367.1 -> 378.3,
+3.1 %), approved by policy `e2/materialize` (min improvement 2 %), applied.
Search statistics: 28 distinct rollouts for a budget of 30.

**Verdict: flag.** The whole gain is the memory term: both searches drop
`sum_amount_by_customer` (about 100 resident groups) and pay 36–40 extra
database reads per 500 reads for it. With memory_cost 0 plain wins outright.
UCT did not beat greedy on the held-out segment (378.3 vs 379.8) and beat it on
the next phase by keeping two `customers` shapes. The search is kept behind
`config_search` for deployments that declare a memory cost; greedy is the
comparison class it has to beat and so far has not.

## E3: Thompson-sampling admission under a shape cap

**Hypothesis.** With room for only 5 of the 11 cacheable shapes, a Beta
posterior per shape over "an epoch serves at least one hit", learned from the
receipts of a previous window, admits and evicts better than LRU, LFU and a
flat prior.

**Metric.** Database reads and hit rate on the apply segment (ops
4,000–8,000) at cap 5. The prior is learned on ops 0–2,000, evaluated by the
loop on 2,000–4,000, and applied to 4,000–8,000.

| policy (cap 5) | db reads | rereads | hit rate | mean groups | oracle |
| --- | --- | --- | --- | --- | --- |
| no cap (plain) | 261 | 207 | 0.739 | 119.8 | 0 |
| LRU | 442 | 110 | 0.558 | 47.0 | 0 |
| LFU | 419 | 151 | 0.581 | 29.9 | 0 |
| Thompson, flat prior | 415 | 23 | 0.585 | 11.6 | 0 |
| Thompson, learned prior (applied by the loop) | **544** | 1 | **0.456** | 9.8 | 0 |

Loop: the prior was supported on the evaluation segment (db reads 147 -> 142,
+3.4 %) and approved; on the apply segment, which is the other phase, it lost
to every baseline by 25–30 %.

**Verdict: drop.** A prior learned on one phase is confidently wrong on the
next: it refuses to admit the shapes that became hot and keeps serving their
reads from the database (1 reread, 544 database reads). Flat Thompson ties LFU
and both beat LRU, so the sampling itself is fine; the learning is the problem.
Code stays behind `bandit_admission` (off); the right fix would be a posterior
that decays, which is not implemented or measured here.

## E4: mistake memory from REREADs

**Hypothesis.** Clustering `invalidate` / `unsupported` certificates by
(relation, reason, missing evidence) identifies fixes outside the cache that
would remove most rereads, and a counterfactual replay can put a number on
each before a human sees it.

**Metric.** Rereads and database reads on the full day with the fix simulated
(the same events with before-images), versus as recorded. Suggestions are
always `needs_human`.

| suggestion | evidence (counterfactual replay) | verdict | authorization |
| --- | --- | --- | --- |
| `ALTER TABLE customers REPLICA IDENTITY FULL` (296 invalidations on 3 shapes lacked before-images of credit, region, tier) | rereads 293 -> **0**, db reads 378 -> **85**, oracle mismatches 0 | supported | needs human |
| admit `max_amount_by_region` with `SortedMultisetExtremumCache` | 74 database reads could become hits; not replayable without the auxiliary state | underdetermined | needs human |

**Verdict: keep.** This is the largest effect in the whole study (a 4.4x cut
in database reads) and it is a one-line change on the source database, found
from receipts alone. Nothing was applied by the loop (`applied: 0`); the
suggestions and their evidence are in `results.json` for review. An earlier
version proposed the same table three times (one per missing-column set); the
proposer now merges clusters per table.

## E5: counterfactual replay from receipts

**Hypothesis.** Because admission only chooses which shapes are resident, the
outcome of a capped admission policy can be estimated exactly from the plain
run's receipts (one per read, one per certified invalidation) without
re-running the certifier or the database.

**Metric.** Estimated versus actual database reads and hits for LRU and LFU at
cap 5 over the full day; estimate time versus replay time.

| policy (cap 5) | estimated db reads | actual | estimated hits | actual | estimate time |
| --- | --- | --- | --- | --- | --- |
| LRU | 792 | 792 | 1208 | 1208 | 2 ms |
| LFU | 735 | 735 | 1265 | 1265 | 7 ms |

**Verdict: keep.** Exact for policies that depend only on reads and
invalidations, in milliseconds instead of a 6–20 s replay. It does not cover
policies that look at values or group counts, and it says nothing about
correctness (that is the oracle's job, and the oracle needs the real replay).

## E6, E7

Not run. E6 (persistent homology of the co-invalidation complex) would be
compared against the E1 entropy signal, which already fires exactly once at
the phase change on this workload; a second detector needs a workload on which
the first one fails. E7 was not attempted.

## What these numbers do not say

They describe one synthetic day. They do not show that any learned policy
improves a real workload, and three of five experiments lost to the plain
cache or to a simpler policy. The one clear win (E4) is a recommendation to a
human about the source database, not a change to the cache. All reported
runs had 0 oracle mismatches; the oracle also caught one harness bug during
the work (see `MUTATIONS.md`).
