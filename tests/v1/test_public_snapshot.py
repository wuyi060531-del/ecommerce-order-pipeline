"""Regress the public extraction, generated data and sealed-release boundary."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd
import pytest

from scripts.check_release import verify_manifest
from scripts.create_demo_data import create_sample, sample_bytes
from src.order_pipeline import transforms
from src.order_pipeline.quality import inspect_csv

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_is_local_and_transforms_preserve_inputs():
    frame = pd.DataFrame(dict(city=[None, "A"], quantity=[2, 1],
                              unit_price=[Decimal("0.10"), Decimal("1.10")],
                              status=["paid", "cancelled"], order_time=["bad", "2027-01-01 00:00:00"]))
    before = frame.copy(deep=True)
    result = transforms.remove_exact_duplicates(frame)
    result = transforms.convert_order_time(transforms.fill_missing_city(result))
    result = transforms.add_order_amount(transforms.keep_known_price(transforms.keep_positive_quantity(result)))
    paid = transforms.keep_paid_orders(result)
    assert transforms.summarize_city_amount(paid).to_dict("records") == [{"city": "未知", "order_amount": Decimal("0.20")}]
    assert pd.isna(paid.order_time.iloc[0])
    pd.testing.assert_frame_equal(frame, before)
    assert not list((ROOT / "src").glob("day*.py"))
    assert not (ROOT / "learning").exists()
    for name, module in list(sys.modules.items()):
        if name == "src" or name.startswith("src."):
            assert Path(module.__file__).resolve().is_relative_to(ROOT.resolve())


def test_demo_data_can_be_recreated_without_external_inputs(tmp_path):
    path = tmp_path / "new.csv"
    metadata = create_sample(path)
    assert path.read_bytes() == sample_bytes() == (ROOT / "data/raw/orders.csv").read_bytes()
    assert metadata["external_inputs"] == []
    assert inspect_csv(path).metrics["sales_amount_cents"] == 6600
    with pytest.raises(ValueError):
        create_sample(path)


@pytest.mark.parametrize("change", ["tamper", "extra"])
def test_release_manifest_rejects_modified_or_unlisted_files(tmp_path, change):
    source = tmp_path / "README.md"
    source.write_text("public sample\n")
    (tmp_path / "SHA256SUMS.json").write_text(json.dumps({"files": {"README.md": hashlib.sha256(source.read_bytes()).hexdigest()}}))
    assert set(verify_manifest(tmp_path)) == {"README.md"}
    if change == "tamper":
        source.write_text("changed\n")
    else:
        (tmp_path / "personal.txt").write_text("not on the allowlist\n")
    with pytest.raises(ValueError):
        verify_manifest(tmp_path)
