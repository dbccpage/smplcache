import unittest
from smplcache import QueryShape, WriteEvent
from cli import calculate_entropy, calculate_topomap

class TestSmplcache(unittest.TestCase):
    def test_query_shape_fingerprint(self):
        shape = QueryShape(
            name="test_shape",
            relation="orders",
            predicate_cols={"status"},
            aggregate_cols={"amount"},
            group_cols={"customer_id"}
        )
        self.assertEqual(shape.fingerprint, {"status", "amount", "customer_id"})

    def test_write_event_intersection(self):
        shape = QueryShape(
            name="test_shape",
            relation="orders",
            predicate_cols={"status"},
            aggregate_cols=set(),
            group_cols=set()
        )
        event_match = WriteEvent("orders", {"status"}, {}, {})
        event_miss = WriteEvent("orders", {"amount"}, {}, {})
        
        self.assertTrue(bool(shape.fingerprint & event_match.changed_cols))
        self.assertFalse(bool(shape.fingerprint & event_miss.changed_cols))

    def test_calculate_entropy(self):
        shape_hits = {"shape_a": 10, "shape_b": 10}
        entropy = calculate_entropy(shape_hits)
        self.assertAlmostEqual(entropy, 1.0)
        
        shape_hits_zero = {"shape_a": 0}
        self.assertEqual(calculate_entropy(shape_hits_zero), 0.0)

    def test_calculate_topomap(self):
        shapes = ["shape_a", "shape_b", "shape_c"]
        # a <-> b, b <-> c (chain)
        correlations = [
            ("shape_a <-> shape_b", 5),
            ("shape_b <-> shape_c", 3)
        ]
        topomap = calculate_topomap(shapes, correlations)
        
        self.assertEqual(topomap["active_nodes"], 3)
        self.assertEqual(topomap["edges"], 2)
        self.assertEqual(topomap["components"], 1)
        # beta_1 = E - V + C = 2 - 3 + 1 = 0
        self.assertEqual(topomap["beta_1"], 0)
        
        # a <-> b, b <-> c, c <-> a (triangle)
        correlations.append(("shape_c <-> shape_a", 2))
        topomap_cycle = calculate_topomap(shapes, correlations)
        self.assertEqual(topomap_cycle["edges"], 3)
        # beta_1 = 3 - 3 + 1 = 1
        self.assertEqual(topomap_cycle["beta_1"], 1)

if __name__ == "__main__":
    unittest.main()
