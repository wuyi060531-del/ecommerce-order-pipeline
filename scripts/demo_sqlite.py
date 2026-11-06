"""在新建的独立目录演示真实 CLI、重跑、SQL 对账与输入保护。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def hashes(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}


def main() -> None:
    parent = ROOT / ".local/demos"
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sqlite-", dir=parent))
    source = ROOT / "data/raw/orders.csv"
    before = source.read_bytes()
    output, database = work / "run", work / "orders.sqlite3"

    def cli(*arguments: str, expected: int = 0) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.order_pipeline", *arguments],
            cwd=ROOT, timeout=60, check=False,
            env={**os.environ, "PIPELINE_LOG_DIR": str(work / "logs")},
        )
        if result.returncode != expected:
            raise RuntimeError(f"{arguments[0]} 退出 {result.returncode}，预期 {expected}；保留 {work} 供检查")

    run_args = ("run", "--input", str(source), "--output", str(output), "--database", str(database))
    cli(*run_args)
    first = hashes(output)
    cli(*run_args)
    if hashes(output) != first:
        raise RuntimeError("重跑产物发生变化")
    cli("analyze", "--database", str(database), "--output", str(work / "analysis"))
    cli("report", "--output", str(output))
    cli("check", "--input", str(source), "--output", str(work / "check"), expected=1)
    metrics = json.loads((output / "quality_metrics.json").read_text())
    summary = json.loads((output / "database_summary.json").read_text())
    analysis = json.loads((work / "analysis/database_summary.json").read_text())
    with sqlite3.connect(database) as connection:
        counts = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                       for table in ("orders", "etl_runs", "dq_issues"))
        amount = connection.execute("SELECT SUM(order_amount_cents) FROM orders WHERE status='paid'").fetchone()[0]
    if (counts != (26, 1, 7) or amount != 6600 or metrics["accepted_orders"] != 26
            or metrics["quarantined_rows"] != 3 or metrics["duplicate_rows"] != 1
            or metrics["sales_amount_cents"] != amount or summary != analysis
            or summary["sales_amount_cents"] != amount or hashes(output) != first
            or source.read_bytes() != before):
        raise RuntimeError("演示核对失败；请检查保留的产物")
    evidence = dict(backend="sqlite", counts=dict(zip(("orders", "etl_runs", "dq_issues"), counts)),
                    sales_amount_cents=amount, rerun_artifacts_equal=True, input_unchanged=True,
                    check_exit=1, cli_steps=5, artifact_sha256=first)
    (work / "verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"演示通过：30 = 26 + 3 + 1；paid 66.00 元；重跑、SQL、输入保护一致。\n报告：{output / 'report.md'}")


if __name__ == "__main__":
    main()
