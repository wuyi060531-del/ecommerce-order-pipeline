"""V1.1：现库值比较、少写与重放；MySQL 模拟不等同实机验证。"""
import json
import sqlite3
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.order_pipeline.errors import ConfigurationError, DatabaseError
from src.order_pipeline.storage import MySQLConfig, MySQLStore, SQLiteStore


def orders_frame():
    return pd.DataFrame([
        dict(order_id="O1", user_id="U1", product_name="商品甲", quantity=3,
             unit_price=Decimal("0.10"), unit_price_cents=10,
             order_time=pd.Timestamp("2026-10-01 08:00:00"), city="杭州", status="paid",
             order_amount=Decimal("0.30"), order_amount_cents=30, source_row=2),
        dict(order_id="O2", user_id="U2", product_name="商品乙", quantity=1,
             unit_price=Decimal("9.90"), unit_price_cents=990,
             order_time=pd.NaT, city="未知", status="paid",
             order_amount=Decimal("9.90"), order_amount_cents=990, source_row=3),
    ])


def issues_frame():
    return pd.DataFrame([dict(source_row=3, order_id="O2", rule="invalid_time",
                             severity="warning", action="set_null", message="时间留空",
                             raw_record='{"order_time":"bad", "city":"未知"}')])


def run_record(key="a"):
    return dict(run_id=key * 64, source_hash=key * 64, source_name="input.csv",
                metrics={"raw_rows": 2, "sales_amount_cents": 1020})


def sqlite_store(tmp_path):
    store = SQLiteStore(tmp_path / "orders.sqlite3")
    store.initialize()
    return store


def audit_counts(store):
    with sqlite3.connect(store.path) as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("orders", "etl_runs", "dq_issues"))


def install_write_probe(store):
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TABLE write_probe (table_name TEXT, operation TEXT)")
        for table in ("orders", "etl_runs", "dq_issues"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER probe_{table}_{operation} AFTER {operation} ON {table} "
                    f"BEGIN INSERT INTO write_probe VALUES ('{table}','{operation}'); END"
                )


def test_sqlite_identical_replay_performs_no_order_or_audit_writes(tmp_path):
    store = sqlite_store(tmp_path)
    first = store.load(orders_frame(), issues_frame(), run_record())
    assert first == dict(incoming=2, written=2, unchanged=0, run_written=1,
                         issues_replaced=1, issues_written=1)
    install_write_probe(store)
    replay = store.load(orders_frame(), issues_frame(), run_record())
    assert replay == dict(incoming=2, written=0, unchanged=2, run_written=0,
                          issues_replaced=0, issues_written=0)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM write_probe").fetchone()[0] == 0
    assert audit_counts(store) == (2, 1, 1)


