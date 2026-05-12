"""
smplcache: Dependency Fingerprint Simulator

Models the Rust middleware layer behind logical replication.
PostgreSQL is not required because the demo starts at the point where a CDC event 
has already been decoded into relation, changed columns, old row, and new row.
"""
from dataclasses import dataclass, field
from typing import Any

@dataclass
class QueryShape:
    name: str
    relation: str
    predicate_cols: set[str]
    aggregate_cols: set[str]
    group_cols: set[str]

    @property
    def fingerprint(self) -> set[str]:
        return self.predicate_cols | self.aggregate_cols | self.group_cols


@dataclass
class WriteEvent:
    relation: str
    changed_cols: set[str]
    old: dict[str, Any]
    new: dict[str, Any]


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


def process_event(shape: QueryShape, cache: AggregateCache, event: WriteEvent) -> str:
    if event.relation != shape.relation:
        return "preserved: unrelated relation"

    if not (shape.fingerprint & event.changed_cols):
        return f"preserved: changed {event.changed_cols} does not intersect {shape.fingerprint}"

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



