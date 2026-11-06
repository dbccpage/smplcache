# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from unittest.mock import patch

import pytest

from smplcache import (
    DEFAULT_MAX_CERTIFICATES,
    AggregateCache,
    Certificate,
    Decision,
    DecisionKind,
    InMemoryStoreAdapter,
    PostgresLogicalStreamDecoder,
    Predicate,
    QueryShape,
    RedisStoreAdapter,
    RegisteredShapesRegistry,
    StoreApplicationError,
    WriteEvent,
    certify_event,
    certify_join_event,
    check_cdc_evidence,
    parse_join_sql,
    process_event,
)


def _orders_shape() -> QueryShape:
    return QueryShape(
        relation="orders",
        agg_fn="sum",
        agg_col="amount",
        group_cols=("customer_id",),
        predicate=Predicate.from_eq("status", "paid"),
        projection_cols={"customer_id", "amount"},
        name="paid_orders_sum",
    )


class _FakeRedisClient:
    def __init__(
        self,
        *,
        fail_delete: bool = False,
        fail_read: bool = False,
        fail_receipt: bool = False,
        sticky_delete: bool = False,
        unacknowledged_receipt: bool = False,
    ) -> None:
        self.data: dict[str, str] = {}
        self.fail_delete = fail_delete
        self.fail_read = fail_read
        self.fail_receipt = fail_receipt
        self.sticky_delete = sticky_delete
        self.unacknowledged_receipt = unacknowledged_receipt

    def __bool__(self) -> bool:
        # Ensure `if self.redis is not None:` is used rather than truthiness
        return False

    def get(self, key: str) -> str | None:
        if self.fail_read:
            raise OSError("redis read failed")
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        if key.startswith("smpl:cert:") and self.fail_receipt:
            raise RuntimeError("redis receipt write failed")
        if key.startswith("smpl:cert:") and self.unacknowledged_receipt:
            return False
        self.data[key] = value
        return True

    def delete(self, key: str) -> int:
        if self.fail_delete:
            raise RuntimeError("redis delete failed")
        if self.sticky_delete:
            return 0
        return int(self.data.pop(key, None) is not None)


# ---------------------------------------------------------------------------
# 1. CDC Evidence Completeness & Type/Depth Validation
# ---------------------------------------------------------------------------


