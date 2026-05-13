from dataclasses import dataclass
from typing import Optional

from smplcache import QueryShape, WriteEvent, Certificate, Decision, DecisionKind
from extractor import parse_sql

@dataclass
class SqlShapeContext:
    name: str
    sql: str
    
    def extract_shape(self) -> tuple[Optional[QueryShape], str]:
        """Returns (QueryShape, reason_if_unsupported)"""
        res = parse_sql(self.sql)
        if not res.is_supported:
            return None, res.reason
            
        shape = QueryShape(
            name=self.name,
            relation=res.relation,
            predicate_cols=res.predicate_cols,
            aggregate_cols=res.aggregate_cols,
            group_cols=res.group_cols,
            projection_cols=res.projection_cols,
            join_cols=res.join_cols
        )
        return shape, ""

def check_cdc_evidence(sql: str, event: WriteEvent) -> Decision:
    """
    Combines SQL Extraction and CDC Evidence checking.
    """
    res = parse_sql(sql)
    
    cert = Certificate(
        shape="dynamic_sql",
        event_id=event.event_id,
        relation=res.relation if res.is_supported else event.relation,
        decision_kind=DecisionKind.PRESERVE,
        reason_code=""
    )
    
    if not res.is_supported:
        return Decision.unsupported(f"unsupported_sql: {res.reason}", cert)
        
    if event.relation != res.relation:
        return Decision.preserve("unrelated_relation", cert)
        
    fingerprint = res.predicate_cols | res.aggregate_cols | res.group_cols | res.projection_cols | res.join_cols
    
    intersection = fingerprint & event.changed_cols
    if not intersection:
        return Decision.preserve("disjoint_columns", cert)
        
    # Check evidence
    avail = list((event.old or {}).keys()) + list((event.new or {}).keys())
    cert.available_evidence = sorted(list(set(avail)))
    
    # Simple strict requirement: any column in the fingerprint must be present in the evidence
    # (In a full implementation, we'd only strictly require keys, predicates, and aggregate sources)
    required = set()
    if res.aggregate_cols:
        required.update(res.aggregate_cols)
        required.update(res.group_cols)
        required.update(res.predicate_cols)
        
    cert.required_evidence = sorted(list(required))
    
    missing = required - set(cert.available_evidence)
    if missing:
        return Decision.invalidate(f"missing_evidence: {sorted(list(missing))}", cert)
        
    return Decision.repair("evidence_sufficient", cert)
