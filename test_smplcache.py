import unittest
from smplcache import (QueryShape, WriteEvent, EvidenceLevel, RepairClass,
                       Authority, DecisionPacket, RepairVerdict, classify_repairability)
from cli import calculate_entropy, calculate_invalidation_graph

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

    def test_calculate_invalidation_graph(self):
        shapes = ["shape_a", "shape_b", "shape_c"]
        # a <-> b, b <-> c (chain)
        correlations = [
            ("shape_a <-> shape_b", 5),
            ("shape_b <-> shape_c", 3)
        ]
        topomap = calculate_invalidation_graph(shapes, correlations)
        
        self.assertEqual(topomap["active_nodes"], 3)
        self.assertEqual(topomap["edges"], 2)
        self.assertEqual(topomap["components"], 1)
        # cycles = E - V + C = 2 - 3 + 1 = 0
        self.assertEqual(topomap["cycles"], 0)
        
        # a <-> b, b <-> c, c <-> a (triangle)
        correlations.append(("shape_c <-> shape_a", 2))
        topomap_cycle = calculate_invalidation_graph(shapes, correlations)
        self.assertEqual(topomap_cycle["edges"], 3)
        # cycles = 3 - 3 + 1 = 1
        self.assertEqual(topomap_cycle["cycles"], 1)

    def test_evidence_rejection(self):
        shape = QueryShape(
            name="rev", relation="orders",
            predicate_cols={"status"}, aggregate_cols={"amount"},
            group_cols={"customer_id"},
            required_evidence=EvidenceLevel.E2,
            repair_class=RepairClass.SINGLE_TABLE_GROUP_SUM
        )
        event = WriteEvent("orders", {"amount"}, {}, {}, evidence_level=EvidenceLevel.E0)
        verdict = classify_repairability(shape, event)
        self.assertEqual(verdict.action, "invalidate")
        self.assertEqual(verdict.decision, "INVALIDATE")
        self.assertEqual(verdict.authority, Authority.CERTIFICATE)
        self.assertEqual(verdict.operator, "evidence_insufficient")
        self.assertFalse(verdict.evidence_met)
        self.assertTrue(verdict.fingerprint_hit)
        self.assertIn("evidence_insufficient", verdict.proof_tags)

    def test_invalidate_only_class(self):
        shape = QueryShape(
            name="dash", relation="orders",
            predicate_cols={"status"}, aggregate_cols=set(),
            group_cols=set(),
            required_evidence=EvidenceLevel.E1,
            repair_class=RepairClass.INVALIDATE_ONLY
        )
        event = WriteEvent("orders", {"status"}, {}, {}, evidence_level=EvidenceLevel.E3)
        verdict = classify_repairability(shape, event)
        self.assertEqual(verdict.action, "invalidate")
        self.assertEqual(verdict.decision, "INVALIDATE")
        self.assertEqual(verdict.authority, Authority.CERTIFICATE)
        self.assertTrue(verdict.evidence_met)
        self.assertTrue(verdict.fingerprint_hit)
        self.assertIn("invalidate-only", verdict.reason)
        self.assertIn("repair_class_invalidate_only", verdict.proof_tags)

    def test_repairable_sum(self):
        shape = QueryShape(
            name="rev", relation="orders",
            predicate_cols={"status"}, aggregate_cols={"amount"},
            group_cols={"customer_id"},
            required_evidence=EvidenceLevel.E2,
            repair_class=RepairClass.SINGLE_TABLE_GROUP_SUM
        )
        event = WriteEvent("orders", {"amount"}, {"amount": 100}, {"amount": 150}, evidence_level=EvidenceLevel.E3)
        verdict = classify_repairability(shape, event)
        self.assertEqual(verdict.action, "repair")
        self.assertEqual(verdict.decision, "REPAIR")
        self.assertEqual(verdict.authority, Authority.CERTIFICATE)
        self.assertEqual(verdict.repair_class, RepairClass.SINGLE_TABLE_GROUP_SUM)
        self.assertEqual(verdict.operator, "aggregate_delta")
        self.assertTrue(verdict.evidence_met)
        self.assertTrue(verdict.fingerprint_hit)
        self.assertIn("old_new_values_present", verdict.proof_tags)
        self.assertIn("aggregate_column_present", verdict.proof_tags)

    def test_preserve_no_intersection(self):
        shape = QueryShape(
            name="rev", relation="orders",
            predicate_cols={"status"}, aggregate_cols={"amount"},
            group_cols={"customer_id"},
            required_evidence=EvidenceLevel.E2,
            repair_class=RepairClass.SINGLE_TABLE_GROUP_SUM
        )
        event = WriteEvent("orders", {"shipping_address"}, {}, {}, evidence_level=EvidenceLevel.E3)
        verdict = classify_repairability(shape, event)
        self.assertEqual(verdict.action, "preserve")
        self.assertFalse(verdict.fingerprint_hit)

    def test_preserve_different_relation(self):
        shape = QueryShape(
            name="inv", relation="inventory",
            predicate_cols=set(), aggregate_cols={"quantity"},
            group_cols={"item_id"},
            required_evidence=EvidenceLevel.E2,
            repair_class=RepairClass.SINGLE_TABLE_GROUP_SUM
        )
        event = WriteEvent("orders", {"status"}, {}, {}, evidence_level=EvidenceLevel.E3)
        verdict = classify_repairability(shape, event)
        self.assertEqual(verdict.action, "preserve")
        self.assertEqual(verdict.authority, Authority.CERTIFICATE)
        self.assertEqual(verdict.operator, "relation_mismatch")

    def test_decision_packet_proof_tags(self):
        shape = QueryShape(
            name="rev", relation="orders",
            predicate_cols={"status"}, aggregate_cols={"amount"},
            group_cols={"customer_id"},
            required_evidence=EvidenceLevel.E2,
            repair_class=RepairClass.SINGLE_TABLE_GROUP_SUM
        )
        # Group key movement
        event = WriteEvent(
            "orders", {"customer_id"},
            {"customer_id": "c1", "amount": 100},
            {"customer_id": "c3", "amount": 100},
            evidence_level=EvidenceLevel.E3
        )
        verdict = classify_repairability(shape, event)
        self.assertEqual(verdict.decision, "REPAIR")
        self.assertEqual(verdict.operator, "group_key_movement")
        self.assertIn("group_key_changed", verdict.proof_tags)
        self.assertIn("old_new_values_present", verdict.proof_tags)
        self.assertIn("aggregate_column_present", verdict.proof_tags)
        self.assertEqual(verdict.fallback, "INVALIDATE")

if __name__ == "__main__":
    unittest.main()
