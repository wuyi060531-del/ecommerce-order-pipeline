"""Build docs/demo/demo-data.json by running the real pipeline on the synthetic sample.

Offline and deterministic: everything runs in a temporary directory, values come from
in-memory results and SQLite queries (not from exported files), and no timestamps or
paths are written. The same data is embedded into docs/demo/index.html between
`build:` markers so the page also works when opened from disk.

    python -m scripts.build_demo_data           # regenerate
    python -m scripts.build_demo_data --check   # fail when committed files are stale
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from unittest import mock

import pandas as pd

from src.order_pipeline import __version__
from src.order_pipeline.errors import DatabaseError
from src.order_pipeline.pipeline import _sales_rows, run_pipeline
from src.order_pipeline.quality import REQUIRED, RULE_VERSION, inspect_csv, read_csv
from src.order_pipeline.storage import SQLiteStore, SQL_DIR

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/orders.csv"
DEMO_DIR = ROOT / "docs/demo"
DATA_FILE = DEMO_DIR / "demo-data.json"
PAGE_FILE = DEMO_DIR / "index.html"
SCHEMA = 1
TABLES = ("orders", "etl_runs", "dq_issues")
BLOCK = "<!-- build:{name} -->{body}<!-- /build:{name} -->"
BLOCK_RE = "<!-- build:{name} -->.*?<!-- /build:{name} -->"


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


# ---------------------------------------------------------------- probes

def _install_probe(connection: sqlite3.Connection, sink: dict) -> None:
    """Temp triggers report every row write made through the store's own connection."""
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='orders'").fetchone() is None:
        return
    connection.create_function(
        "demo_probe", 4, lambda table, op, key, detail: sink["events"].append(
            dict(table=table, op=op, key=str(key), detail=detail)))
    keys = {"orders": "order_id", "etl_runs": "run_id", "dq_issues": "issue_number"}
    for table, key in keys.items():
        for op in ("INSERT", "UPDATE", "DELETE"):
            ref = "OLD" if op == "DELETE" else "NEW"
            detail = "OLD.status || '->' || NEW.status" if (table, op) == ("orders", "UPDATE") else "''"
            connection.execute(
                f"CREATE TEMP TRIGGER probe_{table}_{op} AFTER {op} ON main.{table} "
                f"BEGIN SELECT demo_probe('{table}','{op}',{ref}.{key},{detail}); END")


class Probe:
    """Patch SQLiteStore._connect (as the storage tests do) to observe real writes."""

    def __init__(self) -> None:
        self.sink: dict = {"events": []}
        original = SQLiteStore._connect
        sink = self.sink

        def probed(store: SQLiteStore) -> sqlite3.Connection:
            connection = original(store)
            _install_probe(connection, sink)
            return connection

        self._patch = mock.patch.object(SQLiteStore, "_connect", probed)

    def __enter__(self) -> "Probe":
        self._patch.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self._patch.__exit__(*exc)

    def take(self) -> list[dict]:
        events, self.sink["events"] = self.sink["events"], []
        return events


def _summarise(events: list[dict]) -> list[dict]:
    counts: dict[tuple[str, str], int] = {}
    for event in events:
        counts[(event["table"], event["op"])] = counts.get((event["table"], event["op"]), 0) + 1
    return [dict(table=t, op=o, count=n) for (t, o), n in sorted(counts.items())]


def _observed(events: list[dict]) -> list[dict]:
    result = []
    for event in events:
        key = event["key"][:12] if event["table"] == "etl_runs" else event["key"]
        item = dict(table=event["table"], op=event["op"], key=key)
        if event["detail"]:
            item["detail"] = event["detail"]
        result.append(item)
    return result


def snapshot(database: Path) -> dict:
    if not database.exists():
        return dict(orders=0, etl_runs=0, dq_issues=0, paid_orders=0, paid_sales_cents=0, digest=None)
    connection = sqlite3.connect(database)
    try:
        counts = {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        paid = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(order_amount_cents),0) FROM orders WHERE status='paid'").fetchone()
        digest = hashlib.sha256()
        for sql in ("SELECT * FROM orders ORDER BY order_id",
                    "SELECT run_id,source_hash,source_name,metrics_json FROM etl_runs ORDER BY run_id",
                    "SELECT * FROM dq_issues ORDER BY run_id,issue_number"):
            digest.update(json.dumps(connection.execute(sql).fetchall(), ensure_ascii=False).encode())
    finally:
        connection.close()
    return dict(**counts, paid_orders=paid[0], paid_sales_cents=paid[1], digest=digest.hexdigest()[:12])


