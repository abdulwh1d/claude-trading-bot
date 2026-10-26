"""Kronos forecast plumbing: scoring past calls, the output line and the files. The model
itself (torch, downloaded weights) runs only in the GitHub Action."""
import importlib.util
import os
from datetime import datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kronos_forecast", os.path.join(HERE, "..", "forecast", "kronos_forecast.py"))
kf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kf)

T0 = datetime(2026, 10, 26, 10, 0, tzinfo=kf.NY)


def bars(closes, start=T0):
    idx = pd.date_range(start, periods=len(closes), freq="5min")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0}, index=idx)


def test_calls_are_scored_once_their_hour_has_passed():
    b = {"NQ": bars([100.0] * 12 + [105.0] * 20)}                  # up 5 an hour after 10:00
    log = [
        {"ts": T0.isoformat(), "sym": "NQ", "last": "100", "c1h": "102", "hit": ""},     # said up: right
        {"ts": T0.isoformat(), "sym": "NQ", "last": "100", "c1h": "98", "hit": ""},      # said down: wrong
        {"ts": T0.isoformat(), "sym": "NQ", "last": "100", "c1h": "100", "hit": ""},     # flat call: not scored
        {"ts": (T0 + timedelta(minutes=120)).isoformat(), "sym": "NQ", "last": "105", "c1h": "106", "hit": ""},  # too soon
        {"ts": T0.isoformat(), "sym": "ES", "last": "100", "c1h": "101", "hit": ""},     # no ES bars
    ]
    kf.score_log(log, b)
    assert [r["hit"] for r in log] == ["1", "0", "-", "", ""]
    assert kf.hit_rate(log, "NQ") == (1, 2)


def test_hit_rate_counts_only_the_latest_calls():
    log = [{"sym": "NQ", "hit": "0"}] * 10 + [{"sym": "NQ", "hit": "1"}] * kf.SCORE_N
    assert kf.hit_rate(log, "NQ") == (kf.SCORE_N, kf.SCORE_N)


def test_forecast_line_reads_one_and_two_hours_out():
    pred = pd.DataFrame({"open": range(24), "high": [x + 1.0 for x in range(24)], "low": [x - 1.0 for x in range(24)],
                         "close": [float(x) for x in range(24)]})
    ln = kf.forecast_line("2026-10-26T13:38", "NQ", 5.0, pred, (7, 12))
    assert ln == "2026-10-26T13:38 FC:NQ last=5.00 c1h=11.00 c2h=23.00 hi=24.00 lo=-1.00 hit=7/12"


def test_log_round_trip_keeps_the_newest(tmp_path):
    path = str(tmp_path / "log.csv")
    rows = [{"ts": str(i), "sym": "NQ", "last": "1", "c1h": "2", "hit": ""} for i in range(kf.KEEP_LOG + 5)]
    kf.write_log(rows, path)
    back = kf.read_log(path)
    assert len(back) == kf.KEEP_LOG and back[0]["ts"] == "5"