def test_incomplete_cdc_old_image_invalidates_update() -> None:
    shape = _orders_shape()
    event = WriteEvent(
        relation="orders",
        old={"id": 1},
        new={"id": 1, "customer_id": 10, "status": "paid", "amount": 50, "notes": "b"},
        event_id="evt_partial_old",
        operation="UPDATE",
        old_image_complete=False,
        new_image_complete=True,
        changed_cols_complete=False,
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.INVALIDATE
    assert decision.reason == "incomplete_update_images"
    assert decision.certificate.old_image_complete is False
    assert decision.certificate.changed_cols_complete is False


def test_incomplete_cdc_insert_or_delete_image_invalidates() -> None:
    shape = _orders_shape()
    insert_event = WriteEvent(
        relation="orders",
        old={},
        new={"id": 1},
        event_id="evt_partial_insert",
        operation="INSERT",
        old_image_complete=False,
        new_image_complete=False,
        changed_cols_complete=False,
    )
    delete_event = WriteEvent(
        relation="orders",
        old={"id": 1},
        new={},
        event_id="evt_partial_delete",
        operation="DELETE",
        old_image_complete=False,
        new_image_complete=False,
        changed_cols_complete=False,
    )

    insert_decision = certify_event(shape, insert_event)
    delete_decision = certify_event(shape, delete_event)

    assert insert_decision.kind == DecisionKind.INVALIDATE
    assert insert_decision.reason == "incomplete_or_conflicting_insert_image"
    assert delete_decision.kind == DecisionKind.INVALIDATE
    assert delete_decision.reason == "incomplete_or_conflicting_delete_image"


def test_incomplete_changed_column_evidence_invalidates_update() -> None:
    shape = _orders_shape()
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50, "notes": "a"},
        new={"id": 1, "customer_id": 10, "status": "paid", "amount": 50, "notes": "b"},
        event_id="evt_incomplete_changed_cols",
        operation="UPDATE",
        old_image_complete=True,
        new_image_complete=True,
        changed_cols_complete=False,
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.INVALIDATE
    assert decision.reason == "unknown_changed_columns"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"old_image_complete": "yes"},
        {"new_image_complete": 1},
        {"changed_cols_complete": 0},
        {"schema_change": "false"},
    ],
)
def test_malformed_write_event_boolean_flags_raise_type_error(
    kwargs: dict[str, object],
) -> None:
    base_kwargs: dict[str, object] = {
        "relation": "orders",
        "old": {"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
        "new": {"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
        "event_id": "evt_bad_bool",
    }
    base_kwargs.update(kwargs)

    with pytest.raises(TypeError):
        WriteEvent(**base_kwargs)  # type: ignore[arg-type]


def test_complete_cdc_evidence_allows_unrelated_column_preserve() -> None:
    shape = _orders_shape()
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50, "notes": "a"},
        new={"id": 1, "customer_id": 10, "status": "paid", "amount": 50, "notes": "b"},
        event_id="evt_complete_notes",
        operation="UPDATE",
        old_image_complete=True,
        new_image_complete=True,
        changed_cols_complete=True,
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.PRESERVE
    assert decision.reason == "disjoint_columns"
    assert decision.certificate.operation == "UPDATE"
    assert decision.certificate.old_image_complete is True
    assert decision.certificate.new_image_complete is True
    assert decision.certificate.changed_cols_complete is True


@pytest.mark.parametrize(
    ("old_row", "new_row", "changed_col"),
    [
        ({"id": 1, "meta": {"flag": 1}}, {"id": 1, "meta": {"flag": True}}, "meta"),
        ({"id": 1, "items": [0]}, {"id": 1, "items": [False]}, "items"),
        ({"id": 1, "amount": 1}, {"id": 1, "amount": 1.0}, "amount"),
    ],
)
def test_nested_and_scalar_type_changes_are_not_treated_as_unchanged(
    old_row: dict[str, object],
    new_row: dict[str, object],
    changed_col: str,
) -> None:
    shape = QueryShape(
        name="row_projection",
        relation="orders",
        projection_cols={changed_col},
    )
    event = WriteEvent(
        relation="orders",
        old=old_row,
        new=new_row,
        event_id=f"evt_type_change_{changed_col}",
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.INVALIDATE
    assert decision.reason == "shape_intersected"
    assert changed_col in event.derived_changed_cols()


def test_nested_equal_json_values_preserve_when_types_match() -> None:
    shape = QueryShape(
        name="row_projection",
        relation="orders",
        projection_cols={"meta"},
    )
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "meta": {"flag": 1, "tags": ["a", None]}, "notes": "old"},
        new={"id": 1, "meta": {"flag": 1, "tags": ["a", None]}, "notes": "new"},
        event_id="evt_nested_equal",
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.PRESERVE
    assert decision.reason == "disjoint_columns"


@pytest.mark.parametrize(
    "bad_value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {"not", "json"},
        object(),
    ],
)
def test_unsupported_row_values_fail_closed_to_invalidate(bad_value: object) -> None:
    shape = QueryShape(
        name="row_projection",
        relation="orders",
        projection_cols={"meta"},
    )
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "meta": bad_value},
        new={"id": 1, "meta": bad_value},
        event_id="evt_unsupported_value",
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.INVALIDATE
    assert decision.reason == "unsupported_row_value"


def test_cyclic_and_deep_row_values_fail_closed_without_recursion_error() -> None:
    shape = QueryShape(
        name="row_projection",
        relation="orders",
        projection_cols={"meta"},
    )
    cyclic: list[object] = []
    cyclic.append(cyclic)
    cyclic_event = WriteEvent(
        relation="orders",
        old={"id": 1, "meta": cyclic},
        new={"id": 1, "meta": cyclic},
        event_id="evt_cyclic_value",
    )

    deep_value: object = "leaf"
    for _ in range(65):
        deep_value = [deep_value]
    deep_event = WriteEvent(
        relation="orders",
        old={"id": 1, "meta": deep_value},
        new={"id": 1, "meta": deep_value},
        event_id="evt_deep_value",
    )

    cyclic_decision = certify_event(shape, cyclic_event)
    deep_decision = certify_event(shape, deep_event)

    assert cyclic_decision.kind == DecisionKind.INVALIDATE
    assert cyclic_decision.reason == "unsupported_row_value"
    assert deep_decision.kind == DecisionKind.INVALIDATE
    assert deep_decision.reason == "unsupported_row_value"


