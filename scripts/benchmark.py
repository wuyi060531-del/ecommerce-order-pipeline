"""有边界的串行合成数据评测：每档新进程、新 SQLite，不比较不同后端。"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import pstats
import resource
import subprocess
import sys

from scripts.generate_orders import generate_orders

ROOT = Path(__file__).resolve().parents[1]
SIZES = (1000, 10000, 50000)


def peak_memory_mib() -> float | None:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return peak / (1024 * 1024)
    if sys.platform.startswith("linux"):
        return peak / 1024
    return None


def worker(source: Path, directory: Path, profile: bool = False) -> dict:
    from src.order_pipeline.pipeline import run_pipeline

    metadata = json.loads(source.with_suffix(".synthetic.json").read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    if metadata.get("dataset_kind") != "synthetic" or metadata.get("source_sha256") != fingerprint:
        raise ValueError("性能测试只接受带匹配元数据的合成 CSV。")
    directory.mkdir(parents=True, exist_ok=False)
    profiler = cProfile.Profile() if profile else None
    if profiler:
        profiler.enable()
    result = run_pipeline(source, directory / "artifacts", database_path=directory / "orders.sqlite3")
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(directory / "profile.pstats"))
        with (directory / "profile.txt").open("w", encoding="utf-8") as stream:
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(30)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fingerprint
    report = dict(rows=metadata["rows"], seed=metadata["seed"], source_sha256=fingerprint,
                  dataset_kind="synthetic", backend="sqlite", database_state="new_empty_database",
                  timings=result.timings, metrics=result.metrics, load_statistics=result.load_statistics,
                  peak_memory_mib=peak_memory_mib(), profiled=profile,
                  peak_memory_method="resource.RUSAGE_SELF.ru_maxrss; Darwin=bytes, Linux=KiB")
    (directory / "measurement.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_benchmark(output: Path, label: str, sizes: list[int], seed: int, profile: bool = False) -> dict:
    if not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in label) or label in {".", ".."}:
        raise ValueError("label 只能使用字母、数字、点、横线和下划线。")
    if not sizes or any(n not in SIZES for n in sizes) or len(set(sizes)) != len(sizes):
        raise ValueError("规模只支持互不重复的 1000、10000、50000。")
    output = Path(output)
    raw_directory = (ROOT / "data/raw").resolve()
    if output.resolve() == raw_directory or raw_directory in output.resolve().parents:
        raise ValueError("性能产物不能写入原始数据目录。")
    label_dir = output / label
    if label_dir.exists():
        raise ValueError("该评测标签已存在，请换一个标签以保留原测量证据。")
    label_dir.mkdir(parents=True)
    records = []
    skipped = []
    last_peak = None
    for rows in sorted(sizes):
        if rows > 10000 and (last_peak is None or last_peak > 512):
            skipped.append(dict(rows=rows, reason="前档峰值内存未知或超过 512 MiB，保守停止升级规模"))
            print(f"跳过 {rows} 行：先完成 10000 行内存测量，且峰值不能超过 512 MiB。", flush=True)
            continue
        source = output / "synthetic" / f"SYNTHETIC-{seed}-{rows}.csv"
        if not source.exists():
            generate_orders(source, rows, seed)
        meta = json.loads(source.with_suffix(".synthetic.json").read_text(encoding="utf-8"))
        if meta.get("seed") != seed or meta.get("rows") != rows:
            raise ValueError("已有合成数据元数据不匹配，请使用新评测目录。")
        command = [sys.executable, "-m", "scripts.benchmark", "--worker", "--source", str(source),
                   "--case-dir", str(label_dir / str(rows))]
        if profile:
            command.append("--profile")
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            skipped.append(dict(rows=rows, reason="单档超过 120 秒，已停止；更大规模不再运行"))
            break
        if completed.returncode:
            raise ValueError(f"{rows} 行评测未完成：{(completed.stdout + completed.stderr)[-1000:]}")
        measurement = json.loads((label_dir / str(rows) / "measurement.json").read_text(encoding="utf-8"))
        records.append(measurement)
        last_peak = measurement["peak_memory_mib"]
        print(f"{label} {rows} 行：{measurement['timings']['total_seconds']:.3f}s，峰值 {last_peak} MiB", flush=True)
    source_files = ["src/order_pipeline/quality.py", "src/order_pipeline/pipeline.py", "src/order_pipeline/storage.py",
                    "scripts/generate_orders.py", "scripts/benchmark.py", "src/order_pipeline/transforms.py"]
    report = dict(label=label, records=records, skipped=skipped,
                  method="single observation per size; serial fresh subprocess; fresh SQLite; includes exports, excludes interpreter startup",
                  environment=dict(platform=platform.platform(), architecture=platform.machine(), logical_cpus=os.cpu_count(),
                                   python=platform.python_version(), pandas=version("pandas"), numpy=version("numpy"),
                                   memory_limit_context="no OS-enforced memory cap; 512 MiB gate controls scale escalation"),
                  code_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files})
    (label_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, choices=SIZES, default=[1000, 10000])
    parser.add_argument("--label", default="measurement")
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/benchmarks")
    parser.add_argument("--profile", action="store_true", help="另存 cProfile；不要把带 profiler 的时间当普通测量")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            if args.source is None or args.case_dir is None:
                parser.error("内部 worker 缺少 source/case-dir")
            worker(args.source, args.case_dir, args.profile)
        else:
            run_benchmark(args.output, args.label, args.sizes, args.seed, args.profile)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
