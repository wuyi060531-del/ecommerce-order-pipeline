"""Create demo-v1: entirely fictional, deterministic data with no external input."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ("order_id", "user_id", "product_name", "quantity", "unit_price", "order_time", "city", "status")


def sample_bytes() -> bytes:
    rows = []
    for number in range(1, 30):
        row = dict(
            order_id=f"SYN{number:03d}", user_id=f"DEMO_USER_{number:03d}",
            product_name=f"synthetic-product-{(number - 1) % 8 + 1:02d}",
            quantity="1", unit_price=f"{number}.00" if number <= 11 else "1.25",
            order_time="2027-01-15 09:00:00", city=f"synthetic-city-{(number - 1) % 9 + 1:02d}",
            status="paid" if number <= 11 else "completed" if number % 2 == 0 else "cancelled",
        )
        if number in (10, 12):
            row["city"] = ""
        if number == 11:
            row["order_time"] = "2027-02-30 09:00:00"
        if number == 27:
            row["quantity"] = "0"
        if number == 28:
            row["unit_price"] = ""
        if number == 29:
            row["quantity"] = "-1"
        rows.append(row)
    rows.append(rows[12].copy())
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def create_sample(path: Path) -> dict:
    """Refuse replacement; raw input remains immutable during all ETL runs."""
    metadata_path = path.with_suffix(".synthetic.json")
    if path.exists() or metadata_path.exists():
        raise ValueError("Sample or provenance already exists; choose a new path.")
    payload = sample_bytes()
    metadata = dict(dataset_kind="synthetic", generator="scripts.create_demo_data.sample_bytes",
                    generator_version="demo-v1", external_inputs=[], rows=30,
                    source_sha256=hashlib.sha256(payload).hexdigest(),
                    expected=dict(accepted_orders=26, quarantined_rows=3, duplicate_rows=1,
                                  paid_orders=11, sales_amount_cents=6600, quality_issue_records=7))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/raw/orders.csv")
    parser.add_argument("--verify", action="store_true", help="Compare the shipped CSV without writing it")
    args = parser.parse_args()
    if args.verify:
        if args.output.read_bytes() != sample_bytes():
            raise SystemExit("Demo sample differs from generator.")
        print("demo-v1 reproducibility: OK")
    else:
        create_sample(args.output)
        print("Created fictional demo-v1: 30 rows; paid total 66.00.")


if __name__ == "__main__":
    main()
