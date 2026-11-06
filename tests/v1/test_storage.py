"""真实 SQLite 集成测试与 MySQL 客户端单元测试；不冒充 MySQL 实机验收。"""
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from src.order_pipeline.errors import ConfigurationError, DatabaseError
from src.order_pipeline.storage import MySQLConfig, MySQLStore, SQLiteStore


def orders_frame():
    return pd.DataFrame([
        {"order_id": "O1", "user_id": "U1", "product_name": "键盘", "quantity": 2,
         "unit_price": Decimal("10.05"), "unit_price_cents": 1005,
         "order_time": pd.Timestamp("2026-09-01 08:30:00"), "city": "上海", "status": "paid",
         "order_amount": Decimal("20.10"), "order_amount_cents": 2010, "source_row": 2},
        {"order_id": "O2", "user_id": "U2", "product_name": "鼠标", "quantity": 1,
         "unit_price": Decimal("5.00"), "unit_price_cents": 500,
         "order_time": pd.NaT, "city": "未知", "status": "completed",
         "order_amount": Decimal("5.00"), "order_amount_cents": 500, "source_row": 3},
    ])


def issues_frame():
    return pd.DataFrame([
        {"source_row": 3, "order_id": "O2", "rule": "city_missing", "severity": "warning",
         "action": "fill", "message": "城市补为未知", "raw_record": '{"city":""}'},
        {"source_row": 3, "order_id": "O2", "rule": "invalid_time", "severity": "warning",
         "action": "null", "message": "非法日期保留为NULL", "raw_record": '{"order_time":"bad"}'},
    ])


def run_record(character="a"):
    return {"run_id": character * 64, "source_hash": character * 64,
            "source_name": "orders.csv", "metrics": {"input_rows": 2, "valid_rows": 2}}


def table_counts(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("orders", "etl_runs", "dq_issues"))


def test_sqlite_repeat_import_and_audit_are_idempotent(tmp_path):
    path = tmp_path / "nested" / "pipeline.sqlite3"
    store = SQLiteStore(path)
    orders, issues = orders_frame(), issues_frame()
    orders_before, issues_before = orders.copy(deep=True), issues.copy(deep=True)
    store.initialize()
    store.initialize()
    store.load(orders, issues, run_record())
    store.load(orders, issues, run_record())
    assert table_counts(path) == (2, 1, 2)
    assert store.analyze()["summary"] == [{"total_orders": 2, "paid_orders": 1, "sales_amount_cents": 2010}]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT order_time FROM orders WHERE order_id='O2'").fetchone() == (None,)
        metrics = connection.execute("SELECT metrics_json FROM etl_runs").fetchone()[0]
        assert json.loads(metrics)["input_rows"] == 2
    assert_frame_equal(orders, orders_before)
    assert_frame_equal(issues, issues_before)


def test_sqlite_status_update_last_successful_import_wins(tmp_path):
    path = tmp_path / "pipeline.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    store.load(orders_frame(), issues_frame(), run_record())
    update = orders_frame().iloc[[0]].copy()
    update.loc[:, "status"] = "cancelled"
    store.load(update, issues_frame().iloc[:0], run_record("b"))
    assert table_counts(path) == (2, 2, 2)
    assert store.analyze()["summary"] == [{"total_orders": 2, "paid_orders": 0, "sales_amount_cents": 0}]
    assert store.analyze()["cities"] == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT last_run_id FROM orders WHERE order_id='O1'").fetchone()[0] == "b" * 64


def test_sqlite_transaction_rolls_back_orders_metrics_and_issues(tmp_path):
    path = tmp_path / "pipeline.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    store.load(orders_frame(), issues_frame(), run_record())
    changed = orders_frame().copy()
    changed.loc[:, "status"] = "paid"
    invalid_issues = issues_frame().copy()
    invalid_issues.loc[1, "raw_record"] = "invalid JSON"
    updated_run = run_record()
    updated_run["metrics"] = {"input_rows": 999}
    with pytest.raises(DatabaseError, match="回滚"):
        store.load(changed, invalid_issues, updated_run)
    assert table_counts(path) == (2, 1, 2)
    assert store.analyze()["summary"][0]["sales_amount_cents"] == 2010
    with sqlite3.connect(path) as connection:
        assert json.loads(connection.execute("SELECT metrics_json FROM etl_runs").fetchone()[0])["input_rows"] == 2
    broken_orders = orders_frame().copy()
    broken_orders.loc[1, "quantity"] = 0
    with pytest.raises(DatabaseError, match="回滚"):
        store.load(broken_orders, issues_frame(), run_record("c"))
    assert table_counts(path) == (2, 1, 2)


