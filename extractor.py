import re
from dataclasses import dataclass
from typing import Set

@dataclass
class ExtractorResult:
    is_supported: bool
    relation: str = ""
    predicate_cols: Set[str] = None
    aggregate_cols: Set[str] = None
    group_cols: Set[str] = None
    projection_cols: Set[str] = None
    join_cols: Set[str] = None
    reason: str = ""
    
    def __post_init__(self):
        self.predicate_cols = self.predicate_cols or set()
        self.aggregate_cols = self.aggregate_cols or set()
        self.group_cols = self.group_cols or set()
        self.projection_cols = self.projection_cols or set()
        self.join_cols = self.join_cols or set()

def extract_columns(expr: str) -> Set[str]:
    # Remove string literals
    expr = re.sub(r"'[^']*'", "", expr)
    # Very basic column extractor: finds words that look like identifiers (excluding keywords)
    keywords = {"SUM", "AVG", "COUNT", "MIN", "MAX", "AS", "AND", "OR", "IN", "IS", "NOT", "NULL", "WHERE", "GROUP", "BY", "ORDER", "FROM", "SELECT", "ON"}
    words = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', expr.upper())
    return {w.lower() for w in words if w not in keywords and not w.isdigit()}

def parse_sql(sql: str) -> ExtractorResult:
    sql = sql.replace("\n", " ").strip()
    upper_sql = sql.upper()
    
    # 1. Unsupported feature checks
    if " OVER " in upper_sql or " OVER(" in upper_sql:
        return ExtractorResult(False, reason="window functions are not supported")
    if " LIMIT " in upper_sql or " OFFSET " in upper_sql:
        return ExtractorResult(False, reason="LIMIT/OFFSET are not supported")
    if " LEFT " in upper_sql or " RIGHT " in upper_sql or " OUTER " in upper_sql:
        return ExtractorResult(False, reason="outer joins are not supported")
    if upper_sql.count("SELECT") > 1:
        return ExtractorResult(False, reason="subqueries are not supported")
    
    # Check for basic UDFs (naive heuristic: any function call not in allowed list)
    allowed_funcs = ["SUM", "AVG", "COUNT"]
    funcs = re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', upper_sql)
    for f in funcs:
        if f not in allowed_funcs:
            return ExtractorResult(False, reason=f"unsupported function or UDF detected: {f}")
            
    # 2. Extract clauses
    # This is a highly simplified regex extractor for the strict fragment
    select_match = re.search(r'SELECT\s+(.*?)\s+FROM\s+', upper_sql, re.IGNORECASE)
    if not select_match:
        return ExtractorResult(False, reason="could not parse SELECT clause")
        
    select_clause = select_match.group(1)
    
    from_match = re.search(r'FROM\s+([A-Za-z_][A-Za-z0-9_]*)', upper_sql, re.IGNORECASE)
    if not from_match:
        return ExtractorResult(False, reason="could not parse FROM relation")
        
    relation = from_match.group(1).lower()
    
    where_match = re.search(r'WHERE\s+(.*?)(?:\s+GROUP BY|\s*$)', upper_sql, re.IGNORECASE)
    where_clause = where_match.group(1) if where_match else ""
    
    group_match = re.search(r'GROUP BY\s+(.*?)(?:\s+ORDER BY|\s*$)', upper_sql, re.IGNORECASE)
    group_clause = group_match.group(1) if group_match else ""
    
    # 3. Build fingerprint sets
    predicate_cols = set()
    aggregate_cols = set()
    projection_cols = set()
    group_cols = set()
    join_cols = set() # Skipped inner joins parsing for brevity in this simple implementation
    
    # Process SELECT
    for part in select_clause.split(','):
        part = part.strip()
        agg_match = re.search(r'(SUM|AVG|COUNT)\s*\(\s*(.*?)\s*\)', part, re.IGNORECASE)
        if agg_match:
            cols = extract_columns(agg_match.group(2))
            aggregate_cols.update(cols)
        else:
            cols = extract_columns(part)
            projection_cols.update(cols)
            
    # Process WHERE
    if where_clause:
        predicate_cols.update(extract_columns(where_clause))
        
    # Process GROUP BY
    if group_clause:
        group_cols.update(extract_columns(group_clause))
        
    return ExtractorResult(
        is_supported=True,
        relation=relation,
        predicate_cols=predicate_cols,
        aggregate_cols=aggregate_cols,
        group_cols=group_cols,
        projection_cols=projection_cols,
        join_cols=join_cols
    )
