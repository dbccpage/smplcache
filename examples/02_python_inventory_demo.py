# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
from decimal import Decimal
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smplcache import (
    AggregateCache,
    Predicate,
    QueryShape,
    WriteEvent,
    process_event,
)


def main() -> None:
    shape = QueryShape(
        name="inventory_stock_count",
        relation="inventory",
        agg_fn="sum",
        agg_col="quantity",
        group_cols=("item_id",),
        predicate=Predicate((("warehouse_id", "=", "w1"),)),
    )

    cache = AggregateCache(values={"item_42": Decimal(50)})

    events = [
        WriteEvent(
            relation="inventory",
            changed_cols={"last_audited"},
            old={
                "item_id": "item_42",
                "warehouse_id": "w1",
                "quantity": 50,
                "last_audited": "2023-01-01",
            },
            new={
                "item_id": "item_42",
                "warehouse_id": "w1",
                "quantity": 50,
                "last_audited": "2023-01-02",
            },
        ),
        WriteEvent(
            relation="inventory",
            changed_cols={"quantity"},
            old={"item_id": "item_42", "warehouse_id": "w1", "quantity": 50},
            new={"item_id": "item_42", "warehouse_id": "w1", "quantity": 40},
        ),
    ]

    print("--- smplcache: Inventory Demo ---\n")
    for i, event in enumerate(events, 1):
        result = process_event(shape, cache, event)
        print(f"event {i}: {result}")
        print(f"cache: {cache.values}\n")


if __name__ == "__main__":
    main()