def test_depth_64_row_value_is_supported() -> None:
    shape = QueryShape(
        name="row_projection",
        relation="orders",
        projection_cols={"meta"},
    )
    value: object = "leaf"
    for _ in range(64):
        value = [value]
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "meta": value, "notes": "old"},
        new={"id": 1, "meta": value, "notes": "new"},
        event_id="evt_depth_64_value",
    )

    decision = certify_event(shape, event)

    assert decision.kind == DecisionKind.PRESERVE
    assert decision.reason == "disjoint_columns"


# ---------------------------------------------------------------------------
# 2. PostgresLogicalStreamDecoder (wal2json v1 & pgoutput + CACHE-02 Fix)
# ---------------------------------------------------------------------------


def test_wal2json_decoder_marks_key_only_old_images_incomplete() -> None:
    shape = _orders_shape()
    decoder = PostgresLogicalStreamDecoder()
    payload = {
        "change": [
            {
                "kind": "update",
                "schema": "public",
                "table": "orders",
                "columnnames": ["id", "customer_id", "status", "amount", "notes"],
                "columnvalues": [1, 10, "paid", 50, "updated"],
                "oldkeys": {
                    "keynames": ["id"],
                    "keyvalues": [1],
                },
            },
            {
                "kind": "delete",
                "schema": "public",
                "table": "orders",
                "oldkeys": {
                    "keynames": ["id"],
                    "keyvalues": [1],
                },
            },
        ]
    }

    events = decoder.decode_wal2json_payload(payload)

    assert len(events) == 2
    assert events[0].old == {"id": 1}
    assert events[0].old_image_complete is False
    assert events[0].new_image_complete is False
    assert events[0].changed_cols_complete is False
    assert certify_event(shape, events[0]).kind == DecisionKind.INVALIDATE

    assert events[1].operation == "DELETE"
    assert events[1].new == {}
    assert events[1].changed_cols == {"id"}
    assert events[1].old_image_complete is False
    assert events[1].new_image_complete is False
    assert events[1].changed_cols_complete is False
    assert certify_event(shape, events[1]).kind == DecisionKind.INVALIDATE


def test_pgoutput_decoder_requires_explicit_old_image_completeness() -> None:
    shape = _orders_shape()
    decoder = PostgresLogicalStreamDecoder()
    event = decoder.decode_pgoutput_dict(
        "orders",
        "U",
        {"id": 1},
        {
            "id": 1,
            "customer_id": 10,
            "status": "paid",
            "amount": 50,
            "notes": "updated",
        },
        event_id="0/16B3748",
    )
    decision = certify_event(shape, event)

    assert event.operation == "UPDATE"
    assert event.old_image_complete is False
    assert event.new_image_complete is False
    assert event.changed_cols_complete is False
    assert "status" in event.changed_cols
    assert decision.kind == DecisionKind.INVALIDATE
    assert decision.reason == "incomplete_update_images"


def test_cache_02_wal2json_top_level_nextlsn_prevents_event_id_collision() -> None:
    """
    Regression test for CACHE-02: wal2json v1 top-level `nextlsn` is propagated
    into each decoded WriteEvent's `event_id` (`wal_{nextlsn}_{idx}`) so
    events from distinct WAL batches never collide on `wal_None_0`.
    """
    decoder = PostgresLogicalStreamDecoder()
    batch_1 = {
        "nextlsn": "0/16B3748",
        "change": [
            {
                "kind": "insert",
                "schema": "public",
                "table": "orders",
                "columnnames": ["id", "customer_id", "status", "amount"],
                "columnvalues": [1, 10, "paid", 50],
            }
        ],
    }
    batch_2 = {
        "nextlsn": "0/16B3790",
        "change": [
            {
                "kind": "insert",
                "schema": "public",
                "table": "orders",
                "columnnames": ["id", "customer_id", "status", "amount"],
                "columnvalues": [2, 10, "paid", 75],
            }
        ],
    }

    evts_1 = decoder.decode_wal2json_payload(batch_1)
    evts_2 = decoder.decode_wal2json_payload(batch_2)

    assert evts_1[0].event_id == "wal_0/16B3748_0"
    assert evts_2[0].event_id == "wal_0/16B3790_0"
    assert evts_1[0].event_id != evts_2[0].event_id
    assert evts_1[0].boundary_clock == "0/16B3748"
    assert evts_2[0].boundary_clock == "0/16B3790"


