# 电商订单数据质量与分析报告

规则版本：v1.0.0；运行模式：run。

原始文件 SHA-256：`378d5ea6bd1e976e442d7f361ec89a37a566d8d6ae0661fb1588d5c2fb18caaf`。

## 本批次质量

| 指标 | 数量 |
| --- | --- |
| 原始行数 | 30 |
| 接收订单 | 26 |
| 隔离行数 | 3 |
| 多余重复副本 | 1 |
| 含异常的原始行 | 7 |
| 无异常原始行 | 23 |
| 含警告原始行 | 4 |
| 本批 paid 订单 | 11 |
| 缺失城市 | 2 |
| 无效时间 | 1 |

数量核对：30 = 26（接收） + 3（隔离） + 1（重复副本）。

本批 paid 销售额：**66.00 元**。

接收订单可能仍有城市或时间警告；异常行数不等于隔离行数。城市缺失补未知；无效时间留空且保留订单；仅 paid 计销售额。

| rule | severity | action | 触发次数 |
| --- | --- | --- | --- |
| exact_duplicate | warning | drop_duplicate | 1 |
| invalid_price | error | quarantine | 1 |
| invalid_quantity | error | quarantine | 2 |
| invalid_time | warning | set_null | 1 |
| missing_city | warning | fill_unknown | 2 |

同一行可触发多条规则；完整原始字段与原因见 quality_issues.csv。

## 数据库 SQL 分析

后端：sqlite；范围：全库当前有效订单（不一定等于本批次）。

总订单 26，paid 订单 11，paid 销售额 **66.00 元**。

### 城市排行（前 10 项）

| city | order_count | quantity | sales_amount_cents | sales_amount |
| --- | --- | --- | --- | --- |
| synthetic-city-02 | 2 | 2 | 1300 | 13.00 |
| 未知 | 1 | 1 | 1000 | 10.00 |
| synthetic-city-09 | 1 | 1 | 900 | 9.00 |
| synthetic-city-08 | 1 | 1 | 800 | 8.00 |
| synthetic-city-07 | 1 | 1 | 700 | 7.00 |
| synthetic-city-06 | 1 | 1 | 600 | 6.00 |
| synthetic-city-05 | 1 | 1 | 500 | 5.00 |
| synthetic-city-04 | 1 | 1 | 400 | 4.00 |
| synthetic-city-03 | 1 | 1 | 300 | 3.00 |
| synthetic-city-01 | 1 | 1 | 100 | 1.00 |

### 商品排行（前 10 项）

| product_name | order_count | quantity | sales_amount_cents | sales_amount |
| --- | --- | --- | --- | --- |
| synthetic-product-03 | 2 | 2 | 1400 | 14.00 |
| synthetic-product-02 | 2 | 2 | 1200 | 12.00 |
| synthetic-product-01 | 2 | 2 | 1000 | 10.00 |
| synthetic-product-08 | 1 | 1 | 800 | 8.00 |
| synthetic-product-07 | 1 | 1 | 700 | 7.00 |
| synthetic-product-06 | 1 | 1 | 600 | 6.00 |
| synthetic-product-05 | 1 | 1 | 500 | 5.00 |
| synthetic-product-04 | 1 | 1 | 400 | 4.00 |

SQLite 是本地可运行验证后端；这份报告不构成 MySQL 实机验收。

## 处理边界

同批订单编号冲突全部隔离；跨批次同编号以最新成功导入覆盖。重复运行使用唯一键，不累加订单。

批次 CSV 与数据库全量 SQL 指标分别导出。原始 CSV 不改写；金额按整数分存储，日期缺失不猜测修复。
