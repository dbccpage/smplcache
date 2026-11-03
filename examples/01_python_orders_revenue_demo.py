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
        name="revenue_by_customer_paid",
        relation="orders",
        agg_fn="sum",
        agg_col="amount",
        group_cols=("customer_id",),
        predicate=Predicate((("status", "=", "paid"),)),
    )

    cache = AggregateCache.from_rows(
        shape, [{"customer_id": "c1", "status": "paid", "amount": 100}]
    )

    events = [
        WriteEvent(
            relation="orders",
            changed_cols={"shipping_address"},
            old={
                "customer_id": "c1",
                "status": "paid",
                "amount": 100,
                "shipping_address": "old",
            },
            new={
                "customer_id": "c1",
                "status": "paid",
                "amount": 100,
                "shipping_address": "new",
            },
        ),
        WriteEvent(
            relation="orders",
            changed_cols={"amount"},
            old={"customer_id": "c1", "status": "paid", "amount": 100},
            new={"customer_id": "c1", "status": "paid", "amount": 150},
        ),
        WriteEvent(
            relation="orders",
            changed_cols={"status"},
            old={"customer_id": "c2", "status": "pending", "amount": 80},
            new={"customer_id": "c2", "status": "paid", "amount": 80},
        ),
    ]

    print("--- smplcache: Orders Revenue Demo ---\n")
    for i, event in enumerate(events, 1):
        result = process_event(shape, cache, event)
        print(f"event {i}: {result}")
        print(f"cache: {cache.values}\n")

    print("Final aggregate cache:")
    print(cache.values)


if __name__ == "__main__":
    main()
