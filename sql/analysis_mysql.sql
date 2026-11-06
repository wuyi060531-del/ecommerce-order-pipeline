-- 查询范围：全库当前有效订单；销售额仅计 status='paid'。
-- 相同订单在后续批次中更新后，只统计最新一次成功入库的值。
-- name: summary
SELECT COUNT(*) AS total_orders,
       COALESCE(SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END), 0) AS paid_orders,
       COALESCE(SUM(CASE WHEN status = 'paid' THEN order_amount_cents ELSE 0 END), 0) AS sales_amount_cents
FROM orders;

-- name: cities
SELECT city,
       COUNT(*) AS order_count,
       SUM(quantity) AS quantity,
       SUM(order_amount_cents) AS sales_amount_cents
FROM orders
WHERE status = 'paid'
GROUP BY city
ORDER BY sales_amount_cents DESC, city ASC;

-- name: products
SELECT product_name,
       COUNT(*) AS order_count,
       SUM(quantity) AS quantity,
       SUM(order_amount_cents) AS sales_amount_cents
FROM orders
WHERE status = 'paid'
GROUP BY product_name
ORDER BY sales_amount_cents DESC, product_name ASC;
