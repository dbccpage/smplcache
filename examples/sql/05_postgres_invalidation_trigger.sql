-- License: Licensed under the Apache License, Version 2.0.
-- Copyright: Copyright 2026 Jeremy Carroll
-- ==============================================================================
-- smplcache: PostgreSQL Setup & Demo
-- This script creates the schema, inserts seed data, and creates the JSONB
-- diffing triggers to demonstrate dependency fingerprinting in standard PG.
-- ==============================================================================

-- 1. Create Core Tables
CREATE TABLE customers (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100),
    region VARCHAR(50),
    email VARCHAR(100)
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id VARCHAR(50) REFERENCES customers(id),
    status VARCHAR(50),
    amount INT,
    shipping_address TEXT
);

-- 2. Create the Cache Shape Registry
CREATE TABLE cache_query_shapes (
    shape_hash TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    dependent_columns TEXT[] NOT NULL,
    is_valid BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Seed Data
INSERT INTO customers (id, name, region, email) VALUES 
('c1', 'Acme Corp', 'North', 'contact@acme.com'),
('c2', 'Globex', 'East', 'info@globex.com');

INSERT INTO orders (customer_id, status, amount, shipping_address) VALUES 
('c1', 'paid', 100, '123 Acme St'),
('c1', 'pending', 50, '123 Acme St'),
('c2', 'paid', 80, '456 Globex Blvd');

-- 4. Register a Query Shape
-- Query: SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id;
INSERT INTO cache_query_shapes (shape_hash, query_text, relation_name, dependent_columns)
VALUES (
    'q_hash_revenue_by_cust', 
    'SELECT customer_id, SUM(amount) FROM orders WHERE status = ''paid'' GROUP BY customer_id',
    'orders', 
    ARRAY['status', 'amount', 'customer_id']
);

-- 5. Trigger Functions for JSONB Diffing
CREATE OR REPLACE FUNCTION compute_boundary_delta(old_row JSONB, new_row JSONB)
RETURNS TEXT[] AS $$
DECLARE
    changed_keys TEXT[];
    key TEXT;
BEGIN
    changed_keys := ARRAY[]::TEXT[];
    FOR key IN SELECT jsonb_object_keys(new_row)
    LOOP
        IF old_row->key IS DISTINCT FROM new_row->key THEN
            changed_keys := array_append(changed_keys, key);
        END IF;
    END LOOP;
    RETURN changed_keys;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION route_boundary_to_shapes()
RETURNS TRIGGER AS $$
DECLARE
    changed_fields TEXT[];
    relation TEXT := TG_TABLE_NAME;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        changed_fields := compute_boundary_delta(to_jsonb(OLD), to_jsonb(NEW));
    ELSIF TG_OP = 'INSERT' THEN
        changed_fields := ARRAY(SELECT jsonb_object_keys(to_jsonb(NEW)));
    ELSIF TG_OP = 'DELETE' THEN
        changed_fields := ARRAY(SELECT jsonb_object_keys(to_jsonb(OLD)));
    END IF;

    IF array_length(changed_fields, 1) IS NULL THEN
        RETURN NEW;
    END IF;

    -- The && operator finds array intersections
    UPDATE cache_query_shapes
    SET is_valid = FALSE
    WHERE relation_name = relation
      AND is_valid = TRUE
      AND dependent_columns && changed_fields;

    -- Optional: Log the routing for demonstration purposes
    RAISE NOTICE 'smplcache CDC routing: relation=%, changed_fields=%', relation, changed_fields;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_boundary_router
AFTER INSERT OR UPDATE OR DELETE ON orders
FOR EACH ROW EXECUTE FUNCTION route_boundary_to_shapes();

-- ==============================================================================
-- TESTING THE DEMO
-- Run these one by one to see the effect on `cache_query_shapes`.
-- ==============================================================================

-- Test 1: Update Shipping Address (Orthogonal Write)
-- Expectation: is_valid remains TRUE because 'shipping_address' does not intersect ['status', 'amount', 'customer_id']
-- UPDATE orders SET shipping_address = '789 New St' WHERE id = 1;
-- SELECT * FROM cache_query_shapes;

-- Test 2: Update Amount (Intersecting Write)
-- Expectation: is_valid becomes FALSE because 'amount' is in the dependency fingerprint.
-- UPDATE orders SET amount = 150 WHERE id = 1;
-- SELECT * FROM cache_query_shapes;
