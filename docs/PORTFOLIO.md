# 项目说明与代码阅读指南

仓库：[wuyi060531-del/ecommerce-order-pipeline](https://github.com/wuyi060531-del/ecommerce-order-pipeline)

一句话：Python/Pandas ETL，带可审计的数据质量规则、精确金额、幂等的 SQLite/MySQL 加载、SQL 对账，以及可重现的合成数据演示。

这是一个单机、虚构数据的练习项目，没有企业生产经历，也没有真实业务数据。工程在 AI 辅助下完成，具体分工见 [PROVENANCE](PROVENANCE.md)。

## 想验证核心行为，可以从这三件事入手

1. **质量规则和去重**：用 5 行新数据写出“原始行 = 接收 + 隔离 + 重复副本”这个等式。代码在 `quality.py`、`transforms.py`，行为见 `tests/v1/test_quality.py`。
2. **事务和幂等**：在临时 SQLite 里依次做插入、变化更新、没变化时零写入、A → B → A 恢复；写第二笔时注入失败，确认整批回滚。代码在 `storage.py`，行为见 `tests/v1/test_storage.py` 和 `test_storage_v11.py`。
3. **SQL 对账和失败定位**：用同一个 paid 范围核对明细、城市、商品和数据库总额；让报告发布失败一次，看数据库是否已经提交，再重跑恢复。代码在 `pipeline.py`、`sql/analysis_mysql.sql`，行为见 `tests/v1/test_pipeline.py` 和 `test_pipeline_v11.py`。
