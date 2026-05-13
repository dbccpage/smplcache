# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""
smplcache: Dependency Fingerprint Simulator

Models the Rust middleware layer behind logical replication.
PostgreSQL is not required because the demo starts at the point where a CDC event 
has already been decoded into relation, changed columns, old row, and new row.
"""
from dataclasses import dataclass, field
from typing import Any
from enum import IntEnum, Enum

class EvidenceLevel(IntEnum):
    E0 = 0  # changed column names only
    E1 = 1  # changed columns + new values
    E2 = 2  # old + new values for required columns
    E3 = 3  # full before/after row images + commit metadata

class RepairClass(str, Enum):
    SINGLE_TABLE_GROUP_SUM = "SINGLE_TABLE_GROUP_SUM"
    SINGLE_TABLE_GROUP_COUNT = "SINGLE_TABLE_GROUP_COUNT"
    INNER_JOIN_KEY_PRESERVING_SUM = "INNER_JOIN_KEY_PRESERVING_SUM"
    LEFT_JOIN_AGGREGATE_SAFE = "LEFT_JOIN_AGGREGATE_SAFE"
    INVALIDATE_ONLY = "INVALIDATE_ONLY"

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
    required_evidence: EvidenceLevel = EvidenceLevel.E2
    repair_class: RepairClass = RepairClass.INVALIDATE_ONLY

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
    evidence_level: EvidenceLevel = EvidenceLevel.E3


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


class Authority(str, Enum):
    DIAGNOSTIC = "diagnostic"   # informational only, no action authority
    PROPOSAL = "proposal"       # suggested action, needs human approval
    CERTIFICATE = "certificate" # machine-checkable decision, safe to execute


@dataclass
class DecisionPacket:
    decision: str           # "PRESERVE" | "REPAIR" | "INVALIDATE"
    authority: Authority
    shape: str
    event_relation: str
    evidence_level: EvidenceLevel
    required_evidence: EvidenceLevel
    repair_class: RepairClass | None = None
    operator: str = ""
    reason: str = ""
    proof_tags: list[str] = field(default_factory=list)
    fallback: str = "INVALIDATE"
    # backward compat fields
    evidence_met: bool = True
    fingerprint_hit: bool = False

    @property
    def action(self) -> str:
        return self.decision.lower()


# Backward compatibility alias
RepairVerdict = DecisionPacket


def _collect_proof_tags(shape: 'QueryShape', event: 'WriteEvent') -> list[str]:
    tags = []
    if event.old:
        tags.append("old_values_present")
    if event.new:
        tags.append("new_values_present")
    if event.old and event.new:
        tags.append("old_new_values_present")
    if shape.predicate_cols & event.changed_cols:
        tags.append("predicate_boundary_checked")
    if shape.group_cols & event.changed_cols:
        tags.append("group_key_changed")
    elif shape.group_cols:
        tags.append("group_key_present")
    if shape.aggregate_cols:
        tags.append("aggregate_column_present")
    return tags


def classify_repairability(shape: 'QueryShape', event: 'WriteEvent') -> DecisionPacket:
    if event.relation != shape.relation:
        return DecisionPacket(
            decision="PRESERVE",
            authority=Authority.CERTIFICATE,
            shape=shape.name,
            event_relation=event.relation,
            evidence_level=event.evidence_level,
            required_evidence=shape.required_evidence,
            operator="relation_mismatch",
            reason="unrelated relation",
            proof_tags=["relation_mismatch"],
            fallback="PRESERVE"
        )

    intersection = shape.fingerprint & event.changed_cols
    if not intersection:
        return DecisionPacket(
            decision="PRESERVE",
            authority=Authority.CERTIFICATE,
            shape=shape.name,
            event_relation=event.relation,
            evidence_level=event.evidence_level,
            required_evidence=shape.required_evidence,
            operator="fingerprint_miss",
            reason=f"changed {event.changed_cols} does not intersect {shape.fingerprint}",
            proof_tags=["fingerprint_miss"],
            fallback="PRESERVE"
        )

    if event.evidence_level < shape.required_evidence:
        return DecisionPacket(
            decision="INVALIDATE",
            authority=Authority.CERTIFICATE,
            shape=shape.name,
            event_relation=event.relation,
            evidence_level=event.evidence_level,
            required_evidence=shape.required_evidence,
            operator="evidence_insufficient",
            reason=f"insufficient evidence (have {event.evidence_level.name}, need {shape.required_evidence.name})",
            proof_tags=["evidence_insufficient"],
            fallback="INVALIDATE",
            evidence_met=False,
            fingerprint_hit=True
        )

    if shape.repair_class == RepairClass.INVALIDATE_ONLY:
        return DecisionPacket(
            decision="INVALIDATE",
            authority=Authority.CERTIFICATE,
            shape=shape.name,
            event_relation=event.relation,
            evidence_level=event.evidence_level,
            required_evidence=shape.required_evidence,
            repair_class=RepairClass.INVALIDATE_ONLY,
            operator="invalidate_only_class",
            reason="shape is invalidate-only",
            proof_tags=["repair_class_invalidate_only"],
            fallback="INVALIDATE",
            evidence_met=True,
            fingerprint_hit=True
        )

    proof_tags = _collect_proof_tags(shape, event)

    # Determine operator from changed columns
    predicate_changed = bool(shape.predicate_cols & event.changed_cols)
    group_key_changed = bool(shape.group_cols & event.changed_cols)
    aggregate_changed = bool(shape.aggregate_cols & event.changed_cols)

    if group_key_changed:
        operator = "group_key_movement"
    elif predicate_changed:
        operator = "predicate_boundary_crossing"
    elif aggregate_changed:
        operator = "aggregate_delta"
    else:
        operator = "generic_repair"

    return DecisionPacket(
        decision="REPAIR",
        authority=Authority.CERTIFICATE,
        shape=shape.name,
        event_relation=event.relation,
        evidence_level=event.evidence_level,
        required_evidence=shape.required_evidence,
        repair_class=shape.repair_class,
        operator=operator,
        reason=f"repairable via {shape.repair_class.value}",
        proof_tags=proof_tags,
        fallback="INVALIDATE",
        evidence_met=True,
        fingerprint_hit=True
    )


def process_event(shape: QueryShape, cache: AggregateCache, event: WriteEvent) -> str:
    verdict = classify_repairability(shape, event)

    if verdict.action == "preserve":
        return f"preserved: {verdict.reason}"

    if verdict.action == "invalidate":
        return f"invalidated: {verdict.reason}"

    # Repair path — apply incremental update
    old_matches = row_matches_paid(event.old)
    new_matches = row_matches_paid(event.new)

    old_customer = event.old.get("customer_id")
    new_customer = event.new.get("customer_id")

    old_amount = int(event.old.get("amount", 0))
    new_amount = int(event.new.get("amount", 0))

    if old_matches:
        cache.sub(old_customer, old_amount)

    if new_matches:
        cache.add(new_customer, new_amount)

    return "incrementally updated"