@pytest.mark.parametrize(
    "payload",
    [
        {"change": "not-a-list"},
        {"change": ["not-an-object"]},
        {"change": [{"kind": "truncate", "table": "orders"}]},
        {"change": [{"kind": "update", "action": "DELETE", "table": "orders"}]},
        {"change": [{"kind": "insert", "table": ""}]},
        {
            "change": [
                {
                    "kind": "insert",
                    "table": "orders",
                    "columnnames": ["id", "amount"],
                    "columnvalues": [1],
                }
            ]
        },
        {
            "change": [
                {
                    "kind": "insert",
                    "table": "orders",
                    "columnnames": ["id", "id"],
                    "columnvalues": [1, 2],
                }
            ]
        },
        {
            "change": [
                {
                    "kind": "update",
                    "table": "orders",
                    "columnnames": ["id"],
                    "columnvalues": [1],
                    "oldkeys": {"keynames": ["id"], "keyvalues": []},
                }
            ]
        },
        {
            "change": [
                {
                    "kind": "update",
                    "table": "orders",
                    "columnnames": ["id"],
                    "columnvalues": [1],
                    "oldvalues": [2],
                    "oldkeys": {"keynames": ["id"], "keyvalues": [3]},
                }
            ]
        },
        {
            "change": [
                {
                    "kind": "update",
                    "table": "orders",
                    "columnnames": ["id"],
                    "columnvalues": [1],
                    "oldkeys": "not-a-dict",
                }
            ]
        },
    ],
)
def test_wal2json_decoder_rejects_malformed_payloads(
    payload: dict[str, object],
) -> None:
    decoder = PostgresLogicalStreamDecoder()
    with pytest.raises(ValueError):
        decoder.decode_wal2json_payload(payload)


@pytest.mark.parametrize(
    ("table", "action", "old_tuple", "new_tuple"),
    [
        ("orders", "X", None, None),
        ("", "U", {"id": 1}, {"id": 1}),
        ("orders", "U", ["bad"], {"id": 1}),  # type: ignore[list-item]
        ("orders", "I", {"id": 1}, {"id": 1}),
        ("orders", "D", {"id": 1}, {"id": 1}),
    ],
)
def test_pgoutput_decoder_rejects_malformed_payloads(
    table: str,
    action: str,
    old_tuple: dict[str, object] | None,
    new_tuple: dict[str, object] | None,
) -> None:
    decoder = PostgresLogicalStreamDecoder()
    with pytest.raises(ValueError):
        decoder.decode_pgoutput_dict(table, action, old_tuple, new_tuple)


# ---------------------------------------------------------------------------
# 3. Store Adapters, Receipt Cap (A-45), CACHE-02 & CACHE-03 Fixes
# ---------------------------------------------------------------------------


def test_in_memory_store_adapter_default_certificate_cap_is_100k() -> None:
    store = InMemoryStoreAdapter()
    assert DEFAULT_MAX_CERTIFICATES == 100_000
    assert store.max_certificates == DEFAULT_MAX_CERTIFICATES


@pytest.mark.parametrize("bad_cap", [0, -1, True, False, "100"])
def test_in_memory_store_adapter_rejects_invalid_certificate_cap(
    bad_cap: object,
) -> None:
    with pytest.raises(ValueError, match="max_certificates must be a positive int"):
        InMemoryStoreAdapter(max_certificates=bad_cap)  # type: ignore[arg-type]


def test_in_memory_store_adapter_refuses_new_receipt_above_cap_before_mutation() -> (
    None
):
    store = InMemoryStoreAdapter(max_certificates=1)
    store.set("k1", "v1")
    store.set("k2", "v2")

    first = Decision.preserve(
        "unrelated_relation",
        Certificate(
            shape="paid_orders_sum",
            event_id="evt_1",
            relation="orders",
            decision_kind=DecisionKind.PRESERVE,
            reason_code="unrelated_relation",
        ),
    )
    second = Decision.invalidate(
        "predicate_transition",
        Certificate(
            shape="paid_orders_sum",
            event_id="evt_2",
            relation="orders",
            decision_kind=DecisionKind.INVALIDATE,
            reason_code="predicate_transition",
        ),
    )

    store.apply_certified_decision("k1", first)
    assert len(store.get_certificates()) == 1

    with pytest.raises(StoreApplicationError, match="receipt cap of 1"):
        store.apply_certified_decision("k2", second)

    # Pre-mutation refusal: k2 is not deleted when receipt capacity is exceeded.
    assert store.get("k2") == "v2"
    assert len(store.get_certificates()) == 1


