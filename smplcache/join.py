# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
Upgrade 3: Foreign-key JOIN certification and incremental repair with explicit
auxiliary join-multiplicity / semi-join state (`JoinAuxState`) and memory /
cardinality eviction bounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence
import zlib

from smplcache.smplcache import (
    AggFn,
    AggregateCache,
    Certificate,
    Decision,
    DecisionKind,
    Predicate,
    WriteEvent,
    _ambiguous_relation,
    _decimal_add,
    _normalize_group_atom,
    _checked_group_atom,
    _aggregate_decimal,
    _to_decimal,
    _values_equal,
    normalize_relation,
)

JoinType = Literal["INNER", "LEFT"]
EvictionPolicy = Literal["fifo", "lru", "lfu", "w_lfu", "wtinylfu"]


class CountMinSketch:
    """
    Compact deterministic Count-Min Sketch with conservative update and periodic
    multiplicative aging (halving). Provides O(depth * width) bounded-memory
    frequency estimation across both resident and previously evicted keys.
    """

    _ROW_PRIMES: tuple[int, ...] = (
        0x9E3779B9,
        0x85EBCA6B,
        0xC2B2AE35,
        0x27D4EB2F,
        0x165667B1,
        0xD3A2646C,
        0xFD7046C5,
        0xB55A4F09,
    )

    def __init__(
        self,
        *,
        width: int = 128,
        depth: int = 4,
        reset_window: int | None = 64,
        decay_factor: float = 0.5,
        max_counter: int = 64,
    ) -> None:
        if width <= 0:
            raise ValueError(f"CountMinSketch width must be >= 1, got {width}")
        if depth <= 0:
            raise ValueError(f"CountMinSketch depth must be >= 1, got {depth}")
        if reset_window is not None and reset_window <= 0:
            raise ValueError(
                f"CountMinSketch reset_window must be >= 1, got {reset_window}"
            )
        if not (0.0 <= float(decay_factor) <= 1.0):
            raise ValueError(
                f"CountMinSketch decay_factor must be in [0.0, 1.0], got {decay_factor}"
            )
        self.width = int(width)
        self.depth = int(depth)
        self.reset_window = int(reset_window) if reset_window is not None else None
        self.decay_factor = float(decay_factor)
        self.max_counter = max(1, int(max_counter))
        self._table: list[list[int]] = [[0] * self.width for _ in range(self.depth)]
        self._additions: int = 0
        self._total_additions: int = 0
        self._decay_epochs: int = 0

    @property
    def decay_epochs(self) -> int:
        return self._decay_epochs

    @property
    def total_additions(self) -> int:
        return self._total_additions

    def _hash(self, relation: str, pk_val: Any, row_idx: int) -> int:
        prime = self._ROW_PRIMES[row_idx % len(self._ROW_PRIMES)]
        raw = f"{row_idx}:{relation}:{pk_val}".encode("utf-8")
        crc = zlib.crc32(raw, prime & 0xFFFFFFFF) & 0xFFFFFFFF
        mixed = ((crc ^ prime) * 0x45D9F3B) & 0xFFFFFFFF
        return mixed % self.width

    def increment(self, relation: str, pk_val: Any, amount: int = 1) -> int:
        if amount <= 0:
            return self.estimate(relation, pk_val)
        rel = normalize_relation(relation)
        norm_pk = _normalize_group_atom(pk_val)
        indices = [self._hash(rel, norm_pk, i) for i in range(self.depth)]
        cur_vals = [self._table[i][indices[i]] for i in range(self.depth)]
        min_v = min(cur_vals)
        target_v = min(self.max_counter, min_v + amount)
        for i in range(self.depth):
            idx = indices[i]
            if self._table[i][idx] < target_v:
                self._table[i][idx] = target_v

        self._additions += 1
        self._total_additions += 1
        if self.reset_window is not None and self._additions >= self.reset_window:
            self.decay(self.decay_factor)
        return self.estimate(rel, norm_pk)

    def estimate(self, relation: str, pk_val: Any) -> int:
        rel = normalize_relation(relation)
        norm_pk = _normalize_group_atom(pk_val)
        return min(
            self._table[i][self._hash(rel, norm_pk, i)] for i in range(self.depth)
        )

    def decay(self, factor: float | None = None) -> int:
        eff_factor = float(self.decay_factor) if factor is None else float(factor)
        if not (0.0 <= eff_factor <= 1.0):
            raise ValueError(f"Decay factor must be in [0.0, 1.0], got {eff_factor}")
        decayed_cells = 0
        for row in self._table:
            for j, val in enumerate(row):
                if val > 0:
                    row[j] = int(val * eff_factor)
                    decayed_cells += 1
        self._additions = 0
        self._decay_epochs += 1
        return decayed_cells

    def clone(self) -> CountMinSketch:
        copy_cms = CountMinSketch(
            width=self.width,
            depth=self.depth,
            reset_window=self.reset_window,
            decay_factor=self.decay_factor,
            max_counter=self.max_counter,
        )
        copy_cms._table = [list(r) for r in self._table]
        copy_cms._additions = self._additions
        copy_cms._total_additions = self._total_additions
        copy_cms._decay_epochs = self._decay_epochs
        return copy_cms


class _EvictedJoinKeyError(Exception):
    """Raised when join evaluation or fanout resolution hits an evicted key or bucket."""


@dataclass(frozen=True)
class JoinClause:
    """Represents a single foreign-key equijoin (`INNER JOIN` or `LEFT JOIN`)."""

    right_relation: str
    left_qualifier: str
    left_key: str
    right_key: str
    join_type: JoinType = "INNER"
    right_alias: str | None = None

    @property
    def effective_right_qualifier(self) -> str:
        return (self.right_alias or normalize_relation(self.right_relation)).lower()


@dataclass(frozen=True)
class MultiTableJoinShape:
    """
    Typed multi-table foreign-key JOIN query shape for Upgrade 3 certification
    and incremental maintenance.
    """

    base_relation: str
    base_alias: str | None = None
    joins: tuple[JoinClause, ...] = ()
    agg_fn: AggFn | None = None
    agg_col: tuple[str, str] | None = None
    group_cols: tuple[tuple[str, str], ...] = ()
    predicate_terms: tuple[tuple[str, str, str, Any], ...] = ()
    projection_cols: tuple[tuple[str, str], ...] = ()
    security_cols: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    name: str = "join_shape"
    is_supported: bool = True
    unsupported_reason: str = ""

    @property
    def effective_base_qualifier(self) -> str:
        return (self.base_alias or normalize_relation(self.base_relation)).lower()

    def qualifier_to_relation(self, qualifier: str) -> str:
        q = normalize_relation(qualifier)
        base_rel = normalize_relation(self.base_relation)
        if q in (base_rel, (self.base_alias or "").lower()):
            return base_rel
        for j in self.joins:
            r_rel = normalize_relation(j.right_relation)
            if q in (r_rel, (j.right_alias or "").lower()):
                return r_rel
        return q

    def relations(self) -> frozenset[str]:
        rels = {normalize_relation(self.base_relation)}
        for j in self.joins:
            rels.add(normalize_relation(j.right_relation))
        return frozenset(rels)

    def table_security_cols(self, relation: str) -> frozenset[str]:
        target_rel = normalize_relation(relation)
        return frozenset(
            c.lower()
            for q, c in self.security_cols
            if self.qualifier_to_relation(q) == target_rel
        )

    def table_dependency_set(self, relation: str) -> dict[str, frozenset[str]]:
        """
        Decompose `relation`'s dependency columns in this query into:
        - `read_cols`: aggregate argument and projected columns
        - `join_keys`: equijoin columns on either side of `JOIN ... ON`
        - `filter_cols`: `WHERE` predicate columns
        - `group_cols`: `GROUP BY` columns
        - `security_cols`: row-level security columns
        """
        target_rel = normalize_relation(relation)
        read_cols: set[str] = set()
        join_keys: set[str] = set()
        filter_cols: set[str] = set()
        group_cols: set[str] = set()
        security_cols: set[str] = set()

        for j in self.joins:
            if self.qualifier_to_relation(j.left_qualifier) == target_rel:
                join_keys.add(j.left_key.lower())
            if normalize_relation(j.right_relation) == target_rel:
                join_keys.add(j.right_key.lower())

        if (
            self.agg_col is not None
            and self.qualifier_to_relation(self.agg_col[0]) == target_rel
        ):
            read_cols.add(self.agg_col[1].lower())

        for q, c in self.projection_cols:
            if self.qualifier_to_relation(q) == target_rel:
                read_cols.add(c.lower())

        for q, c in self.group_cols:
            if self.qualifier_to_relation(q) == target_rel:
                group_cols.add(c.lower())

        for q, c, _, _ in self.predicate_terms:
            if self.qualifier_to_relation(q) == target_rel:
                filter_cols.add(c.lower())

        for q, c in self.security_cols:
            if self.qualifier_to_relation(q) == target_rel:
                security_cols.add(c.lower())

        return {
            "read_cols": frozenset(read_cols),
            "join_keys": frozenset(join_keys),
            "filter_cols": frozenset(filter_cols),
            "group_cols": frozenset(group_cols),
            "security_cols": frozenset(security_cols),
        }

    def dependency_set(self) -> dict[str, dict[str, frozenset[str]]]:
        """Return the structured dependency set for every participating table."""
        return {rel: self.table_dependency_set(rel) for rel in sorted(self.relations())}

    def table_fingerprint(self, relation: str) -> frozenset[str]:
        """
        Return all columns of `relation` that the join query result depends on,
        derived directly from `table_dependency_set(relation)`.
        """
        deps = self.table_dependency_set(relation)
        return (
            deps["read_cols"]
            | deps["join_keys"]
            | deps["filter_cols"]
            | deps["group_cols"]
            | deps["security_cols"]
        )

    def table_required_evidence(
        self, relation: str, pk_col: str = "id"
    ) -> frozenset[str]:
        """
        Return the columns required in CDC `old`/`new` payloads on `relation`
        to perform certified join repair and maintain `JoinAuxState`.
        """
        target_rel = normalize_relation(relation)
        if target_rel not in self.relations():
            return frozenset()
        cols: set[str] = {pk_col.lower()}
        for j in self.joins:
            if self.qualifier_to_relation(j.left_qualifier) == target_rel:
                cols.add(j.left_key.lower())
            if normalize_relation(j.right_relation) == target_rel:
                cols.add(j.right_key.lower())

        if (
            self.agg_col is not None
            and self.qualifier_to_relation(self.agg_col[0]) == target_rel
        ):
            cols.add(self.agg_col[1].lower())

        for q, c in self.group_cols:
            if self.qualifier_to_relation(q) == target_rel:
                cols.add(c.lower())

        for q, c, _, _ in self.predicate_terms:
            if self.qualifier_to_relation(q) == target_rel:
                cols.add(c.lower())

        return frozenset(cols)