def test_sqlite_business_aggregates_are_exact_and_case_sensitive(tmp_path):
    store = SQLiteStore(tmp_path / "pipeline.sqlite3")
    store.initialize()
    orders = orders_frame()
    orders.loc[1, ["status", "city", "product_name"]] = ["paid", "上海", "键盘"]
    extra = orders.iloc[[0]].copy()
    extra.loc[:, "order_id"] = "o1"  # 与 O1 不同的订单，不能合并。
    extra.loc[:, "city"] = "shanghai"
    extra.loc[:, "product_name"] = "Keyboard"
    orders = pd.concat([orders, extra], ignore_index=True)
    store.load(orders, issues_frame(), run_record())
    result = store.analyze()
    assert result["summary"] == [{"total_orders": 3, "paid_orders": 3, "sales_amount_cents": 4520}]
    assert result["cities"][0] == {"city": "上海", "order_count": 2, "quantity": 3, "sales_amount_cents": 2510}
    assert result["products"][0] == {"product_name": "键盘", "order_count": 2, "quantity": 3, "sales_amount_cents": 2510}


def test_sqlite_empty_load_and_analysis(tmp_path):
    store = SQLiteStore(tmp_path / "pipeline.sqlite3")
    store.initialize()
    store.load(orders_frame().iloc[:0], issues_frame().iloc[:0], run_record())
    assert store.analyze() == {
        "summary": [{"total_orders": 0, "paid_orders": 0, "sales_amount_cents": 0}],
        "cities": [], "products": [],
    }


def test_sqlite_total_amount_overflow_is_rejected_before_commit(tmp_path):
    path = tmp_path / "pipeline.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    # 每行均符合质量层的金额上限，1024 行仍可被 SQLite 精确 SUM。
    template = orders_frame().iloc[0].to_dict()
    template.update(quantity=9000, unit_price=Decimal("9999999999.99"),
                    unit_price_cents=999999999999,
                    order_amount=Decimal("89999999999910.00"),
                    order_amount_cents=8999999999991000)
    baseline = pd.DataFrame([
        dict(template, order_id=f"LIMIT_{index}", source_row=index + 2)
        for index in range(1024)
    ])
    no_issues = issues_frame().iloc[:0]
    store.load(baseline, no_issues, run_record())
    amount_before = 1024 * template["order_amount_cents"]
    assert store.analyze()["summary"][0]["sales_amount_cents"] == amount_before
    extra = pd.DataFrame([dict(template, order_id="LIMIT_OVERFLOW")])
    with pytest.raises(DatabaseError, match=r"2\^63-1.*回滚"):
        store.load(extra, issues_frame(), run_record("b"))
    assert table_counts(path) == (1024, 1, 0)
    assert store.analyze()["summary"][0]["sales_amount_cents"] == amount_before


def test_sqlite_clear_error_before_etl_and_invalid_path(tmp_path):
    store = SQLiteStore(tmp_path / "pipeline.sqlite3")
    with pytest.raises(DatabaseError, match="先执行"):
        store.analyze()
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(DatabaseError, match="路径"):
        SQLiteStore(directory).initialize()


def set_mysql_env(monkeypatch):
    for key, value in {"MYSQL_HOST": "127.0.0.1", "MYSQL_USER": "test",
                       "MYSQL_PASSWORD": "secret-only-local", "MYSQL_DATABASE": "test_orders"}.items():
        monkeypatch.setenv(key, value)
    for key in ("MYSQL_PORT", "MYSQL_CONNECT_TIMEOUT", "MYSQL_SSL_CA"):
        monkeypatch.delenv(key, raising=False)


def test_mysql_configuration_does_not_expose_password(monkeypatch):
    set_mysql_env(monkeypatch)
    config = MySQLConfig.from_env()
    assert config.port == 3306 and config.connect_timeout == 10
    assert "secret-only-local" not in repr(config)
    monkeypatch.delenv("MYSQL_USER")
    with pytest.raises(ConfigurationError, match="MYSQL_USER"):
        MySQLConfig.from_env()


@pytest.mark.parametrize("variable,value", [
    ("MYSQL_PORT", "not-a-port"), ("MYSQL_PORT", "0"),
    ("MYSQL_CONNECT_TIMEOUT", "61"), ("MYSQL_CONNECT_TIMEOUT", "0"),
])
def test_mysql_config_rejects_invalid_port_and_timeout(monkeypatch, variable, value):
    set_mysql_env(monkeypatch)
    monkeypatch.setenv(variable, value)
    with pytest.raises(ConfigurationError):
        MySQLConfig.from_env()


