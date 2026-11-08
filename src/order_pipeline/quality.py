"""保留逐行审计的订单质量检测；使用独立生产转换模块。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import logging
from pathlib import Path
import re
from time import perf_counter

import pandas as pd

from .transforms import (remove_exact_duplicates, fill_missing_city, keep_positive_quantity,
                         keep_known_price, convert_order_time, add_order_amount, keep_paid_orders)
from .errors import InputError


REQUIRED = ("order_id", "user_id", "product_name", "quantity", "unit_price", "order_time", "city", "status")
ISSUE_COLUMNS = ("source_row", "order_id", "rule", "severity", "action", "message", "raw_record")
RULE_VERSION = "v1.0.0"
MAX_PRICE = Decimal("9999999999.99")
MAX_AMOUNT_CENTS = 9_000_000_000_000_000
# 与 DataGuard 同一口径：只接受半角 0-9，可带 +/- 号和小数部分。
# 刻意不用 \d（它会匹配全角数字），也不接受下划线、科学计数法、".5"、"5."。
NUMBER_TEXT = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
NUMBER_HINT = "仅限半角数字 0-9，不接受下划线、全角数字和科学计数法"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityResult:
    orders: pd.DataFrame
    issues: pd.DataFrame
    metrics: dict[str, int]
    source_hash: str
    source_name: str
    run_id: str


def read_csv(path: Path, encoding: str = "utf-8-sig") -> tuple[pd.DataFrame, str]:
    """先验证结构再按文本读取，避免编号丢失前导零和价格浮点误差。"""
    try:
        payload = Path(path).read_bytes()
        text = payload.decode(encoding)
    except FileNotFoundError as error:
        raise InputError(f"输入文件不存在：{path}") from error
    except (UnicodeError, LookupError) as error:
        raise InputError(f"CSV 无法按 {encoding} 解码；请确认编码或使用 --encoding。") from error
    except OSError as error:
        raise InputError(f"输入文件无法读取：{path}（{error.strerror}）") from error
    if not text.strip():
        raise InputError("CSV 是空文件；需要包含字段名的表头。")
    if "\x00" in text:
        raise InputError("CSV 含 NUL 字符，无法保证解析完整；请检查文件格式和编码。")
    try:
        rows = [row for row in csv.reader(io.StringIO(text), strict=True) if row]
        header = [name.strip() for name in rows[0]]
        if len(set(header)) != len(header) or any(not name for name in header):
            raise InputError("CSV 字段名为空或重复，请修正表头。")
        missing = sorted(set(REQUIRED) - set(header))
        if missing:
            raise InputError("CSV 缺少字段：" + ", ".join(missing))
        for number, row in enumerate(rows[1:], start=2):
            if len(row) != len(header):
                raise InputError(f"CSV 第 {number} 个记录的字段数为 {len(row)}，预期 {len(header)}。")
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, index_col=False)
        frame.columns = header
        if len(frame) != len(rows) - 1:
            raise InputError("CSV 解析记录数不一致，请检查空白行或引号。")
    except (csv.Error, pd.errors.ParserError, pd.errors.EmptyDataError, IndexError) as error:
        raise InputError("CSV 格式无法解析，请检查逗号、引号和表头。") from error
    return frame, hashlib.sha256(payload).hexdigest()


def _number(value: str) -> Decimal | None:
    # Decimal() 会接受下划线和全角数字，并悄悄解释成别的数，所以先用 ASCII 正则把关。
    if not NUMBER_TEXT.fullmatch(value):
        return None
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def inspect_csv(path: Path, encoding: str = "utf-8-sig", timings: dict[str, float] | None = None) -> QualityResult:
    measured = timings if timings is not None else {}
    started = perf_counter()
    raw, source_hash = read_csv(path, encoding)
    measured["csv_read_seconds"] = perf_counter() - started
    started = perf_counter()
    LOGGER.info("读取 %s：%s 条记录，%s 个字段", Path(path).name, len(raw), len(raw.columns))
    duplicates = raw.duplicated()
    unique = remove_exact_duplicates(raw)
    normalized = unique.copy(deep=True)
    for column in REQUIRED:
        normalized[column] = normalized[column].str.strip()
    normalized["status"] = normalized["status"].str.lower()
    city_missing = normalized["city"].eq("")
    normalized.loc[city_missing, "city"] = pd.NA
    normalized = convert_order_time(fill_missing_city(normalized))
    conflicts = normalized["order_id"].duplicated(keep=False) & normalized["order_id"].ne("")
    issues: list[dict] = []
    accepted: list[dict] = []
    quarantined: set[int] = set()
    # cProfile 确认逐行 Series 索引为主要瓶颈；一次转换，保持 Decimal 校验规则。
    raw_records = raw.to_dict(orient="records")
    records = normalized.to_dict(orient="index")
    duplicate_indices = set(raw.index[duplicates])
    conflict_indices = set(normalized.index[conflicts])
    missing_city_indices = set(normalized.index[city_missing])
    raw_json: dict[int, str] = {}
    measured["clean_seconds"] = perf_counter() - started
    started = perf_counter()

    def issue(index: int, rule: str, severity: str, action: str, message: str) -> None:
        if index not in raw_json:
            raw_json[index] = json.dumps(raw_records[index], ensure_ascii=False)
        issues.append(dict(source_row=index + 2, order_id=raw_records[index]["order_id"], rule=rule,
                           severity=severity, action=action, message=message,
                           raw_record=raw_json[index]))

    for index in raw.index:
        if index in duplicate_indices:
            issue(index, "exact_duplicate", "warning", "drop_duplicate", "完全重复的多余副本；保留首次记录")
            continue
        row = records[index]
        errors = 0

        def reject(rule: str, message: str) -> None:
            nonlocal errors
            errors += 1
            issue(index, rule, "error", "quarantine", message)

        for column, limit in (("order_id", 128), ("user_id", 128), ("product_name", 255)):
            if not row[column] or len(row[column]) > limit:
                reject(f"invalid_{column}", f"{column} 必须非空且不超过 {limit} 字符")
        if index in conflict_indices:
            reject("conflicting_order_id", "同一批次订单编号相同但记录不同；全部隔离，不猜测版本")
        quantity = _number(row["quantity"])
        if quantity is None or not (0 < quantity <= 2_147_483_647) or quantity != quantity.to_integral_value():
            reject("invalid_quantity", f"quantity 必须是 1～2147483647 的整数（{NUMBER_HINT}）")
            quantity = None
        price = _number(row["unit_price"])
        # is_signed() 同时挡住负零：-0 满足 0 <= price，却会把 "-0.00" 写进清洗结果。
        if (price is None or price.is_signed() or not (0 <= price <= MAX_PRICE)
                or price != price.quantize(Decimal("0.01"))):
            reject("invalid_price", f"unit_price 必须是非负有限金额，最多两位小数且不超过 9999999999.99（{NUMBER_HINT}）")
            price = None
        if quantity is not None and price is not None:
            amount = quantity * price
            if amount * 100 > MAX_AMOUNT_CENTS:
                reject("amount_overflow", "订单金额超出本项目整数分存储上限")
        if row["status"] not in {"paid", "completed", "cancelled"}:
            reject("invalid_status", "status 仅支持 paid、completed、cancelled")
        if len(row["city"]) > 128:
            reject("invalid_city", "city 不超过 128 字符")
        if index in missing_city_indices:
            issue(index, "missing_city", "warning", "fill_unknown", "缺失城市补为未知，不推测真实城市")
        if pd.isna(row["order_time"]):
            issue(index, "invalid_time", "warning", "set_null", "时间无法解析；留空但保留订单及 paid 金额")
        if errors:
            quarantined.add(index)
        else:
            entry = {column: row[column] for column in REQUIRED}
            entry.update(quantity=int(quantity), unit_price=price.quantize(Decimal("0.01")),
                         unit_price_cents=int(price * 100), source_row=index + 2)
            accepted.append(entry)

    measured["quality_seconds"] = perf_counter() - started
    started = perf_counter()
    orders = pd.DataFrame(accepted, columns=[*REQUIRED, "unit_price_cents", "source_row"])
    orders["quantity"] = orders["quantity"].astype("int64")
    orders["order_time"] = pd.to_datetime(orders["order_time"])
    # 转换模块负责正数量、非缺失价格、逐行金额和 paid 筛选。
    orders = add_order_amount(keep_known_price(keep_positive_quantity(orders)))
    orders["order_amount_cents"] = pd.Series([int(value * 100) for value in orders["order_amount"]],
                                             index=orders.index, dtype=object)
    measured["clean_seconds"] += perf_counter() - started
    started = perf_counter()
    audit = pd.DataFrame(issues, columns=ISSUE_COLUMNS)
    run_id = hashlib.sha256(f"{RULE_VERSION}:{encoding}:{source_hash}".encode()).hexdigest()
    audit["source_file"] = Path(path).name
    audit["source_hash"] = source_hash
    audit["run_id"] = run_id
    audit["record_outcome"] = [
        "quarantined" if row["source_row"] - 2 in quarantined else
        "duplicate_removed" if row["source_row"] - 2 in duplicate_indices else "accepted"
        for row in issues
    ]
    audit["database_status"] = "not_executed"
    warning_rows = {r["source_row"] for r in issues if r["severity"] == "warning"}
    anomalous_rows = {r["source_row"] for r in issues}
    paid = keep_paid_orders(orders)
    metrics = dict(raw_rows=len(raw), accepted_orders=len(orders), quarantined_rows=len(quarantined),
                   duplicate_rows=int(duplicates.sum()), anomalous_rows=len(anomalous_rows),
                   clean_rows=len(raw) - len(anomalous_rows), warning_rows=len(warning_rows),
                   paid_orders=len(paid), sales_amount_cents=sum(paid["order_amount_cents"], 0),
                   missing_city_rows=sum(r["rule"] == "missing_city" for r in issues),
                   invalid_time_rows=sum(r["rule"] == "invalid_time" for r in issues))
    assert metrics["raw_rows"] == metrics["accepted_orders"] + metrics["quarantined_rows"] + metrics["duplicate_rows"]
    LOGGER.info("质量结果：接收 %s，隔离 %s，重复副本 %s，异常原始行 %s", len(orders),
                len(quarantined), metrics["duplicate_rows"], len(anomalous_rows))
    measured["quality_seconds"] += perf_counter() - started
    return QualityResult(orders, audit, metrics, source_hash, Path(path).name, run_id)