@dataclass
class JoinAuxState:
    """
    Explicit auxiliary join-multiplicity and semi-join index state required
    to certify and repair multi-table foreign-key `JOIN` queries across
    fact-table updates, foreign-key reparenting, and 1-to-k dimension-table
    updates.

    Supports configurable memory/cardinality bounds (`max_total_rows`,
    `max_rows_per_table`, `max_fk_fanout`). Evicted PKs and overflowed/evicted
    FK buckets are recorded in `evicted_pks` and `evicted_fk_buckets` so any
    subsequent event touching an evicted key deterministically downgrades to
    `Decision.invalidate("evicted_join_auxiliary_key")`.
    """

    shape: MultiTableJoinShape
    primary_keys: dict[str, str] = field(default_factory=dict)
    table_rows: dict[str, dict[Any, dict[str, Any]]] = field(default_factory=dict)
    # Maps (child_relation, child_fk_col) -> {normalized_fk_val -> set of child PKs}
    fk_index: dict[tuple[str, str], dict[Any, set[Any]]] = field(default_factory=dict)
    is_invalidated: bool = False
    max_total_rows: int | None = None
    max_rows_per_table: Mapping[str, int] | int | None = None
    max_fk_fanout: int | None = None
    evicted_pks: dict[str, set[Any]] = field(default_factory=dict)
    evicted_fk_buckets: dict[tuple[str, str], set[Any]] = field(default_factory=dict)
    eviction_policy: EvictionPolicy = "fifo"
    lfu_decay_window: int | None = None
    lfu_decay_factor: float = 0.5
    window_ratio: float = 0.25
    adapt_epoch_size: int = 16
    min_decay_window: int = 8
    max_decay_window: int = 128
    cms_width: int = 128
    cms_depth: int = 4
    extra_shapes: tuple[MultiTableJoinShape, ...] = ()
    _access_clock: int = field(default=0, init=False, repr=False)
    _insertion_clock: int = field(default=0, init=False, repr=False)
    _decay_epochs: int = field(default=0, init=False, repr=False)
    _row_last_access: dict[str, dict[Any, int]] = field(
        default_factory=dict, init=False, repr=False
    )
    _row_access_freq: dict[str, dict[Any, int]] = field(
        default_factory=dict, init=False, repr=False
    )
    _row_inserted_at: dict[str, dict[Any, int]] = field(
        default_factory=dict, init=False, repr=False
    )
    _cms: CountMinSketch = field(init=False, repr=False)
    _window_ratio: float = field(default=0.25, init=False, repr=False)
    _hill_climb_dir: int = field(default=1, init=False, repr=False)
    _prev_epoch_score: float = field(default=0.5, init=False, repr=False)
    _epoch_recency_hits: int = field(default=0, init=False, repr=False)
    _epoch_main_hits: int = field(default=0, init=False, repr=False)
    _epoch_eviction_misses: int = field(default=0, init=False, repr=False)
    _adapt_epochs: int = field(default=0, init=False, repr=False)
    _window_ratio_history: list[float] = field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        pol = str(self.eviction_policy).lower()
        if pol not in ("fifo", "lru", "lfu", "w_lfu", "wtinylfu"):
            raise ValueError(f"Unknown eviction policy: {self.eviction_policy}")
        self.eviction_policy = pol  # type: ignore[assignment]
        if self.lfu_decay_window is not None and self.lfu_decay_window <= 0:
            raise ValueError(
                f"lfu_decay_window must be >= 1, got {self.lfu_decay_window}"
            )
        if not (0.0 <= float(self.lfu_decay_factor) <= 1.0):
            raise ValueError(
                f"lfu_decay_factor must be in [0.0, 1.0], got {self.lfu_decay_factor}"
            )
        if not (0.05 <= float(self.window_ratio) <= 0.90):
            raise ValueError(
                f"window_ratio must be in [0.05, 0.90], got {self.window_ratio}"
            )
        if self.adapt_epoch_size <= 0:
            raise ValueError(
                f"adapt_epoch_size must be >= 1, got {self.adapt_epoch_size}"
            )
        if self.eviction_policy in ("w_lfu", "wtinylfu") and (
            self.lfu_decay_window is None
        ):
            self.lfu_decay_window = 32
        self._window_ratio = float(self.window_ratio)
        self._window_ratio_history = [self._window_ratio]
        self._cms = CountMinSketch(
            width=self.cms_width,
            depth=self.cms_depth,
            reset_window=self.lfu_decay_window or 64,
            decay_factor=self.lfu_decay_factor,
        )
        for s in self.all_shapes():
            for rel in s.relations():
                self.primary_keys.setdefault(rel, "id")
                self.table_rows.setdefault(rel, {})
                self.evicted_pks.setdefault(rel, set())
                self._row_last_access.setdefault(rel, {})
                self._row_access_freq.setdefault(rel, {})
                self._row_inserted_at.setdefault(rel, {})
            for j in s.joins:
                l_rel = s.qualifier_to_relation(j.left_qualifier)
                l_key = j.left_key.lower()
                self.fk_index.setdefault((l_rel, l_key), {})
                self.evicted_fk_buckets.setdefault((l_rel, l_key), set())
        for raw_rel, rows_map in list(self.table_rows.items()):
            norm_rel = normalize_relation(raw_rel)
            if not rows_map:
                continue
            normalized_rows: dict[Any, dict[str, Any]] = {}
            for raw_pk, row in rows_map.items():
                lower_row = {str(k).lower(): v for k, v in row.items()}
                norm_pk = _normalize_group_atom(raw_pk)
                normalized_rows[norm_pk] = lower_row
                self._index_row_fks(norm_rel, norm_pk, lower_row)
            self.table_rows[norm_rel] = normalized_rows

    def all_shapes(self) -> tuple[MultiTableJoinShape, ...]:
        """Return `(self.shape, *self.extra_shapes)` managed by this auxiliary state."""
        return (self.shape, *self.extra_shapes)

    def all_relations(self) -> frozenset[str]:
        """Return the union of participating relations across all managed shapes."""
        rels: set[str] = set()
        for s in self.all_shapes():
            rels.update(s.relations())
        return frozenset(rels)

    @property
    def cms(self) -> CountMinSketch:
        """Return the Count-Min Sketch frequency estimator."""
        return self._cms

    @property
    def current_window_ratio(self) -> float:
        """Return the current W-TinyLFU recency window ratio in `[0.05, 0.80]`."""
        return self._window_ratio

    @property
    def adapt_epochs(self) -> int:
        """Return the number of hill-climbing window adaptation epochs executed."""
        return self._adapt_epochs

    @property
    def window_ratio_history(self) -> tuple[float, ...]:
        """Return the history of adapted `_window_ratio` values across epochs."""
        return tuple(self._window_ratio_history)

    @property
    def decay_epochs(self) -> int:
        """Return the number of frequency decay epochs executed on this state."""
        return self._decay_epochs

    def decay_frequencies(self, factor: float | None = None) -> int:
        """
        Multiplicatively decay accumulated row access frequencies across all tables
        by `factor` (defaults to `self.lfu_decay_factor`), aging out stale historical
        access counts to mitigate LFU cache pollution across workload phase shifts.
        Also decays the internal Count-Min Sketch.
        Returns the number of row frequency entries that were updated or pruned.
        """
        eff_factor = float(self.lfu_decay_factor) if factor is None else float(factor)
        if not (0.0 <= eff_factor <= 1.0):
            raise ValueError(f"Decay factor must be in [0.0, 1.0], got {eff_factor}")
        decayed_count = 0
        for freq_map in self._row_access_freq.values():
            for pk in list(freq_map.keys()):
                old_f = freq_map[pk]
                new_f = int(old_f * eff_factor)
                decayed_count += 1
                if new_f <= 0:
                    freq_map.pop(pk, None)
                else:
                    freq_map[pk] = new_f
        self._cms.decay(eff_factor)
        self._decay_epochs += 1
        return decayed_count

    def adapt_window_size(self, observed_hit_rate: float | None = None) -> float:
        """
        Execute one W-TinyLFU hill-climbing window adaptation step, adjusting
        `_window_ratio` in `[0.05, 0.80]` and scaling `lfu_decay_window` in
        `[min_decay_window, max_decay_window]`.
        """
        total_hits = self._epoch_recency_hits + self._epoch_main_hits
        total_events = max(1, total_hits + self._epoch_eviction_misses)
        hit_rate = (
            float(observed_hit_rate)
            if observed_hit_rate is not None
            else (total_hits / total_events)
        )
        recency_share = (
            (self._epoch_recency_hits / total_hits) if total_hits > 0 else 0.5
        )
        delta = hit_rate - self._prev_epoch_score

        if observed_hit_rate is not None:
            if delta < -1e-6:
                self._hill_climb_dir = -self._hill_climb_dir
        else:
            if self._epoch_eviction_misses > 0 or recency_share > 0.60:
                self._hill_climb_dir = 1
            elif recency_share < 0.35 and delta >= -1e-6:
                self._hill_climb_dir = -1
            elif delta < -1e-6:
                self._hill_climb_dir = -self._hill_climb_dir

        step = 0.05
        new_ratio = round(
            min(0.80, max(0.05, self._window_ratio + self._hill_climb_dir * step)),
            4,
        )
        self._window_ratio = new_ratio
        norm_w = (self._window_ratio - 0.05) / 0.75
        span = max(1, self.max_decay_window - self.min_decay_window)
        adapted_win = int(round(self.max_decay_window - norm_w * span))
        self.lfu_decay_window = max(
            self.min_decay_window, min(self.max_decay_window, adapted_win)
        )
        self._cms.reset_window = self.lfu_decay_window

        self._prev_epoch_score = hit_rate
        self._adapt_epochs += 1
        self._window_ratio_history.append(self._window_ratio)
        self._epoch_recency_hits = 0
        self._epoch_main_hits = 0
        self._epoch_eviction_misses = 0
        return self._window_ratio

    def record_decision_outcome(self, decision: Decision) -> None:
        """
        Record certification/repair outcome telemetry for W-TinyLFU hill-climbing
        window adaptation.
        """
        if decision.kind == DecisionKind.INVALIDATE and (
            decision.reason == "evicted_join_auxiliary_key"
        ):
            self._epoch_eviction_misses += 1

    def _record_insertion(self, relation: str, pk_val: Any) -> None:
        rel = normalize_relation(relation)
        norm_pk = _normalize_group_atom(pk_val)
        if norm_pk not in self._row_inserted_at.setdefault(rel, {}):
            self._insertion_clock += 1
            self._row_inserted_at[rel][norm_pk] = self._insertion_clock

    def record_access(self, relation: str, pk_val: Any) -> None:
        """
        Record logical access recency and frequency for a row in `table_rows[relation]`.
        When windowed LFU decay (`w_lfu`) or adaptive W-TinyLFU (`wtinylfu`) is active,
        updates the `CountMinSketch` and triggers periodic frequency decay and
        hill-climbing window adaptation.
        """
        rel = normalize_relation(relation)
        norm_pk = _normalize_group_atom(pk_val)
        if norm_pk not in self.table_rows.get(rel, {}):
            return
        prev_touch = self._row_last_access.get(rel, {}).get(norm_pk, 0)
        self._access_clock += 1
        if prev_touch > 0:
            lag = self._access_clock - prev_touch
            win_bound = max(
                2, int(round(max(4, self.total_rows()) * self._window_ratio))
            )
            if lag <= win_bound:
                self._epoch_recency_hits += 1
            else:
                self._epoch_main_hits += 1

        cms_epochs_before = self._cms.decay_epochs
        self._cms.increment(rel, norm_pk)
        if (
            self._cms.decay_epochs > cms_epochs_before
            and self.eviction_policy == "wtinylfu"
        ):
            for freq_map in self._row_access_freq.values():
                for pk in list(freq_map.keys()):
                    new_f = int(freq_map[pk] * self.lfu_decay_factor)
                    if new_f <= 0:
                        freq_map.pop(pk, None)
                    else:
                        freq_map[pk] = new_f
            self._decay_epochs += 1
        elif (
            self.eviction_policy == "w_lfu"
            or (
                self.eviction_policy != "wtinylfu" and self.lfu_decay_window is not None
            )
        ) and self.lfu_decay_window is not None:
            if self._access_clock % self.lfu_decay_window == 0:
                self.decay_frequencies(self.lfu_decay_factor)

        self._row_last_access.setdefault(rel, {})[norm_pk] = self._access_clock
        freq_dict = self._row_access_freq.setdefault(rel, {})
        freq_dict[norm_pk] = freq_dict.get(norm_pk, 0) + 1

        if (
            self.eviction_policy == "wtinylfu"
            and self._access_clock % self.adapt_epoch_size == 0
        ):
            self.adapt_window_size()

    def _resident_multiplicity_for_shape(
        self,
        shape: MultiTableJoinShape,
        relation: str,
        pk_val: Any,
    ) -> int:
        target_rel = normalize_relation(relation)
        if target_rel not in shape.relations():
            return 0
        base_rel = normalize_relation(shape.base_relation)
        if target_rel == base_rel:
            return 0
        norm_pk = _normalize_group_atom(pk_val)
        if norm_pk is None or norm_pk is _normalize_group_atom(None):
            return 0

        touched_by_rel: dict[str, set[Any]] = {target_rel: {norm_pk}}
        changed = True
        while changed:
            changed = False
            for j in reversed(shape.joins):
                r_rel = normalize_relation(j.right_relation)
                l_rel = shape.qualifier_to_relation(j.left_qualifier)
                l_key = j.left_key.lower()
                r_key = j.right_key.lower()
                if r_rel not in touched_by_rel:
                    continue
                r_pk_col = self.pk_col_for(r_rel)
                if r_key == r_pk_col:
                    r_vals = touched_by_rel[r_rel]
                else:
                    r_vals = {
                        _normalize_group_atom(r_row.get(r_key))
                        for r_pk, r_row in self.table_rows.get(r_rel, {}).items()
                        if r_pk in touched_by_rel[r_rel]
                        and r_row.get(r_key) is not None
                    }
                bucket_map = self.fk_index.get((l_rel, l_key), {})
                l_rows = self.table_rows.get(l_rel, {})
                l_pks: set[Any] = set()
                for rv in r_vals:
                    for child_pk in bucket_map.get(rv, set()):
                        if child_pk in l_rows:
                            l_pks.add(child_pk)
                prev_l = touched_by_rel.setdefault(l_rel, set())
                if not (l_pks <= prev_l):
                    prev_l.update(l_pks)
                    changed = True

        resident_refs = sum(
            len(pks) for rel_k, pks in touched_by_rel.items() if rel_k != target_rel
        )
        return 1 + resident_refs

    def _resident_multiplicity(self, relation: str, pk_val: Any) -> int:
        """
        Return the structural join multiplicity weight for `(relation, pk_val)`
        among currently resident rows in `table_rows` and `fk_index` across all
        managed shapes (`self.shape` and `self.extra_shapes`). Base fact rows
        return 0; dimension rows return `sum_{q} (1 + resident_refs_q)`.
        """
        if not self.extra_shapes:
            return self._resident_multiplicity_for_shape(self.shape, relation, pk_val)
        return sum(
            self._resident_multiplicity_for_shape(s, relation, pk_val)
            for s in self.all_shapes()
        )

    def _select_eviction_candidate(self, relation: str) -> Any:
        rel = normalize_relation(relation)
        rows = self.table_rows.get(rel, {})
        if not rows:
            return None
        policy = self.eviction_policy.lower()
        if policy == "fifo":
            ins_map = self._row_inserted_at.get(rel, {})
            return min(rows.keys(), key=lambda pk: (ins_map.get(pk, 0), str(pk)))
        if policy == "lru":
            access_map = self._row_last_access.get(rel, {})
            ins_map = self._row_inserted_at.get(rel, {})
            return min(
                rows.keys(),
                key=lambda pk: (access_map.get(pk, 0), ins_map.get(pk, 0), str(pk)),
            )
        if policy in ("lfu", "w_lfu"):
            freq_map = self._row_access_freq.get(rel, {})
            access_map = self._row_last_access.get(rel, {})
            ins_map = self._row_inserted_at.get(rel, {})

            def _lfu_score(pk: Any) -> tuple[int, int, int, str]:
                mult = self._resident_multiplicity(rel, pk)
                freq = freq_map.get(pk, 0) + mult
                last_touch = access_map.get(pk, 0)
                ins = ins_map.get(pk, 0)
                return (freq, last_touch, ins, str(pk))

            return min(rows.keys(), key=_lfu_score)
        if policy == "wtinylfu":
            freq_map = self._row_access_freq.get(rel, {})
            access_map = self._row_last_access.get(rel, {})
            ins_map = self._row_inserted_at.get(rel, {})
            row_keys = list(rows.keys())
            if len(row_keys) <= 1:
                return row_keys[0]
            by_recency = sorted(
                row_keys,
                key=lambda pk: (access_map.get(pk, 0), ins_map.get(pk, 0), str(pk)),
                reverse=True,
            )
            win_cnt = max(
                1,
                min(len(row_keys) - 1, int(round(len(row_keys) * self._window_ratio))),
            )
            window_pks = set(by_recency[:win_cnt])

            def _wtinylfu_score(pk: Any) -> tuple[int, int, int, int, int, str]:
                mult = self._resident_multiplicity(rel, pk)
                in_win = 1 if pk in window_pks else 0
                cms_est = self._cms.estimate(rel, pk)
                exact_f = freq_map.get(pk, 0)
                score = cms_est + exact_f + (mult * 2)
                last_touch = access_map.get(pk, 0)
                ins = ins_map.get(pk, 0)
                return (
                    1 if mult > 0 else 0,
                    in_win,
                    score,
                    last_touch,
                    ins,
                    str(pk),
                )

            return min(row_keys, key=_wtinylfu_score)
        return next(iter(rows))

    def _select_global_eviction_candidate(self) -> tuple[str, Any] | None:
        non_empty = [r for r, m in self.table_rows.items() if m]
        if not non_empty:
            return None
        policy = self.eviction_policy.lower()
        if policy == "fifo":

            def _rel_fifo_key(r: str) -> tuple[int, str]:
                cand = self._select_eviction_candidate(r)
                ins = self._row_inserted_at.get(r, {}).get(cand, 0)
                return (ins, str(cand))

            target_rel = min(non_empty, key=_rel_fifo_key)
            return (target_rel, self._select_eviction_candidate(target_rel))
        elif policy == "lru":

            def _rel_lru_key(r: str) -> tuple[int, int, str]:
                cand = self._select_eviction_candidate(r)
                touch = self._row_last_access.get(r, {}).get(cand, 0)
                ins = self._row_inserted_at.get(r, {}).get(cand, 0)
                return (touch, ins, str(cand))

            target_rel = min(non_empty, key=_rel_lru_key)
            return (target_rel, self._select_eviction_candidate(target_rel))
        elif policy in ("lfu", "w_lfu"):

            def _rel_lfu_key(r: str) -> tuple[int, int, int, str]:
                cand = self._select_eviction_candidate(r)
                mult = self._resident_multiplicity(r, cand)
                freq = self._row_access_freq.get(r, {}).get(cand, 0) + mult
                touch = self._row_last_access.get(r, {}).get(cand, 0)
                ins = self._row_inserted_at.get(r, {}).get(cand, 0)
                return (freq, touch, ins, str(cand))

            target_rel = min(non_empty, key=_rel_lfu_key)
            return (target_rel, self._select_eviction_candidate(target_rel))
        elif policy == "wtinylfu":
            all_entries: list[tuple[str, Any, int]] = []
            for r in non_empty:
                for pk in self.table_rows[r]:
                    all_entries.append((r, pk, self._resident_multiplicity(r, pk)))
            if not all_entries:
                return None
            if len(all_entries) == 1:
                return (all_entries[0][0], all_entries[0][1])

            unref_entries = [(r, pk) for r, pk, mult in all_entries if mult == 0]
            pool_for_window = (
                unref_entries
                if len(unref_entries) > 1
                else [(r, pk) for r, pk, _ in all_entries]
            )
            by_recency = sorted(
                pool_for_window,
                key=lambda item: (
                    self._row_last_access.get(item[0], {}).get(item[1], 0),
                    self._row_inserted_at.get(item[0], {}).get(item[1], 0),
                    item[0],
                    str(item[1]),
                ),
                reverse=True,
            )
            win_cnt = max(
                1,
                min(
                    len(pool_for_window) - 1,
                    int(round(len(pool_for_window) * self._window_ratio)),
                ),
            )
            window_set = set(by_recency[:win_cnt])

            def _global_wtinylfu_key(
                entry: tuple[str, Any, int],
            ) -> tuple[int, int, int, int, int, str, str]:
                r, pk, mult = entry
                in_win = 1 if (r, pk) in window_set else 0
                cms_est = self._cms.estimate(r, pk)
                exact_f = self._row_access_freq.get(r, {}).get(pk, 0)
                score = cms_est + exact_f + (mult * 2)
                touch = self._row_last_access.get(r, {}).get(pk, 0)
                ins = self._row_inserted_at.get(r, {}).get(pk, 0)
                return (
                    1 if mult > 0 else 0,
                    in_win,
                    score,
                    touch,
                    ins,
                    r,
                    str(pk),
                )

            victim = min(all_entries, key=_global_wtinylfu_key)
            return (victim[0], victim[1])
        else:
            target_rel = max(non_empty, key=lambda r: len(self.table_rows[r]))
            return (target_rel, self._select_eviction_candidate(target_rel))

    def pk_col_for(self, relation: str) -> str:
        rel = normalize_relation(relation)
        return self.primary_keys.get(rel, "id").lower()

    def total_rows(self) -> int:
        return sum(len(rows) for rows in self.table_rows.values())

    def max_rows_for(self, relation: str) -> int | None:
        rel = normalize_relation(relation)
        if isinstance(self.max_rows_per_table, int):
            return self.max_rows_per_table
        if isinstance(self.max_rows_per_table, Mapping):
            for k, v in self.max_rows_per_table.items():
                if normalize_relation(k) == rel:
                    return int(v)
        return None

    def evict_row(self, relation: str, pk_val: Any) -> bool:
        """
        Evict `pk_val` from `table_rows[relation]`, recording the eviction in
        `evicted_pks[relation]` and tainting any foreign-key bucket in
        `evicted_fk_buckets` that referenced `pk_val`.
        """
        rel = normalize_relation(relation)
        norm_pk = _normalize_group_atom(pk_val)
        self.evicted_pks.setdefault(rel, set()).add(norm_pk)
        if rel in self._row_last_access:
            self._row_last_access[rel].pop(norm_pk, None)
        if rel in self._row_access_freq:
            self._row_access_freq[rel].pop(norm_pk, None)
        if rel in self._row_inserted_at:
            self._row_inserted_at[rel].pop(norm_pk, None)
        existing = self.table_rows.get(rel, {}).pop(norm_pk, None)
        if existing is not None:
            for child_rel, fk_col in self.fk_index:
                if child_rel == rel:
                    raw_fk = existing.get(fk_col)
                    if raw_fk is not None:
                        norm_fk = _normalize_group_atom(raw_fk)
                        self.evicted_fk_buckets.setdefault(
                            (child_rel, fk_col), set()
                        ).add(norm_fk)
            self._unindex_row_fks(rel, norm_pk, existing)
            return True
        return False

    def evict_fk_bucket(self, child_relation: str, fk_col: str, fk_val: Any) -> None:
        """
        Evict the foreign-key multiplicity bucket `(child_relation, fk_col)[fk_val]`
        and mark it in `evicted_fk_buckets`.
        """
        key_pair = (normalize_relation(child_relation), fk_col.lower())
        norm_fk = _normalize_group_atom(fk_val)
        self.evicted_fk_buckets.setdefault(key_pair, set()).add(norm_fk)
        bucket_map = self.fk_index.get(key_pair)
        if bucket_map is not None:
            bucket_map.pop(norm_fk, None)

    def _enforce_capacity_bounds(self, preferred_rel: str | None = None) -> None:
        for rel, rows_map in list(self.table_rows.items()):
            tbl_limit = self.max_rows_for(rel)
            if tbl_limit is not None:
                bound = max(0, tbl_limit)
                while len(rows_map) > bound:
                    victim_pk = self._select_eviction_candidate(rel)
                    if victim_pk is None:
                        break
                    self.evict_row(rel, victim_pk)

        if self.max_total_rows is not None:
            total_bound = max(0, self.max_total_rows)
            while self.total_rows() > total_bound:
                cand = self._select_global_eviction_candidate()
                if cand is None:
                    break
                target_rel, victim_pk = cand
                self.evict_row(target_rel, victim_pk)

    @classmethod
    def from_tables(
        cls,
        shape: MultiTableJoinShape,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        primary_keys: Mapping[str, str] | None = None,
        *,
        eviction_policy: EvictionPolicy = "fifo",
        lfu_decay_window: int | None = None,
        lfu_decay_factor: float = 0.5,
        window_ratio: float = 0.25,
        adapt_epoch_size: int = 16,
        min_decay_window: int = 8,
        max_decay_window: int = 128,
        extra_shapes: Sequence[MultiTableJoinShape] = (),
        max_total_rows: int | None = None,
        max_rows_per_table: Mapping[str, int] | int | None = None,
        max_fk_fanout: int | None = None,
        prior_state: JoinAuxState | None = None,
    ) -> JoinAuxState:
        pk_map = (
            {normalize_relation(k): v.lower() for k, v in primary_keys.items()}
            if primary_keys
            else {}
        )
        state = cls(
            shape=shape,
            primary_keys=pk_map,
            eviction_policy=eviction_policy,
            lfu_decay_window=lfu_decay_window,
            lfu_decay_factor=lfu_decay_factor,
            window_ratio=window_ratio,
            adapt_epoch_size=adapt_epoch_size,
            min_decay_window=min_decay_window,
            max_decay_window=max_decay_window,
            extra_shapes=tuple(extra_shapes),
            max_total_rows=max_total_rows,
            max_rows_per_table=max_rows_per_table,
            max_fk_fanout=max_fk_fanout,
        )
        for seed_shape in state.all_shapes():
            for join in seed_shape.joins:
                if join.right_key.lower() != state.pk_col_for(join.right_relation):
                    raise ValueError("Right join key must be the primary key")
        managed_rels = state.all_relations()
        # Validate the complete seed before indexing or eviction can hide duplicates.
        supplied_rels = {normalize_relation(r) for r in tables}
        if not managed_rels <= supplied_rels:
            raise ValueError("Seed must explicitly include every participating table")
        seen: dict[str, set[Any]] = {rel: set() for rel in managed_rels}
        for raw_rel, rows in tables.items():
            rel = normalize_relation(raw_rel)
            if rel not in managed_rels:
                continue
            pk_col = state.pk_col_for(rel)
            required = {pk_col}
            join_key_cols = {pk_col}
            for seed_shape in state.all_shapes():
                required.update(seed_shape.table_required_evidence(rel, pk_col))
                for join in seed_shape.joins:
                    if seed_shape.qualifier_to_relation(join.left_qualifier) == rel:
                        join_key_cols.add(join.left_key.lower())
                    if normalize_relation(join.right_relation) == rel:
                        join_key_cols.add(join.right_key.lower())
            for row in rows:
                lower = {str(k).lower(): v for k, v in row.items()}
                if not required <= lower.keys() or lower.get(pk_col) is None:
                    raise ValueError(f"Incomplete seed evidence for {rel}")
                if any(
                    isinstance(lower.get(col), (bool, float)) for col in join_key_cols
                ):
                    raise ValueError("Boolean and float join keys are unsupported")
                pk = _normalize_group_atom(lower[pk_col])
                try:
                    if pk in seen[rel]:
                        raise ValueError(f"Duplicate primary key in seed for {rel}")
                    seen[rel].add(pk)
                except TypeError as exc:
                    raise ValueError(f"Invalid primary key for {rel}") from exc
        if prior_state is not None:
            state._access_clock = prior_state._access_clock
            state._insertion_clock = prior_state._insertion_clock
            state._decay_epochs = prior_state._decay_epochs
            state._cms = prior_state._cms.clone()
            state._window_ratio = prior_state._window_ratio
            state._hill_climb_dir = prior_state._hill_climb_dir
            state._prev_epoch_score = prior_state._prev_epoch_score
            state._adapt_epochs = prior_state._adapt_epochs
            state._window_ratio_history = list(prior_state._window_ratio_history)
            if prior_state.lfu_decay_window is not None:
                state.lfu_decay_window = prior_state.lfu_decay_window
            for rel_k, m in prior_state._row_last_access.items():
                state._row_last_access[rel_k] = dict(m)
            for rel_k, m in prior_state._row_access_freq.items():
                state._row_access_freq[rel_k] = dict(m)
            for rel_k, m in prior_state._row_inserted_at.items():
                state._row_inserted_at[rel_k] = dict(m)

            for raw_rel, rows in tables.items():
                rel = normalize_relation(raw_rel)
                if rel not in managed_rels:
                    continue
                pk_col = state.pk_col_for(rel)
                prior_evicted = prior_state.evicted_pks.get(rel, set())
                for r in rows:
                    lower_r = {str(k).lower(): v for k, v in r.items()}
                    pk_val = _normalize_group_atom(lower_r.get(pk_col))
                    state.table_rows[rel][pk_val] = lower_r
                    state._record_insertion(rel, pk_val)
                    state._index_row_fks(rel, pk_val, lower_r)
                    if pk_val not in state._row_access_freq.setdefault(rel, {}):
                        state._row_access_freq[rel][pk_val] = (
                            0 if pk_val in prior_evicted else 1
                        )
                    if pk_val not in state._row_last_access.setdefault(rel, {}):
                        state._row_last_access[rel][pk_val] = (
                            0 if pk_val in prior_evicted else state._access_clock
                        )
            state._enforce_capacity_bounds()
            return state

        all_joins: list[tuple[MultiTableJoinShape, JoinClause]] = [
            (s, j) for s in state.all_shapes() for j in s.joins
        ]
        for raw_rel, rows in tables.items():
            rel = normalize_relation(raw_rel)
            if rel not in managed_rels:
                continue
            pk_col = state.pk_col_for(rel)
            for r in rows:
                lower_r = {str(k).lower(): v for k, v in r.items()}
                pk_val = _normalize_group_atom(lower_r.get(pk_col))
                state.table_rows[rel][pk_val] = lower_r
                state._record_insertion(rel, pk_val)
                state._index_row_fks(rel, pk_val, lower_r)
                state.record_access(rel, pk_val)
                seen_parents: set[tuple[str, Any]] = set()
                for s_obj, j in all_joins:
                    if s_obj.qualifier_to_relation(j.left_qualifier) == rel:
                        fk_key = j.left_key.lower()
                        raw_fk = lower_r.get(fk_key)
                        if raw_fk is not None:
                            p_rel = normalize_relation(j.right_relation)
                            norm_fk = _normalize_group_atom(raw_fk)
                            if (p_rel, norm_fk) not in seen_parents:
                                seen_parents.add((p_rel, norm_fk))
                                state.record_access(p_rel, raw_fk)
                state._enforce_capacity_bounds(rel)
        return state

    def _index_row_fks(
        self, rel: str, pk_val: Any, lower_row: Mapping[str, Any]
    ) -> None:
        for (child_rel, fk_col), bucket_map in self.fk_index.items():
            if child_rel == rel:
                raw_fk = lower_row.get(fk_col)
                if raw_fk is not None:
                    norm_fk = _normalize_group_atom(raw_fk)
                    if norm_fk in self.evicted_fk_buckets.get(
                        (child_rel, fk_col), set()
                    ):
                        continue
                    pks = bucket_map.setdefault(norm_fk, set())
                    if (
                        self.max_fk_fanout is not None
                        and len(pks) >= self.max_fk_fanout
                        and pk_val not in pks
                    ):
                        self.evict_fk_bucket(child_rel, fk_col, norm_fk)
                    else:
                        pks.add(pk_val)

    def _unindex_row_fks(
        self, rel: str, pk_val: Any, lower_row: Mapping[str, Any]
    ) -> None:
        for (child_rel, fk_col), bucket_map in self.fk_index.items():
            if child_rel == rel:
                raw_fk = lower_row.get(fk_col)
                if raw_fk is not None:
                    norm_fk = _normalize_group_atom(raw_fk)
                    pks = bucket_map.get(norm_fk)
                    if pks is not None:
                        pks.discard(pk_val)
                        if not pks:
                            bucket_map.pop(norm_fk, None)

    def join_multiplicity(
        self,
        dimension_relation: str,
        dim_key_val: Any,
        *,
        shape: MultiTableJoinShape | None = None,
    ) -> int:
        """
        Return the number of base fact rows referencing `dim_key_val` in
        `dimension_relation`. Returns 0 if referencing keys were evicted.
        """
        eff_shape = shape or self.shape
        rel = normalize_relation(dimension_relation)
        if rel == normalize_relation(eff_shape.base_relation):
            return 0
        try:
            return len(
                self.get_referencing_fact_pks(
                    dimension_relation,
                    {dim_key_val},
                    record_access=False,
                    shape=eff_shape,
                )
            )
        except _EvictedJoinKeyError:
            return 0

    def get_referencing_fact_pks(
        self,
        dimension_relation: str,
        dim_key_vals: Iterable[Any],
        *,
        record_access: bool = True,
        shape: MultiTableJoinShape | None = None,
    ) -> set[Any]:
        """
        Resolve the set of base fact row PKs that join with any of `dim_key_vals`
        in `dimension_relation` (supporting both direct star joins and chained
        snowflake joins). Raises `_EvictedJoinKeyError` if any required FK bucket
        or referenced row PK was evicted.
        """
        eff_shape = shape or self.shape
        target_dim = normalize_relation(dimension_relation)
        base_rel = normalize_relation(eff_shape.base_relation)
        norm_keys = {
            _normalize_group_atom(k)
            for k in dim_key_vals
            if k is not None and k is not _normalize_group_atom(None)
        }
        if not norm_keys:
            return set()

        touched_keys_by_rel: dict[str, set[Any]] = {target_dim: set(norm_keys)}
        changed = True
        while changed:
            changed = False
            for j in reversed(eff_shape.joins):
                r_rel = normalize_relation(j.right_relation)
                l_rel = eff_shape.qualifier_to_relation(j.left_qualifier)
                l_key = j.left_key.lower()
                r_key = j.right_key.lower()
                if r_rel not in touched_keys_by_rel:
                    continue

                r_pk_col = self.pk_col_for(r_rel)
                if r_key == r_pk_col:
                    r_vals = touched_keys_by_rel[r_rel]
                else:
                    if self.evicted_pks.get(r_rel):
                        raise _EvictedJoinKeyError(
                            f"Evicted rows in {r_rel} prevent non-PK join key resolution"
                        )
                    r_vals = {
                        _normalize_group_atom(r_row.get(r_key))
                        for r_pk, r_row in self.table_rows.get(r_rel, {}).items()
                        if r_pk in touched_keys_by_rel[r_rel]
                        and r_row.get(r_key) is not None
                    }

                ev_buckets = self.evicted_fk_buckets.get((l_rel, l_key), set())
                bucket_map = self.fk_index.get((l_rel, l_key), {})
                l_pks: set[Any] = set()
                for rv in r_vals:
                    if rv in ev_buckets:
                        raise _EvictedJoinKeyError(
                            f"FK bucket ({l_rel}, {l_key})[{rv}] was evicted"
                        )
                    for child_pk in bucket_map.get(rv, set()):
                        if child_pk in self.evicted_pks.get(l_rel, set()):
                            raise _EvictedJoinKeyError(
                                f"Referenced row {child_pk} in {l_rel} was evicted"
                            )
                        if record_access:
                            self.record_access(l_rel, child_pk)
                        l_pks.add(child_pk)

                prev_l = touched_keys_by_rel.setdefault(l_rel, set())
                if not (l_pks <= prev_l):
                    prev_l.update(l_pks)
                    changed = True

        return touched_keys_by_rel.get(base_rel, set())

    def evaluate_fact_row_contributions(
        self,
        fact_row: Mapping[str, Any],
        *,
        override_rel: str | None = None,
        override_removed_pk: Any = None,
        override_upserted_row: tuple[Any, dict[str, Any]] | None = None,
        shape: MultiTableJoinShape | None = None,
        record_access: bool = True,
    ) -> list[tuple[Any, Decimal, int]]:
        """
        Evaluate a single base fact row against the current dimension tables in
        `self.table_rows` (with an optional hypothetical single-row dimension
        mutation applied), returning a list of `(group_key, sum_delta, non_null_delta)`
        for each resulting composite joined row that satisfies `shape.predicate_terms`.
        Raises `_EvictedJoinKeyError` if a referenced dimension key was evicted.
        """
        eff_shape = shape or self.shape
        base_rel = normalize_relation(eff_shape.base_relation)
        base_qual = eff_shape.effective_base_qualifier
        lower_fact = {str(k).lower(): v for k, v in fact_row.items()}
        base_pk_col = self.pk_col_for(base_rel)
        base_pk = lower_fact.get(base_pk_col)
        if base_pk is not None and record_access:
            self.record_access(base_rel, base_pk)

        composite_envs: list[dict[str, dict[str, Any]]] = [
            {base_rel: lower_fact, base_qual: lower_fact}
        ]

        for j in eff_shape.joins:
            r_rel = normalize_relation(j.right_relation)
            r_qual = j.effective_right_qualifier
            l_qual = j.left_qualifier.lower()
            l_key = j.left_key.lower()
            r_key = j.right_key.lower()
            r_pk_col = self.pk_col_for(r_rel)

            dim_map = self.table_rows.get(r_rel, {})
            ev_dim_pks = self.evicted_pks.get(r_rel, set())

            next_envs: list[dict[str, dict[str, Any]]] = []
            for env in composite_envs:
                l_val = env.get(l_qual, {}).get(l_key)
                matched_dim_rows: list[dict[str, Any]] = []

                if l_val is not None:
                    norm_l_val = _normalize_group_atom(l_val)
                    if r_key == r_pk_col:
                        if norm_l_val in ev_dim_pks:
                            raise _EvictedJoinKeyError(
                                f"Dimension key {norm_l_val} in {r_rel} was evicted"
                            )
                    elif ev_dim_pks:
                        raise _EvictedJoinKeyError(
                            f"Relation {r_rel} has evicted rows during non-PK scan"
                        )

                    if r_key == r_pk_col and override_rel != r_rel:
                        cand = dim_map.get(norm_l_val)
                        if cand is not None:
                            if record_access:
                                self.record_access(r_rel, norm_l_val)
                            matched_dim_rows.append(cand)
                    else:
                        for d_pk, d_row in dim_map.items():
                            if (
                                override_rel == r_rel
                                and override_removed_pk is not None
                                and _values_equal(d_pk, override_removed_pk)
                            ):
                                continue
                            if (
                                override_rel == r_rel
                                and override_upserted_row is not None
                                and _values_equal(d_pk, override_upserted_row[0])
                            ):
                                continue
                            r_val = d_row.get(r_key)
                            if r_val is not None and _values_equal(l_val, r_val):
                                if record_access:
                                    self.record_access(r_rel, d_pk)
                                matched_dim_rows.append(d_row)

                        if override_rel == r_rel and override_upserted_row is not None:
                            u_row = override_upserted_row[1]
                            u_val = u_row.get(r_key)
                            if u_val is not None and _values_equal(l_val, u_val):
                                matched_dim_rows.append(u_row)

                if matched_dim_rows:
                    for dr in matched_dim_rows:
                        new_env = dict(env)
                        new_env[r_rel] = dr
                        new_env[r_qual] = dr
                        next_envs.append(new_env)
                elif j.join_type.upper() == "LEFT":
                    new_env = dict(env)
                    new_env[r_rel] = {}
                    new_env[r_qual] = {}
                    next_envs.append(new_env)

            composite_envs = next_envs
            if not composite_envs:
                break

        contributions: list[tuple[Any, Decimal, int]] = []
        is_grouped = len(eff_shape.group_cols) > 0

        for env in composite_envs:
            pred_ok = True
            for q, col, op, lit in eff_shape.predicate_terms:
                cell_val = env.get(q.lower(), {}).get(col.lower())
                if not Predicate(terms=((col.lower(), op, lit),)).matches(
                    {col.lower(): cell_val}
                ):
                    pred_ok = False
                    break
            if not pred_ok:
                continue

            if not is_grouped:
                g_key: Any = ()
            elif len(eff_shape.group_cols) == 1:
                q0, c0 = eff_shape.group_cols[0]
                g_key = _checked_group_atom(env.get(q0.lower(), {}).get(c0.lower()))
            else:
                g_key = tuple(
                    _checked_group_atom(env.get(q.lower(), {}).get(c.lower()))
                    for q, c in eff_shape.group_cols
                )

            if eff_shape.agg_fn == "count_star":
                hash(g_key)
                contributions.append((g_key, Decimal(0), 1))
            elif eff_shape.agg_col is not None:
                hash(g_key)
                aq, ac = eff_shape.agg_col
                raw_v = env.get(aq.lower(), {}).get(ac.lower())
                if raw_v is None:
                    contributions.append((g_key, Decimal(0), 0))
                elif eff_shape.agg_fn == "count":
                    contributions.append((g_key, Decimal(0), 1))
                elif eff_shape.agg_fn in ("sum", "avg"):
                    contributions.append((g_key, _aggregate_decimal(raw_v), 1))
                else:
                    raise ValueError(
                        f"Unsupported aggregate function: {eff_shape.agg_fn}"
                    )
            else:
                raise ValueError("Missing agg_col for aggregate shape")

        return contributions

    def apply_row_mutation(
        self,
        relation: str,
        op: str,
        old_map: dict[str, Any],
        new_map: dict[str, Any],
    ) -> None:
        rel = normalize_relation(relation)
        if rel not in self.all_relations():
            return
        pk_col = self.pk_col_for(rel)
        tbl = self.table_rows.setdefault(rel, {})

        if op == "INSERT":
            pk_val = _normalize_group_atom(new_map.get(pk_col))
            tbl[pk_val] = dict(new_map)
            self._record_insertion(rel, pk_val)
            self._index_row_fks(rel, pk_val, new_map)
            self.record_access(rel, pk_val)
            self._enforce_capacity_bounds(rel)
        elif op == "DELETE":
            pk_val = _normalize_group_atom(old_map.get(pk_col))
            existing = tbl.pop(pk_val, old_map)
            self._unindex_row_fks(rel, pk_val, existing)
            if rel in self._row_last_access:
                self._row_last_access[rel].pop(pk_val, None)
            if rel in self._row_access_freq:
                self._row_access_freq[rel].pop(pk_val, None)
            if rel in self._row_inserted_at:
                self._row_inserted_at[rel].pop(pk_val, None)
        elif op == "UPDATE":
            old_pk = _normalize_group_atom(old_map.get(pk_col, new_map.get(pk_col)))
            new_pk = _normalize_group_atom(new_map.get(pk_col, old_map.get(pk_col)))
            existing = tbl.pop(old_pk, old_map)
            self._unindex_row_fks(rel, old_pk, existing)
            if not _values_equal(old_pk, new_pk):
                if rel in self._row_last_access:
                    self._row_last_access[rel].pop(old_pk, None)
                if rel in self._row_access_freq:
                    self._row_access_freq[rel].pop(old_pk, None)
                if rel in self._row_inserted_at:
                    self._row_inserted_at[rel].pop(old_pk, None)
            merged = dict(existing)
            merged.update(new_map)
            tbl[new_pk] = merged
            self._record_insertion(rel, new_pk)
            self._index_row_fks(rel, new_pk, merged)
            self.record_access(rel, new_pk)
            self._enforce_capacity_bounds(rel)


