# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
"""Store adapters that apply certified decisions to real cache keys and keep a receipt per key.

Ported from `anomalon/omega_simplex_cache/store_adapter.py` (read-only
reference) with the imports pointed at `smplcache.smplcache`. Behaviour kept:

- the adapters have no repair executor, so a proposed REPAIR is applied as an
  effective INVALIDATE with reason ``repair_execution_unavailable``;
- INVALIDATE / UNSUPPORTED delete the key and read it back; a key that remains
  raises `StoreApplicationError`;
- every successful application stores a receipt (the effective certificate)
  next to the key (``smpl:cert:<key>:<event_id>`` in Redis) or in a capped
  in-memory list that refuses new applications at the cap rather than dropping
  old receipts.

Added here: `apply_certified_decision` also appends the effective certificate
to an optional tuner `ReceiptLog`, so store applications sit in the same hash
chain as the proposals that changed what the store holds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, replace
import json
from typing import Any, Dict, List, Optional

from smplcache.smplcache import Certificate, Decision, DecisionKind
from smplcache.tuning._engine import ReceiptLog


class StoreApplicationError(RuntimeError):
    """Application or receipt persistence did not complete successfully."""


def effective_certificate(decision: Decision) -> Certificate:
    if decision.kind == DecisionKind.REPAIR:
        return replace(
            decision.certificate,
            decision_kind=DecisionKind.INVALIDATE,
            reason_code="repair_execution_unavailable",
            repair_program=None,
        )
    return replace(
        decision.certificate, decision_kind=decision.kind, reason_code=decision.reason
    )


def certificate_json(cert: Certificate) -> Dict[str, Any]:
    payload = asdict(cert)
    payload.update(decision=cert.decision_kind.value, reason=cert.reason_code)
    payload["decision_kind"] = cert.decision_kind.value
    return payload


class SmplCacheStore(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Any]: ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def apply_certified_decision(self, key: str, decision: Decision) -> Certificate: ...


DEFAULT_MAX_CERTIFICATES = 100_000
"""Receipts an ``InMemoryStoreAdapter`` retains before it refuses to apply."""


class InMemoryStoreAdapter(SmplCacheStore):
    """Process-local dictionary and successful-application receipt list.

    The receipt list is capped. At the cap, an application is refused before it
    touches the store, so no applied decision is left without its receipt and no
    old receipt is dropped.
    """

    def __init__(
        self,
        max_certificates: int = DEFAULT_MAX_CERTIFICATES,
        receipt_log: ReceiptLog | None = None,
    ) -> None:
        if max_certificates < 1:
            raise ValueError("max_certificates must be at least 1")
        self._store: Dict[str, Any] = {}
        self._certificates: List[Certificate] = []
        self._max_certificates = max_certificates
        self.receipt_log = receipt_log

    def get(self, key: str) -> Optional[Any]:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def apply_certified_decision(self, key: str, decision: Decision) -> Certificate:
        cert = effective_certificate(decision)
        if len(self._certificates) >= self._max_certificates:
            raise StoreApplicationError(
                f"receipt cap of {self._max_certificates} reached; application refused"
            )
        try:
            if cert.decision_kind in (
                DecisionKind.INVALIDATE,
                DecisionKind.UNSUPPORTED,
            ):
                self.delete(key)
                # Read through the public accessor as well as checking presence;
                # None is a legal cached value and cannot prove absence alone.
                remaining = self.get(key)
                if remaining is not None or key in self._store:
                    raise StoreApplicationError("cache key remains after eviction")
            self._certificates.append(cert)
            if self.receipt_log is not None:
                self.receipt_log.append(
                    "applied",
                    f"store:{key}",
                    {"key": key, "certificate": certificate_json(cert)},
                )
        except Exception as exc:
            raise StoreApplicationError("cache application failed") from exc
        return cert

    def get_certificates(self) -> List[Certificate]:
        return self._certificates.copy()


class RedisStoreAdapter(SmplCacheStore):
    """redis-py compatible adapter; with no client it keeps a local JSON dictionary."""

    def __init__(
        self, redis_client: Any = None, receipt_log: ReceiptLog | None = None
    ) -> None:
        self.redis = redis_client
        self._mock_memory: Dict[str, str] = {}
        self.receipt_log = receipt_log

    def _raw_get(self, key: str) -> Any:
        if self.redis is not None:
            return self.redis.get(key)
        return self._mock_memory.get(key)

    def get(self, key: str) -> Optional[Any]:
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

    def receipt_key(self, key: str, event_id: str) -> str:
        return f"smpl:cert:{key}:{event_id}"

    def apply_certified_decision(self, key: str, decision: Decision) -> Certificate:
        cert = effective_certificate(decision)
        try:
            if cert.decision_kind in (
                DecisionKind.INVALIDATE,
                DecisionKind.UNSUPPORTED,
            ):
                self.delete(key)
                if self._raw_get(key) is not None:
                    raise StoreApplicationError("cache key remains after eviction")
            cert_key = self.receipt_key(key, cert.event_id)
            cert_json = json.dumps(certificate_json(cert), sort_keys=True)
            if self.redis is not None:
                if self.redis.set(cert_key, cert_json) is not True:
                    raise StoreApplicationError(
                        "receipt persistence was not acknowledged"
                    )
            else:
                self._mock_memory[cert_key] = cert_json
            if self.receipt_log is not None:
                self.receipt_log.append(
                    "applied",
                    f"store:{key}",
                    {
                        "key": key,
                        "receipt_key": cert_key,
                        "certificate": certificate_json(cert),
                    },
                )
        except Exception as exc:
            raise StoreApplicationError(
                "cache application or receipt persistence failed"
            ) from exc
        return cert