def _status_of(database: Path, order_id: str) -> str | None:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT status FROM orders WHERE order_id=?", (order_id,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- collection

def _rows(source: Path, result, raw: pd.DataFrame) -> list[dict]:
    records = raw.to_dict(orient="records")
    accepted = {int(r["source_row"]): r for r in result.orders.to_dict(orient="records")}
    issues: dict[int, list[dict]] = {}
    outcomes: dict[int, str] = {}
    for item in result.issues.to_dict(orient="records"):
        row = int(item["source_row"])
        issues.setdefault(row, []).append(
            dict(rule=item["rule"], severity=item["severity"], action=item["action"], message=item["message"]))
        outcomes[row] = item["record_outcome"]
    first_seen: dict[tuple, int] = {}
    rows = []
    for index, record in enumerate(records):
        number = index + 2  # same numbering as quality_issues.source_row: header is record 1
        key = tuple(record[c] for c in raw.columns)
        duplicate_of = first_seen.setdefault(key, number)
        outcome = outcomes.get(number, "accepted")
        if (number in accepted) != (outcome == "accepted"):
            raise RuntimeError(f"record {number}: accepted set and issue outcomes disagree")
        clean = None
        if number in accepted:
            order = accepted[number]
            moment = order["order_time"]
            clean = dict(
                city=order["city"], status=order["status"], quantity=int(order["quantity"]),
                unit_price_cents=int(order["unit_price_cents"]),
                order_amount_cents=int(order["order_amount_cents"]),
                order_time=None if pd.isna(moment) else pd.Timestamp(moment).strftime("%Y-%m-%d %H:%M:%S"))
        rows.append(dict(
            record=number, order_id=record["order_id"], raw={c: record[c] for c in REQUIRED},
            outcome=outcome, duplicate_of=duplicate_of if outcome == "duplicate_removed" else None,
            issues=issues.get(number, []), clean=clean))
    return rows


def _independent_paid_cents(source: Path) -> int:
    """Re-read the raw CSV with only csv + Decimal (no pipeline code); valid for this sample."""
    seen: set[tuple] = set()
    total = Decimal(0)
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = tuple(row.values())
            if key in seen:
                continue
            seen.add(key)
            if row["status"].strip().lower() == "paid":
                total += int(row["quantity"]) * Decimal(row["unit_price"]) * 100
    return int(total)


def _batch_b(tmp: Path) -> Path:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    lines = [line.replace(",paid", ",cancelled") if line.startswith("SYN001,") else line for line in lines]
    lines.append("SYN030,DEMO_USER_030,synthetic-product-06,2,3.50,2027-01-15 09:00:00,synthetic-city-03,paid")
    path = tmp / "orders_batch_b.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _diff(database: Path, frame: pd.DataFrame) -> list[dict]:
    fields = ("quantity", "unit_price_cents", "city", "status", "order_amount_cents")
    with sqlite3.connect(database) as connection:
        current = {r[0]: dict(zip(fields, r[1:])) for r in connection.execute(
            f"SELECT order_id,{','.join(fields)} FROM orders")}
    result = []
    for record in frame.to_dict(orient="records"):
        new = {f: (int(record[f]) if f != "city" and f != "status" else record[f]) for f in fields}
        old = current.get(record["order_id"])
        if old is None:
            result.append(dict(order_id=record["order_id"], kind="insert", values=new))
        elif old != new:
            result.append(dict(order_id=record["order_id"], kind="update",
                               changes={f: [old[f], new[f]] for f in fields if old[f] != new[f]}))
    return result


