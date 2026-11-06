"""V1.1 追踪、最小写入及文件发布失败恢复的真实 SQLite 回归。"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from scripts import benchmark
from src.order_pipeline import pipeline
from src.order_pipeline.errors import OutputError
from src.order_pipeline.quality import inspect_csv

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/orders.csv"


def test_lineage_explains_file_row_disposition_and_database_presence(tmp_path):
    inspected = inspect_csv(RAW)
    assert set(inspected.issues.source_file) == {"orders.csv"}
    assert set(inspected.issues.source_hash) == {hashlib.sha256(RAW.read_bytes()).hexdigest()}
    assert set(inspected.issues.run_id) == {inspected.run_id}
    assert set(inspected.issues.database_status) == {"not_executed"}
    output = tmp_path / "report"
    database = tmp_path / "orders.sqlite3"
    pipeline.run_pipeline(RAW, output, database_path=database)
    issues = pd.read_csv(output / "quality_issues.csv")
    with sqlite3.connect(database) as connection:
        for issue in issues.itertuples(index=False):
            if issue.record_outcome == "accepted":
                assert issue.database_status == "committed_or_unchanged"
                assert connection.execute("SELECT COUNT(*) FROM orders WHERE order_id=?", (issue.order_id,)).fetchone()[0] == 1
            elif issue.record_outcome == "quarantined":
                assert issue.database_status == "not_imported"
            else:
                assert issue.record_outcome == "duplicate_removed"
                assert issue.database_status == "duplicate_not_imported"


def test_phase_measurements_do_not_pollute_deterministic_artifacts_and_rerun_writes_zero(tmp_path):
    output = tmp_path / "report"
    database = tmp_path / "orders.sqlite3"
    first = pipeline.run_pipeline(RAW, output, database_path=database)
    snapshot = {p.name: p.read_bytes() for p in output.iterdir()}
    second = pipeline.run_pipeline(RAW, output, database_path=database)
    expected = {"csv_read_seconds", "clean_seconds", "quality_seconds", "database_load_seconds", "sql_seconds", "report_seconds", "total_seconds"}
    assert set(first.timings) == expected
    assert all(seconds >= 0 for seconds in first.timings.values())
    assert first.timings["total_seconds"] >= max(v for k, v in first.timings.items() if k != "total_seconds")
    assert first.load_statistics["written"] == 26
    assert second.load_statistics == dict(incoming=26, written=0, unchanged=26, run_written=0, issues_replaced=0, issues_written=0)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == snapshot


def _single_order(path: Path, quantity: int) -> Path:
    path.write_text("order_id,user_id,product_name,quantity,unit_price,order_time,city,status\n"
                    f"RECOVERY,U1,测试商品,{quantity},0.10,2026-01-01 10:00:00,杭州,paid\n", encoding="utf-8")
    return path


def test_publication_failure_restores_prior_files_and_idempotent_retry_finishes(tmp_path, monkeypatch):
    source = _single_order(tmp_path / "first.csv", 1)
    changed = _single_order(tmp_path / "changed.csv", 3)
    output = tmp_path / "report"
    database = tmp_path / "orders.sqlite3"
    pipeline.run_pipeline(source, output, database_path=database)
    snapshot = {p.name: p.read_bytes() for p in output.iterdir()}
    real_replace = pipeline.os.replace

    def denied_publish(src, dst):
        if Path(src).name.startswith("report-stage-") and Path(dst) == output:
            raise PermissionError("injected unwritable publication destination")
        return real_replace(src, dst)

    with monkeypatch.context() as context:
        context.setattr(pipeline.os, "replace", denied_publish)
        with pytest.raises(OutputError, match="安全重跑"):
            pipeline.run_pipeline(changed, output, database_path=database)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == snapshot
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT quantity FROM orders").fetchone()[0] == 3
    retried = pipeline.run_pipeline(changed, output, database_path=database)
    assert retried.load_statistics["written"] == 0
    assert retried.metrics["sales_amount_cents"] == 30
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM etl_runs").fetchone()[0] == 2


def test_unwritable_export_before_load_does_not_touch_database(tmp_path, monkeypatch):
    def denied(*_args):
        raise PermissionError("injected export permission failure")
    monkeypatch.setattr(pipeline, "_csv", denied)
    database = tmp_path / "orders.sqlite3"
    with pytest.raises(OutputError):
        pipeline.run_pipeline(RAW, tmp_path / "output", database_path=database)
    assert not database.exists()
    assert not (tmp_path / "output/manifest.json").exists()


def test_cli_can_log_outside_read_only_application_directory(tmp_path):
    log_dir = tmp_path / "writable-logs"
    result = subprocess.run(
        [sys.executable, "-m", "src.order_pipeline", "run", "--input", str(tmp_path / "missing.csv"), "--output", str(tmp_path / "report")],
        cwd=ROOT, env={**os.environ, "PIPELINE_LOG_DIR": str(log_dir)}, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "不存在" in (log_dir / "pipeline.log").read_text(encoding="utf-8")


def test_fifty_thousand_requires_prior_measured_memory_not_an_unchecked_large_run(tmp_path, monkeypatch):
    def should_not_spawn(*_args, **_kwargs):
        raise AssertionError("no preflight measurement exists")
    monkeypatch.setattr(benchmark.subprocess, "run", should_not_spawn)
    monkeypatch.setattr(benchmark.platform, "platform", lambda: "unit-test-platform")
    result = benchmark.run_benchmark(tmp_path, "safe", [50000], 1)
    assert result["records"] == []
    assert result["skipped"][0]["rows"] == 50000


def test_benchmark_worker_refuses_unmarked_or_modified_input(tmp_path):
    source = tmp_path / "synthetic.csv"
    source.write_text("not a benchmark input", encoding="utf-8")
    source.with_suffix(".synthetic.json").write_text(json.dumps({"dataset_kind": "synthetic", "source_sha256": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError, match="匹配元数据"):
        benchmark.worker(source, tmp_path / "case")
    assert not (tmp_path / "case").exists()
