# License: Licensed under the Apache License, Version 2.0.
# Copyright: Copyright 2026 Jeremy Carroll
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from smplcache.extractor import parse_sql
from smplcache.smplcache import (
    Certificate,
    Decision,
    DecisionKind,
    QueryShape,
    WriteEvent,
    certify_event,
    normalize_relation,
)


@dataclass
class SqlShapeContext:
    name: str
    sql: str
    dialect: str = "postgres"
    schema: Mapping[str, Any] | Iterable[str] | None = None
    security_cols: Iterable[str] = ()

    def extract_shape(self) -> tuple[Optional[QueryShape], str]:
        """Returns (QueryShape, reason_if_unsupported)."""
        res = parse_sql(
            self.sql,
            dialect=self.dialect,
            schema=self.schema,
            name=self.name,
            security_cols=self.security_cols,
        )
        if not res.is_supported or res.shape is None:
            return None, res.reason
        return res.shape, ""


def check_cdc_evidence(
    sql: str,
    event: WriteEvent,
    *,
    dialect: str = "postgres",
    schema: Mapping[str, Any] | Iterable[str] | None = None,
) -> Decision:
    """
    Combines AST allowlist SQL extraction and default-deny CDC certification.
    """
    res = parse_sql(
        sql,
        dialect=dialect,
        schema=schema,
        name="dynamic_sql",
    )
    if not res.is_supported or res.shape is None:
        cert = Certificate(
            shape="dynamic_sql",
            event_id=str(event.event_id),
            relation=normalize_relation(event.relation) or event.relation,
            decision_kind=DecisionKind.UNSUPPORTED,
            reason_code="",
        )
        return Decision.unsupported(f"unsupported_sql: {res.reason}", cert)

    return certify_event(res.shape, event)