def collect() -> dict:
    before_bytes = SOURCE.read_bytes()
    raw, source_hash = read_csv(SOURCE)
    result = inspect_csv(SOURCE)
    metrics = {k: int(v) for k, v in result.metrics.items()}
    rows = _rows(SOURCE, result, raw)
    rules = []
    for rule in sorted({i["rule"] for r in rows for i in r["issues"]}):
        hits = [(r["record"], i) for r in rows for i in r["issues"] if i["rule"] == rule]
        rules.append(dict(rule=rule, severity=hits[0][1]["severity"], action=hits[0][1]["action"],
                          message=hits[0][1]["message"], count=len(hits), records=[h[0] for h in hits]))

    with tempfile.TemporaryDirectory(prefix="demo-data-") as name, Probe() as probe:
        tmp = Path(name)
        database, out_a, out_b = tmp / "orders.sqlite3", tmp / "run-a", tmp / "run-b"
        scenarios: dict = {}

        empty = snapshot(database)
        first = run_pipeline(SOURCE, out_a, database_path=database)
        first_events = probe.take()
        artifacts = sorted(p.name for p in out_a.iterdir())
        digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out_a.iterdir()}
        after_first = snapshot(database)
        analysis = SQLiteStore(database).analyze()  # SQL over batch A only, before any later batch
        scenarios["first_load"] = dict(stats={k: int(v) for k, v in first.load_statistics.items()},
                                       writes=_summarise(first_events), before=empty, after=after_first)

        again = run_pipeline(SOURCE, out_a, database_path=database)
        rerun_events = probe.take()
        after_rerun = snapshot(database)
        scenarios["rerun"] = dict(
            stats={k: int(v) for k, v in again.load_statistics.items()}, writes=_summarise(rerun_events),
            before=after_first, after=after_rerun, state_identical=after_first == after_rerun,
            artifacts_identical=digests == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in out_a.iterdir()})

        batch_b = _batch_b(tmp)
        result_b = inspect_csv(batch_b)
        run_b = dict(run_id=result_b.run_id, source_hash=result_b.source_hash,
                     source_name=result_b.source_name, metrics=result_b.metrics)
        broken = result_b.orders.copy(deep=True)
        broken.loc[broken["order_id"] == "SYN002", "quantity"] = 0  # same injection as tests/v1/test_storage.py
        diff = _diff(database, broken)
        store = SQLiteStore(database)
        try:
            store.load(broken, result_b.issues, run_b)
            raise RuntimeError("the injected write was expected to fail")
        except DatabaseError as error:
            message = str(error)
        failed_events = probe.take()
        after_fail = snapshot(database)
        scenarios["failure"] = dict(
            batch=dict(name="orders_batch_b.csv", raw_rows=int(result_b.metrics["raw_rows"]),
                       accepted_orders=int(result_b.metrics["accepted_orders"])),
            diff=diff, injected=dict(order_id="SYN002", field="quantity", value=0, constraint="CHECK(quantity > 0)"),
            writes_before_failure=_observed(failed_events), error=message,
            before=after_first, after=after_fail, rolled_back=after_first == after_fail,
            syn001_status_after=_status_of(database, "SYN001"))

        fixed = run_pipeline(batch_b, out_b, database_path=database)
        fixed_events = probe.take()
        scenarios["recovery"] = dict(
            stats={k: int(v) for k, v in fixed.load_statistics.items()}, writes=_summarise(fixed_events),
            before=after_fail, after=snapshot(database), syn001_status_after=_status_of(database, "SYN001"))


    if SOURCE.read_bytes() != before_bytes:
        raise RuntimeError("input file changed")
    summary = {k: int(v) for k, v in analysis["summary"][0].items()}
    cities = [{k: v for k, v in r.items()} for r in analysis["cities"]]
    products = [{k: v for k, v in r.items()} for r in analysis["products"]]

    def compare(sql_rows: list[dict], pandas_rows: list[dict], key: str) -> list[dict]:
        mine = {r[key]: int(r["sales_amount_cents"]) for r in pandas_rows}
        return [dict(name=r[key], sql_cents=int(r["sales_amount_cents"]), pandas_cents=mine[r[key]],
                     match=int(r["sales_amount_cents"]) == mine[r[key]]) for r in sql_rows]

    by_city = compare(cities, _sales_rows(result.orders, "city"), "city")
    by_product = compare(products, _sales_rows(result.orders, "product_name"), "product_name")
    independent = _independent_paid_cents(SOURCE)
    checks = [
        dict(id="paid_sales", label="paid 销售额（分）", sql=summary["sales_amount_cents"],
             other=independent, other_label="独立重算：标准库 csv + Decimal 直读原始文件（仅覆盖本样本）"),
        dict(id="paid_sales_batch", label="paid 销售额（分）", sql=summary["sales_amount_cents"],
             other=metrics["sales_amount_cents"], other_label="Pandas / Decimal 本批汇总"),
        dict(id="paid_orders", label="paid 订单数", sql=summary["paid_orders"],
             other=metrics["paid_orders"], other_label="Pandas 本批汇总"),
        dict(id="total_orders", label="入库订单数", sql=summary["total_orders"],
             other=metrics["accepted_orders"], other_label="质量层接收订单数"),
        dict(id="city_sum", label="城市分组合计（分）", sql=sum(r["sql_cents"] for r in by_city),
             other=summary["sales_amount_cents"], other_label="SQL 全库 paid 总额"),
        dict(id="product_sum", label="商品分组合计（分）", sql=sum(r["sql_cents"] for r in by_product),
             other=summary["sales_amount_cents"], other_label="SQL 全库 paid 总额"),
        dict(id="rows", label="原始行 = 接收 + 隔离 + 重复", sql=metrics["raw_rows"],
             other=metrics["accepted_orders"] + metrics["quarantined_rows"] + metrics["duplicate_rows"],
             other_label="三类之和"),
    ]
    for check in checks:
        check["left_label"] = "原始行数" if check["id"] == "rows" else "SQL 查询结果"
        check["match"] = check["sql"] == check["other"]
    if not all(c["match"] for c in checks) or not all(r["match"] for r in by_city + by_product):
        raise RuntimeError("reconciliation mismatch; refusing to write demo data")

    sql_text = (SQL_DIR / "analysis_mysql.sql").read_text(encoding="utf-8")
    summary_sql = re.search(r"-- name: summary\n(.*?);", sql_text, re.S).group(1).strip()
    issues_total = int(len(result.issues))
    return dict(
        schema=SCHEMA,
        meta=dict(app_version=__version__, rule_version=RULE_VERSION, backend="sqlite",
                  source_file="data/raw/orders.csv", source_sha256=source_hash, run_id=result.run_id,
                  synthetic=True, generator="scripts/build_demo_data.py"),
        metrics=metrics, rules=rules, rows=rows,
        stages=dict(
            read=dict(records=len(raw), fields=len(raw.columns), columns=list(raw.columns)),
            validate=dict(accepted=metrics["accepted_orders"], quarantined=metrics["quarantined_rows"],
                          duplicates=metrics["duplicate_rows"], issues=issues_total,
                          anomalous_rows=metrics["anomalous_rows"]),
            normalise=dict(missing_city=metrics["missing_city_rows"], invalid_time=metrics["invalid_time_rows"],
                           paid_orders=metrics["paid_orders"], sales_amount_cents=metrics["sales_amount_cents"]),
            load=dict(stats=scenarios["first_load"]["stats"], tables=after_first),
            analyze=dict(total_orders=summary["total_orders"], paid_orders=summary["paid_orders"],
                         sales_amount_cents=summary["sales_amount_cents"], city_rows=len(cities),
                         product_rows=len(products)),
            report=dict(files=artifacts)),
        sql=dict(summary_query=summary_sql, summary=summary, cities=cities, products=products),
        reconciliation=dict(checks=checks, by_city=by_city, by_product=by_product),
        scenarios=scenarios)