def rebuild_join_cache(
    shape: MultiTableJoinShape,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    primary_keys: Mapping[str, str] | None = None,
) -> AggregateCache:
    """
    Build an `AggregateCache` (with full `_group_state` `(sum, non_null_count, row_count)`)
    from multi-table rows for `shape`.
    """
    cache = AggregateCache()
    cache._from_rows = True
    if shape.agg_fn is None:
        return cache

    aux = JoinAuxState.from_tables(shape, tables, primary_keys=primary_keys)
    is_grouped = len(shape.group_cols) > 0
    if not is_grouped:
        cache._group_state[()] = (Decimal(0), 0, 0)

    base_rel = normalize_relation(shape.base_relation)
    for fact_row in aux.table_rows.get(base_rel, {}).values():
        contribs = aux.evaluate_fact_row_contributions(fact_row, shape=shape)
        for g_key, s_delta, n_delta in contribs:
            s_cur, n_cur, r_cur = cache._group_state.get(g_key, (Decimal(0), 0, 0))
            cache._group_state[g_key] = (
                _decimal_add(s_cur, s_delta),
                n_cur + n_delta,
                r_cur + 1,
            )

    for g_key in list(cache._group_state.keys()):
        cache._refresh_group_value(g_key, shape.agg_fn, is_grouped)
    return cache


