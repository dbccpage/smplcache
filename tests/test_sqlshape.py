# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
import unittest

from smplcache import DecisionKind, WriteEvent
from smplcache.extractor import parse_sql
from smplcache.sqlshape import check_cdc_evidence


class TestExtractor(unittest.TestCase):
    def test_basic_aggregate(self) -> None:
        sql = "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
        res = parse_sql(sql)
        self.assertTrue(res.is_supported)
        self.assertEqual(res.relation, "orders")
        self.assertEqual(res.aggregate_cols, {"amount"})
        self.assertEqual(res.group_cols, {"customer_id"})
        self.assertEqual(res.predicate_cols, {"status"})
        self.assertEqual(res.projection_cols, {"customer_id"})
        assert res.shape is not None
        self.assertEqual(res.shape.agg_fn, "sum")
        self.assertEqual(res.shape.agg_col, "amount")
        self.assertEqual(
            res.shape.required_evidence(),
            frozenset({"amount", "customer_id", "status"}),
        )

    def test_unsupported_arithmetic_expression_in_aggregate(self) -> None:
        sql = "SELECT SUM(amount * tax_rate) FROM sales WHERE region_id = 5"
        res = parse_sql(sql)
        self.assertFalse(res.is_supported)
        self.assertEqual(res.reason, "Mul")

    def test_predicate_extraction(self) -> None:
        sql = "SELECT item_id FROM inventory WHERE quantity > 100 AND location = 'warehouse'"
        res = parse_sql(sql)
        self.assertTrue(res.is_supported)
        self.assertEqual(res.predicate_cols, {"quantity", "location"})
        self.assertEqual(res.projection_cols, {"item_id"})

    def test_unsupported_window(self) -> None:
        sql = "SELECT customer_id, SUM(amount) OVER(PARTITION BY region) FROM orders"
        res = parse_sql(sql)
        self.assertFalse(res.is_supported)
        self.assertEqual(res.reason, "Window")

    def test_unsupported_udf(self) -> None:
        sql = "SELECT customer_id, COMPLEX_MATH(amount) FROM orders"
        res = parse_sql(sql)
        self.assertFalse(res.is_supported)
        self.assertEqual(res.reason, "Anonymous")

    def test_unsupported_subquery(self) -> None:
        sql = "SELECT customer_id FROM orders WHERE amount > (SELECT AVG(amount) FROM orders)"
        res = parse_sql(sql)
        self.assertFalse(res.is_supported)
        self.assertEqual(res.reason, "Subquery")


class TestSqlShape(unittest.TestCase):
    def test_evidence_sufficiency(self) -> None:
        sql = "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"

        # 1. Missing evidence (only amount is provided, missing status/customer_id)
        event_missing = WriteEvent(
            relation="orders",
            changed_cols={"amount"},
            old={"amount": 100},
            new={"amount": 150},
        )
        dec_missing = check_cdc_evidence(sql, event_missing)
        self.assertEqual(dec_missing.kind, DecisionKind.INVALIDATE)
        self.assertIn("missing_evidence", dec_missing.reason)

        # 2. Sufficient evidence
        event_sufficient = WriteEvent(
            relation="orders",
            changed_cols={"amount"},
            old={"customer_id": "c1", "status": "paid", "amount": 100},
            new={"customer_id": "c1", "status": "paid", "amount": 150},
        )
        dec_suff = check_cdc_evidence(sql, event_sufficient)
        self.assertEqual(dec_suff.kind, DecisionKind.REPAIR)
        self.assertEqual(dec_suff.reason, "value_change")


if __name__ == "__main__":
    unittest.main()
