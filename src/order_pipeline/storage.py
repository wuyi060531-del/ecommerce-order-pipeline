"""订单与质量审计存储；SQL 统计范围为全库当前明细。

单批次以事务写入，order_id 冲突由最新成功导入覆盖。质量层负责
隔离同批次冲突订单，本模块不在存储时猜测冲突的业务含义。
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .errors import ConfigurationError, DatabaseError

LOGGER = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
ORDER_COLUMNS = (
    "order_id", "user_id", "product_name", "quantity", "unit_price_cents",
    "order_time", "city", "status", "order_amount_cents", "source_row", "last_run_id",
)
ISSUE_COLUMNS = (
    "source_row", "order_id", "rule", "severity", "action", "message", "raw_record",
)
BUSINESS_COLUMNS = ORDER_COLUMNS[:9]
LOOKUP_CHUNK_SIZE = 500

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL,
    source_name TEXT NOT NULL, metrics_json TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    order_time TEXT, city TEXT NOT NULL, status TEXT NOT NULL,
    order_amount_cents INTEGER NOT NULL CHECK(order_amount_cents >= 0),
    source_row INTEGER NOT NULL,
    last_run_id TEXT NOT NULL REFERENCES etl_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_status_city ON orders(status, city);
CREATE INDEX IF NOT EXISTS idx_orders_status_product ON orders(status, product_name);
CREATE TABLE IF NOT EXISTS dq_issues (
    run_id TEXT NOT NULL REFERENCES etl_runs(run_id), issue_number INTEGER NOT NULL,
    source_row INTEGER NOT NULL, order_id TEXT, rule TEXT NOT NULL,
    severity TEXT NOT NULL, action TEXT NOT NULL, message TEXT NOT NULL,
    raw_record TEXT NOT NULL, PRIMARY KEY(run_id, issue_number)
);
"""


def _sql_statements(path: Path) -> List[str]:
    """仅解析仓库自己的简单 SQL 文件，不处理用户提供的 SQL。"""
    sql = re.sub(r"(?m)^\s*--.*$", "", path.read_text(encoding="utf-8"))
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def _analysis_queries() -> Dict[str, str]:
    text = (SQL_DIR / "analysis_mysql.sql").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^-- name: (\w+)\s*$", text)
    return {parts[i]: parts[i + 1].strip().rstrip(";") for i in range(1, len(parts), 2)}


def _orders_rows(orders: pd.DataFrame, run_id: str) -> List[Tuple[Any, ...]]:
    rows = []
    for record in orders.to_dict(orient="records"):
        record["last_run_id"] = run_id
        order_time = record["order_time"]
        record["order_time"] = (
            None if pd.isna(order_time)
            else pd.Timestamp(order_time).isoformat(sep=" ", timespec="microseconds")
        )
        for column in ("quantity", "unit_price_cents", "order_amount_cents", "source_row"):
            record[column] = int(record[column])
        rows.append(tuple(record[column] for column in ORDER_COLUMNS))
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("同批次重复 order_id 必须先在质量层处理")
    return rows


def _issue_rows(issues: pd.DataFrame, run_id: str) -> List[Tuple[Any, ...]]:
    rows = []
    for number, record in enumerate(issues.to_dict(orient="records"), start=1):
        record["source_row"] = int(record["source_row"])
        if pd.isna(record["order_id"]):
            record["order_id"] = None
        # JSON 校验同时覆盖 SQLite，避免两种后端的审计行为不同。
        _json_signature(record["raw_record"])
        rows.append((run_id, number) + tuple(record[column] for column in ISSUE_COLUMNS))
    return rows