def _compute_join_event_deltas(
    shape: MultiTableJoinShape,
    event_rel: str,
    op: str,
    old_map: dict[str, Any],
    new_map: dict[str, Any],
    aux_state: JoinAuxState,
    *,
    record_access: bool = True,
) -> tuple[
    list[tuple[Any, Decimal, int]],
    list[tuple[Any, Decimal, int]],
    int,
]:
    """
    Compute `(old_contribs, new_contribs, matched_fact_row_count)` for `event`
    against `aux_state`.
    """
    base_rel = normalize_relation(shape.base_relation)
    if event_rel == base_rel:
        old_contribs = (
            aux_state.evaluate_fact_row_contributions(
                old_map, shape=shape, record_access=record_access
            )
            if op in ("UPDATE", "DELETE")
            else []
        )
        new_contribs = (
            aux_state.evaluate_fact_row_contributions(
                new_map, shape=shape, record_access=record_access
            )
            if op in ("UPDATE", "INSERT")
            else []
        )
        return old_contribs, new_contribs, 1

    dim_pk_col = aux_state.pk_col_for(event_rel)
    old_pk = (
        _normalize_group_atom(old_map.get(dim_pk_col))
        if op in ("UPDATE", "DELETE")
        else None
    )
    new_pk = (
        _normalize_group_atom(new_map.get(dim_pk_col))
        if op in ("UPDATE", "INSERT")
        else None
    )

    touched_join_keys: set[Any] = set()
    for j in shape.joins:
        if normalize_relation(j.right_relation) == event_rel:
            rk = j.right_key.lower()
            if op in ("UPDATE", "DELETE") and old_map.get(rk) is not None:
                touched_join_keys.add(_normalize_group_atom(old_map.get(rk)))
            if op in ("UPDATE", "INSERT") and new_map.get(rk) is not None:
                touched_join_keys.add(_normalize_group_atom(new_map.get(rk)))

    affected_fact_pks = aux_state.get_referencing_fact_pks(
        event_rel, touched_join_keys, record_access=record_access, shape=shape
    )
    if not affected_fact_pks:
        return [], [], 0

    old_contribs_all: list[tuple[Any, Decimal, int]] = []
    new_contribs_all: list[tuple[Any, Decimal, int]] = []
    fact_table = aux_state.table_rows.get(base_rel, {})

    for f_pk in affected_fact_pks:
        if f_pk in aux_state.evicted_pks.get(base_rel, set()):
            raise _EvictedJoinKeyError(f"Fact row {f_pk} was evicted")
        f_row = fact_table.get(f_pk)
        if f_row is None:
            raise _EvictedJoinKeyError(f"Fact row {f_pk} missing from aux_state")
        old_contribs_all.extend(
            aux_state.evaluate_fact_row_contributions(
                f_row, shape=shape, record_access=record_access
            )
        )
        new_contribs_all.extend(
            aux_state.evaluate_fact_row_contributions(
                f_row,
                override_rel=event_rel,
                override_removed_pk=old_pk if op in ("UPDATE", "DELETE") else None,
                override_upserted_row=(
                    (new_pk, new_map) if op in ("UPDATE", "INSERT") else None
                ),
                shape=shape,
                record_access=record_access,
            )
        )

    return old_contribs_all, new_contribs_all, len(affected_fact_pks)


