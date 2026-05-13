# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
smplcache: Dependency Fingerprint Simulator

Models the Rust middleware layer behind logical replication.
PostgreSQL is not required because the demo starts at the point where a CDC event 
has already been decoded into relation, changed columns, old row, and new row.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    PRESERVE = "preserve"
    REPAIR = "repair"
    INVALIDATE = "invalidate"
    UNSUPPORTED = "unsupported"


@dataclass
class Certificate:
    shape: str
    event_id: str
    relation: str
    decision_kind: DecisionKind
    reason_code: str
    required_evidence: list[str] = field(default_factory=list)
    available_evidence: list[str] = field(default_factory=list)
    repair_program: str | None = None
    boundary_clock: str | None = None


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    certificate: Certificate

    @classmethod
    def preserve(cls, reason: str, cert: Certificate) -> "Decision":
        cert.decision_kind = DecisionKind.PRESERVE
        cert.reason_code = reason
        return cls(DecisionKind.PRESERVE, reason, cert)

    @classmethod
    def repair(cls, reason: str, cert: Certificate) -> "Decision":
        cert.decision_kind = DecisionKind.REPAIR
        cert.reason_code = reason
        return cls(DecisionKind.REPAIR, reason, cert)

    @classmethod
    def invalidate(cls, reason: str, cert: Certificate) -> "Decision":
        cert.decision_kind = DecisionKind.INVALIDATE
        cert.reason_code = reason
        return cls(DecisionKind.INVALIDATE, reason, cert)

    @classmethod
    def unsupported(cls, reason: str, cert: Certificate) -> "Decision":
        cert.decision_kind = DecisionKind.UNSUPPORTED
        cert.reason_code = reason
        return cls(DecisionKind.UNSUPPORTED, reason, cert)

    def __str__(self) -> str:
        return self.reason


@dataclass
class QueryShape:
    name: str
    relation: str
    predicate_cols: set[str] = field(default_factory=set)
    aggregate_cols: set[str] = field(default_factory=set)
    group_cols: set[str] = field(default_factory=set)
    projection_cols: set[str] = field(default_factory=set)
    join_cols: set[str] = field(default_factory=set)
    security_cols: set[str] = field(default_factory=set)

    @property
    def fingerprint(self) -> set[str]:
        return (
            self.predicate_cols
            | self.aggregate_cols
            | self.group_cols
            | self.projection_cols
            | self.join_cols
            | self.security_cols
        )


@dataclass
class WriteEvent:
    relation: str
    changed_cols: set[str]
    old: dict[str, Any]
    new: dict[str, Any]
    event_id: str = "unknown"

    @property
    def operation(self) -> str:
        if not self.old and self.new:
            return "INSERT"
        elif self.old and not self.new:
            return "DELETE"
        return "UPDATE"


@dataclass
class AggregateCache:
    values: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, amount: int) -> None:
        if key:
            self.values[key] = self.values.get(key, 0) + amount

    def sub(self, key: str, amount: int) -> None:
        if key:
            self.values[key] = self.values.get(key, 0) - amount


def row_matches_paid(row: dict[str, Any]) -> bool:
    return row.get("status") == "paid"


def certify_event(shape: QueryShape, event: WriteEvent) -> Decision:
    cert = Certificate(
        shape=shape.name,
        event_id=event.event_id,
        relation=event.relation,
        decision_kind=DecisionKind.PRESERVE,
        reason_code=""
    )

    if event.relation != shape.relation:
        return Decision.preserve("unrelated_relation", cert)

    intersection = shape.fingerprint & event.changed_cols
    if not intersection:
        return Decision.preserve("disjoint_columns", cert)

    # For aggregate columns
    if shape.aggregate_cols:
        # Check unsupported MIN/MAX
        for agg in shape.aggregate_cols:
            if "min" in agg.lower() or "max" in agg.lower():
                cert.required_evidence = ["auxiliary_extremum_state"]
                return Decision.unsupported("min_max_requires_auxiliary_state", cert)

        old_matches = row_matches_paid(event.old) if event.old else False
        new_matches = row_matches_paid(event.new) if event.new else False
        
        has_predicate_exit = old_matches and not new_matches
        has_predicate_entry = not old_matches and new_matches
        
        required_evidence = ["amount", "customer_id", "status"]
        cert.required_evidence = required_evidence
        
        avail_evidence = list((event.old or {}).keys()) + list((event.new or {}).keys())
        cert.available_evidence = sorted(list(set(avail_evidence)))
        
        # Theorem 7 Evidence check
        if any(req not in cert.available_evidence for req in required_evidence):
            return Decision.invalidate("missing_evidence_for_repair", cert)

        cert.repair_program = "paid_sum_by_group_key"
        
        if event.operation == "UPDATE" and old_matches == new_matches and (event.old or {}).get("customer_id") == (event.new or {}).get("customer_id"):
             if (event.old or {}).get("amount") == (event.new or {}).get("amount"):
                 return Decision.preserve("noop_update", cert)
             else:
                 return Decision.repair("value_change", cert)

        if has_predicate_entry:
             return Decision.repair("predicate_entry", cert)
        if has_predicate_exit:
             return Decision.repair("predicate_exit", cert)
             
        if (event.old or {}).get("customer_id") != (event.new or {}).get("customer_id"):
             return Decision.repair("group_move", cert)
             
        return Decision.repair(event.operation.lower(), cert)

    return Decision.invalidate("shape_intersected", cert)


def process_event(shape: QueryShape, cache: AggregateCache, event: WriteEvent) -> Decision:
    decision = certify_event(shape, event)
    
    if decision.kind == DecisionKind.REPAIR:
        old_matches = row_matches_paid(event.old) if event.old else False
        new_matches = row_matches_paid(event.new) if event.new else False

        old_customer = (event.old or {}).get("customer_id")
        new_customer = (event.new or {}).get("customer_id")

        old_amount = int((event.old or {}).get("amount", 0))
        new_amount = int((event.new or {}).get("amount", 0))

        if old_matches:
            cache.sub(old_customer, old_amount)

        if new_matches:
            cache.add(new_customer, new_amount)

    return decision
