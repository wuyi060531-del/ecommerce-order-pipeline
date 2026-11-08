"""公开样本和质量规则的黑盒验收。"""

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.order_pipeline.errors import InputError
from src.order_pipeline.quality import inspect_csv


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/orders.csv"
COLUMNS = [
    "order_id", "user_id", "product_name", "quantity", "unit_price",
    "order_time", "city", "status",
]


def make_row(**changes):
    row = dict(zip(COLUMNS, [
        "T001", "U001", "练习书", "2", "12.50",
        "2026-10-01 08:30:00", "杭州", "paid",
    ]))
    row.update(changes)
    return row


def write_orders(tmp_path, rows, *, encoding="utf-8", name="input.csv"):
    path = tmp_path / name
    with path.open("w", encoding=encoding, newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_public_sample_quality_counts_amounts_and_raw_protection():
    before = RAW.read_bytes()
    result = inspect_csv(RAW)
    assert RAW.read_bytes() == before
    expected = {
        "raw_rows": 30, "accepted_orders": 26, "quarantined_rows": 3,
        "duplicate_rows": 1, "paid_orders": 11, "sales_amount_cents": 6600,
        "missing_city_rows": 2, "invalid_time_rows": 1,
        "anomalous_rows": 7, "clean_rows": 23, "warning_rows": 4,
    }
    for key, value in expected.items():
        assert result.metrics[key] == value, key
    assert result.orders.order_id.is_unique
    assert result.source_hash == hashlib.sha256(before).hexdigest()
    assert result.source_name == "orders.csv"
    assert result.run_id == inspect_csv(RAW).run_id
    assert len(result.run_id) >= 16
    assert set(COLUMNS + ["source_row", "order_amount", "unit_price_cents", "order_amount_cents"]) <= set(result.orders)
    assert not {"SYN027", "SYN028", "SYN029"} & set(result.orders.order_id)
    assert result.orders.loc[result.orders.order_id == "SYN011", "order_time"].isna().all()
    assert result.orders.loc[result.orders.order_id == "SYN010", "city"].item() == "未知"
    assert result.orders["order_amount"].map(type).eq(Decimal).all()
    assert result.issues.source_row.min() >= 2
    assert result.issues.source_row.max() <= 31
    assert result.issues.raw_record.map(json.loads).map(type).eq(dict).all()
    assert {"source_row", "order_id", "rule", "severity", "action", "message", "raw_record"} <= set(result.issues)


def test_normalization_retains_identifiers_and_uses_decimal_money(tmp_path):
    path = write_orders(tmp_path, [make_row(
        order_id=" 000123 ", user_id=" 000004 ", product_name=" 纸张 ",
        city=" 上海 ", quantity="3", unit_price="0.10", status=" PAID ",
    )])
    result = inspect_csv(path)
    row = result.orders.iloc[0]
    assert row.order_id == "000123"
    assert row.user_id == "000004"
    assert row.product_name == "纸张"
    assert row.city == "上海"
    assert row.status == "paid"
    assert row.quantity == 3
    assert row.unit_price == Decimal("0.10")
    assert row.order_amount == Decimal("0.30")
    assert row.unit_price_cents == 10
    assert row.order_amount_cents == 30
    assert result.metrics["sales_amount_cents"] == 30


@pytest.mark.parametrize("field,value", [
    ("order_id", ""), ("user_id", " "), ("product_name", ""),
    ("order_id", "x" * 1000), ("user_id", "x" * 1000),
    ("product_name", "x" * 1000), ("city", "x" * 1000),
    ("quantity", "0"), ("quantity", "-1"), ("quantity", "1.5"),
    ("quantity", "abc"), ("quantity", ""), ("quantity", "NaN"),
    ("quantity", "Infinity"), ("quantity", "2147483648"),
    ("unit_price", ""), ("unit_price", "abc"), ("unit_price", "-0.01"),
    ("unit_price", "NaN"), ("unit_price", "Infinity"),
    ("unit_price", "1.001"), ("unit_price", "10000000000.00"),
    ("status", "refunded"), ("status", ""),
    # Decimal() 本身接受下列写法并改写成别的数，必须在解析前拒绝（Issue #3）。
    ("quantity", "1_0"), ("quantity", "１２"), ("quantity", "1e2"),
    ("unit_price", "1_0.50"), ("unit_price", "３.５０"), ("unit_price", "1e1"),
    ("unit_price", ".5"), ("unit_price", "5."),
    ("unit_price", "-0"), ("unit_price", "-0.00"),
])
def test_invalid_business_values_are_quarantined_with_original_evidence(tmp_path, field, value):
    path = write_orders(tmp_path, [make_row(**{field: value})])
    before = path.read_bytes()
    result = inspect_csv(path)
    assert result.metrics["raw_rows"] == 1
    assert result.metrics["accepted_orders"] == 0
    assert result.metrics["quarantined_rows"] == 1
    assert result.metrics["anomalous_rows"] == 1
    assert result.orders.empty
    assert not result.issues.empty
    assert (result.issues.source_row == 2).all()
    assert (result.issues.message.str.len() > 0).all()
    assert any(json.loads(raw)[field] == value for raw in result.issues.raw_record)
    assert path.read_bytes() == before


def test_zero_price_is_valid_but_amount_overflow_is_not(tmp_path):
    path = write_orders(tmp_path, [
        make_row(order_id="free", unit_price="0"),
        make_row(order_id="overflow", quantity="2147483647", unit_price="9999999999.99"),
    ])
    result = inspect_csv(path)
    assert result.orders.order_id.tolist() == ["free"]
    assert result.orders.order_amount.iloc[0] == Decimal("0")
    assert result.metrics["quarantined_rows"] == 1
    assert "overflow" in set(result.issues.order_id)


@pytest.mark.parametrize("quantity,price,expected_quantity,expected_price", [
    ("10", "2.50", 10, Decimal("2.50")),
    ("+3", "+5.00", 3, Decimal("5.00")),
    ("007", "007.5", 7, Decimal("7.50")),
    ("1", "0", 1, Decimal("0.00")),
    ("1", "0.00", 1, Decimal("0.00")),
])
def test_conventional_ascii_numbers_remain_valid(tmp_path, quantity, price, expected_quantity, expected_price):
    result = inspect_csv(write_orders(tmp_path, [make_row(quantity=quantity, unit_price=price)]))
    assert result.metrics["accepted_orders"] == 1
    assert result.metrics["quarantined_rows"] == 0
    assert result.issues.empty
    order = result.orders.iloc[0]
    assert order.quantity == expected_quantity
    assert order.unit_price == expected_price


def test_lookalike_numbers_are_quarantined_beside_valid_rows_without_leaking(tmp_path):
    path = write_orders(tmp_path, [
        make_row(order_id="ok-1"),
        make_row(order_id="underscore", quantity="1_0", unit_price="1_0.50"),
        make_row(order_id="fullwidth", quantity="１２", unit_price="３.５０"),
        make_row(order_id="negzero", unit_price="-0.00"),
        make_row(order_id="ok-2", quantity="3", unit_price="4.00"),
    ])
    result = inspect_csv(path)
    assert result.orders.order_id.tolist() == ["ok-1", "ok-2"]
    assert result.metrics["raw_rows"] == 5
    assert result.metrics["accepted_orders"] == 2
    assert result.metrics["quarantined_rows"] == 3
    assert result.metrics["sales_amount_cents"] == 2 * 1250 + 3 * 400
    assert not result.orders.unit_price.astype(str).str.startswith("-").any()
    evidence = {row.order_id: json.loads(row.raw_record) for row in result.issues.itertuples()}
    assert evidence["underscore"]["quantity"] == "1_0"
    assert evidence["underscore"]["unit_price"] == "1_0.50"
    assert evidence["fullwidth"]["quantity"] == "１２"
    assert evidence["fullwidth"]["unit_price"] == "３.５０"
    assert evidence["negzero"]["unit_price"] == "-0.00"
    assert set(result.issues.rule) == {"invalid_quantity", "invalid_price"}


def test_one_bad_row_keeps_all_reasons_without_overcounting_rows(tmp_path):
    path = write_orders(tmp_path, [make_row(
        user_id="", quantity="0", unit_price="bad", status="unknown",
        city="", order_time="2026-09-31 00:00:00",
    )])
    result = inspect_csv(path)
    assert result.metrics["quarantined_rows"] == 1
    assert result.metrics["anomalous_rows"] == 1
    assert result.metrics["raw_rows"] == 1
    assert result.metrics["missing_city_rows"] == 1
    assert result.metrics["invalid_time_rows"] == 1
    assert len(result.issues) >= 6
    assert result.issues.rule.nunique() >= 6


def test_exact_duplicate_keeps_first_and_audits_extra_copy(tmp_path):
    first = make_row()
    path = write_orders(tmp_path, [first, first, make_row(order_id="T002")])
    result = inspect_csv(path)
    assert result.orders.order_id.tolist() == ["T001", "T002"]
    assert result.orders.source_row.tolist() == [2, 4]
    assert result.metrics["duplicate_rows"] == 1
    assert result.metrics["quarantined_rows"] == 0
    assert result.issues.source_row.tolist() == [3]


def test_conflicting_same_batch_id_is_not_silently_last_write_wins(tmp_path):
    path = write_orders(tmp_path, [
        make_row(order_id=" T001 "), make_row(order_id="T001", quantity="3"),
        make_row(order_id="T002"),
    ])
    result = inspect_csv(path)
    assert result.orders.order_id.tolist() == ["T002"]
    assert result.metrics["quarantined_rows"] == 2
    assert result.metrics["duplicate_rows"] == 0
    assert {2, 3} <= set(result.issues.source_row)


def test_missing_city_and_bad_time_are_visible_warnings_not_silent_deletions(tmp_path):
    path = write_orders(tmp_path, [make_row(city=" ", order_time="2026-09-31 10:00:00")])
    result = inspect_csv(path)
    assert result.metrics["accepted_orders"] == 1
    assert result.metrics["quarantined_rows"] == 0
    assert result.metrics["warning_rows"] == 1
    assert result.metrics["anomalous_rows"] == 1
    assert result.orders.city.tolist() == ["未知"]
    assert result.orders.order_time.isna().all()
    assert result.metrics["paid_orders"] == 1
    assert result.metrics["sales_amount_cents"] == 2500


def test_paid_scope_excludes_completed_and_cancelled(tmp_path):
    path = write_orders(tmp_path, [
        make_row(order_id="P", status="paid"),
        make_row(order_id="C", status="completed"),
        make_row(order_id="X", status="cancelled"),
    ])
    result = inspect_csv(path)
    assert result.metrics["accepted_orders"] == 3
    assert result.metrics["paid_orders"] == 1
    assert result.metrics["sales_amount_cents"] == 2500


@pytest.mark.parametrize("content", ["", " \n\t", "order_id,user_id\na,b\n", ",".join(COLUMNS + ["order_id"]) + "\n", ",".join(COLUMNS) + '\n"unfinished\n', ",".join(COLUMNS) + "\na,b\n"])
def test_file_and_schema_problems_raise_actionable_input_error(tmp_path, content):
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(InputError) as caught:
        inspect_csv(path)
    assert str(caught.value)
    assert path.read_text(encoding="utf-8") == content


def test_missing_file_and_wrong_encoding_are_input_errors(tmp_path):
    with pytest.raises(InputError, match="不存在"):
        inspect_csv(tmp_path / "missing.csv")
    path = write_orders(tmp_path, [make_row()], encoding="gbk")
    with pytest.raises(InputError, match="编码|解码"):
        inspect_csv(path)
    assert inspect_csv(path, encoding="gbk").metrics["accepted_orders"] == 1
    with pytest.raises(InputError):
        inspect_csv(path, encoding="definitely-not-a-codec")


def test_header_only_input_returns_structured_zero_result(tmp_path):
    path = write_orders(tmp_path, [])
    result = inspect_csv(path)
    assert result.orders.empty
    assert result.issues.empty
    assert all(value == 0 for value in result.metrics.values())
    assert set(COLUMNS + ["order_amount", "source_row"]) <= set(result.orders)
    assert {"rule", "message", "raw_record"} <= set(result.issues)


def test_quoted_csv_field_is_preserved_and_input_is_immutable(tmp_path):
    path = write_orders(tmp_path, [make_row(product_name='纸张, "A4"\n加厚')])
    before = path.read_bytes()
    result = inspect_csv(path)
    assert result.orders.product_name.item() == '纸张, "A4"\n加厚'
    assert path.read_bytes() == before