def certify_join_event(
    shape: MultiTableJoinShape,
    event: WriteEvent,
    aux_state: JoinAuxState | None = None,
) -> Decision:
    """
    Default-deny certifier for multi-table foreign-key `JOIN` shapes (Upgrade 3).
    Requires `aux_state: JoinAuxState` for any write that touches a participating
    table's join/aggregate/predicate/group fingerprint, and downgrades evicted
    keys or overflowed fanout buckets to `invalidate("evicted_join_auxiliary_key")`.
    """
    event_rel = normalize_relation(event.relation)
    cert = Certificate(
        shape=shape.name,
        event_id=str(event.event_id),
        relation=event_rel or event.relation,
        decision_kind=DecisionKind.PRESERVE,
        reason_code="",
    )

    if not shape.is_supported:
        return Decision.unsupported(
            shape.unsupported_reason or "unsupported_join_shape", cert
        )

    for join in shape.joins:
        pk_col = aux_state.pk_col_for(join.right_relation) if aux_state else "id"
        if join.right_key.lower() != pk_col:
            return Decision.unsupported("right_join_key_not_primary_key", cert)

    if shape.agg_fn is not None and shape.agg_fn not in (
        "sum",
        "count",
        "count_star",
        "avg",
    ):
        return Decision.unsupported(f"unsupported_agg_fn: {shape.agg_fn}", cert)

    participating_rels = shape.relations()
    explicit_op = event.explicit_operation.upper() if event.explicit_operation else None

    if event.schema_change or explicit_op in ("SCHEMA_CHANGE", "DDL"):
        if any(_ambiguous_relation(rel, event_rel) for rel in participating_rels):
            return Decision.invalidate("ambiguous_relation", cert)
        if event.relation.strip() in ("*", "") or event_rel in participating_rels:
            return Decision.invalidate("schema_evolution", cert)
        return Decision.preserve("unrelated_relation", cert)

    if explicit_op == "TX_VIOLATION":
        if any(_ambiguous_relation(rel, event_rel) for rel in participating_rels):
            return Decision.invalidate("ambiguous_relation", cert)
        if event.relation.strip() in ("*", "") or event_rel in participating_rels:
            return Decision.invalidate("transaction_boundary_violation", cert)
        return Decision.preserve("unrelated_relation", cert)

    # Rule 1: Relation not in any participating join table -> preserve unrelated_relation
    if event_rel in ("", "*") or any(
        _ambiguous_relation(rel, event_rel) for rel in participating_rels
    ):
        return Decision.invalidate("ambiguous_relation", cert)
    if event_rel not in participating_rels:
        return Decision.preserve("unrelated_relation", cert)

    if explicit_op == "TRUNCATE":
        return Decision.invalidate("truncate_event", cert)

    if explicit_op and explicit_op not in ("INSERT", "UPDATE", "DELETE"):
        return Decision.invalidate("unsupported_event_operation", cert)

    old_map = {str(k).lower(): v for k, v in (event.old or {}).items()}
    new_map = {str(k).lower(): v for k, v in (event.new or {}).items()}
    caller_cols = frozenset(str(c).lower() for c in (event.changed_cols or set()))
    unavail_cols = frozenset(str(c).lower() for c in (event.unavailable_cols or set()))

    tbl_sec_cols = shape.table_security_cols(event_rel)
    tbl_fp = shape.table_fingerprint(event_rel)

    group_cols = {
        col.lower()
        for qualifier, col in shape.group_cols
        if shape.qualifier_to_relation(qualifier) == event_rel
    }
    predicate_cols = {
        col.lower()
        for qualifier, col, _, _ in shape.predicate_terms
        if shape.qualifier_to_relation(qualifier) == event_rel
    }
    join_key_cols = {aux_state.pk_col_for(event_rel) if aux_state else "id"}
    for join in shape.joins:
        if shape.qualifier_to_relation(join.left_qualifier) == event_rel:
            join_key_cols.add(join.left_key.lower())
        if normalize_relation(join.right_relation) == event_rel:
            join_key_cols.add(join.right_key.lower())
    agg_col = None
    if shape.agg_fn in ("sum", "avg") and shape.agg_col is not None:
        qualifier, col = shape.agg_col
        if shape.qualifier_to_relation(qualifier) == event_rel:
            agg_col = col.lower()
    for row in (old_map, new_map):
        if any(isinstance(row.get(col), (bool, float)) for col in group_cols):
            return Decision.invalidate("invalid_evidence_type", cert)
        if agg_col is not None and isinstance(row.get(agg_col), float):
            return Decision.invalidate("invalid_evidence_type", cert)
        if any(isinstance(row.get(col), float) for col in predicate_cols):
            return Decision.invalidate("invalid_evidence_type", cert)
        if any(isinstance(row.get(col), (bool, float)) for col in join_key_cols):
            return Decision.invalidate("invalid_evidence_type", cert)
        try:
            for col in join_key_cols & row.keys():
                hash(_normalize_group_atom(row[col]))
        except (TypeError, ValueError, InvalidOperation, OverflowError):
            return Decision.invalidate("invalid_evidence_type", cert)

    if unavail_cols & tbl_sec_cols:
        return Decision.invalidate("security_column_changed", cert)

    if unavail_cols & tbl_fp:
        cert.required_evidence = sorted(shape.table_required_evidence(event_rel))
        cert.available_evidence = sorted(
            (old_map.keys() & new_map.keys())
            if (old_map and new_map)
            else (new_map.keys() or old_map.keys())
        )
        return Decision.invalidate("missing_evidence_for_repair", cert)

    if explicit_op:
        op = explicit_op
        if op == "INSERT" and (old_map or not new_map):
            return Decision.invalidate("invalid_event_payload", cert)
        if op == "DELETE" and (new_map or not old_map):
            return Decision.invalidate("invalid_event_payload", cert)
        if op == "UPDATE" and (not old_map or not new_map):
            cert.required_evidence = sorted(shape.table_required_evidence(event_rel))
            cert.available_evidence = sorted(old_map.keys() & new_map.keys())
            return Decision.invalidate("missing_evidence_for_repair", cert)
    else:
        if not old_map and new_map:
            op = "INSERT"
        elif old_map and not new_map:
            op = "DELETE"
        elif old_map and new_map:
            op = "UPDATE"
        else:
            op = "EMPTY"

    # Rule 2: Derive changed_cols from old/new; if caller supplies it and they disagree -> invalidate
    if op == "INSERT":
        derived_cols = frozenset(new_map.keys()) | unavail_cols
        if caller_cols and (caller_cols - unavail_cols) != frozenset(new_map.keys()):
            return Decision.invalidate("changed_cols_mismatch", cert)
    elif op == "DELETE":
        derived_cols = frozenset(old_map.keys()) | unavail_cols
        if caller_cols and (caller_cols - unavail_cols) != frozenset(old_map.keys()):
            return Decision.invalidate("changed_cols_mismatch", cert)
    elif op == "UPDATE":
        common_keys = old_map.keys() & new_map.keys()
        known_diff = frozenset(
            k for k in common_keys if not _values_equal(old_map[k], new_map[k])
        )
        known_same = frozenset(
            k for k in common_keys if _values_equal(old_map[k], new_map[k])
        )
        sym_diff = frozenset(old_map.keys() ^ new_map.keys())
        derived_cols = known_diff | sym_diff | unavail_cols | caller_cols
        if caller_cols:
            non_unavail_caller = caller_cols - unavail_cols
            if not (known_diff <= non_unavail_caller) or (
                non_unavail_caller & known_same
            ):
                return Decision.invalidate("changed_cols_mismatch", cert)
            if not sym_diff and non_unavail_caller != known_diff:
                return Decision.invalidate("changed_cols_mismatch", cert)
    else:
        derived_cols = unavail_cols

    if op == "EMPTY":
        if not caller_cols and not unavail_cols:
            return Decision.invalidate("missing_evidence_for_repair", cert)
        effective_changed = caller_cols | unavail_cols
    else:
        effective_changed = derived_cols

    # Rule 3 & 4: UPDATE whose changed columns are disjoint from table_fingerprint(event_rel) -> preserve
    if op not in ("INSERT", "DELETE"):
        if not (effective_changed & tbl_fp):
            return Decision.preserve("disjoint_columns", cert)

    # Rule 5: Security columns or non-aggregate projection shape -> invalidate
    if (effective_changed & tbl_sec_cols) or (
        op in ("INSERT", "DELETE") and tbl_sec_cols
    ):
        return Decision.invalidate("security_column_changed", cert)

    if shape.agg_fn is None:
        return Decision.invalidate("shape_intersected", cert)

    # Rule 6: Require valid JoinAuxState and full required evidence on old and new separately
    if aux_state is None or aux_state.is_invalidated:
        cert.required_evidence = [
            *sorted(shape.table_required_evidence(event_rel)),
            "join_aux_state",
        ]
        return Decision.invalidate("missing_join_auxiliary_state", cert)

    pk_col = aux_state.pk_col_for(event_rel)
    req = shape.table_required_evidence(event_rel, pk_col=pk_col)
    cert.required_evidence = sorted(req)

    if op == "UPDATE":
        avail_old = set(old_map.keys())
        avail_new = set(new_map.keys())
        cert.available_evidence = sorted(avail_old & avail_new)
        if not (req <= avail_old and req <= avail_new):
            return Decision.invalidate("missing_evidence_for_repair", cert)
    elif op == "INSERT":
        avail_new = set(new_map.keys())
        cert.available_evidence = sorted(avail_new)
        if not (req <= avail_new):
            return Decision.invalidate("missing_evidence_for_repair", cert)
    elif op == "DELETE":
        avail_old = set(old_map.keys())
        cert.available_evidence = sorted(avail_old)
        if not (req <= avail_old):
            return Decision.invalidate("missing_evidence_for_repair", cert)
    else:
        cert.available_evidence = []
        return Decision.invalidate("missing_evidence_for_repair", cert)

    # Check if target row PK was evicted from JoinAuxState
    ev_rel_pks = aux_state.evicted_pks.get(event_rel, set())
    if op in ("UPDATE", "DELETE"):
        old_pk = _normalize_group_atom(old_map.get(pk_col))
        if old_pk in ev_rel_pks:
            return Decision.invalidate("evicted_join_auxiliary_key", cert)
    if op in ("UPDATE", "INSERT"):
        new_pk = _normalize_group_atom(new_map.get(pk_col))
        if new_pk in ev_rel_pks:
            return Decision.invalidate("evicted_join_auxiliary_key", cert)

    rel_limit = aux_state.max_rows_for(event_rel)
    if (rel_limit is not None and rel_limit <= 0) or (
        aux_state.max_total_rows is not None and aux_state.max_total_rows <= 0
    ):
        return Decision.invalidate("evicted_join_auxiliary_key", cert)

    # Verify consistency between incoming event old/new PK and JoinAuxState
    rel_store = aux_state.table_rows.get(event_rel, {})
    if op in ("UPDATE", "DELETE"):
        old_pk = _normalize_group_atom(old_map.get(pk_col))
        stored_row = rel_store.get(old_pk)
        if stored_row is None:
            return Decision.invalidate("stale_join_auxiliary_state", cert)
        for req_c in req:
            if not _values_equal(old_map.get(req_c), stored_row.get(req_c)):
                return Decision.invalidate("stale_join_auxiliary_state", cert)
    if op == "INSERT":
        new_pk = _normalize_group_atom(new_map.get(pk_col))
        if new_pk in rel_store:
            return Decision.invalidate("stale_join_auxiliary_state", cert)

    try:
        old_contribs, new_contribs, matched_facts = _compute_join_event_deltas(
            shape, event_rel, op, old_map, new_map, aux_state, record_access=False
        )
    except _EvictedJoinKeyError:
        return Decision.invalidate("evicted_join_auxiliary_key", cert)
    except (TypeError, ValueError, InvalidOperation):
        return Decision.invalidate("invalid_evidence_type", cert)

    base_rel = normalize_relation(shape.base_relation)
    if event_rel != base_rel and matched_facts == 0:
        return Decision.preserve("zero_join_multiplicity", cert)

    # Check if net group state delta across all groups is identically zero
    group_deltas: dict[Any, tuple[Decimal, int, int]] = {}
    for g_key, s_d, n_d in old_contribs:
        cs, cn, cr = group_deltas.get(g_key, (Decimal(0), 0, 0))
        group_deltas[g_key] = (_decimal_add(cs, s_d.copy_negate()), cn - n_d, cr - 1)
    for g_key, s_d, n_d in new_contribs:
        cs, cn, cr = group_deltas.get(g_key, (Decimal(0), 0, 0))
        group_deltas[g_key] = (_decimal_add(cs, s_d), cn + n_d, cr + 1)

    has_nonzero_delta = any(
        s_d != Decimal(0) or n_d != 0 or r_d != 0
        for s_d, n_d, r_d in group_deltas.values()
    )

    rel_join_keys = shape.table_dependency_set(event_rel)["join_keys"]
    join_key_changed = op == "UPDATE" and any(
        not _values_equal(old_map.get(jk), new_map.get(jk)) for jk in rel_join_keys
    )

    if event_rel == base_rel and join_key_changed:
        cert.repair_program = f"join_{shape.agg_fn}_by_group"
        return Decision.repair("foreign_key_reparent", cert)

    if event_rel != base_rel and join_key_changed and matched_facts > 0:
        cert.repair_program = f"join_{shape.agg_fn}_by_group"
        return Decision.repair("dimension_update", cert)

    if op == "UPDATE" and not has_nonzero_delta:
        return Decision.preserve("noop_update", cert)

    if event_rel != base_rel and not has_nonzero_delta:
        return Decision.preserve("noop_update", cert)

    cert.repair_program = f"join_{shape.agg_fn}_by_group"

    if event_rel == base_rel:
        if op == "INSERT":
            return Decision.repair("insert", cert)
        if op == "DELETE":
            return Decision.repair("delete", cert)
        if not old_contribs and new_contribs:
            return Decision.repair("predicate_entry", cert)
        if old_contribs and not new_contribs:
            return Decision.repair("predicate_exit", cert)
        old_groups = {g for g, _, _ in old_contribs}
        new_groups = {g for g, _, _ in new_contribs}
        if old_groups != new_groups:
            return Decision.repair("group_move", cert)
        return Decision.repair("value_change", cert)

    if op == "INSERT":
        return Decision.repair("dimension_insert", cert)
    if op == "DELETE":
        return Decision.repair("dimension_delete", cert)
    return Decision.repair("dimension_update", cert)