def test_store_adapters_downgrade_unexecuted_repair_to_invalidation() -> None:
    shape = _orders_shape()
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
        new={"id": 1, "customer_id": 10, "status": "paid", "amount": 70},
        event_id="evt_repair_candidate",
    )
    proposed = certify_event(shape, event)
    assert proposed.kind == DecisionKind.REPAIR
    before = asdict(proposed.certificate)

    memory_store = InMemoryStoreAdapter()
    memory_store.set("paid_orders_sum", {"10": 50})
    applied_memory = memory_store.apply_certified_decision("paid_orders_sum", proposed)

    assert applied_memory.decision_kind == DecisionKind.INVALIDATE
    assert applied_memory.reason_code == "repair_execution_unavailable"
    assert applied_memory.repair_program is None
    assert memory_store.get("paid_orders_sum") is None
    assert asdict(proposed.certificate) == before

    client = _FakeRedisClient()
    redis_store = RedisStoreAdapter(client)
    redis_store.set("paid_orders_sum", {"10": 50})
    applied_redis = redis_store.apply_certified_decision("paid_orders_sum", proposed)

    assert applied_redis.decision_kind == DecisionKind.INVALIDATE
    assert applied_redis.reason_code == "repair_execution_unavailable"
    assert redis_store.get("paid_orders_sum") is None
    receipt = json.loads(client.data["smpl:cert:paid_orders_sum:evt_repair_candidate"])
    assert receipt["decision"] == "invalidate"
    assert receipt["reason"] == "repair_execution_unavailable"


@pytest.mark.parametrize("failure", ["delete", "read", "remaining-null"])
def test_memory_store_failures_do_not_append_success_receipt(failure: str) -> None:
    adapter = InMemoryStoreAdapter()
    adapter.set("paid_orders_sum", None)
    cert = Certificate(
        shape="paid_orders_sum",
        event_id="evt_fail",
        relation="orders",
        decision_kind=DecisionKind.INVALIDATE,
        reason_code="predicate_transition",
    )
    decision = Decision.invalidate("predicate_transition", cert)
    method = "get" if failure == "read" else "delete"
    kwargs = (
        {"return_value": False}
        if failure == "remaining-null"
        else {"side_effect": OSError("injected")}
    )
    with patch.object(adapter, method, **kwargs):
        with pytest.raises(StoreApplicationError):
            adapter.apply_certified_decision("paid_orders_sum", decision)
    assert adapter.get_certificates() == []


def test_redis_store_failures_raise_store_application_error() -> None:
    cert = Certificate(
        shape="paid_orders_sum",
        event_id="evt_fail",
        relation="orders",
        decision_kind=DecisionKind.INVALIDATE,
        reason_code="predicate_transition",
    )
    decision = Decision.invalidate("predicate_transition", cert)

    for kwargs in (
        {"fail_delete": True},
        {"fail_read": True},
        {"sticky_delete": True},
        {"fail_receipt": True},
        {"unacknowledged_receipt": True},
    ):
        client = _FakeRedisClient(**kwargs)  # type: ignore[arg-type]
        redis_store = RedisStoreAdapter(client)
        redis_store.data_seed = True  # type: ignore[attr-defined]
        client.data["paid_orders_sum"] = json.dumps({"10": 50})
        with pytest.raises(StoreApplicationError):
            redis_store.apply_certified_decision("paid_orders_sum", decision)
        assert "smpl:cert:paid_orders_sum:evt_fail" not in client.data


def test_cache_02_store_adapter_rejects_conflicting_duplicate_receipt_key() -> None:
    """
    Regression test for CACHE-02: if two events for the same cache key carry
    the same `event_id` with conflicting certificate contents, the store adapter
    rejects the overwrite with `StoreApplicationError` rather than silently
    clobbering the prior receipt.
    """
    store = InMemoryStoreAdapter()
    store.set("k1", "v1")

    first = Decision.preserve(
        "disjoint_columns",
        Certificate(
            shape="paid_orders_sum",
            event_id="dup_evt",
            relation="orders",
            decision_kind=DecisionKind.PRESERVE,
            reason_code="disjoint_columns",
        ),
    )
    conflicting = Decision.invalidate(
        "predicate_transition",
        Certificate(
            shape="paid_orders_sum",
            event_id="dup_evt",
            relation="orders",
            decision_kind=DecisionKind.INVALIDATE,
            reason_code="predicate_transition",
        ),
    )

    store.apply_certified_decision("k1", first)
    # Idempotent replay of the exact same decision is allowed:
    store.apply_certified_decision("k1", first)

    with pytest.raises(StoreApplicationError, match="conflicting receipt"):
        store.apply_certified_decision("k1", conflicting)

    # Prior value and prior receipt remain intact:
    assert store.get("k1") == "v1"
    assert store.get_certificates()[0].decision_kind == DecisionKind.PRESERVE


