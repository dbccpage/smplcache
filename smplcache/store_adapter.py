# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
# SPDX-License-Identifier: Apache-2.0
"""
Cache eviction store adapters and shape registry with receipt custody.

Store adapters have no repair executor or delta input: a proposed `REPAIR`
decision is applied as an effective `INVALIDATE` (`repair_execution_unavailable`)
by evicting the cached key, verifying post-eviction absence, and recording an
immutable receipt snapshot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, replace
import json
from typing import Any

from smplcache.join import MultiTableJoinShape, certify_join_event
from smplcache.smplcache import (
    Certificate,
    Decision,
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
    normalize_relation,
)

DEFAULT_MAX_CERTIFICATES = 100_000
"""Maximum receipts retained by an `InMemoryStoreAdapter` before refusing application."""


class StoreApplicationError(RuntimeError):
    """Application, post-eviction readback, or receipt custody failed."""


def _clone_certificate(cert: Certificate) -> Certificate:
    """Return a detached copy of `cert` with independent evidence lists."""
    return replace(
        cert,
        required_evidence=list(cert.required_evidence),
        available_evidence=list(cert.available_evidence),
    )


def _effective_certificate(decision: Decision) -> Certificate:
    base = _clone_certificate(decision.certificate)
    if decision.kind == DecisionKind.REPAIR:
        return replace(
            base,
            decision_kind=DecisionKind.INVALIDATE,
            reason_code="repair_execution_unavailable",
            repair_program=None,
        )
    return replace(
        base,
        decision_kind=decision.kind,
        reason_code=decision.reason,
    )


class SmplCacheStore(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None:
        pass

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    def apply_certified_decision(self, key: str, decision: Decision) -> Certificate:
        pass


class InMemoryStoreAdapter(SmplCacheStore):
    """
    Process-local dictionary store with bounded, detached receipt custody.

    - At `max_certificates`, `apply_certified_decision` refuses before mutating
      `self._store`, so no applied decision is left without a receipt.
    - Stored and returned `Certificate` instances are deep-copied so caller
      mutations cannot alter retained receipts.
    - Conflicting duplicate `(key, event_id)` receipts are refused before store
      mutation when `event_id` is explicitly populated.
    """

    def __init__(self, max_certificates: int = DEFAULT_MAX_CERTIFICATES) -> None:
        if type(max_certificates) is not int or max_certificates < 1:
            raise ValueError("max_certificates must be a positive int")
        self._store: dict[str, Any] = {}
        self._certificates: list[Certificate] = []
        self._receipt_index: dict[tuple[str, str], tuple[str, str]] = {}
        self._max_certificates = max_certificates

    @property
    def max_certificates(self) -> int:
        return self._max_certificates

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def apply_certified_decision(self, key: str, decision: Decision) -> Certificate:
        cert = _effective_certificate(decision)
        if len(self._certificates) >= self._max_certificates:
            raise StoreApplicationError(
                f"receipt cap of {self._max_certificates} reached; application refused"
            )
        receipt_id = (key, str(cert.event_id))
        sig = (cert.decision_kind.value, cert.reason_code)
        if (
            cert.event_id
            and str(cert.event_id) != "unknown"
            and receipt_id in self._receipt_index
            and self._receipt_index[receipt_id] != sig
        ):
            raise StoreApplicationError(
                f"conflicting receipt for key={key!r}, event_id={cert.event_id!r}"
            )
        try:
            if cert.decision_kind in (
                DecisionKind.INVALIDATE,
                DecisionKind.UNSUPPORTED,
            ):
                self.delete(key)
                remaining = self.get(key)
                if remaining is not None or key in self._store:
                    raise StoreApplicationError("cache key remains after eviction")
            self._certificates.append(_clone_certificate(cert))
            if cert.event_id and str(cert.event_id) != "unknown":
                self._receipt_index[receipt_id] = sig
        except StoreApplicationError:
            raise
        except Exception as exc:
            raise StoreApplicationError("cache application failed") from exc
        return _clone_certificate(cert)

    def get_certificates(self) -> list[Certificate]:
        return [_clone_certificate(c) for c in self._certificates]


class RedisStoreAdapter(SmplCacheStore):
    """
    Redis-compatible store adapter; an omitted `redis_client` uses a local JSON
    dictionary for deterministic testing.
    """

    def __init__(self, redis_client: Any = None) -> None:
        self.redis = redis_client
        self._mock_memory: dict[str, str] = {}

    def _raw_get(self, key: str) -> Any:
        if self.redis is not None:
            return self.redis.get(key)
        return self._mock_memory.get(key)

    def get(self, key: str) -> Any | None:
        value = self._raw_get(key)
        return json.loads(value) if value is not None else None

    def set(self, key: str, value: Any) -> None:
        raw = json.dumps(value)
        if self.redis is not None:
            self.redis.set(key, raw)
        else:
            self._mock_memory[key] = raw

    def delete(self, key: str) -> bool:
        if self.redis is not None:
            return bool(self.redis.delete(key))
        if key in self._mock_memory:
            del self._mock_memory[key]
            return True
        return False

    def apply_certified_decision(self, key: str, decision: Decision) -> Certificate:
        cert = _effective_certificate(decision)
        cert_key = f"smpl:cert:{key}:{cert.event_id}"
        try:
            existing_raw = self._raw_get(cert_key)
            if (
                existing_raw is not None
                and cert.event_id
                and str(cert.event_id) != "unknown"
            ):
                existing = json.loads(existing_raw)
                if (
                    existing.get("decision") != cert.decision_kind.value
                    or existing.get("reason") != cert.reason_code
                ):
                    raise StoreApplicationError(
                        f"conflicting receipt for key={key!r}, event_id={cert.event_id!r}"
                    )
            if cert.decision_kind in (
                DecisionKind.INVALIDATE,
                DecisionKind.UNSUPPORTED,
            ):
                self.delete(key)
                if self._raw_get(key) is not None:
                    raise StoreApplicationError("cache key remains after eviction")
            payload = asdict(cert)
            payload.update(
                decision=cert.decision_kind.value,
                reason=cert.reason_code,
            )
            cert_json = json.dumps(payload)
            if self.redis is not None:
                if self.redis.set(cert_key, cert_json) is not True:
                    raise StoreApplicationError(
                        "receipt persistence was not acknowledged"
                    )
            else:
                self._mock_memory[cert_key] = cert_json
        except StoreApplicationError:
            raise
        except Exception as exc:
            raise StoreApplicationError(
                "cache application or receipt persistence failed"
            ) from exc
        return _clone_certificate(cert)


class RegisteredShapesRegistry:
    """
    Registry binding `QueryShape` / `MultiTableJoinShape` instances to an
    `InMemoryStoreAdapter` and tracking effective applied decision telemetry.
    """

    def __init__(self, max_certificates: int = DEFAULT_MAX_CERTIFICATES) -> None:
        self.shapes: dict[str, QueryShape | MultiTableJoinShape] = {}
        self.store = InMemoryStoreAdapter(max_certificates=max_certificates)
        self.decision_counts: dict[str, int] = {
            "preserve": 0,
            "repair": 0,
            "invalidate": 0,
            "unsupported": 0,
        }

    def register_shape(self, shape: QueryShape | MultiTableJoinShape) -> None:
        self.shapes[shape.name] = shape

    def _shape_matches_event(
        self, shape: QueryShape | MultiTableJoinShape, event: WriteEvent
    ) -> bool:
        evt_rel = normalize_relation(event.relation)
        if isinstance(shape, MultiTableJoinShape):
            return evt_rel in shape.relations()
        return normalize_relation(shape.relation) == evt_rel

    def process_write_event(self, event: WriteEvent) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for shape in self.shapes.values():
            if self._shape_matches_event(shape, event):
                if isinstance(shape, MultiTableJoinShape):
                    dec = certify_join_event(shape, event, None)
                else:
                    dec = certify_event(shape, event)
                cert = self.store.apply_certified_decision(shape.name, dec)
                self.decision_counts[cert.decision_kind.value] += 1
                results.append(
                    {
                        "shape": shape.name,
                        "decision": cert.decision_kind.value,
                        "reason": cert.reason_code,
                        "proposed_decision": dec.kind.value,
                        "certificate": asdict(cert),
                    }
                )
        return results


__all__ = [
    "DEFAULT_MAX_CERTIFICATES",
    "InMemoryStoreAdapter",
    "RedisStoreAdapter",
    "RegisteredShapesRegistry",
    "SmplCacheStore",
    "StoreApplicationError",
]
