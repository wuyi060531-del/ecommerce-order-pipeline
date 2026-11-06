"""显式 MySQL 实机验收：独占、空的专用测试库；不安装或启动服务。

公开合成 CSV 完整 ETL 对账，再验证少写、重放、约束、回滚和连接。
结束时按精确订单／批次键清理本次数据，保留表结构。连接模拟测试
不构成本脚本实机通过；未配置退出 2，失败退出 1，通过退出 0。
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from src.order_pipeline.errors import ConfigurationError, DatabaseError, PipelineError
from src.order_pipeline.pipeline import PROJECT_ROOT, run_pipeline
from src.order_pipeline.quality import inspect_csv
from src.order_pipeline.storage import MySQLConfig, MySQLStore, SQLiteStore


def _expect(condition: bool, message: str) -> None:
    # python -O 运行也不能跳过验收。
    if not condition:
        raise AssertionError(message)


def _config_for_verification() -> MySQLConfig:
    if os.environ.get("MYSQL_INTEGRATION") != "1":
        raise ConfigurationError("请显式设置 MYSQL_INTEGRATION=1 及专用测试库的 MYSQL_*。")
    config = MySQLConfig.from_env()
    if not re.fullmatch(r"[A-Za-z0-9_]+_(test|smoke)", config.database):
        raise ConfigurationError("实机验收仅允许名字以 _test 或 _smoke 结尾的独占专用数据库。")
    return config


def _rows(store: MySQLStore, sql: str, parameters: tuple = ()) -> list[dict]:
    connection = store._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()
    finally:
        connection.close()


def _counts(store: MySQLStore) -> tuple[int, int, int]:
    return tuple(int(_rows(store, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])
                 for table in ("orders", "etl_runs", "dq_issues"))


def _run(key: str, name: str) -> dict:
    digest = hashlib.sha256(key.encode("ascii")).hexdigest()
    return dict(run_id=digest, source_hash=digest, source_name=name, metrics={"verification": 1})


def _cleanup(store: MySQLStore, order_ids: list[str], run_ids: list[str]) -> None:
    connection = store._connect()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            markers = ",".join("%s" for _ in run_ids)
            if order_ids:
                ids = ",".join("%s" for _ in order_ids)
                cursor.execute(
                    f"DELETE FROM orders WHERE order_id IN ({ids}) AND last_run_id IN ({markers})",
                    tuple(order_ids) + tuple(run_ids),
                )
            cursor.execute(f"DELETE FROM dq_issues WHERE run_id IN ({markers})", tuple(run_ids))
            cursor.execute(f"DELETE FROM etl_runs WHERE run_id IN ({markers})", tuple(run_ids))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _reject_sql(store: MySQLStore, sql: str, parameters: tuple, name: str) -> None:
    """真实服务发出非法 SQL 后回滚；不把断网或语法错误当约束通过。"""
    connection = store._connect()
    rejected = False
    try:
        connection.begin()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
        except Exception as error:
            code = error.args[0] if getattr(error, "args", None) else None
            rejected = type(error).__name__ in {"IntegrityError", "DataError", "OperationalError"}
            rejected = rejected and code in {1048, 1062, 1452, 3140, 3819, 4025}
    finally:
        connection.rollback()
        connection.close()
    _expect(rejected, name + "未由数据库约束明确拒绝")


def _check_schema_and_constraints(store: MySQLStore, run_id: str) -> None:
    schema = _rows(store, "SELECT COLUMN_NAME AS col, DATA_TYPE AS kind FROM INFORMATION_SCHEMA.COLUMNS "
                         "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders'")
    kinds = {row["col"]: row["kind"] for row in schema}
    _expect(kinds.get("unit_price_cents") == "bigint" and kinds.get("order_amount_cents") == "bigint",
            "货币字段必须实际使用 BIGINT 整数分")
    _expect(kinds.get("order_time") == "datetime", "订单时间字段必须实际使用 DATETIME")
    for column, value in (("quantity", 0), ("unit_price_cents", -1),
                          ("order_amount_cents", -1), ("user_id", None)):
        _reject_sql(store, f"UPDATE orders SET {column}=%s WHERE order_id=%s", (value, "SYN001"), column)
    _reject_sql(store, "UPDATE orders SET last_run_id=%s WHERE order_id=%s", ("f" * 64, "SYN001"), "外键")
    _reject_sql(store, "INSERT INTO orders SELECT * FROM orders WHERE order_id=%s", ("SYN001",), "主键")
    _reject_sql(store, "UPDATE dq_issues SET raw_record=%s WHERE run_id=%s", ("not-json", run_id), "JSON")
    print("通过：真实 BIGINT/DATETIME、CHECK、NOT NULL、主键、外键及 JSON 约束。")


def _precision_orders(token: str) -> pd.DataFrame:
    prototype = dict(user_id="VERIFY_USER", product_name="Precision", quantity=3,
                     unit_price=Decimal("0.10"), unit_price_cents=10, order_time=pd.NaT,
                     city="PrecisionCity", status="paid", order_amount=Decimal("0.30"),
                     order_amount_cents=30, source_row=2)
    return pd.DataFrame([
        dict(prototype, order_id="VERIFY_" + token + "_precision"),
        dict(prototype, order_id="VERIFY_" + token + "_A", city="CaseCity", product_name="CaseProduct"),
        dict(prototype, order_id="VERIFY_" + token + "_a", city="casecity", product_name="caseproduct"),
    ])


def _execute_checks(store: MySQLStore, source: Path, quality: Any, directory: Path,
                    token: str, owned_orders: list[str], owned_runs: list[str]) -> None:
    sqlite_path = directory / "reference.sqlite3"
    sqlite_result = run_pipeline(source, directory / "sqlite-report", database_path=sqlite_path)
    mysql_result = run_pipeline(source, directory / "mysql-report", backend="mysql")
    reference = SQLiteStore(sqlite_path)
    _expect(sqlite_result.metrics == mysql_result.metrics == quality.metrics, "公开 CSV ETL 质量指标不一致")
    _expect(store.analyze() == reference.analyze(), "MySQL 与 SQLite 公开 CSV SQL 结果不一致")
    _expect(_counts(store) == (26, 1, 7), "公开 CSV 订单／批次／质量审计数量错误")
    for filename in ("orders_clean.csv", "quality_issues.csv", "batch_city_sales.csv", "batch_product_sales.csv"):
        _expect((directory / "sqlite-report" / filename).read_bytes() == (directory / "mysql-report" / filename).read_bytes(),
                "公开 CSV 导出不一致：" + filename)
    _expect(_rows(store, "SELECT order_time FROM orders WHERE order_id=%s", ("SYN011",))[0]["order_time"] is None,
            "无效日期必须保留为 SQL NULL，订单不能丢失")
    _expect(_rows(store, "SELECT order_amount_cents FROM orders WHERE order_id=%s", ("SYN011",))[0]["order_amount_cents"] == 1100,
            "公开样本 11.00×1 整数分错误")
    print("通过：公开 30 行 CSV 完整 ETL、26 订单／7 质量记录、NULL 日期及 SQLite／SQL／导出逐项对账。")
    run = dict(run_id=quality.run_id, source_hash=quality.source_hash,
               source_name=quality.source_name, metrics=quality.metrics)
    replay = store.load(quality.orders, quality.issues, run)
    _expect(replay["written"] == 0 and replay["unchanged"] == 26
            and replay["run_written"] == 0 and replay["issues_replaced"] == 0,
            "公开 CSV 重跑没有跳过未变化订单／审计")
    _expect(_counts(store) == (26, 1, 7), "重复导入增加了订单或质量审计")
    _check_schema_and_constraints(store, quality.run_id)
    _expect(store.analyze() == reference.analyze(), "非法 SQL 回滚后数据发生变化")
    print("通过：公开 CSV 重跑写入 0、未变化 26，批次与 DQ 审计未重复。")

    precision = _precision_orders(token)
    precision_run = _run(token + "precision", "mysql_precision_smoke.csv")
    owned_orders.extend(precision.order_id.tolist())
    owned_runs.append(precision_run["run_id"])
    no_issues = quality.issues.iloc[:0]
    actual = store.load(precision, no_issues, precision_run)
    expected = reference.load(precision, no_issues, precision_run)
    _expect(actual == expected and actual["written"] == 3, "精度样本增量写入不一致")
    _expect(store.analyze() == reference.analyze(), "0.10×3 或区分大小写的 SQL 聚合错误")
    _expect(_rows(store, "SELECT order_amount_cents FROM orders WHERE order_id=%s", (precision.order_id.iloc[0],))[0]["order_amount_cents"] == 30,
            "0.10×3 必须严格为 30 分")
    print("通过：0.10×3=30 分；大小写不同的订单键与城市／商品分别统计。")

    before = store.analyze()
    before_counts = _counts(store)
    failed_run = _run(token + "failed", "mysql_rollback_smoke.csv")
    owned_runs.append(failed_run["run_id"])
    invalid = quality.orders.iloc[:2].copy(deep=True)
    invalid.iloc[0, invalid.columns.get_loc("status")] = "cancelled"
    invalid.iloc[1, invalid.columns.get_loc("quantity")] = 0
    try:
        store.load(invalid, quality.issues, failed_run)
    except DatabaseError:
        pass
    else:
        raise AssertionError("无效 quantity 未拒绝，事务回滚验收失败")
    _expect(store.analyze() == before and _counts(store) == before_counts, "失败事务遗留部分订单或审计")
    print("通过：部分更新后遇到无效数量，订单、run 和 DQ 全部回滚。")

    changed = quality.orders.iloc[[0]].copy(deep=True)
    changed.loc[:, "status"] = "cancelled"
    changed_run = _run(token + "changed", "mysql_replay_smoke.csv")
    owned_runs.append(changed_run["run_id"])
    _expect(store.load(changed, no_issues, changed_run)["written"] == 1, "状态更新未写入")
    replay = store.load(quality.orders, quality.issues, run)
    _expect(replay["written"] == 1 and replay["unchanged"] == 25, "A/B/A 重放被已见 run_id 错误跳过")
    _expect(store.analyze() == before, "A/B/A 没有恢复 A 的业务值")
    print("通过：相同订单状态更新及 A/B/A 重放，保留 latest-successful-import-wins。")

    disconnected = store._connect()
    disconnected.close()
    real_connect = store._connect
    try:
        store._connect = lambda: disconnected
        try:
            store.load(quality.orders, quality.issues, run)
        except DatabaseError:
            pass
        else:
            raise AssertionError("关闭后的真实客户端连接未明确拒绝写入")
    finally:
        store._connect = real_connect
    _expect(MySQLStore(store.config).analyze() == before, "重连后已提交数据未持久化")
    print("通过：主动关闭真实客户端连接后写入明确失败；重连后的已提交数据仍在。")
    print("边界：此项不宣称已验证服务端崩溃、真实网络中断或提交结果不明时的恢复。")


def main() -> int:
    try:
        config = _config_for_verification()
    except ConfigurationError as error:
        print("待实机验收：" + str(error))
        return 2
    source = PROJECT_ROOT / "data/raw/orders.csv"
    token = uuid.uuid4().hex
    store = MySQLStore(config)
    owns_data = False
    owned_orders: list[str] = []
    owned_runs: list[str] = []
    outcome = 1
    try:
        original_bytes = source.read_bytes()
        quality = inspect_csv(source)
        store.initialize()
        _expect(_counts(store) == (0, 0, 0), "专用测试库的三张业务表必须全空；拒绝写入或清理已有数据")
        owns_data = True
        owned_orders.extend(quality.orders.order_id.tolist())
        owned_runs.append(quality.run_id)
        print("已确认显式授权、专用测试库后缀与空业务表；请确保本次验收独占该库。")
        with tempfile.TemporaryDirectory(prefix="order-pipeline-mysql-verify-") as temporary:
            _execute_checks(store, source, quality, Path(temporary), token, owned_orders, owned_runs)
        _expect(source.read_bytes() == original_bytes, "公开合成 CSV 被修改")
        outcome = 0
    except ConfigurationError as error:
        print("待实机验收：" + str(error))
        outcome = 2
    except (PipelineError, AssertionError) as error:
        print("未通过：" + str(error))
    except Exception:
        print("未通过：实机数据库或文件操作异常；请检查专用库、版本、权限和连接。")
    finally:
        if owns_data:
            try:
                _cleanup(store, owned_orders, owned_runs)
                _expect(_counts(store) == (0, 0, 0), "本次测试数据清理后仍有记录")
                print("本次精确订单／批次／DQ 数据已清理，表结构保留。")
            except Exception:
                print("清理未获确认：请检查专用测试库的本次订单与 smoke 批次；禁止继续用于其他项目。")
                outcome = 1
    if outcome == 0:
        print("MySQL 实机验收通过。")
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
