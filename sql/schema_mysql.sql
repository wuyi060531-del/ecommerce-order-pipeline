-- MySQL 8.0.19+，先创建专用数据库，再在该库执行本文件。
-- 明细金额统一存为整数分；不使用 FLOAT 存储货币。
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    source_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_name TEXT NOT NULL,
    metrics_json JSON NOT NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    quantity BIGINT NOT NULL,
    unit_price_cents BIGINT NOT NULL,
    order_time DATETIME(6) NULL,
    city VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    order_amount_cents BIGINT NOT NULL,
    source_row BIGINT NOT NULL,
    last_run_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    CONSTRAINT chk_orders_quantity CHECK (quantity > 0),
    CONSTRAINT chk_orders_price CHECK (unit_price_cents >= 0),
    CONSTRAINT chk_orders_amount CHECK (order_amount_cents >= 0),
    CONSTRAINT fk_orders_run FOREIGN KEY (last_run_id) REFERENCES etl_runs(run_id),
    INDEX idx_orders_status_city (status, city),
    INDEX idx_orders_status_product (status, product_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS dq_issues (
    run_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    issue_number BIGINT NOT NULL,
    source_row BIGINT NOT NULL,
    order_id TEXT NULL,
    rule VARCHAR(128) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    action VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    raw_record JSON NOT NULL,
    PRIMARY KEY (run_id, issue_number),
    CONSTRAINT fk_issues_run FOREIGN KEY (run_id) REFERENCES etl_runs(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
