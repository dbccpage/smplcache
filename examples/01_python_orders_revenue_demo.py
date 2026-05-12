import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smplcache import QueryShape, WriteEvent, AggregateCache, process_event

def main() -> None:
    shape = QueryShape(
        name="revenue_by_customer_paid",
        relation="orders",
        predicate_cols={"status"},
        aggregate_cols={"amount"},
        group_cols={"customer_id"},
    )

    cache = AggregateCache(values={"c1": 100})

    events = [
        WriteEvent(
            relation="orders",
            changed_cols={"shipping_address"},
            old={"customer_id": "c1", "status": "paid", "amount": 100, "shipping_address": "old"},
            new={"customer_id": "c1", "status": "paid", "amount": 100, "shipping_address": "new"},
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
