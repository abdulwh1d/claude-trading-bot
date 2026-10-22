"""GEX levels for the TradingView dashboard.

Pulls CBOE's free delayed option chains and computes dealer gamma exposure (GEX) by
strike, the gamma flip, the call wall, the put wall and the biggest GEX spikes for:

    ES / MES  ->  SPX (primary), SPY
    NQ / MNQ  ->  NDX (primary), QQQ
    GC / MGC  ->  GLD

Method (as in CodeCraftIA/Options-Gamma-Exposure-Analysis-Tool and
FlashAlpha-lab/gex-explained, the SpotGamma convention):

    GEX per contract = gamma x open interest x 100 x spot^2 x 1%
    calls count as +GEX, puts as -GEX (dealers assumed long calls, short puts)

The gamma flip is where total GEX changes sign as spot moves. It's found by
re-pricing every contract's gamma (Black-Scholes, CBOE's implied vol) on a grid of
spot prices, as SqueezeMetrics and SpotGamma do. If the sweep finds no crossing, it
falls back to the zero crossing of the per-strike profile, as the starred repos do.

Output, one line per underlying, in the underlying's own prices (the indicator
converts them to futures prices with the live futures/index ratio):

    2026-10-21T08:45 SPX spot=5801.20 flip=5750.50 cw=5900 pw=5700 total=+4.21 k=5800:+3.21,5900:+2.10

GEX values are $ billions per 1% move. Files written:

    gex/latest.txt     today's lines, for all five underlyings. Paste into the indicator.
    gex/history.txt    every run, newest last (keeps the last 60 runs per underlying)

Usage:  python gex/gex_levels.py            (fetch from CBOE)
        python gex/gex_levels.py --file chain_dir/  (offline: read SPX.json etc.)

Standard library only. Not financial advice: GEX rests on assumptions about who is
long and short the options, and the data is delayed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
UNDERLYINGS = {"SPX": "_SPX", "SPY": "SPY", "NDX": "_NDX", "QQQ": "QQQ", "GLD": "GLD"}
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

MAX_DAYS = 60        # expirations included
WALL_BAND = 0.10     # walls searched within +-10% of spot
SPIKE_BAND = 0.07    # spikes searched within +-7% of spot
N_SPIKES = 6
KEEP_RUNS = 60
HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------- data --
def fetch(symbol: str) -> dict:
    req = urllib.request.Request(URL.format(symbol), headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_chain(payload: dict, now: datetime) -> tuple[float, list[dict]]:
    """Spot price and one row per contract: strike, type, years to expiry, gamma, OI, IV."""
    data = payload.get("data", payload)
    spot = float(data["current_price"])
    rows = []
    for o in data.get("options", []):
        m = OCC.match(str(o.get("option", "")))
        if not m:
            continue
        _, ymd, cp, k = m.groups()
        expiry = datetime.strptime(ymd, "%y%m%d").replace(hour=16, tzinfo=NY)
        days = (expiry - now).total_seconds() / 86400.0
        if days < -0.01 or days > MAX_DAYS:
            continue
        oi = float(o.get("open_interest") or 0)
        if oi <= 0:
            continue
        rows.append({
            "strike": int(k) / 1000.0,
            "cp": cp,
            "t": max(days, 1.0 / 24) / 365.0,
            "gamma": float(o.get("gamma") or 0),
            "oi": oi,
            "iv": float(o.get("iv") or 0),
        })
    return spot, rows


# --------------------------------------------------------------------- math --
def bs_gamma(s: float, k: float, t: float, iv: float, r: float = 0.0) -> float:
    if s <= 0 or k <= 0 or t <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(s / k) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (s * iv * math.sqrt(t))


def contract_gex(gamma: float, oi: float, spot: float, cp: str) -> float:
    g = gamma * oi * 100 * spot * spot * 0.01
    return g if cp == "C" else -g


def gex_by_strike(spot: float, rows: list[dict]) -> dict[float, float]:
    out: dict[float, float] = {}
    for r in rows:
        gamma = r["gamma"] if r["gamma"] > 0 else bs_gamma(spot, r["strike"], r["t"], r["iv"])
        out[r["strike"]] = out.get(r["strike"], 0.0) + contract_gex(gamma, r["oi"], spot, r["cp"])
    return out


def total_gex_at(s: float, rows: list[dict]) -> float:
    return sum(contract_gex(bs_gamma(s, r["strike"], r["t"], r["iv"]), r["oi"], s, r["cp"])
               for r in rows if r["iv"] > 0)


def gamma_flip(spot: float, rows: list[dict], profile: dict[float, float]) -> float | None:
    """Spot level where total GEX changes sign, nearest to the current spot."""
    grid = [spot * (0.90 + 0.0025 * i) for i in range(81)]          # -10% .. +10%
    totals = [total_gex_at(s, rows) for s in grid]
    crossings = []
    for (s1, g1), (s2, g2) in zip(zip(grid, totals), zip(grid[1:], totals[1:])):
        if g1 == 0:
            crossings.append(s1)
        elif g1 * g2 < 0:
            crossings.append(s1 + (s2 - s1) * (-g1 / (g2 - g1)))
    if crossings:
        return min(crossings, key=lambda x: abs(x - spot))
    # Fallback: zero crossing of the per-strike profile nearest to spot.
    ks = sorted(k for k in profile if abs(k / spot - 1) <= WALL_BAND)
    best = None
    for k1, k2 in zip(ks, ks[1:]):
        g1, g2 = profile[k1], profile[k2]
        if g1 * g2 < 0:
            x = k1 + (k2 - k1) * (-g1 / (g2 - g1))
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return best


def levels(spot: float, rows: list[dict]) -> dict:
    profile = gex_by_strike(spot, rows)
    near = {k: g for k, g in profile.items() if abs(k / spot - 1) <= WALL_BAND}
    if not near:
        raise ValueError("no strikes near spot")
    cw = max(near, key=lambda k: near[k])
    pw = min(near, key=lambda k: near[k])
    spikes = sorted(((k, g) for k, g in profile.items() if abs(k / spot - 1) <= SPIKE_BAND),
                    key=lambda kg: abs(kg[1]), reverse=True)[:N_SPIKES]
    return {
        "spot": spot,
        "flip": gamma_flip(spot, rows, profile),
        "cw": cw if near[cw] > 0 else None,
        "pw": pw if near[pw] < 0 else None,
        "total": sum(profile.values()) / 1e9,
        "spikes": [(k, g / 1e9) for k, g in sorted(spikes)],
    }


def fmt_num(x: float | None) -> str:
    if x is None:
        return "na"
    return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-9 else str(int(round(x)))


def line(stamp: str, name: str, lv: dict) -> str:
    ks = ",".join(f"{fmt_num(k)}:{g:+.2f}" for k, g in lv["spikes"])
    return (f"{stamp} {name} spot={lv['spot']:.2f} flip={fmt_num(lv['flip'])} cw={fmt_num(lv['cw'])} "
            f"pw={fmt_num(lv['pw'])} total={lv['total']:+.2f} k={ks}")


# -------------------------------------------------------------------- files --
def merge_history(path: str, new_lines: list[str]) -> None:
    old = []
    if os.path.exists(path):
        with open(path) as f:
            old = [ln.strip() for ln in f if ln.strip()]
    keyed = {}
    for ln in old + new_lines:
        parts = ln.split(" ")
        if len(parts) >= 2:
            keyed[(parts[0], parts[1])] = ln
    by_und: dict[str, list[str]] = {}
    for (stamp, und), ln in sorted(keyed.items()):
        by_und.setdefault(und, []).append(ln)
    kept = sorted((ln for lns in by_und.values() for ln in lns[-KEEP_RUNS:]), key=lambda s: s.split(" ")[0])
    with open(path, "w") as f:
        f.write("\n".join(kept) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", help="read <dir>/<NAME>.json instead of fetching (offline/testing)")
    ap.add_argument("--now", help="override the time, e.g. 2026-10-21T08:45 (New York)")
    ap.add_argument("--out", default=HERE, help="folder for latest.txt and history.txt")
    args = ap.parse_args(argv)

    now = datetime.strptime(args.now, "%Y-%m-%dT%H:%M").replace(tzinfo=NY) if args.now else datetime.now(NY)
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    out_lines, failures = [], []
    for name, symbol in UNDERLYINGS.items():
        try:
            if args.file:
                with open(os.path.join(args.file, f"{name}.json")) as f:
                    payload = json.load(f)
            else:
                payload = fetch(symbol)
            spot, rows = parse_chain(payload, now)
            out_lines.append(line(stamp, name, levels(spot, rows)))
        except Exception as e:  # one bad feed shouldn't stop the others
            failures.append(f"{name}: {type(e).__name__}: {e}")

    for ln in out_lines:
        print(ln)
    for fl in failures:
        print("FAILED " + fl, file=sys.stderr)
    if out_lines:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "latest.txt"), "w") as f:
            f.write("\n".join(out_lines) + "\n")
        merge_history(os.path.join(args.out, "history.txt"), out_lines)
    return 0 if out_lines else 1


if __name__ == "__main__":
    sys.exit(main())
