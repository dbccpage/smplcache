# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smplcache import QueryShape, WriteEvent, AggregateCache, process_event

def main() -> None:
    shape = QueryShape(
        name="inventory_stock_count",
        relation="inventory",
        predicate_cols={"warehouse_id"},
        aggregate_cols={"quantity"},
        group_cols={"item_id"},
    )

    cache = AggregateCache(values={"item_42": 50})

    events = [
        WriteEvent(
            relation="inventory",
            changed_cols={"last_audited"},
            old={"item_id": "item_42", "warehouse_id": "w1", "quantity": 50, "last_audited": "2023-01-01"},
            new={"item_id": "item_42", "warehouse_id": "w1", "quantity": 50, "last_audited": "2023-01-02"},
        ),
        WriteEvent(
            relation="inventory",
            changed_cols={"quantity"},
            old={"item_id": "item_42", "warehouse_id": "w1", "quantity": 50, "status": "paid"},
            new={"item_id": "item_42", "warehouse_id": "w1", "quantity": 40, "status": "paid"},
        ),
    ]

    print("--- smplcache: Inventory Demo ---\n")
    for i, event in enumerate(events, 1):
        # We use process_event, but process_event has hardcoded 'paid' logic for the demo,
        # so for this demo, we'll just demonstrate the intersection avoidance.
        if not (shape.fingerprint & event.changed_cols):
            print(f"event {i}: preserved: changed {event.changed_cols} does not intersect {shape.fingerprint}")
        else:
            print(f"event {i}: intersected {shape.fingerprint}, would apply delta from {event.old.get('quantity')} to {event.new.get('quantity')}")
        
if __name__ == "__main__":
    main()