def _apply_contribs_to_cache(
    shape: MultiTableJoinShape,
    cache: AggregateCache,
    old_contribs: Sequence[tuple[Any, Decimal, int]],
    new_contribs: Sequence[tuple[Any, Decimal, int]],
) -> None:
    is_grouped = len(shape.group_cols) > 0
    touched_groups: list[Any] = []

    for g_key, s_delta, n_delta in old_contribs:
        s_cur, n_cur, r_cur = cache._group_state.get(g_key, (Decimal(0), 0, 0))
        cache._group_state[g_key] = (
            _decimal_add(s_cur, s_delta.copy_negate()),
            n_cur - n_delta,
            r_cur - 1,
        )
        if g_key not in touched_groups:
            touched_groups.append(g_key)

    for g_key, s_delta, n_delta in new_contribs:
        s_cur, n_cur, r_cur = cache._group_state.get(g_key, (Decimal(0), 0, 0))
        cache._group_state[g_key] = (
            _decimal_add(s_cur, s_delta),
            n_cur + n_delta,
            r_cur + 1,
        )
        if g_key not in touched_groups:
            touched_groups.append(g_key)

    for g_key in touched_groups:
        cache._refresh_group_value(g_key, shape.agg_fn, is_grouped)


def process_join_event(
    shape: MultiTableJoinShape,
    cache: AggregateCache,
    event: WriteEvent,
    aux_state: JoinAuxState | None = None,
) -> Decision:
    """
    Certify `event` against `shape` and `aux_state`, apply the incremental
    repair program to `cache` on `REPAIR`, and keep `aux_state` synchronized
    on both `REPAIR` and `PRESERVE` (or mark `aux_state.is_invalidated = True`
    on `INVALIDATE`).
    """
    decision = certify_join_event(shape, event, aux_state)
    if aux_state is not None:
        aux_state.record_decision_outcome(decision)

    if decision.kind == DecisionKind.INVALIDATE:
        if aux_state is not None:
            aux_state.is_invalidated = True
        return decision

    if decision.kind == DecisionKind.UNSUPPORTED:
        return decision

    if decision.kind == DecisionKind.REPAIR and not cache._from_rows:
        if aux_state is not None:
            aux_state.is_invalidated = True
        return Decision.invalidate("missing_aggregate_state", decision.certificate)

    event_rel = normalize_relation(event.relation)
    if aux_state is None or event_rel not in shape.relations():
        return decision

    old_map = {str(k).lower(): v for k, v in (event.old or {}).items()}
    new_map = {str(k).lower(): v for k, v in (event.new or {}).items()}
    op = event.operation

    if decision.kind == DecisionKind.PRESERVE:
        pk_col = aux_state.pk_col_for(event_rel)
        if decision.reason == "disjoint_columns":
            if op == "UPDATE" and (pk_col in old_map or pk_col in new_map):
                old_pk = _normalize_group_atom(old_map.get(pk_col, new_map.get(pk_col)))
                rel_store = aux_state.table_rows.get(event_rel, {})
                if old_pk in rel_store:
                    rel_store[old_pk].update(new_map)
            return decision
        if (
            (op == "INSERT" and pk_col in new_map)
            or (op == "DELETE" and pk_col in old_map)
            or (op == "UPDATE" and (pk_col in old_map or pk_col in new_map))
        ):
            aux_state.apply_row_mutation(event_rel, op, old_map, new_map)
        return decision

    try:
        old_contribs, new_contribs, _ = _compute_join_event_deltas(
            shape, event_rel, op, old_map, new_map, aux_state
        )
        _apply_contribs_to_cache(shape, cache, old_contribs, new_contribs)
        aux_state.apply_row_mutation(event_rel, op, old_map, new_map)
    except _EvictedJoinKeyError:
        aux_state.is_invalidated = True
        return Decision.invalidate("evicted_join_auxiliary_key", decision.certificate)
    except (TypeError, ValueError, InvalidOperation, ZeroDivisionError):
        aux_state.is_invalidated = True
        return Decision.invalidate("repair_type_error", decision.certificate)

    return decision