def fake_mysql_driver(monkeypatch):
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    connection.cursor.return_value.__enter__.return_value = cursor
    driver = SimpleNamespace(connect=MagicMock(return_value=connection),
                             cursors=SimpleNamespace(DictCursor=object()))
    monkeypatch.setattr("src.order_pipeline.storage.importlib.import_module", lambda _: driver)
    return driver, connection, cursor


def test_mysql_mock_uses_parameterized_sql_and_commits(monkeypatch):
    driver, connection, cursor = fake_mysql_driver(monkeypatch)
    store = MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders"))
    store.initialize()
    assert cursor.execute.call_count == 3  # SQL 文件中的真实建表语句。
    cursor.reset_mock()
    store.load(orders_frame(), issues_frame(), run_record())
    connection.begin.assert_called_once()
    assert connection.commit.call_count == 2
    connection.rollback.assert_not_called()
    assert connection.close.call_count == 2
    order_sql, parameters = cursor.executemany.call_args_list[0].args
    assert "AS incoming ON DUPLICATE KEY UPDATE" in order_sql
    assert "键盘" not in order_sql and parameters[0][2] == "键盘"
    assert parameters[1][5] is None
    kwargs = driver.connect.call_args.kwargs
    assert kwargs["autocommit"] is False and kwargs["charset"] == "utf8mb4"


def test_mysql_mock_rolls_back_on_failure_and_redacts_errors(monkeypatch):
    _, connection, cursor = fake_mysql_driver(monkeypatch)
    cursor.executemany.side_effect = RuntimeError("hidden password")
    store = MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders"))
    with pytest.raises(DatabaseError) as error:
        store.load(orders_frame(), issues_frame(), run_record())
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()
    assert "hidden" not in str(error.value)


def test_mysql_mock_connection_errors_are_redacted(monkeypatch):
    driver, _, _ = fake_mysql_driver(monkeypatch)
    driver.connect.side_effect = RuntimeError("secret_password_in_driver_message")
    store = MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders"))
    with pytest.raises(DatabaseError, match="连接失败") as error:
        store.initialize()
    assert "secret_password" not in str(error.value)


def test_mysql_mock_analysis_executes_sql_file(monkeypatch):
    _, connection, cursor = fake_mysql_driver(monkeypatch)
    cursor.fetchall.side_effect = [
        [{"total_orders": 2, "paid_orders": Decimal(1), "sales_amount_cents": Decimal(2010)}],
        [{"city": "上海", "order_count": 1, "quantity": Decimal(2), "sales_amount_cents": Decimal(2010)}],
        [{"product_name": "键盘", "order_count": 1, "quantity": Decimal(2), "sales_amount_cents": Decimal(2010)}],
    ]
    result = MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders")).analyze()
    assert cursor.execute.call_count == 3
    assert "GROUP BY city" in cursor.execute.call_args_list[1].args[0]
    assert type(result["summary"][0]["sales_amount_cents"]) is int
    connection.close.assert_called_once()


def test_mysql_dependency_is_optional_with_actionable_error(monkeypatch):
    def unavailable(_):
        raise ImportError("not installed")
    monkeypatch.setattr("src.order_pipeline.storage.importlib.import_module", unavailable)
    with pytest.raises(ConfigurationError, match="requirements-mysql.txt"):
        MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders")).initialize()


def test_mysql_cleanup_disconnect_does_not_leak_driver_error(monkeypatch):
    _, connection, cursor = fake_mysql_driver(monkeypatch)
    cursor.executemany.side_effect = RuntimeError("hidden in write error")
    connection.rollback.side_effect = RuntimeError("hidden in rollback error")
    connection.close.side_effect = RuntimeError("hidden in close error")
    with pytest.raises(DatabaseError, match="已尝试回滚") as error:
        MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders")).load(
            orders_frame(), issues_frame(), run_record(),
        )
    assert "hidden" not in str(error.value)


def test_mysql_ssl_ca_enables_server_certificate_and_identity_checks(monkeypatch):
    driver, _, _ = fake_mysql_driver(monkeypatch)
    store = MySQLStore(MySQLConfig("localhost", "test", "hidden", "test_orders", ssl_ca="/test/ca.pem"))
    store.initialize()
    kwargs = driver.connect.call_args.kwargs
    assert kwargs["ssl_ca"] == "/test/ca.pem"
    assert kwargs["ssl_verify_cert"] is True and kwargs["ssl_verify_identity"] is True
