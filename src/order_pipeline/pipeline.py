"""ETL 编排与可复现文件产物；数据库事务独立于文件发布。"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from typing import Any

import pandas as pd

from .transforms import keep_paid_orders, summarize_city_amount
from . import __version__
from .errors import ConfigurationError, OutputError
from .quality import QualityResult, RULE_VERSION, inspect_csv
from .storage import MySQLConfig, MySQLStore, SQLiteStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data/processed/orders.sqlite3"
LOGGER = logging.getLogger(__name__)
ARTIFACTS = {
    "orders_clean.csv", "quality_issues.csv", "quality_metrics.json", "report.md", "manifest.json",
    "city_sales.csv", "product_sales.csv", "batch_city_sales.csv", "batch_product_sales.csv",
    "database_summary.json",
}


@dataclass(frozen=True)
class RunResult:
    output_dir: Path
    metrics: dict[str, int]
    run_id: str
    timings: dict[str, float] = field(default_factory=dict)
    load_statistics: dict[str, int] = field(default_factory=dict)


def money(cents: int) -> str:
    return format(Decimal(int(cents)) / 100, ".2f")


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _validate_output(output: Path, source: Path | None = None, database: Path | None = None, mode: str | None = None) -> None:
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise OutputError("输出必须是独立目录，不能是文件或符号链接。")
    if source is not None and _inside(source, output):
        raise OutputError("输出目录不能包含原始输入文件；原始 CSV 必须保持不变。")
    if database is not None and (_inside(database, output) or (source is not None and database.resolve() == source.resolve())):
        raise OutputError("数据库路径不能覆盖输入，也不能放在会被替换的报告目录内。")
    if output.exists() and any(output.iterdir()):
        manifest = output / "manifest.json"
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise OutputError("非空输出目录不是 V1 管理的产物目录；请换一个新目录。") from error
        if not isinstance(metadata, dict) or metadata.get("application") != "order-pipeline-v1" or any(
            p.name not in ARTIFACTS or not p.is_file() or p.is_symlink() for p in output.iterdir()
        ):
            raise OutputError("输出目录中有非 V1 产物；拒绝覆盖个人文件。")
        if mode is not None and metadata.get("mode") != mode:
            raise OutputError("输出目录属于另一种运行模式，请使用独立目录保留原产物。")


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _spreadsheet_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    # Inspect leading whitespace/control/BOM without stripping the exported value.
    start = 0
    while start < len(value) and (value[start].isspace() or ord(value[start]) < 32 or value[start] == "\ufeff"):
        start += 1
    probe = value[start:]
    if value[0] in "\t\r\n" or probe.startswith(tuple("=+-@＝＋－＠")):
        # Visible text marker survives ordinary CSV save/reopen; quotes alone are not
        # formula protection. Source, in-memory frames and database remain untouched.
        return "text:" + value
    return value


def _csv(path: Path, frame: pd.DataFrame) -> None:
    frame.map(_spreadsheet_text).to_csv(
        path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d %H:%M:%S",
        lineterminator="\n", quoting=csv.QUOTE_ALL,
    )


def _sales_rows(orders: pd.DataFrame, dimension: str) -> list[dict]:
    paid = keep_paid_orders(orders)
    grouped = paid.groupby(dimension, sort=True).agg(
        order_count=("order_id", "size"), quantity=("quantity", "sum"),
        sales_amount_cents=("order_amount_cents", "sum"),
    ).reset_index()
    if dimension == "city":
        # 用转换模块的 Decimal 城市汇总独立核对整数分结果。
        prior = summarize_city_amount(paid)
        expected = {row.city: int(row.order_amount * 100) for row in prior.itertuples(index=False)}
        assert expected == dict(zip(grouped["city"], grouped["sales_amount_cents"]))
    grouped = grouped.sort_values(["sales_amount_cents", dimension], ascending=[False, True])
    return grouped.to_dict(orient="records")


def _sales_csv(path: Path, rows: list[dict], dimension: str) -> None:
    display = [{**row, "sales_amount": money(row["sales_amount_cents"])} for row in rows]
    _csv(path, pd.DataFrame(display, columns=[dimension, "order_count", "quantity", "sales_amount_cents", "sales_amount"]))


def _write_quality(directory: Path, result: QualityResult) -> None:
    columns = ["order_id", "user_id", "product_name", "quantity", "unit_price", "order_time", "city", "status", "order_amount", "source_row"]
    _csv(directory / "orders_clean.csv", result.orders[columns])
    _csv(directory / "quality_issues.csv", result.issues)
    _json(directory / "quality_metrics.json", result.metrics)
    _sales_csv(directory / "batch_city_sales.csv", _sales_rows(result.orders, "city"), "city")
    _sales_csv(directory / "batch_product_sales.csv", _sales_rows(result.orders, "product_name"), "product_name")


def _write_analysis(directory: Path, analysis: dict[str, list[dict]], backend: str) -> None:
    _json(directory / "database_summary.json", {
        **analysis["summary"][0], "backend": backend, "scope": "all_committed_orders",
        "sales_amount": money(analysis["summary"][0]["sales_amount_cents"]),
    })
    _sales_csv(directory / "city_sales.csv", analysis["cities"], "city")
    _sales_csv(directory / "product_sales.csv", analysis["products"], "product_name")


def _table(frame: pd.DataFrame) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")
    lines = ["| " + " | ".join(map(cell, frame.columns)) + " |", "| " + " | ".join("---" for _ in frame.columns) + " |"]
    lines.extend("| " + " | ".join(map(cell, row)) + " |" for row in frame.itertuples(index=False, name=None))
    return "\n".join(lines)


def _report(directory: Path, metadata: dict) -> None:
    lines = ["# 电商订单数据质量与分析报告", "", f"规则版本：{RULE_VERSION}；运行模式：{metadata['mode']}。", ""]
    if "source_hash" in metadata:
        lines += [f"原始文件 SHA-256：`{metadata['source_hash']}`。", ""]
    metrics_path = directory / "quality_metrics.json"
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        labels = {"raw_rows": "原始行数", "accepted_orders": "接收订单", "quarantined_rows": "隔离行数",
                  "duplicate_rows": "多余重复副本", "anomalous_rows": "含异常的原始行", "clean_rows": "无异常原始行",
                  "warning_rows": "含警告原始行", "paid_orders": "本批 paid 订单", "missing_city_rows": "缺失城市", "invalid_time_rows": "无效时间"}
        lines += ["## 本批次质量", "", _table(pd.DataFrame([(label, metrics[key]) for key, label in labels.items()], columns=["指标", "数量"])), "",
                  f"数量核对：{metrics['raw_rows']} = {metrics['accepted_orders']}（接收） + {metrics['quarantined_rows']}（隔离） + {metrics['duplicate_rows']}（重复副本）。", "",
                  f"本批 paid 销售额：**{money(metrics['sales_amount_cents'])} 元**。", "",
                  "接收订单可能仍有城市或时间警告；异常行数不等于隔离行数。城市缺失补未知；无效时间留空且保留订单；仅 paid 计销售额。", ""]
        issues = pd.read_csv(directory / "quality_issues.csv", keep_default_na=False)
        if not issues.empty:
            counts = issues.groupby(["rule", "severity", "action"]).size().reset_index(name="触发次数")
            lines += [_table(counts), "", "同一行可触发多条规则；完整原始字段与原因见 quality_issues.csv。", ""]
    database_path = directory / "database_summary.json"
    if database_path.exists():
        summary = json.loads(database_path.read_text(encoding="utf-8"))
        lines += ["## 数据库 SQL 分析", "", f"后端：{summary['backend']}；范围：全库当前有效订单（不一定等于本批次）。", "",
                  f"总订单 {summary['total_orders']}，paid 订单 {summary['paid_orders']}，paid 销售额 **{summary['sales_amount']} 元**。", ""]
        for filename, title in (("city_sales.csv", "城市排行"), ("product_sales.csv", "商品排行")):
            frame = pd.read_csv(directory / filename, dtype=str, keep_default_na=False)
            lines += [f"### {title}（前 10 项）", "", _table(frame.head(10)), ""]
        if summary["backend"] == "sqlite":
            lines += ["SQLite 是本地可运行验证后端；这份报告不构成 MySQL 实机验收。", ""]
    else:
        lines += ["本次只检查 CSV，未写入数据库，也未执行数据库分析。", ""]
    lines += ["## 处理边界", "", "同批订单编号冲突全部隔离；跨批次同编号以最新成功导入覆盖。重复运行使用唯一键，不累加订单。", "",
              "批次 CSV 与数据库全量 SQL 指标分别导出。原始 CSV 不改写；金额按整数分存储，日期缺失不猜测修复。", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _manifest(directory: Path, metadata: dict) -> None:
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.iterdir()) if path.name != "manifest.json"}
    _json(directory / "manifest.json", {**metadata, "application": "order-pipeline-v1", "version": __version__, "files": files})


def _publish(stage: Path, destination: Path) -> None:
    backup = None
    try:
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=destination.name + "-backup-", dir=destination.parent))
            backup.rmdir()  # 仅移除刚创建的空目录，给原产物留恢复位置。
            os.replace(destination, backup)
        os.replace(stage, destination)
    except OSError:
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup)  # 已验证仅包含本工具管理的产物。


def _store(backend: str, database_path: Path | None):
    if backend == "sqlite":
        return SQLiteStore(database_path or DEFAULT_DATABASE)
    if backend == "mysql":
        return MySQLStore(MySQLConfig.from_env())
    raise ConfigurationError("backend 仅支持 sqlite 或 mysql。")


def _stage(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=output.name + "-stage-", dir=output.parent))


def run_pipeline(input_path: Path, output_dir: Path, backend: str = "sqlite", database_path: Path | None = None,
                 encoding: str = "utf-8-sig") -> RunResult:
    started = perf_counter()
    timings: dict[str, float] = {}
    input_path, output_dir = Path(input_path), Path(output_dir)
    database = Path(database_path) if database_path is not None else DEFAULT_DATABASE
    _validate_output(output_dir, input_path, database, mode="run")
    result = inspect_csv(input_path, encoding, timings=timings)
    store = _store(backend, database_path)
    stage = None
    try:
        stage = _stage(output_dir)
        phase = perf_counter()
        _write_quality(stage, result)
        timings["report_seconds"] = perf_counter() - phase
        phase = perf_counter()
        store.initialize()
        load_statistics = store.load(result.orders, result.issues, dict(run_id=result.run_id, source_hash=result.source_hash,
                                                                      source_name=result.source_name, metrics=result.metrics)) or {}
        timings["database_load_seconds"] = perf_counter() - phase
        LOGGER.info("数据库事务已提交：%s；开始 SQL 分析", backend)
        phase = perf_counter()
        analysis = store.analyze()
        timings["sql_seconds"] = perf_counter() - phase
        phase = perf_counter()
        committed_issues = result.issues.copy(deep=True)
        committed_issues["database_status"] = committed_issues["record_outcome"].map({
            "accepted": "committed_or_unchanged", "quarantined": "not_imported", "duplicate_removed": "duplicate_not_imported",
        })
        _csv(stage / "quality_issues.csv", committed_issues)
        _write_analysis(stage, analysis, backend)
        metadata = dict(mode="run", source_hash=result.source_hash, run_id=result.run_id, encoding=encoding, backend=backend)
        _report(stage, metadata)
        _manifest(stage, metadata)
        _publish(stage, output_dir)
        timings["report_seconds"] += perf_counter() - phase
        LOGGER.info("产物发布成功：%s", output_dir)
    except OSError as error:
        raise OutputError("文件产物发布失败；已提交的数据库事务不会撤销，修正路径或权限后可安全重跑。") from error
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
    timings["total_seconds"] = perf_counter() - started
    return RunResult(output_dir, result.metrics, result.run_id, timings, load_statistics)


def check_pipeline(input_path: Path, output_dir: Path, encoding: str = "utf-8-sig") -> RunResult:
    output_dir = Path(output_dir)
    _validate_output(output_dir, Path(input_path), mode="check")
    result = inspect_csv(input_path, encoding)
    stage = None
    try:
        stage = _stage(output_dir)
        _write_quality(stage, result)
        metadata = dict(mode="check", source_hash=result.source_hash, run_id=result.run_id, encoding=encoding)
        _report(stage, metadata)
        _manifest(stage, metadata)
        _publish(stage, output_dir)
    except OSError as error:
        raise OutputError("数据检查产物无法写入，请检查输出目录。") from error
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
    return RunResult(output_dir, result.metrics, result.run_id)


def analyze_database(output_dir: Path, backend: str = "sqlite", database_path: Path | None = None) -> Path:
    output_dir = Path(output_dir)
    database = Path(database_path) if database_path is not None else DEFAULT_DATABASE
    _validate_output(output_dir, database=database, mode="analyze")
    if backend == "sqlite" and not database.is_file():
        raise ConfigurationError("SQLite 数据库不存在，请先执行 run。")
    analysis = _store(backend, database_path).analyze()
    stage = None
    try:
        stage = _stage(output_dir)
        _write_analysis(stage, analysis, backend)
        metadata = dict(mode="analyze", backend=backend)
        _report(stage, metadata)
        _manifest(stage, metadata)
        _publish(stage, output_dir)
    except OSError as error:
        raise OutputError("SQL 分析结果无法写入，请检查输出目录。") from error
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
    return output_dir


def regenerate_report(output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    _validate_output(output_dir)
    stage = None
    try:
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest.get("files"), dict):
            raise OutputError("产物清单格式错误，请重新执行 run 或 analyze。")
        # 拒绝使用被意外修改的分析产物生成貌似可信的报告。
        for name, digest in manifest["files"].items():
            if name == "report.md":
                continue  # 报告是可再生产物，不是报告生成器的输入。
            if name not in ARTIFACTS or hashlib.sha256((output_dir / name).read_bytes()).hexdigest() != digest:
                raise OutputError("产物校验失败，请重新运行 ETL 或分析生成完整结果。")
        stage = _stage(output_dir)
        for path in output_dir.iterdir():
            shutil.copy2(path, stage / path.name)
        _report(stage, manifest)
        _manifest(stage, {k: v for k, v in manifest.items() if k != "files"})
        _publish(stage, output_dir)
    except (OSError, ValueError, KeyError) as error:
        raise OutputError("报告所需产物缺失或格式错误，请先执行 run 或 analyze。") from error
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
    return output_dir / "report.md"