class JoinCacheManager:
    """
    High-level manager coordinating a `MultiTableJoinShape`, an `AggregateCache`,
    and a `JoinAuxState` under a configured eviction policy.
    """

    def __init__(
        self,
        shape: MultiTableJoinShape,
        initial_tables: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        *,
        primary_keys: Mapping[str, str] | None = None,
        eviction_policy: EvictionPolicy = "fifo",
        lfu_decay_window: int | None = None,
        lfu_decay_factor: float = 0.5,
        window_ratio: float = 0.25,
        adapt_epoch_size: int = 16,
        max_total_rows: int | None = None,
        max_rows_per_table: Mapping[str, int] | int | None = None,
        max_fk_fanout: int | None = None,
        cache: AggregateCache | None = None,
        aux: JoinAuxState | None = None,
        aux_state: JoinAuxState | None = None,
    ) -> None:
        self.shape = shape
        self.primary_keys = dict(primary_keys or {})
        self.eviction_policy: EvictionPolicy = eviction_policy
        self.lfu_decay_window = lfu_decay_window
        self.lfu_decay_factor = lfu_decay_factor
        self.window_ratio = window_ratio
        self.adapt_epoch_size = adapt_epoch_size
        self.max_total_rows = max_total_rows
        self.max_rows_per_table = max_rows_per_table
        self.max_fk_fanout = max_fk_fanout

        tables = initial_tables or {rel: [] for rel in shape.relations()}
        self.cache = cache or rebuild_join_cache(
            shape, tables, primary_keys=self.primary_keys
        )
        self.aux_state = (
            aux
            or aux_state
            or JoinAuxState.from_tables(
                shape,
                tables,
                primary_keys=self.primary_keys,
                eviction_policy=self.eviction_policy,
                lfu_decay_window=self.lfu_decay_window,
                lfu_decay_factor=self.lfu_decay_factor,
                window_ratio=self.window_ratio,
                adapt_epoch_size=self.adapt_epoch_size,
                max_total_rows=self.max_total_rows,
                max_rows_per_table=self.max_rows_per_table,
                max_fk_fanout=self.max_fk_fanout,
            )
        )

    @property
    def aux(self) -> JoinAuxState:
        return self.aux_state

    def certify(self, event: WriteEvent) -> Decision:
        return certify_join_event(self.shape, event, self.aux_state)

    def process(self, event: WriteEvent) -> Decision:
        return process_join_event(self.shape, self.cache, event, self.aux_state)

    def rebuild(
        self,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        preserve_telemetry: bool = False,
    ) -> None:
        self.cache = rebuild_join_cache(
            self.shape, tables, primary_keys=self.primary_keys
        )
        self.aux_state = JoinAuxState.from_tables(
            self.shape,
            tables,
            primary_keys=self.primary_keys,
            eviction_policy=self.eviction_policy,
            lfu_decay_window=self.lfu_decay_window,
            lfu_decay_factor=self.lfu_decay_factor,
            window_ratio=self.window_ratio,
            adapt_epoch_size=self.adapt_epoch_size,
            max_total_rows=self.max_total_rows,
            max_rows_per_table=self.max_rows_per_table,
            max_fk_fanout=self.max_fk_fanout,
            prior_state=self.aux_state if preserve_telemetry else None,
        )