def test_cache_03_stored_receipt_is_isolated_from_caller_mutation() -> None:
    """
    Regression test for CACHE-03: mutating the caller's Certificate or
    returned Certificate after `apply_certified_decision()` does not corrupt
    the stored receipt inside `InMemoryStoreAdapter`.
    """
    cert = Certificate(
        shape="paid_orders_sum",
        event_id="evt_immutable",
        relation="orders",
        decision_kind=DecisionKind.PRESERVE,
        reason_code="disjoint_columns",
        required_evidence=["amount", "customer_id", "status"],
        available_evidence=["amount", "customer_id", "status"],
    )
    decision = Decision.preserve("disjoint_columns", cert)

    mem_store = InMemoryStoreAdapter()
    mem_store.set("paid_orders_sum", {"10": 50})
    returned = mem_store.apply_certified_decision("paid_orders_sum", decision)

    # Mutate both caller's original certificate and returned certificate
    cert.reason_code = "tampered_caller_reason"
    cert.required_evidence.append("injected_col")
    returned.reason_code = "tampered_returned_reason"
    returned.available_evidence.append("injected_avail")

    stored = mem_store.get_certificates()[0]
    assert stored.reason_code == "disjoint_columns"
    assert stored.required_evidence == ["amount", "customer_id", "status"]
    assert stored.available_evidence == ["amount", "customer_id", "status"]


# ---------------------------------------------------------------------------
# 4. RegisteredShapesRegistry & CACHE-01 Join Regression Tests
# ---------------------------------------------------------------------------


def test_registry_reports_effective_store_decisions_and_preserves_failure_atomicity() -> (
    None
):
    shape = _orders_shape()
    event = WriteEvent(
        relation="orders",
        old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
        new={"id": 1, "customer_id": 10, "status": "paid", "amount": 70},
        event_id="evt_registry_repair_candidate",
    )

    registry = RegisteredShapesRegistry()
    registry.register_shape(shape)
    registry.store.set("paid_orders_sum", {"10": 50})

    results = registry.process_write_event(event)

    assert len(results) == 1
    assert results[0]["decision"] == "invalidate"
    assert results[0]["proposed_decision"] == "repair"
    assert results[0]["reason"] == "repair_execution_unavailable"
    assert registry.decision_counts == {
        "preserve": 0,
        "repair": 0,
        "invalidate": 1,
        "unsupported": 0,
    }
    assert registry.store.get("paid_orders_sum") is None

    broken_registry = RegisteredShapesRegistry()
    broken_registry.register_shape(shape)
    with patch.object(broken_registry.store, "delete", side_effect=OSError("injected")):
        with pytest.raises(StoreApplicationError):
            broken_registry.process_write_event(event)
    assert sum(broken_registry.decision_counts.values()) == 0
    assert broken_registry.store.get_certificates() == []


