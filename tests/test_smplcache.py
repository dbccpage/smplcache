# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
from decimal import Decimal
import unittest

from smplcache import (
    AggregateCache,
    Certificate,
    Decision,
    DecisionKind,
    Predicate,
    QueryShape,
    WriteEvent,
    process_event,
)
from smplcache.cli import calculate_entropy, calculate_topomap


class TestSmplcache(unittest.TestCase):
    def test_query_shape_fingerprint(self) -> None:
        shape = QueryShape(
            name="test_shape",
            relation="orders",
            agg_fn="sum",
            agg_col="amount",
            group_cols=("customer_id",),
            predicate=Predicate((("status", "=", "paid"),)),
        )
        self.assertEqual(
            shape.fingerprint(),
            frozenset({"status", "amount", "customer_id"}),
        )
        self.assertEqual(
            shape.fingerprint,
            frozenset({"status", "amount", "customer_id"}),
        )

    def test_write_event_intersection(self) -> None:
        shape = QueryShape(
            name="test_shape",
            relation="orders",
            predicate=Predicate((("status", "=", "paid"),)),
        )
        event_match = WriteEvent("orders", {"status"}, {}, {})
        event_miss = WriteEvent("orders", {"amount"}, {}, {})

        self.assertTrue(bool(shape.fingerprint() & event_match.changed_cols))
        self.assertFalse(bool(shape.fingerprint() & event_miss.changed_cols))

    def test_decision_constructors(self) -> None:
        cert1 = Certificate(
            shape="shape_a",
            event_id="1",
            relation="r",
            decision_kind=DecisionKind.PRESERVE,
            reason_code="",
        )
        preserve = Decision.preserve("no intersection", cert1)
        cert2 = Certificate(
            shape="shape_b",
            event_id="2",
            relation="r",
            decision_kind=DecisionKind.REPAIR,
            reason_code="",
            repair_program="sum_by_group",
        )
        repair = Decision.repair("delta applied", cert2)
        cert3 = Certificate(
            shape="shape_c",
            event_id="3",
            relation="r",
            decision_kind=DecisionKind.INVALIDATE,
            reason_code="",
        )
        invalidate = Decision.invalidate("obstructed", cert3)
        cert4 = Certificate(
            shape="shape_d",
            event_id="4",
            relation="r",
            decision_kind=DecisionKind.UNSUPPORTED,
            reason_code="",
        )
        unsupported = Decision.unsupported("no certifier", cert4)

        self.assertEqual(preserve.kind, DecisionKind.PRESERVE)
        self.assertEqual(repair.kind, DecisionKind.REPAIR)
        self.assertEqual(invalidate.kind, DecisionKind.INVALIDATE)
        self.assertEqual(unsupported.kind, DecisionKind.UNSUPPORTED)
        self.assertEqual(str(preserve), "no intersection")
        self.assertEqual(preserve.certificate.shape, "shape_a")
        self.assertEqual(repair.certificate.repair_program, "sum_by_group")

    def test_process_event_returns_preserve_decision(self) -> None:
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
        event = WriteEvent(
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
        )

        decision = process_event(shape, cache, event)

        self.assertEqual(decision.kind, DecisionKind.PRESERVE)
        self.assertEqual(cache.values, {"c1": Decimal(100)})
        self.assertIn("disjoint_columns", decision.reason)

    def test_process_event_returns_repair_decision(self) -> None:
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
        event = WriteEvent(
            relation="orders",
            changed_cols={"amount"},
            old={"customer_id": "c1", "status": "paid", "amount": 100},
            new={"customer_id": "c1", "status": "paid", "amount": 150},
        )

        decision = process_event(shape, cache, event)

        self.assertEqual(decision.kind, DecisionKind.REPAIR)
        self.assertEqual(decision.certificate.repair_program, "sum_by_group")
        self.assertEqual(cache.values, {"c1": Decimal(150)})

    def test_calculate_entropy(self) -> None:
        shape_hits = {"shape_a": 10, "shape_b": 10}
        entropy = calculate_entropy(shape_hits)
        self.assertAlmostEqual(entropy, 1.0)

        shape_hits_zero = {"shape_a": 0}
        self.assertEqual(calculate_entropy(shape_hits_zero), 0.0)

    def test_calculate_topomap(self) -> None:
        shapes = ["shape_a", "shape_b", "shape_c"]
        correlations = [
            ("shape_a <-> shape_b", 5),
            ("shape_b <-> shape_c", 3),
        ]
        topomap = calculate_topomap(shapes, correlations)

        self.assertEqual(topomap["active_nodes"], 3)
        self.assertEqual(topomap["edges"], 2)
        self.assertEqual(topomap["components"], 1)
        self.assertEqual(topomap["beta_1"], 0)

        correlations.append(("shape_c <-> shape_a", 2))
        topomap_cycle = calculate_topomap(shapes, correlations)
        self.assertEqual(topomap_cycle["edges"], 3)
        self.assertEqual(topomap_cycle["beta_1"], 1)


if __name__ == "__main__":
    unittest.main()