# ---------------------------------------------------------------- page embedding

def _static(data: dict) -> str:
    e = html.escape
    m, outcome = data["metrics"], {"accepted": "接收", "quarantined": "隔离", "duplicate_removed": "重复副本"}
    out = ['<section id="static-summary" aria-labelledby="static-title">',
           '<h2 id="static-title">静态摘要 <span lang="en">Static summary</span></h2>',
           "<p>浏览器未启用 JavaScript，下面是同一份 demo-data.json 的文字摘要；交互视图需要启用 JavaScript。</p>",
           "<h3>数量</h3><table><tbody>"]
    for label, value in (("原始行", m["raw_rows"]), ("接收", m["accepted_orders"]), ("隔离", m["quarantined_rows"]),
                         ("重复副本", m["duplicate_rows"]), ("paid 订单", m["paid_orders"]),
                         ("paid 销售额", f"{money(m['sales_amount_cents'])} 元（{m['sales_amount_cents']} 分）")):
        out.append(f"<tr><th scope=\"row\">{label}</th><td>{e(str(value))}</td></tr>")
    out.append("</tbody></table><h3>被隔离、丢弃或带警告的行</h3><table><thead><tr><th>记录</th><th>订单号</th>"
               "<th>结果</th><th>规则</th></tr></thead><tbody>")
    for row in data["rows"]:
        if row["issues"]:
            rules = "、".join(f"{i['rule']}（{i['action']}）" for i in row["issues"])
            out.append(f"<tr><td>{row['record']}</td><td>{e(row['order_id'])}</td>"
                       f"<td>{outcome[row['outcome']]}</td><td>{e(rules)}</td></tr>")
    out.append("</tbody></table><h3>SQL 对账</h3><table><thead><tr><th>项目</th><th>SQL</th><th>对照值</th>"
               "<th>结果</th></tr></thead><tbody>")
    for c in data["reconciliation"]["checks"]:
        out.append(f"<tr><td>{e(c['label'])}</td><td>{c['sql']}</td><td>{c['other']}（{e(c['other_label'])}）</td>"
                   f"<td>{'一致' if c['match'] else '不一致'}</td></tr>")
    s = data["scenarios"]
    out.append("</tbody></table><h3>事务失败与重跑</h3><ul>")
    rerun, fail, rec = s["rerun"], s["failure"], s["recovery"]
    out.append(f"<li>同一文件重跑：接收 {rerun['stats']['incoming']}，写入 {rerun['stats']['written']}，"
               f"未变化 {rerun['stats']['unchanged']}；数据库状态{'完全相同' if rerun['state_identical'] else '发生变化'}。</li>")
    first = fail["writes_before_failure"]
    out.append(f"<li>第二批写入失败：失败前事务内已有 {len(first)} 次写入，数据库报错后整批回滚；"
               f"订单 {fail['before']['orders']} → {fail['after']['orders']}，批次 {fail['before']['etl_runs']} → "
               f"{fail['after']['etl_runs']}，问题记录 {fail['before']['dq_issues']} → {fail['after']['dq_issues']}，"
               f"状态{'完全相同' if fail['rolled_back'] else '不同'}。</li>")
    out.append(f"<li>修正后重跑：接收 {rec['stats']['incoming']}，写入 {rec['stats']['written']}，"
               f"未变化 {rec['stats']['unchanged']}；订单 {rec['after']['orders']}，paid 销售额 "
               f"{money(rec['after']['paid_sales_cents'])} 元。</li></ul></section>")
    return "".join(out)


