"""生成有明确标识的合成订单；不读取或覆盖原始教学数据。"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["order_id", "user_id", "product_name", "quantity", "unit_price", "order_time", "city", "status", "dataset_kind"]


def generate_orders(output: Path, rows: int, seed: int = 20261030) -> dict:
    """固定种子、最多五万行；拒绝覆盖已有文件或写入 raw/。"""
    output = Path(output)
    if not 1 <= rows <= 50_000:
        raise ValueError("合成数据规模必须为 1～50000 行。")
    if output.suffix != ".csv" or output.exists() or output.with_suffix(".synthetic.json").exists():
        raise ValueError("请使用尚不存在的 .csv 路径，不能覆盖已有数据。")
    if (ROOT / "data/raw").resolve() in output.resolve().parents:
        raise ValueError("合成数据不能写入原始数据目录。")
    rng = random.Random(seed)
    products = [("合成键盘", 12990), ("合成鼠标", 5990), ("合成台灯", 9900), ("合成水杯", 3900)]
    cities = ["杭州", "上海", "深圳", "重庆", "北京"]
    start = datetime(2026, 1, 1, 9)
    counts: dict[str, int] = {}
    previous: dict = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for i in range(rows):
            product, price = rng.choice(products)
            row = dict(order_id=f"SYN{seed}_{i:06d}", user_id=f"U{rng.randrange(1, 2001):04d}",
                       product_name=product, quantity=str(rng.randrange(1, 6)),
                       unit_price=f"{price // 100}.{price % 100:02d}",
                       order_time=(start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                       city=rng.choice(cities), status=rng.choice(["paid", "paid", "completed", "cancelled"]),
                       dataset_kind="synthetic")
            slot = i % 100
            case = "normal"
            changes = {0: ("quantity", "0"), 1: ("quantity", "-2"), 2: ("quantity", "1.5"),
                       3: ("unit_price", ""), 4: ("unit_price", "-0.01"), 5: ("unit_price", "0.001"),
                       6: ("quantity", "NaN"), 7: ("unit_price", "Infinity"), 8: ("city", ""),
                       9: ("order_time", "2026-02-30 09:00:00"), 10: ("status", "unknown"),
                       11: ("order_id", ""), 12: ("user_id", ""), 13: ("product_name", ""),
                       14: ("city", "合" * 129), 18: ("unit_price", "0.00")}
            if slot in changes:
                key, value = changes[slot]
                row[key] = value
                case = f"case_{slot:02d}_{key}"
            elif slot == 16:
                row["order_id"] = f"SYN{seed}_CONFLICT_{i // 100:04d}"
                case = "conflict_first"
            elif slot == 17:
                row = dict(previous, quantity=str(int(previous["quantity"]) + 1))
                case = "conflict_second"
            elif slot == 19:
                row.update(city=" 杭州 ", status=" PAID ")
                case = "standardize"
            elif slot == 99:
                row = previous.copy()
                case = "exact_duplicate"
            writer.writerow(row)
            counts[case] = counts.get(case, 0) + 1
            previous = row
    metadata = dict(dataset_kind="synthetic", seed=seed, rows=rows, cases=counts,
                    source_sha256=hashlib.sha256(output.read_bytes()).hexdigest(), generator_version=1)
    output.with_suffix(".synthetic.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, choices=[1000, 10000, 50000], default=1000)
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = args.output or ROOT / f".local/synthetic/SYNTHETIC-orders-{args.rows}.csv"
    try:
        generate_orders(path, args.rows, args.seed)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"已生成 {args.rows} 行合成测试数据：{path}（不是业务数据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
