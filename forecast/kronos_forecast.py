"""Kronos forecasts for A.W. Strategy on TradingView.

Kronos (shiyu-coder/Kronos, MIT) is an open-source foundation model trained on candlestick
charts from 45 exchanges. Each run pulls the last few days of 5-minute bars for NQ, ES, RTY,
YM and gold from Yahoo Finance, asks Kronos-small for the next 2 hours (24 bars, 5 sampled
paths averaged) from the last 400 bars, and adds one line per market to gex/latest.txt and
gex/history.txt, the file the strategy already reads:

    2026-10-26T13:38 FC:NQ last=29495.25 c1h=29520.50 c2h=29540.00 hi=29580.00 lo=29460.00 hit=11/20

last is the latest close, c1h and c2h the forecast close 1 and 2 hours out, hi and lo the
forecast range. hit is how many of the last (up to) 40 one-hour direction calls were right,
scored against the bars that followed; every call is kept in forecast/log.csv.

Usage:  python forecast/kronos_forecast.py
An experiment. A model's forecast is a guess, not a trading signal.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NY = ZoneInfo("America/New_York")
SYMBOLS = {"NQ": "NQ=F", "ES": "ES=F", "RTY": "RTY=F", "YM": "YM=F", "GC": "GC=F"}
LOOKBACK = 400        # bars of context (Kronos-small reads up to 512)
PRED_LEN = 24         # 2 hours of 5-minute bars
HOUR = 12             # bars in an hour
SAMPLES = 5           # sampled paths, averaged
SCORE_N = 40          # direction calls in the hit rate
KEEP_LOG = 3000
LOG = os.path.join(HERE, "log.csv")
LOG_FIELDS = ["ts", "sym", "last", "c1h", "hit"]


def fetch(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(NY)
    return df


def read_log(path: str = LOG) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_log(rows: list[dict], path: str = LOG) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        for r in rows[-KEEP_LOG:]:
            w.writerow({k: r.get(k, "") for k in LOG_FIELDS})


def score_log(rows: list[dict], bars: dict[str, pd.DataFrame]) -> None:
    """Fill in hit (1 right, 0 wrong) for calls whose hour has passed: right when the first
    close an hour or more later moved from the price at the call the way the call said."""
    for r in rows:
        if r.get("hit") not in ("", None) or r["sym"] not in bars:
            continue
        b = bars[r["sym"]]
        t = datetime.fromisoformat(r["ts"])
        after = b[b.index >= t + timedelta(minutes=60)]
        if after.empty:
            continue
        pred = float(r["c1h"]) - float(r["last"])
        actual = float(after["close"].iloc[0]) - float(r["last"])
        # A flat call or a flat hour isn't scored ("-").
        r["hit"] = "-" if pred == 0 or actual == 0 else ("1" if (pred > 0) == (actual > 0) else "0")


def hit_rate(rows: list[dict], sym: str) -> tuple[int, int]:
    scored = [int(r["hit"]) for r in rows if r["sym"] == sym and r.get("hit") in ("0", "1")][-SCORE_N:]
    return sum(scored), len(scored)


def forecast_line(stamp: str, sym: str, last: float, pred: pd.DataFrame, hits: tuple[int, int]) -> str:
    c1h = float(pred["close"].iloc[HOUR - 1])
    c2h = float(pred["close"].iloc[-1])
    hi = float(pred["high"].max())
    lo = float(pred["low"].min())
    return (f"{stamp} FC:{sym} last={last:.2f} c1h={c1h:.2f} c2h={c2h:.2f} hi={hi:.2f} lo={lo:.2f} "
            f"hit={hits[0]}/{hits[1]}")


def load_gex_module():
    spec = importlib.util.spec_from_file_location("gex_levels", os.path.join(ROOT, "gex", "gex_levels.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "gex"), help="folder with latest.txt and history.txt")
    ap.add_argument("--model", default="NeoQuasar/Kronos-small")
    ap.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    args = ap.parse_args(argv)

    stamp = datetime.now(NY).strftime("%Y-%m-%dT%H:%M")
    bars: dict[str, pd.DataFrame] = {}
    for sym, ticker in SYMBOLS.items():
        try:
            df = fetch(ticker)
            if len(df) >= 100:
                bars[sym] = df
            else:
                print(f"FAILED {sym}: only {len(df)} bars", file=sys.stderr)
        except Exception as e:  # one bad feed shouldn't stop the others
            print(f"FAILED {sym}: {type(e).__name__}: {e}", file=sys.stderr)
    if not bars:
        return 1

    log = read_log()
    score_log(log, bars)

    sys.path.insert(0, HERE)
    from model import Kronos, KronosPredictor, KronosTokenizer
    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)

    lines = []
    for sym, df in bars.items():
        try:
            x = df.iloc[-LOOKBACK:]
            last_t = x.index[-1]
            x_ts = pd.Series(x.index)
            y_ts = pd.Series(pd.date_range(last_t + timedelta(minutes=5), periods=PRED_LEN, freq="5min"))
            pred = predictor.predict(df=x.reset_index(drop=True), x_timestamp=x_ts, y_timestamp=y_ts,
                                     pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=SAMPLES, verbose=False)
            last = float(x["close"].iloc[-1])
            log.append({"ts": last_t.isoformat(), "sym": sym, "last": f"{last:.4f}",
                        "c1h": f"{float(pred['close'].iloc[HOUR - 1]):.4f}", "hit": ""})
            lines.append(forecast_line(stamp, sym, last, pred, hit_rate(log, sym)))
        except Exception as e:
            print(f"FAILED {sym} forecast: {type(e).__name__}: {e}", file=sys.stderr)

    write_log(log)
    for ln in lines:
        print(ln)
    if lines:
        latest = os.path.join(args.out, "latest.txt")
        old = open(latest).read().splitlines() if os.path.exists(latest) else []
        keep = [ln for ln in old if ln.strip() and " FC:" not in ln]
        with open(latest, "w") as f:
            f.write("\n".join(keep + lines) + "\n")
        load_gex_module().merge_history(os.path.join(args.out, "history.txt"), lines)
    return 0 if lines else 1


if __name__ == "__main__":
    sys.exit(main())