def _json_text(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _embed(page: str, data: dict) -> str:
    body = "\n" + '<script type="application/json" id="demo-data">\n' + _json_text(data).replace("</", "<\\/") + "</script>\n"
    for name, text in (("data", body), ("static", "\n" + _static(data) + "\n")):
        pattern = BLOCK_RE.format(name=name)
        if not re.search(pattern, page, re.S):
            raise RuntimeError(f"index.html is missing the build:{name} markers")
        page = re.sub(pattern, lambda _m, t=text, n=name: BLOCK.format(name=n, body=t), page, flags=re.S)
    return page


def render() -> tuple[str, str]:
    data = collect()
    return _json_text(data), _embed(PAGE_FILE.read_text(encoding="utf-8"), data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if committed demo files are stale")
    args = parser.parse_args(argv)
    data_text, page_text = render()
    if args.check:
        stale = [p.name for p, t in ((DATA_FILE, data_text), (PAGE_FILE, page_text))
                 if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            print("demo files are stale: " + ", ".join(stale) + "; run: python -m scripts.build_demo_data",
                  file=sys.stderr)
            return 1
        print("demo files are up to date")
        return 0
    DATA_FILE.write_text(data_text, encoding="utf-8", newline="\n")
    PAGE_FILE.write_text(page_text, encoding="utf-8", newline="\n")
    print(f"wrote {DATA_FILE.relative_to(ROOT)} and refreshed embedded blocks in {PAGE_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