def test_cache_01_join_query_does_not_emit_false_preserve_on_joined_table_delete() -> (
    None
):
    """
    Regression test for CACHE-01:
    1. `check_cdc_evidence` on an unaggregated multi-table JOIN query refuses
       with `DecisionKind.UNSUPPORTED` (`unsupported_sql: Join`) rather than
       emitting a false `PRESERVE("unrelated_relation")` when a row in the
       joined `orders` table is deleted.
    2. For supported multi-table equijoin aggregate queries (`parse_join_sql` +
       `certify_join_event`), deleting or updating a row in a joined table
       is certified against the joined relation and never ignored as an
       `unrelated_relation`.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, total INTEGER)"
    )
    conn.execute("INSERT INTO users VALUES (1, 'Ada')")
    conn.execute("INSERT INTO orders VALUES (10, 1, 100)")

    join_sql = "SELECT users.name, orders.total FROM users JOIN orders ON users.id = orders.user_id"
    before = conn.execute(join_sql).fetchall()
    conn.execute("DELETE FROM orders WHERE id = 10")
    after = conn.execute(join_sql).fetchall()
    assert before != after

    delete_orders_evt = WriteEvent(
        relation="orders",
        old={"id": 10, "user_id": 1, "total": 100},
        new={},
        event_id="evt_delete_joined_order",
    )

    # 1. Single-table SQL checker refuses JOIN queries fail-closed:
    single_decision = check_cdc_evidence(join_sql, delete_orders_evt)
    assert single_decision.kind == DecisionKind.UNSUPPORTED
    assert "Join" in single_decision.reason

    # 2. Multi-table join shape tracks all joined relations (users and orders):
    agg_join_sql = (
        "SELECT users.name, SUM(orders.total) "
        "FROM orders JOIN users ON orders.user_id = users.id "
        "GROUP BY users.name"
    )
    join_res = parse_join_sql(agg_join_sql, name="user_order_totals")
    assert join_res.is_supported and join_res.shape is not None

    # Deleting from either `orders` or `users` is recognized as a participating relation:
    dec_orders = certify_join_event(join_res.shape, delete_orders_evt, aux_state=None)
    assert dec_orders.kind == DecisionKind.INVALIDATE
    assert dec_orders.reason != "unrelated_relation"

    delete_users_evt = WriteEvent(
        relation="users",
        old={"id": 1, "name": "Ada"},
        new={},
        event_id="evt_delete_joined_user",
    )
    dec_users = certify_join_event(join_res.shape, delete_users_evt, aux_state=None)
    assert dec_users.kind == DecisionKind.INVALIDATE
    assert dec_users.reason != "unrelated_relation"


def test_sql_checker_agrees_with_certify_event_on_supported_queries() -> None:
    sql = "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    shape = _orders_shape()
    events = [
        WriteEvent(
            relation="orders",
            old={
                "id": 1,
                "customer_id": 10,
                "status": "paid",
                "amount": 50,
                "notes": "a",
            },
            new={
                "id": 1,
                "customer_id": 10,
                "status": "paid",
                "amount": 50,
                "notes": "b",
            },
            event_id="evt_preserve",
        ),
        WriteEvent(
            relation="orders",
            old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
            new={"id": 1, "customer_id": 10, "status": "paid", "amount": 80},
            event_id="evt_repair",
        ),
        WriteEvent(
            relation="orders",
            old={"id": 1, "customer_id": 10, "status": "pending", "amount": 50},
            new={"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
            event_id="evt_entry_repair",
        ),
    ]

    for evt in events:
        d_sql = check_cdc_evidence(sql, evt)
        d_cert = certify_event(shape, evt)
        assert d_sql.kind == d_cert.kind
        assert d_sql.reason == d_cert.reason


def test_aggregate_transition_matrix_preserve_repair_and_zero_group_eviction() -> None:
    shape = _orders_shape()
    cache = AggregateCache.from_rows(
        shape,
        [{"id": 1, "customer_id": 10, "status": "paid", "amount": 50}],
    )
    assert cache.values[10] == 50

    # 1. Same-group value change -> in-place repair
    d1 = process_event(
        shape,
        cache,
        WriteEvent(
            relation="orders",
            old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
            new={"id": 1, "customer_id": 10, "status": "paid", "amount": 80},
            event_id="e1",
        ),
    )
    assert d1.kind == DecisionKind.REPAIR
    assert cache.values[10] == 80

    # 2. Update paid -> pending (exits predicate) -> group row count drops to 0 and group is evicted
    d2 = process_event(
        shape,
        cache,
        WriteEvent(
            relation="orders",
            old={"id": 1, "customer_id": 10, "status": "paid", "amount": 80},
            new={"id": 1, "customer_id": 10, "status": "pending", "amount": 80},
            event_id="e2",
        ),
    )
    assert d2.kind == DecisionKind.REPAIR
    assert 10 not in cache.values

    # 3. Unseeded totals-only cache (`AggregateCache({10: 50})`) refuses repair on membership removal
    totals_only_cache = AggregateCache({10: 50})
    d3 = process_event(
        shape,
        totals_only_cache,
        WriteEvent(
            relation="orders",
            old={"id": 1, "customer_id": 10, "status": "paid", "amount": 50},
            new={"id": 1, "customer_id": 10, "status": "pending", "amount": 50},
            event_id="e3",
        ),
    )
    assert d3.kind == DecisionKind.INVALIDATE
    assert d3.reason == "missing_aggregate_state"
