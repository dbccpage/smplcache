-- ==============================================================================
-- smplcache: SQL Server Incremental Aggregate Repair Demo
-- Proves that CDC boundaries can stream directly into aggregates without full
-- cache invalidation or query re-execution.
-- ==============================================================================
USE [master];
GO

DROP DATABASE IF EXISTS SmplCacheInc;
CREATE DATABASE SmplCacheInc;
GO

USE SmplCacheInc;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    -- 1. Create Core Tables
    DROP TABLE IF EXISTS dbo.orders;
    DROP TABLE IF EXISTS dbo.customers;
    
    CREATE TABLE dbo.customers (
        id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100),
    );

    CREATE TABLE dbo.orders (
        id INT IDENTITY(1,1) PRIMARY KEY,
        customer_id VARCHAR(50) REFERENCES dbo.customers(id),
        status VARCHAR(50),
        amount INT,
        shipping_address NVARCHAR(MAX)
    );

    -- 2. Create the Incremental Cache Storage
    DROP TABLE IF EXISTS dbo.cached_aggregates;
    CREATE TABLE dbo.cached_aggregates (
        shape_hash VARCHAR(100),
        group_key VARCHAR(50),
        value INT,
        PRIMARY KEY (shape_hash, group_key)
    );

    -- 3. Seed Data
    INSERT INTO dbo.customers (id, name) VALUES ('c1', 'Acme'), ('c2', 'Globex'), ('c3', 'Initech');

    INSERT INTO dbo.orders (customer_id, status, amount, shipping_address) VALUES 
    ('c1', 'paid', 100, '123 Acme St'),    -- revenue_by_cust: c1 += 100
    ('c1', 'pending', 50, '123 Acme St'),  -- not paid, ignored
    ('c2', 'paid', 80, '456 Globex Blvd'); -- revenue_by_cust: c2 += 80

    -- 4. Initial Cache Population for Shape: q_hash_revenue_by_cust
    -- Query: SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id
    INSERT INTO dbo.cached_aggregates (shape_hash, group_key, value) VALUES 
    ('q_hash_revenue_by_cust', 'c1', 100),
    ('q_hash_revenue_by_cust', 'c2', 80);

    -- 5. Trigger Function: Streaming Incremental Repair
    DROP TRIGGER IF EXISTS dbo.orders_incremental_router;
    EXEC('
    CREATE TRIGGER orders_incremental_router
    ON dbo.orders
    AFTER INSERT, UPDATE, DELETE
    AS
    BEGIN
        SET NOCOUNT ON;

        -- TOPOLOGICAL DELTA ROUTING
        -- Instead of invalidating, we compute the boundary changes (inserted - deleted)
        -- and stream them directly into the cached materialized view.

        -- STEP 1: Subtract OLD values (from deleted rows or pre-update state)
        -- Only subtract if the row PREVIOUSLY matched the predicate (status = ''paid'')
        UPDATE ca
        SET value = ca.value - d.amount_to_sub
        FROM dbo.cached_aggregates ca
        JOIN (
            SELECT customer_id, SUM(amount) as amount_to_sub
            FROM deleted
            WHERE status = ''paid''
            GROUP BY customer_id
        ) d ON ca.group_key = d.customer_id
        WHERE ca.shape_hash = ''q_hash_revenue_by_cust'';

        -- STEP 2: Add NEW values (from inserted rows or post-update state)
        -- Only add if the row NOW matches the predicate (status = ''paid'')
        -- Use MERGE to handle new group_keys that didn''t exist in the cache before.
        MERGE dbo.cached_aggregates AS target
        USING (
            SELECT customer_id, SUM(amount) as amount_to_add
            FROM inserted
            WHERE status = ''paid''
            GROUP BY customer_id
        ) AS source
        ON target.shape_hash = ''q_hash_revenue_by_cust'' AND target.group_key = source.customer_id
        WHEN MATCHED THEN 
            UPDATE SET target.value = target.value + source.amount_to_add
        WHEN NOT MATCHED THEN 
            INSERT (shape_hash, group_key, value)
            VALUES (''q_hash_revenue_by_cust'', source.customer_id, source.amount_to_add);
    END;
    ');

    COMMIT TRANSACTION;
    PRINT '====================================================';
    PRINT 'SUCCESS: Incremental Aggregate engine initialized.';
    PRINT '====================================================';

END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
    PRINT 'ERROR: ' + @ErrorMessage;
    RAISERROR (@ErrorMessage, 16, 1);
END CATCH
GO

/* ==============================================================================
   TESTING THE DEMO: Drop this into SSMS to prove it
   ==============================================================================

PRINT '--- INITIAL STATE ---';
SELECT * FROM dbo.cached_aggregates;

-- TEST 1: Orthogonal Write (Update Shipping Address)
-- Net Result: -100 + 100 = 0. Cache handles it seamlessly.
PRINT '--- TEST 1: Orthogonal Write (Update Address) ---';
UPDATE dbo.orders SET shipping_address = '999 Safe Harbor Rd' WHERE id = 1;
SELECT * FROM dbo.cached_aggregates;

-- TEST 2: Intersecting Value Update (Change Amount)
-- Net Result: -100 + 150 = +50. Cache updates c1 to 150.
PRINT '--- TEST 2: Amount Changed ---';
UPDATE dbo.orders SET amount = 150 WHERE id = 1;
SELECT * FROM dbo.cached_aggregates;

-- TEST 3: Predicate Change - Entering Result (Pending -> Paid)
-- Net Result: +50 for c1. Cache updates c1 to 200.
PRINT '--- TEST 3: Row enters predicate (Pending -> Paid) ---';
UPDATE dbo.orders SET status = 'paid' WHERE id = 2;
SELECT * FROM dbo.cached_aggregates;

-- TEST 4: Predicate Change - Leaving Result (Paid -> Cancelled)
-- Net Result: -80 for c2. Cache updates c2 to 0.
PRINT '--- TEST 4: Row leaves predicate (Paid -> Cancelled) ---';
UPDATE dbo.orders SET status = 'cancelled' WHERE id = 3;
SELECT * FROM dbo.cached_aggregates;

-- TEST 5: Join Key / Group Key Change (Move c1 to c3)
-- Net Result: -150 from c1, +150 to c3.
PRINT '--- TEST 5: Group Key Change (c1 -> c3) ---';
UPDATE dbo.orders SET customer_id = 'c3' WHERE id = 1;
SELECT * FROM dbo.cached_aggregates;

============================================================================== */