class SharedJoinAuxPool:
    """
    Shared multi-query `JoinAuxState` memory pool coordinating multiple concurrent
    `MultiTableJoinShape` queries over a single unified bounded auxiliary table store
    (`max_total_rows`).

    Eliminates duplicate dimension/fact row storage across queries and scores
    candidate evictions using cross-query structural join multiplicity
    (`sum_{q in shapes} resident_multiplicity_q(rel, pk)`) and shared access
    frequency telemetry (`CountMinSketch` / W-TinyLFU hill-climbing).
    """

    def __init__(
        self,
        shapes: Sequence[MultiTableJoinShape],
        initial_tables: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        *,
        primary_keys: Mapping[str, str] | None = None,
        eviction_policy: EvictionPolicy = "wtinylfu",
        lfu_decay_window: int | None = None,
        lfu_decay_factor: float = 0.5,
        window_ratio: float = 0.25,
        adapt_epoch_size: int = 16,
        max_total_rows: int | None = None,
        max_rows_per_table: Mapping[str, int] | int | None = None,
        max_fk_fanout: int | None = None,
    ) -> None:
        if not shapes:
            raise ValueError(
                "SharedJoinAuxPool requires at least one MultiTableJoinShape"
            )
        normalized_shapes: list[MultiTableJoinShape] = []
        seen_names: set[str] = set()
        for idx, s in enumerate(shapes):
            q_name = s.name
            if q_name in seen_names or q_name == "join_shape" and len(shapes) > 1:
                q_name = f"{s.name}_{idx}"
                s = MultiTableJoinShape(
                    base_relation=s.base_relation,
                    base_alias=s.base_alias,
                    joins=s.joins,
                    agg_fn=s.agg_fn,
                    agg_col=s.agg_col,
                    group_cols=s.group_cols,
                    predicate_terms=s.predicate_terms,
                    projection_cols=s.projection_cols,
                    security_cols=s.security_cols,
                    name=q_name,
                    is_supported=s.is_supported,
                    unsupported_reason=s.unsupported_reason,
                )
            seen_names.add(q_name)
            normalized_shapes.append(s)

        self.shapes: tuple[MultiTableJoinShape, ...] = tuple(normalized_shapes)
        self.primary_keys = dict(primary_keys or {})
        self.eviction_policy: EvictionPolicy = eviction_policy
        self.lfu_decay_window = lfu_decay_window
        self.lfu_decay_factor = lfu_decay_factor
        self.window_ratio = window_ratio
        self.adapt_epoch_size = adapt_epoch_size
        self.max_total_rows = max_total_rows
        self.max_rows_per_table = max_rows_per_table
        self.max_fk_fanout = max_fk_fanout

        all_rels = {rel for s in self.shapes for rel in s.relations()}
        tables = initial_tables or {rel: [] for rel in all_rels}
        self.caches: dict[str, AggregateCache] = {
            s.name: rebuild_join_cache(s, tables, primary_keys=self.primary_keys)
            for s in self.shapes
        }
        self.invalidated_queries: set[str] = set()
        self.aux_state = JoinAuxState.from_tables(
            self.shapes[0],
            tables,
            primary_keys=self.primary_keys,
            eviction_policy=self.eviction_policy,
            lfu_decay_window=self.lfu_decay_window,
            lfu_decay_factor=self.lfu_decay_factor,
            window_ratio=self.window_ratio,
            adapt_epoch_size=self.adapt_epoch_size,
            extra_shapes=self.shapes[1:],
            max_total_rows=self.max_total_rows,
            max_rows_per_table=self.max_rows_per_table,
            max_fk_fanout=self.max_fk_fanout,
        )

    @property
    def aux(self) -> JoinAuxState:
        return self.aux_state

    def cross_query_multiplicity(self, relation: str, pk_val: Any) -> int:
        """
        Return the total structural join multiplicity of `(relation, pk_val)`
        summed across all registered queries in this shared pool.
        """
        return self.aux_state._resident_multiplicity(relation, pk_val)

    def certify_all(self, event: WriteEvent) -> dict[str, Decision]:
        """Certify `event` against all registered queries without mutating state."""
        decisions: dict[str, Decision] = {}
        for s in self.shapes:
            if s.name in self.invalidated_queries:
                cert = Certificate(
                    shape=s.name,
                    event_id=str(event.event_id),
                    relation=normalize_relation(event.relation) or event.relation,
                    decision_kind=DecisionKind.INVALIDATE,
                    reason_code="missing_join_auxiliary_state",
                    required_evidence=[
                        *sorted(
                            s.table_required_evidence(
                                normalize_relation(event.relation)
                            )
                        ),
                        "join_aux_state",
                    ],
                )
                decisions[s.name] = Decision.invalidate(
                    "missing_join_auxiliary_state", cert
                )
            else:
                decisions[s.name] = certify_join_event(s, event, self.aux_state)
        return decisions

    def process_event(
        self,
        event: WriteEvent,
        *,
        on_invalidate: (
            Callable[[], Mapping[str, Sequence[Mapping[str, Any]]]] | None
        ) = None,
    ) -> dict[str, Decision]:
        """
        Certify `event` across all registered queries, apply repairs to each
        query's `AggregateCache` using pre-mutation shared auxiliary state, and
        then mutate the shared `JoinAuxState` once.
        """
        decisions = self.certify_all(event)
        event_rel = normalize_relation(event.relation)
        old_map = {str(k).lower(): v for k, v in (event.old or {}).items()}
        new_map = {str(k).lower(): v for k, v in (event.new or {}).items()}
        op = event.operation

        any_valid_sync = False
        any_invalidated = False

        for s in self.shapes:
            dec = decisions[s.name]
            self.aux_state.record_decision_outcome(dec)
            if dec.kind == DecisionKind.INVALIDATE:
                self.invalidated_queries.add(s.name)
                any_invalidated = True
            elif dec.kind == DecisionKind.PRESERVE:
                if event_rel in s.relations():
                    any_valid_sync = True
            elif dec.kind == DecisionKind.REPAIR:
                try:
                    old_contribs, new_contribs, _ = _compute_join_event_deltas(
                        s,
                        event_rel,
                        op,
                        old_map,
                        new_map,
                        self.aux_state,
                        record_access=False,
                    )
                    _apply_contribs_to_cache(
                        s, self.caches[s.name], old_contribs, new_contribs
                    )
                    any_valid_sync = True
                except _EvictedJoinKeyError:
                    inv_dec = Decision.invalidate(
                        "evicted_join_auxiliary_key", dec.certificate
                    )
                    decisions[s.name] = inv_dec
                    self.invalidated_queries.add(s.name)
                    any_invalidated = True
                except (TypeError, ValueError, InvalidOperation, ZeroDivisionError):
                    inv_dec = Decision.invalidate("repair_type_error", dec.certificate)
                    decisions[s.name] = inv_dec
                    self.invalidated_queries.add(s.name)
                    any_invalidated = True

        if any_valid_sync and event_rel in self.aux_state.all_relations():
            pk_col = self.aux_state.pk_col_for(event_rel)
            if (
                (op == "INSERT" and pk_col in new_map)
                or (op == "DELETE" and pk_col in old_map)
                or (op == "UPDATE" and (pk_col in old_map or pk_col in new_map))
            ):
                self.aux_state.apply_row_mutation(event_rel, op, old_map, new_map)

        if any_invalidated and on_invalidate is not None:
            live_tables = on_invalidate()
            self.rebuild(live_tables, preserve_telemetry=True, only_invalidated=True)

        return decisions

    def rebuild(
        self,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        preserve_telemetry: bool = True,
        only_invalidated: bool = False,
    ) -> None:
        for s in self.shapes:
            if not only_invalidated or s.name in self.invalidated_queries:
                self.caches[s.name] = rebuild_join_cache(
                    s, tables, primary_keys=self.primary_keys
                )
        self.invalidated_queries.clear()
        self.aux_state = JoinAuxState.from_tables(
            self.shapes[0],
            tables,
            primary_keys=self.primary_keys,
            eviction_policy=self.eviction_policy,
            lfu_decay_window=self.lfu_decay_window,
            lfu_decay_factor=self.lfu_decay_factor,
            window_ratio=self.window_ratio,
            adapt_epoch_size=self.adapt_epoch_size,
            extra_shapes=self.shapes[1:],
            max_total_rows=self.max_total_rows,
            max_rows_per_table=self.max_rows_per_table,
            max_fk_fanout=self.max_fk_fanout,
            prior_state=self.aux_state if preserve_telemetry else None,
        )


MultiQueryJoinCacheManager = SharedJoinAuxPool