def _json_signature(value: Any) -> str:
    """MySQL JSON 会调整空白与键序；比较 JSON 值，保留字段中的原始文本。"""
    decoded = json.loads(value) if isinstance(value, str) else value
    return json.dumps(decoded, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _business_signature(row: Tuple[Any, ...]) -> Tuple[Any, ...]:
    values = list(row[:len(BUSINESS_COLUMNS)])
    values[5] = None if values[5] is None else pd.Timestamp(values[5]).isoformat()
    return tuple(values)


def _existing_orders(cursor: Any, rows: List[Tuple[Any, ...]], mysql: bool) -> Dict[str, Tuple[Any, ...]]:
    existing = {}
    marker = "%s" if mysql else "?"
    for start in range(0, len(rows), LOOKUP_CHUNK_SIZE):
        ids = [row[0] for row in rows[start:start + LOOKUP_CHUNK_SIZE]]
        placeholders = ",".join(marker for _ in ids)
        cursor.execute(
            f"SELECT {','.join(BUSINESS_COLUMNS)} FROM orders WHERE order_id IN ({placeholders})"
            + (" FOR UPDATE" if mysql else ""), tuple(ids),
        )
        for found in cursor.fetchall():
            record = dict(found)
            existing[record["order_id"]] = tuple(record[column] for column in BUSINESS_COLUMNS)
    return existing


def _write_batch(cursor: Any, orders: pd.DataFrame, issues: pd.DataFrame,
                 run: Dict[str, Any], mysql: bool) -> Dict[str, int]:
    """仅在调用者事务内运行；跳过相同值，不按已见 run_id 跳过订单。"""
    run_id = run["run_id"]
    order_rows, issue_rows = _orders_rows(orders, run_id), _issue_rows(issues, run_id)
    run_values = _run_values(run)
    marker = "%s" if mysql else "?"
    cursor.execute(
        f"SELECT source_hash,source_name,metrics_json FROM etl_runs WHERE run_id={marker}"
        + (" FOR UPDATE" if mysql else ""), (run_id,),
    )
    found_run = cursor.fetchone()
    run_changed = found_run is None or (
        found_run["source_hash"], found_run["source_name"], _json_signature(found_run["metrics_json"])
    ) != (run_values[1], run_values[2], _json_signature(run_values[3]))
    if run_changed:
        if mysql:
            statement = (
                "INSERT INTO etl_runs (run_id,source_hash,source_name,metrics_json) "
                "VALUES (%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE "
                "source_hash=incoming.source_hash,source_name=incoming.source_name,"
                "metrics_json=incoming.metrics_json"
            )
        else:
            statement = (
                "INSERT INTO etl_runs (run_id,source_hash,source_name,metrics_json) "
                "VALUES (?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                "source_hash=excluded.source_hash,source_name=excluded.source_name,"
                "metrics_json=excluded.metrics_json"
            )
        cursor.execute(statement, run_values)
    existing = _existing_orders(cursor, order_rows, mysql)
    changed = [row for row in order_rows if row[0] not in existing
               or _business_signature(row) != _business_signature(existing[row[0]])]
    if changed:
        placeholders = ",".join(marker for _ in ORDER_COLUMNS)
        alias = "incoming" if mysql else "excluded"
        updates = ",".join(f"{col}={alias}.{col}" for col in ORDER_COLUMNS[1:])
        statement = f"INSERT INTO orders ({','.join(ORDER_COLUMNS)}) VALUES ({placeholders}) "
        statement += ("AS incoming ON DUPLICATE KEY UPDATE " if mysql
                      else "ON CONFLICT(order_id) DO UPDATE SET ") + updates
        cursor.executemany(statement, changed)
    issue_names = ("run_id", "issue_number") + ISSUE_COLUMNS
    cursor.execute(
        f"SELECT {','.join(issue_names)} FROM dq_issues WHERE run_id={marker} ORDER BY issue_number"
        + (" FOR UPDATE" if mysql else ""), (run_id,),
    )
    stored_issues = [tuple(dict(row)[column] for column in issue_names) for row in cursor.fetchall()]

    def issue_signature(row: Tuple[Any, ...]) -> Tuple[Any, ...]:
        return row[:-1] + (_json_signature(row[-1]),)

    issues_changed = list(map(issue_signature, stored_issues)) != list(map(issue_signature, issue_rows))
    if issues_changed:
        cursor.execute(f"DELETE FROM dq_issues WHERE run_id={marker}", (run_id,))
        if issue_rows:
            cursor.executemany(
                f"INSERT INTO dq_issues ({','.join(issue_names)}) VALUES ("
                + ",".join(marker for _ in issue_names) + ")", issue_rows,
            )
    return dict(incoming=len(order_rows), written=len(changed), unchanged=len(order_rows) - len(changed),
                run_written=int(run_changed), issues_replaced=int(issues_changed),
                issues_written=len(issue_rows) if issues_changed else 0)


def _run_values(run: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        run["run_id"], run["source_hash"], run["source_name"],
        json.dumps(run["metrics"], ensure_ascii=False, sort_keys=True, allow_nan=False),
    )


def _analysis_result(cursor: Any) -> Dict[str, List[Dict[str, Any]]]:
    result = {}
    for name, query in _analysis_queries().items():
        cursor.execute(query)
        rows = [dict(row) for row in cursor.fetchall()]
        # MySQL SUM(BIGINT) 返回 Decimal；这里都是整数计数或分。
        result[name] = [
            {key: int(value) if isinstance(value, Decimal) else value for key, value in row.items()}
            for row in rows
        ]
    return result


def _mysql_cleanup(connection: Any, rollback: bool = False) -> None:
    """断线时 rollback/close 也可能失败，仍避免暴露驱动的敏感上下文。"""
    try:
        if rollback:
            connection.rollback()
        else:
            connection.close()
    except Exception:
        LOGGER.warning("MySQL %s未获确认，请检查连接状态", "回滚" if rollback else "连接关闭")


class SQLiteStore:
    """默认本地后端，不依赖运行中的数据库服务。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except sqlite3.Error:
            raise DatabaseError("无法打开 SQLite 数据库，请检查文件路径及权限。") from None

    def initialize(self) -> None:
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            connection.executescript(SQLITE_SCHEMA)
            connection.commit()
        except (OSError, sqlite3.Error):
            raise DatabaseError("SQLite 建表失败，请检查路径权限及现有表结构。") from None
        finally:
            if connection is not None:
                connection.close()

    def load(self, orders: pd.DataFrame, issues: pd.DataFrame, run: Dict[str, Any]) -> Dict[str, int]:
        connection = self._connect()
        try:
            run_id = run["run_id"]
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                stats = _write_batch(connection.cursor(), orders, issues, run, mysql=False)
                # SQLite 的 SUM(INTEGER) 使用 signed 64-bit；在提交前用
                # Python 整数精确预检，避免成功入库后才发现 SQL 无法统计。
                paid_total = sum(
                    int(row[0]) for row in connection.execute(
                        "SELECT order_amount_cents FROM orders WHERE status='paid'"
                    )
                )
                if paid_total > 2**63 - 1:
                    raise DatabaseError(
                        "SQLite 全库 paid 销售额超过整数分上限（2^63-1），当前批次已回滚；"
                        "请使用 MySQL 后端处理该规模的金额。"
                    )
        except (sqlite3.Error, KeyError, TypeError, ValueError, OverflowError):
            raise DatabaseError("SQLite 写入失败，当前批次已回滚；请检查输入与表结构。") from None
        finally:
            connection.close()
        LOGGER.info("SQLite 已提交批次 %s：接收%d，写入%d，未变化%d", run_id,
                    stats["incoming"], stats["written"], stats["unchanged"])
        return stats

    def analyze(self) -> Dict[str, List[Dict[str, Any]]]:
        connection = self._connect()
        try:
            return _analysis_result(connection.cursor())
        except (sqlite3.Error, OSError):
            raise DatabaseError("SQLite 分析失败，请先执行成功的 ETL 入库。") from None
        finally:
            connection.close()


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    user: str
    password: str = field(repr=False)
    database: str
    port: int = 3306
    connect_timeout: int = 10
    ssl_ca: Optional[str] = None

    @classmethod
    def from_env(cls) -> "MySQLConfig":
        required = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
        missing = [key for key in required if key not in os.environ or (key != "MYSQL_PASSWORD" and not os.environ[key].strip())]
        if missing:
            raise ConfigurationError("缺少 MySQL 环境变量：" + ", ".join(missing))
        try:
            port = int(os.environ.get("MYSQL_PORT", "3306"))
            timeout = int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "10"))
        except ValueError:
            raise ConfigurationError("MYSQL_PORT 和 MYSQL_CONNECT_TIMEOUT 必须为整数。") from None
        if not 1 <= port <= 65535 or not 1 <= timeout <= 60:
            raise ConfigurationError("MYSQL_PORT 必须为 1～65535；连接超时必须为 1～60 秒。")
        return cls(
            host=os.environ["MYSQL_HOST"], user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"], database=os.environ["MYSQL_DATABASE"],
            port=port, connect_timeout=timeout, ssl_ca=os.environ.get("MYSQL_SSL_CA") or None,
        )


class MySQLStore:
    """可选 MySQL 8.0.19+ 后端；DDL 与分析 SQL 来自 sql/ 文件。"""

    def __init__(self, config: MySQLConfig):
        self.config = config

    def _connect(self) -> Any:
        try:
            pymysql = importlib.import_module("pymysql")
        except ImportError:
            raise ConfigurationError(
                "MySQL 后端需要可选依赖：.venv/bin/python -m pip install -r requirements-mysql.txt"
            ) from None
        kwargs = {
            "host": self.config.host, "port": self.config.port, "user": self.config.user,
            "password": self.config.password, "database": self.config.database,
            "charset": "utf8mb4", "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": self.config.connect_timeout,
            "read_timeout": 30, "write_timeout": 30, "autocommit": False,
            "sql_mode": "STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION",
        }
        if self.config.ssl_ca:
            kwargs.update(ssl_ca=self.config.ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
        try:
            return pymysql.connect(**kwargs)
        except Exception:
            # 驱动错误可能包含连接字符串；不向用户或日志转发原始异常。
            raise DatabaseError("MySQL 连接失败，请检查服务、专用数据库、权限及环境变量。") from None

    def initialize(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                for statement in _sql_statements(SQL_DIR / "schema_mysql.sql"):
                    cursor.execute(statement)
            connection.commit()
        except Exception:
            _mysql_cleanup(connection, rollback=True)
            raise DatabaseError("MySQL 建表失败，请检查版本（8.0.19+）、权限及现有表结构。") from None
        finally:
            _mysql_cleanup(connection)

    def load(self, orders: pd.DataFrame, issues: pd.DataFrame, run: Dict[str, Any]) -> Dict[str, int]:
        connection = self._connect()
        try:
            run_id = run["run_id"]
            connection.begin()
            with connection.cursor() as cursor:
                stats = _write_batch(cursor, orders, issues, run, mysql=True)
            connection.commit()
        except Exception:
            _mysql_cleanup(connection, rollback=True)
            raise DatabaseError("MySQL 写入未确认成功，已尝试回滚；请检查连接、输入与表结构后重跑。") from None
        finally:
            _mysql_cleanup(connection)
        LOGGER.info("MySQL 已提交批次 %s：接收%d，写入%d，未变化%d", run_id,
                    stats["incoming"], stats["written"], stats["unchanged"])
        return stats

    def analyze(self) -> Dict[str, List[Dict[str, Any]]]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                return _analysis_result(cursor)
        except Exception:
            raise DatabaseError("MySQL 分析失败，请先执行成功的 ETL 入库。") from None
        finally:
            _mysql_cleanup(connection)
