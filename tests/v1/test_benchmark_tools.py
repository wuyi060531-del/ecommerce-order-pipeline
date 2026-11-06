"""合成数据的可复现性和输入保护；不在普通测试里进行压力评测。"""
from pathlib import Path

import pandas as pd
import pytest

from scripts.benchmark import run_benchmark
from scripts.generate_orders import generate_orders
from src.order_pipeline.quality import inspect_csv


def test_generator_is_reproducible_marked_and_exercises_real_rules(tmp_path):
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    metadata = generate_orders(first, 100, 7)
    assert generate_orders(second, 100, 7) == metadata
    assert first.read_bytes() == second.read_bytes()
    assert set(pd.read_csv(first).dataset_kind) == {"synthetic"}
    result = inspect_csv(first)
    assert result.metrics["raw_rows"] == 100
    assert result.metrics["accepted_orders"] == 84
    assert result.metrics["quarantined_rows"] == 15
    assert result.metrics["duplicate_rows"] == 1
    assert {"invalid_price", "invalid_quantity", "missing_city", "invalid_time", "conflicting_order_id"} <= set(result.issues.rule)


def test_generator_refuses_overwrite_and_excess_scale(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("personal data", encoding="utf-8")
    with pytest.raises(ValueError):
        generate_orders(path, 1000)
    assert path.read_text() == "personal data"
    with pytest.raises(ValueError):
        generate_orders(tmp_path / "too-big.csv", 50001)


def test_generator_cannot_target_original_input():
    from scripts.generate_orders import ROOT
    raw = ROOT / "data/raw/orders.csv"
    before = raw.read_bytes()
    with pytest.raises(ValueError):
        generate_orders(raw, 1000)
    assert raw.read_bytes() == before


@pytest.mark.parametrize("label,sizes", [("../unsafe", [1000]), ("..", [1000]), ("valid", [100000]), ("valid", [1000, 1000])])
def test_benchmark_rejects_unbounded_or_unsafe_requests(tmp_path: Path, label, sizes):
    with pytest.raises(ValueError):
        run_benchmark(tmp_path, label, sizes, 1)
    assert not list(tmp_path.iterdir())
