# smplcache: Cache Invalidation by Shape, Not Table

This document outlines a lightweight, standard PostgreSQL implementation of "Dependency Fingerprinting" for CDC-based cache invalidation. It demonstrates how to avoid over-invalidation without requiring a custom database kernel.

## The Core Concept
Instead of invalidating a cached `SELECT` query every time a table changes, we attach a **Dependency Fingerprint** to the cached query. A trigger (or logical replication consumer) computes the exact **Delta** (the fields that actually changed) and intersects it with the fingerprint.

If there is an intersection, the cache is invalidated.
If there is no intersection, the cache remains fresh.

---

## 1. Schema: The Cache Registry

First, we create a registry that maps cached query hashes to their structural dependencies.

```sql
CREATE TABLE cache_query_shapes (
    shape_hash TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    -- The dependency fingerprint:
    dependent_columns TEXT[] NOT NULL,
    is_valid BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Example: Registering the Shape
-- Query: SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id;
INSERT INTO cache_query_shapes (shape_hash, query_text, relation_name, dependent_columns)
VALUES (
    'q_hash_8a7f', 
    'SELECT customer_id, SUM(amount) FROM orders WHERE status = ''paid'' GROUP BY customer_id',
    'orders', 
    ARRAY['status', 'amount', 'customer_id'] -- The Fingerprint
);
```

---

## 2. The Trigger: Computing the Boundary Delta

We use a standard PostgreSQL JSONB function to diff the `OLD` and `NEW` rows. This finds the exact fields that were mutated. 

```sql
CREATE OR REPLACE FUNCTION compute_boundary_delta(old_row JSONB, new_row JSONB)
RETURNS TEXT[] AS $$
DECLARE
    changed_keys TEXT[];
    key TEXT;
BEGIN
    changed_keys := ARRAY[]::TEXT[];
    -- Iterate through keys to find differences
    FOR key IN SELECT jsonb_object_keys(new_row)
    LOOP
        IF old_row->key IS DISTINCT FROM new_row->key THEN
            changed_keys := array_append(changed_keys, key);
        END IF;
    END LOOP;
    RETURN changed_keys;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
```

---

## 3. The Execution: Routing the Boundary

When a row changes, the trigger computes the delta and intersects it with the active fingerprints using PostgreSQL's array overlap operator (`&&`).

```sql
CREATE OR REPLACE FUNCTION route_boundary_to_shapes()
RETURNS TRIGGER AS $$
DECLARE
    changed_fields TEXT[];
    relation TEXT := TG_TABLE_NAME;
BEGIN
    -- Compute the delta (boundary)
    IF TG_OP = 'UPDATE' THEN
        changed_fields := compute_boundary_delta(to_jsonb(OLD), to_jsonb(NEW));
    ELSIF TG_OP = 'INSERT' THEN
        -- All provided fields are "changes"
        changed_fields := ARRAY(SELECT jsonb_object_keys(to_jsonb(NEW)));
    ELSIF TG_OP = 'DELETE' THEN
        -- All deleted fields are "changes"
        changed_fields := ARRAY(SELECT jsonb_object_keys(to_jsonb(OLD)));
    END IF;

    -- If no fields actually changed, exit early
    IF array_length(changed_fields, 1) IS NULL THEN
        RETURN NEW;
    END IF;

    -- Intersect the Boundary with the Dependency Fingerprints
    -- The && operator returns TRUE if the arrays overlap
    UPDATE cache_query_shapes
    SET is_valid = FALSE
    WHERE relation_name = relation
      AND is_valid = TRUE
      AND dependent_columns && changed_fields; -- THE INTERSECTION

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach to the target table
CREATE TRIGGER orders_boundary_router
AFTER INSERT OR UPDATE OR DELETE ON orders
FOR EACH ROW EXECUTE FUNCTION route_boundary_to_shapes();
```

---

## Why this is better than Table-Level CDC Invalidation

1. **Immunity to Orthogonal Writes**: If an application updates `orders.shipping_address`, the `changed_fields` array is `['shipping_address']`. When evaluated against the fingerprint `['status', 'amount', 'customer_id']`, the `&&` overlap returns `FALSE`. The heavy aggregate cache survives.
2. **Deterministic CDC**: This same logic can be moved entirely out of the database trigger and into the Rust/Go middleware that consumes the PostgreSQL logical replication stream. The middleware holds the `cache_query_shapes` map in memory and intersects the replication WAL events with the fingerprints locally.
3. **No Parsing in the Hot Path**: You aren't parsing SQL on every write. You parse it once to generate the fingerprint, and then use highly optimized set-intersections at runtime.

---

## Addendum: Beyond Column Fingerprints to Dependency Classes

The column-fingerprint idea is the simple version. The stronger version is to classify dependencies by role, not just by column.

For a cached query shape, dependencies are not all equal:

```text
predicate dependency:
  can move a row into or out of the result

projection dependency:
  can change the returned value

aggregate dependency:
  can change a fold/sum/count/min/max

grouping dependency:
  can move a row between groups

join-key dependency:
  can change relationship membership

security dependency:
  can change whether a subscriber is allowed to see the event/result
```

Example:

```sql
SELECT c.region, SUM(o.amount)
FROM orders o
JOIN customers c ON c.id = o.customer_id
WHERE o.status = 'paid'
GROUP BY c.region;
```

A better fingerprint is:

```text
orders:
  predicate: status
  aggregate: amount
  join_key: customer_id

customers:
  join_key: id
  grouping: region
```

Then invalidation can become more precise:

```text
orders.shipping_address changed:
  no invalidation

orders.amount changed:
  update aggregate

orders.status changed:
  row may enter/leave result

orders.customer_id changed:
  join membership changed

customers.region changed:
  group assignment changed

customers.email changed:
  no invalidation
```

This also opens the door to masked CDC:

```text
raw boundary event
  -> dependency classifier
  -> cache invalidation/update
  -> subscriber-specific projection/mask
```

So the same machinery can answer three questions:

1. Does this cache entry need invalidation?
2. Can it be incrementally updated instead of dropped?
3. What is this subscriber allowed to see?

That is the bigger idea: cached queries are not strings; they are dependency-bearing shapes.

No custom kernel required for the first version. PostgreSQL logical replication plus a Rust-side shape registry could test this cleanly. Longer term, I think this becomes a shape-native cache optimizer rather than a query-result cache, but the dependency-class version is probably the smallest useful proof.

---

## Future Extension: Invalidation Correlation Matrix

Once smplcache tracks query shapes and CDC invalidation boundaries, it can build a workload-level correlation matrix.

Let each cached query shape be an index `i`.

For each CDC event, collect the set of shapes invalidated by that event. If one event invalidates shapes `[3, 12, 18]`, increment the pairwise counters:

```text
M[3,12] += 1
M[3,18] += 1
M[12,18] += 1
```

Over time, this creates an invalidation correlation matrix.

### Why this matters

This matrix shows whether the customer's cache is healthy or structurally coupled.

* Mostly diagonal matrix: writes invalidate isolated query shapes.
* Dense matrix: writes cause broad collateral invalidation.
* Block-diagonal matrix: workload decomposes cleanly into independent domains.
* Dominant cluster: one table/column family is causing most cache collapse.

### Customer-facing insight

Instead of saying:

> “Your cache hit rate is low.”

smplcache could say:

> “72% of cache invalidation is caused by a correlated cluster involving `orders.status`, affecting Recent Orders, Monthly Revenue, and Fulfillment Dashboard. Isolating this dependency or materializing this shape would improve cache stability.”

This turns smplcache from a cache proxy into a workload advisor.
