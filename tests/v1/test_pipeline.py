"""实际执行 CSV→质量检测→SQLite→SQL→报告，并检查重跑与输入保护。"""

import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from src.order_pipeline.errors import PipelineError
from src.order_pipeline.pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/orders.csv"
COLUMNS = [
    "order_id", "user_id", "product_name", "quantity", "unit_price",
    "order_time", "city", "status",
]
OUTPUT_FILES = {
    "orders_clean.csv", "quality_issues.csv", "quality_metrics.json", "report.md",
    "manifest.json", "city_sales.csv", "product_sales.csv", "batch_city_sales.csv",
    "batch_product_sales.csv", "database_summary.json",
}


def make_row(**changes):
    row = dict(zip(COLUMNS, [
        "T001", "U001", "练习书", "2", "12.50",
        "2026-10-01 08:30:00", "杭州", "paid",
    ]))
    row.update(changes)
    return row


def write_orders(tmp_path, rows, name="input.csv"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "src.order_pipeline", *map(str, arguments)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def test_real_sample_pipeline_reconciles_sql_exports_and_quality_report(tmp_path):
    output = tmp_path / "output"
    database = tmp_path / "orders.sqlite3"
    before = RAW.read_bytes()
    result = run_pipeline(RAW, output, backend="sqlite", database_path=database)
    assert RAW.read_bytes() == before
    assert Path(result.output_dir) == output
    assert OUTPUT_FILES <= {path.name for path in output.iterdir()}
    assert result.metrics["raw_rows"] == 30
    assert result.metrics["accepted_orders"] == 26
    assert result.metrics["quarantined_rows"] == 3
    assert result.metrics["duplicate_rows"] == 1
    assert result.metrics["paid_orders"] == 11
    assert result.metrics["sales_amount_cents"] == 6600
    assert json.loads((output / "quality_metrics.json").read_text(encoding="utf-8")) == result.metrics
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 26
        assert connection.execute("SELECT COUNT(*) FROM etl_runs").fetchone()[0] == 1
        assert connection.execute("SELECT SUM(order_amount_cents) FROM orders WHERE status='paid'").fetchone()[0] == 6600
        assert connection.execute("SELECT order_time FROM orders WHERE order_id='SYN011'").fetchone()[0] is None
        assert connection.execute("SELECT COUNT(*) FROM dq_issues").fetchone()[0] >= 7
    clean = pd.read_csv(output / "orders_clean.csv", dtype=str, keep_default_na=False)
    assert len(clean) == 26
    assert clean.order_id.is_unique
    for filename, expected_rows in [("city_sales.csv", 10), ("product_sales.csv", 8),
                                     ("batch_city_sales.csv", 10), ("batch_product_sales.csv", 8)]:
        summary = pd.read_csv(output / filename)
        assert len(summary) == expected_rows, filename
        assert summary.sales_amount_cents.sum() == 6600, filename
        assert summary.sales_amount_cents.is_monotonic_decreasing, filename
    products = pd.read_csv(output / "product_sales.csv").set_index("product_name")
    expected = {"synthetic-product-01": 1000, "synthetic-product-02": 1200,
                "synthetic-product-03": 1400, "synthetic-product-04": 400,
                "synthetic-product-05": 500, "synthetic-product-06": 600,
                "synthetic-product-07": 700, "synthetic-product-08": 800}
    assert products.sales_amount_cents.to_dict() == expected
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "66.00" in report
    assert "30" in report and "26" in report
    assert "paid" in report
    assert "隔离" in report or "异常" in report
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8"))


def test_identical_rerun_is_idempotent_for_database_audit_and_export(tmp_path):
    output = tmp_path / "output"
    database = tmp_path / "orders.sqlite3"
    first = run_pipeline(RAW, output, database_path=database)
    stable_files = ["orders_clean.csv", "quality_issues.csv", "quality_metrics.json",
                    "city_sales.csv", "product_sales.csv"]
    snapshot = {name: (output / name).read_bytes() for name in stable_files}
    with sqlite3.connect(database) as connection:
        counts = [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("orders", "dq_issues", "etl_runs")]
    second = run_pipeline(RAW, output, database_path=database)
    assert second.metrics == first.metrics
    assert {name: (output / name).read_bytes() for name in stable_files} == snapshot
    with sqlite3.connect(database) as connection:
        assert [connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("orders", "dq_issues", "etl_runs")] == counts


def test_later_batch_updates_order_without_duplicates_and_separates_batch_from_database(tmp_path):
    database = tmp_path / "orders.sqlite3"
    first = write_orders(tmp_path, [make_row(), make_row(order_id="T002")], "first.csv")
    second = write_orders(tmp_path, [make_row(quantity="3"),
                                     make_row(order_id="T003", status="completed")], "second.csv")
    run_pipeline(first, tmp_path / "first", database_path=database)
    result = run_pipeline(second, tmp_path / "second", database_path=database)
    assert result.metrics["accepted_orders"] == 2
    assert result.metrics["sales_amount_cents"] == 3750
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3
        assert connection.execute("SELECT quantity FROM orders WHERE order_id='T001'").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM etl_runs").fetchone()[0] == 2
    database_sales = pd.read_csv(tmp_path / "second/product_sales.csv")
    batch_sales = pd.read_csv(tmp_path / "second/batch_product_sales.csv")
    assert database_sales.sales_amount_cents.sum() == 6250
    assert batch_sales.sales_amount_cents.sum() == 3750


def test_header_only_pipeline_generates_empty_reports_and_real_sql_database(tmp_path):
    source = write_orders(tmp_path, [])
    output = tmp_path / "output"
    database = tmp_path / "orders.sqlite3"
    result = run_pipeline(source, output, database_path=database)
    assert all(value == 0 for value in result.metrics.values())
    assert OUTPUT_FILES <= {path.name for path in output.iterdir()}
    assert pd.read_csv(output / "orders_clean.csv").empty
    assert pd.read_csv(output / "product_sales.csv").empty
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM etl_runs").fetchone()[0] == 1


@pytest.mark.parametrize("target", ["input", "input_parent", "database_input", "database_in_output"])
def test_output_and_database_paths_cannot_overwrite_raw_input(tmp_path, target):
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    source = write_orders(source_dir, [make_row()])
    before = source.read_bytes()
    output = tmp_path / "output"
    database = tmp_path / "orders.sqlite3"
    if target == "input":
        output = source
    elif target == "input_parent":
        output = source_dir
    elif target == "database_input":
        database = source
    else:
        database = output / "orders.sqlite3"
    with pytest.raises(PipelineError):
        run_pipeline(source, output, database_path=database)
    assert source.read_bytes() == before


def test_foreign_output_directory_is_preserved_instead_of_replaced(tmp_path):
    source = write_orders(tmp_path, [make_row()])
    output = tmp_path / "output"
    output.mkdir()
    personal = output / "personal-notes.txt"
    personal.write_text("我的未提交资料", encoding="utf-8")
    with pytest.raises(PipelineError):
        run_pipeline(source, output, database_path=tmp_path / "orders.sqlite3")
    assert personal.read_text(encoding="utf-8") == "我的未提交资料"
    assert not (output / "manifest.json").exists()


def test_database_failure_does_not_claim_success_or_modify_source(tmp_path):
    source = write_orders(tmp_path, [make_row()])
    before = source.read_bytes()
    database = tmp_path / "not-a-database"
    database.mkdir()
    output = tmp_path / "output"
    with pytest.raises(PipelineError):
        run_pipeline(source, output, database_path=database)
    assert source.read_bytes() == before
    assert not (output / "manifest.json").exists()


def test_missing_file_does_not_emit_success_artifacts(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(PipelineError):
        run_pipeline(tmp_path / "missing.csv", output, database_path=tmp_path / "orders.sqlite3")
    assert not (output / "manifest.json").exists()
    assert not (tmp_path / "orders.sqlite3").exists()


def test_cli_help_lists_four_user_workflows():
    result = cli("--help")
    assert result.returncode == 0
    for command in ("check", "run", "analyze", "report"):
        assert command in result.stdout


def test_cli_invalid_input_returns_clear_error_without_traceback(tmp_path):
    result = cli("run", "--input", tmp_path / "missing.csv", "--output", tmp_path / "output")
    assert result.returncode == 2
    message = result.stdout + result.stderr
    assert "不存在" in message
    assert "Traceback" not in message
    assert not (tmp_path / "output/manifest.json").exists()


def test_cli_check_returns_nonzero_for_quarantine_and_exports_audit(tmp_path):
    source = write_orders(tmp_path, [make_row(quantity="0")])
    output = tmp_path / "check"
    before = source.read_bytes()
    result = cli("check", "--input", source, "--output", output)
    assert result.returncode == 1, result.stdout + result.stderr
    assert source.read_bytes() == before
    assert (output / "quality_issues.csv").exists()
    metrics = json.loads((output / "quality_metrics.json").read_text(encoding="utf-8"))
    assert metrics["quarantined_rows"] == 1
    assert metrics["accepted_orders"] == 0
    assert "Traceback" not in result.stdout + result.stderr


def test_cli_check_clean_input_succeeds_without_database_access(tmp_path):
    source = write_orders(tmp_path, [make_row()])
    output = tmp_path / "check"
    result = cli("check", "--input", source, "--output", output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "quality_metrics.json").exists()
    assert not (output / "database_summary.json").exists()
    assert "未写入数据库" in (output / "report.md").read_text(encoding="utf-8")


def test_report_refuses_changed_underlying_data_instead_of_claiming_trustworthy_results(tmp_path):
    source = write_orders(tmp_path, [make_row()])
    output = tmp_path / "output"
    run_pipeline(source, output, database_path=tmp_path / "orders.sqlite3")
    old_report = (output / "report.md").read_bytes()
    metrics = output / "quality_metrics.json"
    metrics.write_text('{"raw_rows":999}', encoding="utf-8")
    result = cli("report", "--output", output)
    assert result.returncode == 2
    assert "校验" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert (output / "report.md").read_bytes() == old_report
    assert metrics.read_text(encoding="utf-8") == '{"raw_rows":999}'


@pytest.mark.parametrize("manifest", [[], {"application": "order-pipeline-v1", "mode": "run", "files": []}])
def test_corrupt_manifest_types_return_clear_error_and_preserve_files(tmp_path, manifest):
    output = tmp_path / "output"
    output.mkdir()
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    result = cli("report", "--output", output)
    assert result.returncode == 2
    assert "Traceback" not in result.stdout + result.stderr
    assert path.read_bytes() == before
    assert not (output / "report.md").exists()


@pytest.mark.parametrize("command", ["check", "analyze"])
def test_other_workflow_cannot_erase_an_existing_etl_batch_output(tmp_path, command):
    source = write_orders(tmp_path, [make_row()])
    output = tmp_path / "output"
    database = tmp_path / "orders.sqlite3"
    run_pipeline(source, output, database_path=database)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    result = cli(command, "--input", source, "--output", output, "--database", database)
    assert result.returncode == 2
    assert "Traceback" not in result.stdout + result.stderr
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_cli_run_analyze_and_report_work_from_documented_commands(tmp_path):
    source = write_orders(tmp_path, [make_row()])
    output = tmp_path / "output"
    database = tmp_path / "orders.sqlite3"
    result = cli("run", "--input", source, "--output", output, "--backend", "sqlite", "--database", database)
    assert result.returncode == 0, result.stdout + result.stderr
    assert OUTPUT_FILES <= {path.name for path in output.iterdir()}
    analysis = tmp_path / "analysis"
    result = cli("analyze", "--output", analysis, "--backend", "sqlite", "--database", database)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (analysis / "database_summary.json").exists()
    assert not (analysis / "quality_metrics.json").exists()
    assert OUTPUT_FILES <= {path.name for path in output.iterdir()}
    (output / "report.md").unlink()
    result = cli("report", "--output", output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "25.00" in report
