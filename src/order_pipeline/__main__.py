"""统一命令行：python -m src.order_pipeline run。"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .errors import PipelineError
from .pipeline import PROJECT_ROOT, analyze_database, check_pipeline, regenerate_report, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="电商订单数据工程 V1.1：检查、ETL、SQL 分析与报告")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "检查 CSV 并导出质量审计"), ("run", "执行完整 ETL 与 SQL 分析"),
                            ("analyze", "对现有数据库做 SQL 分析"), ("report", "从已校验产物重新生成报告")):
        command = commands.add_parser(name, help=help_text, description=help_text)
        default = "check" if name == "check" else "analysis" if name == "analyze" else "v1"
        command.add_argument("--output", type=Path, default=PROJECT_ROOT / f"data/processed/{default}", help="独立产物目录")
        if name in {"check", "run"}:
            command.add_argument("--input", type=Path, default=PROJECT_ROOT / "data/raw/orders.csv", help="8 字段订单 CSV")
            command.add_argument("--encoding", default="utf-8-sig", help="输入编码，默认 UTF-8（支持 BOM）")
        if name in {"run", "analyze"}:
            command.add_argument("--backend", choices=("sqlite", "mysql"), default="sqlite", help="默认 SQLite 本地验证；MySQL 使用环境变量")
            command.add_argument("--database", type=Path, help="仅 SQLite 文件路径，默认 data/processed/orders.sqlite3")
    args = parser.parse_args(argv)
    try:
        log_dir = Path(os.environ.get("PIPELINE_LOG_DIR", str(PROJECT_ROOT / "logs")))
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(), logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")], force=True)
        if args.command in {"run", "analyze"} and args.backend == "mysql" and args.database is not None:
            parser.error("--database 是 SQLite 文件路径；MySQL 库名请设置 MYSQL_DATABASE。")
        if args.command == "run":
            result = run_pipeline(args.input, args.output, args.backend, args.database, args.encoding)
            print(f"ETL 完成：接收 {result.metrics['accepted_orders']}，隔离 {result.metrics['quarantined_rows']}，报告 {result.output_dir / 'report.md'}")
        elif args.command == "check":
            result = check_pipeline(args.input, args.output, args.encoding)
            print(f"数据检查完成：异常原始行 {result.metrics['anomalous_rows']}；审计与报告：{result.output_dir}")
            return 1 if result.metrics["quarantined_rows"] else 0
        elif args.command == "analyze":
            output = analyze_database(args.output, args.backend, args.database)
            print(f"SQL 分析完成：{output / 'report.md'}")
        else:
            print(f"报告已生成：{regenerate_report(args.output)}")
    except PipelineError as error:
        logging.error("%s", error)
        return 2
    except OSError:
        print("文件或日志无法访问，请检查目录权限。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
