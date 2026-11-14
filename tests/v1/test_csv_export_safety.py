"""Spreadsheet exports must not execute source text or mutate ETL/audit values."""
import csv
from decimal import Decimal
import hashlib
import json
import sqlite3

import pandas as pd
import pytest

from src.order_pipeline.pipeline import _csv, analyze_database, check_pipeline, regenerate_report, run_pipeline
from test_pipeline import make_row, write_orders


@pytest.mark.parametrize('value', [
    '=1+1', '+1+1', '-1+1', '@SUM(1)', '＝1+1', '＋1', '－1', '＠SUM(1)',
    '  =1', '\t=1', '\r=1', '\n=1', '\ufeff=1', '\u3000=1', '\x01=1',
    '=1,"quoted"\nnext', '\tordinary text',
])
def test_untrusted_text_is_marked_only_in_export(tmp_path, value):
    frame = pd.DataFrame({'text': [value], 'number': [-2], 'money': [Decimal('1.20')]})
    before = frame.copy(deep=True)
    target = tmp_path / 'export.csv'
    _csv(target, frame)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row == {'text': 'text:' + value, 'number': '-2', 'money': '1.20'}
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize('value', ['ordinary', '00123', "'=1", 'word,=1', 'word;=1', 'word\n=1', '', '中文'])
def test_ordinary_text_is_not_changed(tmp_path, value):
    target = tmp_path / 'export.csv'
    _csv(target, pd.DataFrame({'text': [value]}))
    with target.open(encoding='utf-8-sig', newline='') as stream:
        assert next(csv.DictReader(stream))['text'] == value


def test_all_modes_protect_exports_and_preserve_database_and_raw_audit(tmp_path):
    raw = make_row(order_id='=1+1', user_id='+2', product_name='@SUM(1)', city='-2', order_time='bad')
    source = write_orders(tmp_path, [raw])
    before = source.read_bytes()
    db = tmp_path / 'orders.sqlite3'
    checked, output, analyzed = (tmp_path / name for name in ('check', 'run', 'analysis'))
    check_pipeline(source, checked)
    run_pipeline(source, output, database_path=db)
    repeated = run_pipeline(source, output, database_path=db)
    assert repeated.load_statistics['unchanged'] == 1
    analyze_database(analyzed, database_path=db)
    for folder in (checked, output, analyzed):
        csv_before = {p.name: p.read_bytes() for p in folder.glob('*.csv')}
        regenerate_report(folder)
        assert csv_before == {p.name: p.read_bytes() for p in folder.glob('*.csv')}
        for name, payload in csv_before.items():
            with (folder / name).open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                for field in ('order_id', 'user_id', 'product_name', 'city'):
                    if field in row:
                        assert row[field] == 'text:' + raw[field]
                if 'raw_record' in row:
                    assert json.loads(row['raw_record']) == raw
            manifest = json.loads((folder / 'manifest.json').read_text())
            assert manifest['files'][name] == hashlib.sha256(payload).hexdigest()
    assert source.read_bytes() == before
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT order_id,user_id,product_name,city FROM orders').fetchone() == tuple(raw[k] for k in ('order_id','user_id','product_name','city'))
        assert json.loads(connection.execute('SELECT raw_record FROM dq_issues').fetchone()[0]) == raw
        assert connection.execute('SELECT count(*) FROM orders').fetchone()[0] == 1
