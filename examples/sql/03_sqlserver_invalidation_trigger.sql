-- ==============================================================================
-- smplcache: SQL Server Setup & Demo
-- This script creates the schema, inserts seed data, and uses an AFTER UPDATE
-- trigger with STRING_SPLIT to demonstrate dependency fingerprint intersection.
-- ==============================================================================
-- 0. Create New Database
USE master;
GO

-- Now drop the database
/*
USE master;
GO
DROP DATABASE IF EXISTS SmplCache;
GO
*/
CREATE DATABASE SmplCache;
GO

USE SmplCache;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    -- 1. Create Core Tables
    DROP TABLE IF EXISTS dbo.orders;
    DROP TABLE IF EXISTS dbo.customers;
    
    CREATE TABLE dbo.customers (
        id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100),
        region VARCHAR(50),
        email VARCHAR(100)
    );

    CREATE TABLE dbo.orders (
        id INT IDENTITY(1,1) PRIMARY KEY,
        customer_id VARCHAR(50) REFERENCES dbo.customers(id),
        status VARCHAR(50),
        amount INT,
        shipping_address NVARCHAR(MAX)
    );

    -- 2. Create the Cache Shape Registry
    DROP TABLE IF EXISTS dbo.cache_query_shapes;
    CREATE TABLE dbo.cache_query_shapes (
        shape_hash VARCHAR(100) PRIMARY KEY,
        query_text NVARCHAR(MAX) NOT NULL,
        relation_name VARCHAR(100) NOT NULL,
        dependent_columns VARCHAR(500) NOT NULL, -- Format: 'col1,col2,col3'
        is_valid BIT DEFAULT 1,
        created_at DATETIME DEFAULT GETDATE()
    );

    -- 3. Seed Data
    INSERT INTO dbo.customers (id, name, region, email) VALUES 
    ('c1', 'Acme Corp', 'North', 'contact@acme.com'),
    ('c2', 'Globex', 'East', 'info@globex.com');

    INSERT INTO dbo.orders (customer_id, status, amount, shipping_address) VALUES 
    ('c1', 'paid', 100, '123 Acme St'),
    ('c1', 'pending', 50, '123 Acme St'),
    ('c2', 'paid', 80, '456 Globex Blvd');

    -- 4. Register a Query Shape
    INSERT INTO dbo.cache_query_shapes (shape_hash, query_text, relation_name, dependent_columns)
    VALUES (
        'q_hash_revenue_by_cust', 
        'SELECT customer_id, SUM(amount) FROM orders WHERE status = ''paid'' GROUP BY customer_id',
        'orders', 
        'status,amount,customer_id' -- The Fingerprint
    );

    -- 5. Trigger Function for T-SQL Diffing & Routing
    -- We use dynamic SQL here because CREATE TRIGGER must be the first statement in a query batch.
    DROP TRIGGER IF EXISTS dbo.orders_boundary_router;
    EXEC('
    CREATE TRIGGER orders_boundary_router
    ON dbo.orders
    AFTER INSERT, UPDATE, DELETE
    AS
    BEGIN
        SET NOCOUNT ON;
        
        DECLARE @changed_columns TABLE (col_name VARCHAR(100));

        IF EXISTS(SELECT 1 FROM inserted) AND EXISTS(SELECT 1 FROM deleted)
        BEGIN
            IF UPDATE(status) INSERT INTO @changed_columns VALUES (''status'');
            IF UPDATE(amount) INSERT INTO @changed_columns VALUES (''amount'');
            IF UPDATE(customer_id) INSERT INTO @changed_columns VALUES (''customer_id'');
            IF UPDATE(shipping_address) INSERT INTO @changed_columns VALUES (''shipping_address'');
        END
        ELSE IF EXISTS(SELECT 1 FROM inserted)
        BEGIN
            INSERT INTO @changed_columns VALUES (''status''), (''amount''), (''customer_id''), (''shipping_address'');
        END
        ELSE
        BEGIN
            INSERT INTO @changed_columns VALUES (''status''), (''amount''), (''customer_id''), (''shipping_address'');
        END

        IF NOT EXISTS(SELECT 1 FROM @changed_columns) RETURN;

        UPDATE cqs
        SET is_valid = 0
        FROM dbo.cache_query_shapes cqs
        CROSS APPLY STRING_SPLIT(cqs.dependent_columns, '','') dep
        JOIN @changed_columns cc ON cc.col_name = dep.value
        WHERE cqs.relation_name = ''orders'' AND cqs.is_valid = 1;
    END;
    ');

    COMMIT TRANSACTION;
    PRINT '====================================================';
    PRINT 'SUCCESS: SmplCache dataset successfully initialized!';
    PRINT 'Created tables: dbo.customers, dbo.orders, dbo.cache_query_shapes';
    PRINT 'Inserted seed data and caching fingerprints.';
    PRINT 'Created trigger: dbo.orders_boundary_router';
    PRINT '====================================================';

END TRY
BEGIN CATCH
    -- Emulating a "Finally" block for failure cases
    IF @@TRANCOUNT > 0
    BEGIN
        ROLLBACK TRANSACTION;
        PRINT '====================================================';
        PRINT 'TRANSACTION ROLLED BACK DUE TO ERROR.';
        PRINT 'All DDL and DML changes in this batch were reversed.';
        PRINT '====================================================';
    END

    DECLARE   @ErrorMessage     NVARCHAR(4000)  = ERROR_MESSAGE()
            , @ErrorSeverity    INT             = ERROR_SEVERITY()
            , @ErrorState       INT             = ERROR_STATE();

    PRINT 'ERROR DETAILS:';
    PRINT @ErrorMessage;
    
    RAISERROR (@ErrorMessage, @ErrorSeverity, @ErrorState);
END CATCH
GO

/* Remove this line and its partner to test the demo
-- ==============================================================================
-- TESTING THE DEMO
-- Run these one by one to see the effect on `cache_query_shapes`.
-- ==============================================================================

-- Test 1: Update Shipping Address (Orthogonal Write)
-- Expectation: is_valid remains 1 because 'shipping_address' does not intersect 'status,amount,customer_id'
UPDATE dbo.orders SET shipping_address = '789 New St' WHERE id = 1;
SELECT * FROM dbo.cache_query_shapes;

-- Test 2: Update Amount (Intersecting Write)
-- Expectation: is_valid becomes 0 because 'amount' is in the dependency fingerprint.
UPDATE dbo.orders SET amount = 150 WHERE id = 1;
SELECT * FROM dbo.cache_query_shapes;
*/