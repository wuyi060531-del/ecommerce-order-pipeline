# 工程验证与展示交接（2026-11-14）

基线为公开 main 493ed0f7c44fd4bdd6b77ecf42802a983fad8297。未导入私人工作区历史。
由 Codex 在维护者授权下协助修改和验证；没有真实订单、客户或生产环境。

## 本次改动

修复 CSV 公式前缀，范围仅为 `pipeline._csv` 导出副本。统一引用字段，在危险文本
前加可见 `text:`，不依赖 Excel 的单引号解释。输入、内存实体、数据库和 raw_record
原文不变；规则、事务、唯一键、批次身份、SQLite/MySQL SQL 保持现状。
具体格式与限制见 [CSV_EXPORT.md](CSV_EXPORT.md)。文件哈希和发布说明同步更新。

已存在且经过验证的能力：质量隔离与审计、整数分、同事务写入三张表、回滚、幂等
重跑与 A/B/A、数据库提交后文件发布失败的恢复。不重复改写这些功能。

## 实际执行

隔离 Python 3.12，requirements-dev.txt/requirements-mysql.txt 固定版本依赖。

- 基线 `python -m pytest tests/v1 -q`：145 通过。
- 新回归在旧实现：18 失败、8 通过；修复后 26 通过。
- 完整 `python -m pytest tests/v1 -q`：171 通过，无跳过；一个 Pandas/NumPy NaT 兼容性警告。
- `python -m ruff check src/order_pipeline scripts tests/v1`：通过。
- `python -m scripts.check_release`：哈希、文件集合、私密信息边界及文档链接通过。
- `python -m scripts.demo_sqlite`：30=26+3+1；paid 66.00；重跑零写入；SQL、输入和哈希一致。
- 配置独立 MySQL 8.4.13、空的 order_pipeline_ci_test 库及 `MYSQL_INTEGRATION=1` 后，
  `python -m scripts.verify_mysql`：完整 ETL/SQLite 对账、约束、重跑、精度与大小写、
  事务回滚、A/B/A、关闭连接后的明确失败七组验收通过。
- `run --backend mysql` 后实际重启该测试容器，再 `analyze --backend mysql`：
  重启前后 database_summary.json 完全相同。没有删除已有用户容器或数据卷。
- `docker build --pull=false --tag order-pipeline:engineering-review .`：未完成，Docker Hub
  的 docker/dockerfile 前端令牌请求网络超时。本机容器构建/冒烟不能标成通过。

上述为本机结果，新提交的 GitHub CI 以 PR Actions 为准。既有基线 CI 已包含
Python 3.10/3.12、真实 MySQL 和容器。未用本机 Python 3.12 的结果替代其他平台。

## Claude / GitHub 展示入口

这是 CLI / 报告项目，没有现成 Web 应用，不应为视觉优化搭建新的全栈平台。

1. README 演示：`python -m scripts.demo_sqlite`，按输出路径打开 report.md。
2. 主要展示：本批质量、隔离理由、全库 SQL 汇总、重跑零写入、事务失败回滚。
3. 源码入口：`__main__.py` CLI；`quality.py` 规则；`storage.py` 事务；`pipeline.py` 发布。
4. 展示产物：report.md、quality_issues.csv、quality_metrics.json、database_summary.json、manifest.json。
   CSV 的 text: 是防公式展示标记，不能当成数据库的原始值。
5. 接口是 `python -m src.order_pipeline --help` 下的 run/check/analyze/report，
   参数与 MySQL 配置见 [RUNNING.md](RUNNING.md)。不增加不必要的 HTTP 接口。
6. 保留“合成、单机、单写者”说明、原始行数恒等式及本批/全库范围差别。
   截图不能冒称线上订单或真实吞吐；归因见 [PROVENANCE.md](PROVENANCE.md)。

## 真正保留的风险

CSV 无类型，不保证在所有表格软件、用户编辑或格式转换后仍安全；未实际启动 Excel。
旧产物需重跑 ETL/分析，report 命令不会重新导出旧 CSV。数据库和文件发布不原子；
按既有策略幂等补齐。单写者、latest-successful-import-wins 不代表事件时间最新值。
未验证 Windows、服务器崩溃或结果未知的提交故障。