def test_sqlite_same_values_new_run_keeps_original_value_provenance(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    new_batch = orders_frame()
    new_batch["source_row"] += 20
    stats = store.load(new_batch, issues_frame(), run_record("b"))
    assert stats["written"] == 0 and stats["unchanged"] == 2 and stats["run_written"] == 1
    assert audit_counts(store) == (2, 2, 2)
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute("SELECT source_row,last_run_id FROM orders ORDER BY order_id").fetchall()
        assert rows == [(2, "a" * 64), (3, "a" * 64)]


def test_sqlite_one_changed_and_one_added_order_write_only_those_rows(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    install_write_probe(store)
    update = orders_frame()
    update.loc[0, "status"] = "cancelled"
    extra = update.iloc[[1]].copy()
    extra.loc[:, "order_id"] = "O3"
    stats = store.load(pd.concat([update, extra], ignore_index=True), issues_frame(), run_record("b"))
    assert (stats["incoming"], stats["written"], stats["unchanged"]) == (3, 2, 1)
    with sqlite3.connect(store.path) as connection:
        writes = connection.execute("SELECT operation FROM write_probe WHERE table_name='orders' ORDER BY operation").fetchall()
        assert writes == [("INSERT",), ("UPDATE",)]
    assert store.analyze()["summary"][0] == dict(total_orders=3, paid_orders=2, sales_amount_cents=1980)


def test_sqlite_a_b_a_replay_restores_a_values_despite_existing_run_id(tmp_path):
    store = sqlite_store(tmp_path)
    batch_a = orders_frame()
    store.load(batch_a, issues_frame(), run_record())
    batch_b = batch_a.copy(deep=True)
    batch_b.loc[0, "status"] = "cancelled"
    store.load(batch_b, issues_frame(), run_record("b"))
    replay = store.load(batch_a, issues_frame(), run_record())
    assert replay["written"] == 1 and replay["unchanged"] == 1
    assert replay["run_written"] == 0 and replay["issues_replaced"] == 0
    assert store.analyze()["summary"][0]["sales_amount_cents"] == 1020
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT last_run_id FROM orders WHERE order_id='O1'").fetchone()[0] == "a" * 64


def test_sqlite_audit_changes_are_independent_of_unchanged_orders(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    updated_run = run_record()
    updated_run["metrics"] = {"raw_rows": 9}
    stats = store.load(orders_frame(), issues_frame().iloc[:0], updated_run)
    assert stats == dict(incoming=2, written=0, unchanged=2, run_written=1,
                         issues_replaced=1, issues_written=0)
    assert audit_counts(store) == (2, 1, 0)
    with sqlite3.connect(store.path) as connection:
        assert json.loads(connection.execute("SELECT metrics_json FROM etl_runs").fetchone()[0]) == {"raw_rows": 9}


def test_sqlite_json_formatting_and_datetime_representations_do_not_trigger_writes(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    # V1.0 默认 datetime adapter 留下的文本没有 .000000；升级不能误判为变化。
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE orders SET order_time='2026-10-01 08:00:00' WHERE order_id='O1'")
    install_write_probe(store)
    alternate = orders_frame().astype({"order_time": "object"})
    alternate.loc[0, "order_time"] = "2026-10-01 08:00:00.000000"
    alternate.loc[1, "order_time"] = None
    issues = issues_frame()
    issues.loc[0, "raw_record"] = '{ "city": "未知", "order_time": "bad" }'
    stats = store.load(alternate, issues, run_record())
    assert stats["written"] == 0 and stats["issues_replaced"] == 0
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM write_probe").fetchone()[0] == 0


def test_sqlite_lookup_is_chunked_beyond_default_sqlite_variable_limit(tmp_path, monkeypatch):
    store = sqlite_store(tmp_path)
    prototype = orders_frame().iloc[0].to_dict()
    orders = pd.DataFrame([dict(prototype, order_id=f"ORDER_{i}") for i in range(1001)])
    no_issues = issues_frame().iloc[:0]
    store.load(orders, no_issues, run_record())
    queries = []
    connect = store._connect

    def traced_connection():
        connection = connect()
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(store, "_connect", traced_connection)
    stats = store.load(orders, no_issues, run_record())
    lookups = [sql for sql in queries if "FROM orders WHERE order_id IN" in sql]
    assert len(lookups) == 3
    assert stats["written"] == 0 and stats["unchanged"] == 1001


def test_sqlite_invalid_changed_row_cannot_be_hidden_by_incremental_skip(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    invalid = orders_frame()
    invalid.loc[1, "quantity"] = 0
    with pytest.raises(DatabaseError, match="回滚"):
        store.load(invalid, issues_frame(), run_record("b"))
    assert audit_counts(store) == (2, 1, 1)
    assert store.analyze()["summary"][0]["sales_amount_cents"] == 1020
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT quantity FROM orders WHERE order_id='O2'").fetchone()[0] == 1


def test_sqlite_unchanged_orders_do_not_skip_invalid_audit_validation(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    bad_issues = issues_frame()
    bad_issues.loc[0, "raw_record"] = '{"not_json":NaN}'
    with pytest.raises(DatabaseError, match="回滚"):
        store.load(orders_frame(), bad_issues, run_record())
    assert audit_counts(store) == (2, 1, 1)


def test_sqlite_same_batch_duplicate_ids_fail_without_partial_changes(tmp_path):
    store = sqlite_store(tmp_path)
    store.load(orders_frame(), issues_frame(), run_record())
    duplicate = orders_frame().iloc[[0, 0]].copy()
    duplicate.iloc[0, duplicate.columns.get_loc("status")] = "cancelled"
    with pytest.raises(DatabaseError, match="回滚"):
        store.load(duplicate, issues_frame(), run_record("b"))
    assert audit_counts(store) == (2, 1, 1)
    assert store.analyze()["summary"][0]["sales_amount_cents"] == 1020


def mysql_with_existing(monkeypatch, changed=False):
    store = MySQLStore(MySQLConfig("localhost", "test", "secret", "orders_test"))
    connection, cursor = MagicMock(), MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    records = orders_frame().to_dict(orient="records")
    records[0]["order_time"] = records[0]["order_time"].to_pydatetime()
    records[1]["order_time"] = None
    if changed:
        records[0]["status"] = "cancelled"
    existing_issues = [dict(run_id="a" * 64, issue_number=1, **issues_frame().iloc[0].to_dict())]

    def execute(sql, parameters=None):
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        if "FROM etl_runs WHERE" in sql:
            cursor.fetchone.return_value = dict(source_hash="a" * 64, source_name="input.csv",
                                                metrics_json='{"sales_amount_cents":1020, "raw_rows":2}')
        elif "FROM orders WHERE order_id IN" in sql:
            cursor.fetchall.return_value = records
        elif "FROM dq_issues WHERE" in sql:
            cursor.fetchall.return_value = existing_issues

    cursor.execute.side_effect = execute
    monkeypatch.setattr(store, "_connect", lambda: connection)
    return store, connection, cursor


def test_mysql_mock_same_values_skip_orders_and_audit_with_datetime_json_normalization(monkeypatch):
    store, connection, cursor = mysql_with_existing(monkeypatch)
    stats = store.load(orders_frame(), issues_frame(), run_record())
    assert stats == dict(incoming=2, written=0, unchanged=2, run_written=0,
                         issues_replaced=0, issues_written=0)
    cursor.executemany.assert_not_called()
    assert all(call.args[0].startswith("SELECT ") for call in cursor.execute.call_args_list)
    assert all(call.args[0].endswith("FOR UPDATE") for call in cursor.execute.call_args_list)
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_mysql_mock_existing_run_does_not_skip_changed_current_values(monkeypatch):
    store, connection, cursor = mysql_with_existing(monkeypatch, changed=True)
    stats = store.load(orders_frame(), issues_frame(), run_record())
    assert stats["written"] == 1 and stats["unchanged"] == 1 and stats["run_written"] == 0
    cursor.executemany.assert_called_once()
    sql, rows = cursor.executemany.call_args.args
    assert "AS incoming ON DUPLICATE KEY UPDATE" in sql
    assert len(rows) == 1 and rows[0][0] == "O1" and rows[0][7] == "paid"
    connection.commit.assert_called_once()


def test_mysql_mock_lookup_batches_500_ids_and_parameterizes_values(monkeypatch):
    store, connection, cursor = mysql_with_existing(monkeypatch)
    cursor.execute.side_effect = None
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    prototype = orders_frame().iloc[0].to_dict()
    orders = pd.DataFrame([dict(prototype, order_id=f"ORDER_'{i}") for i in range(501)])
    stats = store.load(orders, issues_frame().iloc[:0], run_record("b"))
    lookups = [call for call in cursor.execute.call_args_list if "FROM orders WHERE order_id IN" in call.args[0]]
    assert [len(call.args[1]) for call in lookups] == [500, 1]
    assert all("ORDER_'" not in call.args[0] and call.args[0].endswith("FOR UPDATE") for call in lookups)
    assert stats["written"] == 501
    connection.commit.assert_called_once()


def test_mysql_mock_incremental_constraint_error_rolls_back_without_leaking_driver_error(monkeypatch):
    store, connection, cursor = mysql_with_existing(monkeypatch)
    bad = orders_frame()
    bad.loc[0, "quantity"] = 0
    cursor.executemany.side_effect = RuntimeError("driver secret contents")
    with pytest.raises(DatabaseError, match="回滚") as error:
        store.load(bad, issues_frame(), run_record())
    assert "secret" not in str(error.value)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_mysql_mock_connection_lost_before_transaction_fails_cleanly(monkeypatch):
    store, connection, _ = mysql_with_existing(monkeypatch)
    connection.begin.side_effect = RuntimeError("connection lost secret")
    connection.rollback.side_effect = RuntimeError("rollback lost secret")
    with pytest.raises(DatabaseError) as error:
        store.load(orders_frame(), issues_frame(), run_record())
    assert "secret" not in str(error.value)
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def verification_env(monkeypatch, database="orders_test"):
    for key, value in dict(MYSQL_INTEGRATION="1", MYSQL_HOST="127.0.0.1", MYSQL_USER="test",
                           MYSQL_PASSWORD="not-printed", MYSQL_DATABASE=database).items():
        monkeypatch.setenv(key, value)


def test_mysql_live_verifier_without_opt_in_is_pending_without_connecting(monkeypatch, capsys):
    from scripts import verify_mysql
    monkeypatch.delenv("MYSQL_INTEGRATION", raising=False)
    constructor = MagicMock()
    monkeypatch.setattr(verify_mysql, "MySQLStore", constructor)
    assert verify_mysql.main() == 2
    assert "待实机验收" in capsys.readouterr().out
    constructor.assert_not_called()


@pytest.mark.parametrize("database", ["orders", "latest", "test_orders", "orders_test_backup", "orders_test;DROP"])
def test_mysql_live_verifier_rejects_unsafe_database_names_before_connecting(monkeypatch, database):
    from scripts import verify_mysql
    verification_env(monkeypatch, database)
    constructor = MagicMock()
    monkeypatch.setattr(verify_mysql, "MySQLStore", constructor)
    with pytest.raises(ConfigurationError, match="_test"):
        verify_mysql._config_for_verification()
    constructor.assert_not_called()


def test_mysql_live_verifier_does_not_write_or_cleanup_nonempty_database(monkeypatch, capsys):
    from scripts import verify_mysql
    verification_env(monkeypatch)
    store = MagicMock()
    execute, cleanup = MagicMock(), MagicMock()
    monkeypatch.setattr(verify_mysql, "MySQLStore", lambda _: store)
    monkeypatch.setattr(verify_mysql, "_counts", lambda _: (1, 0, 0))
    monkeypatch.setattr(verify_mysql, "_execute_checks", execute)
    monkeypatch.setattr(verify_mysql, "_cleanup", cleanup)
    assert verify_mysql.main() == 1
    assert "必须全空" in capsys.readouterr().out
    execute.assert_not_called()
    cleanup.assert_not_called()
    store.load.assert_not_called()


def test_mysql_live_verifier_cleanup_uses_only_exact_owned_order_and_run_keys():
    from scripts import verify_mysql
    store, connection, cursor = MagicMock(), MagicMock(), MagicMock()
    store._connect.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    verify_mysql._cleanup(store, ["OWN_'ORDER"], ["a" * 64, "b" * 64])
    calls = cursor.execute.call_args_list
    assert len(calls) == 3 and all("WHERE " in call.args[0] for call in calls)
    assert "order_id IN (%s) AND last_run_id IN (%s,%s)" in calls[0].args[0]
    assert calls[0].args[1] == ("OWN_'ORDER", "a" * 64, "b" * 64)
    assert "OWN_'ORDER" not in calls[0].args[0]
    connection.commit.assert_called_once()


@pytest.mark.parametrize("error_code", [1064, 2006, 2013])
def test_mysql_live_verifier_does_not_count_syntax_or_disconnect_as_constraint_pass(error_code):
    from scripts import verify_mysql
    store, connection, cursor = MagicMock(), MagicMock(), MagicMock()
    store._connect.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    driver_error = type("OperationalError", (Exception,), {})
    cursor.execute.side_effect = driver_error(error_code, "driver details hidden")
    with pytest.raises(AssertionError, match="未由数据库约束"):
        verify_mysql._reject_sql(store, "UPDATE orders SET quantity=%s", (0,), "CHECK")
    connection.rollback.assert_called_once()
