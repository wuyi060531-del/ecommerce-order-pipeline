# 项目介绍与本人复现要求

仓库：[wuyi060531-del/ecommerce-order-pipeline](https://github.com/wuyi060531-del/ecommerce-order-pipeline)，已上传并保持 Private；暂不添加 LICENSE。

GitHub 简介：`Python/Pandas ETL with auditable data quality rules, exact money, idempotent SQLite/MySQL loading, SQL reconciliation, and reproducible synthetic demos.`

## 可据实使用的项目经历候选

> 在 Codex 辅助下维护并验证 Python 电商订单批处理工程，覆盖 CSV 数据质量审计、异常隔离、Decimal / 整数分金额、SQLite / MySQL 事务加载及 SQL 对账。完成独立源码拆分和 synthetic 演示，验证重跑幂等、A/B/A 恢复与文件发布故障后的安全重试；测试数量引用本候选目录的实测结果。项目为单机虚构数据实验，无企业生产经历。

不能把 AI 主导工程写成“本人独立完成”。拆分前的历史 MySQL 与本工程 GitHub CI 结果应分别标注数据、提交及环境，见[验收](VALIDATION.md)；不将 synthetic 实验写成真实企业吞吐。未来本人亲自修改、复现和解释的部分可按真实贡献补充。

## 必须亲自复现的三项核心能力

一次只做一项，不预填完成或掌握记录。

1. **从新 CSV 独立组织 ETL 与质量核对**：解释去重、隔离、修复和警告，用 5 行新数据写出数量等式并实现；源码 `quality.py` / `transforms.py`，行为参考 `tests/v1/test_quality.py`。必须解释副本、Series / DataFrame、NaT 和 Decimal 的作用。
2. **亲自证明事务和幂等**：在临时 SQLite 完成插入、变化更新、未变零写入、A/B/A；写入第二笔时注入失败并证明整批回滚。源码 `storage.py`，参考 `tests/v1/test_storage.py` / `test_storage_v11.py`，不能只背“唯一键防重”。
3. **独立 SQL 对账与失败定位**：用同一 paid 范围核对明细、城市、商品和数据库总额，解释本批与全库区别；故意使文件发布失败，定位数据库是否已提交并重跑恢复。源码 `pipeline.py` / `sql/analysis_mysql.sql`，参考 `tests/v1/test_pipeline.py` / `test_pipeline_v11.py`。

测试通过只证明已覆盖行为；独立解释、换需求迁移和延迟记忆另行验证。
