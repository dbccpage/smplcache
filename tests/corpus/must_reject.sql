SELECT o.customer_id, SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY o.customer_id
SELECT o.customer_id, SUM(o.amount) FROM orders o LEFT JOIN customers c ON o.customer_id = c.id GROUP BY o.customer_id
SELECT o.customer_id FROM orders o CROSS JOIN customers c
SELECT * FROM orders
SELECT o.* FROM orders o
SELECT customer_id, SUM(amount) FROM orders GROUP BY customer_id HAVING SUM(amount) > 100
SELECT DISTINCT customer_id FROM orders
SELECT COUNT(DISTINCT customer_id) FROM orders
SELECT customer_id, amount FROM orders ORDER BY amount DESC
SELECT customer_id FROM orders LIMIT 10
SELECT customer_id FROM orders LIMIT 10 OFFSET 5
SELECT customer_id, SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) FROM orders GROUP BY customer_id
SELECT customer_id FROM orders WHERE status LIKE 'paid%'
SELECT customer_id FROM orders WHERE status ILIKE 'paid%'
SELECT customer_id FROM orders WHERE amount BETWEEN 10 AND 100
SELECT customer_id FROM orders WHERE status = 'paid' OR status = 'pending'
SELECT customer_id FROM orders WHERE NOT status = 'cancelled'
SELECT customer_id FROM orders WHERE status IN ('paid', 'pending')
SELECT customer_id FROM orders WHERE status IS NULL
SELECT customer_id, SUM(amount) OVER (PARTITION BY customer_id) FROM orders
SELECT customer_id FROM orders WHERE amount > (SELECT AVG(amount) FROM orders)
SELECT customer_id FROM (SELECT customer_id FROM orders) sub
WITH paid AS (SELECT customer_id, amount FROM orders) SELECT customer_id, SUM(amount) FROM paid GROUP BY customer_id
SELECT customer_id FROM orders UNION SELECT customer_id FROM archived_orders
SELECT customer_id, MIN(amount) FROM orders GROUP BY customer_id
SELECT customer_id, MAX(amount) FROM orders GROUP BY customer_id
SELECT customer_id, COALESCE(amount, 0) FROM orders
SELECT customer_id, SUM(amount * tax_rate) FROM orders GROUP BY customer_id
SELECT customer_id FROM orders; SELECT customer_id FROM orders
SELECT customer_id FROM orders -- trailing comment
SELECT /* inline comment */ customer_id FROM orders
SELECT customer_id FROM "orders"
SELECT "customer_id", SUM(amount) FROM orders GROUP BY "customer_id"
SELECT c.customer_id FROM orders o
SELECT customer_id, SUM(amount), COUNT(amount) FROM orders GROUP BY customer_id
UPDATE orders SET amount = 0
SELECT customer_id FROM orders WHERE customer_id = @customer_id
SELECT customer_id FROM orders WHERE amount = tax
