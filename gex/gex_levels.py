"""GEX levels for A.W. Strategy on TradingView.

Pulls CBOE's free delayed option chains and computes dealer gamma exposure (GEX) by
strike, the gamma flip, the call wall, the put wall and a ladder of every strike near spot for:

    ES / MES   ->  SPX (with SPY)        RTY / M2K  ->  RUT (with IWM)
    NQ / MNQ   ->  NDX (with QQQ)        YM / MYM   ->  DIA (with DJX)
    GC / MGC   ->  GLD                   SI / SIL   ->  SLV
    CL / MCL   ->  USO                   NG         ->  UNG
    ZB / UB    ->  TLT

Method (as in CodeCraftIA/Options-Gamma-Exposure-Analysis-Tool and
FlashAlpha-lab/gex-explained, the SpotGamma convention):

    GEX per contract = gamma x open interest x 100 x spot^2 x 1%
    calls count as +GEX, puts as -GEX (dealers assumed long calls, short puts)

The gamma flip is where total GEX changes sign as spot moves. It's found by
re-pricing every contract's gamma (Black-Scholes, CBOE's implied vol) on a grid of
spot prices, as SqueezeMetrics and SpotGamma do. If the sweep finds no crossing, it
falls back to the zero crossing of the per-strike profile, as the starred repos do.

SPX and NDX lines combine the index chain with its ETF (SPY, QQQ): ETF strikes are
moved onto the index scale (strike x index/ETF price) and their open interest scaled
so each contract keeps its dollar gamma. Strikes are grouped into buckets (5 points
for SPX, 25 for NDX) so the walls show where gamma piles up, not one odd strike.
Walls are searched within 5% of spot. The ladder lists every strike bucket within 2%
of spot (the range a day trades in) holding at least 1% of the biggest one's GEX.

Output, one line per underlying, in the underlying's own prices (the indicator
converts them to futures prices with the live futures/index ratio):

    2026-10-21T08:45 SPX spot=5801.20 flip=5750.50 cw=5900 pw=5700 total=+4.21 k=5800:+3.21,5900:+2.10 src=SPX+SPY

Each line also carries v=strike:calls/puts for the 8 busiest strikes by today's options
volume within 2% of spot (index-sized contracts): the options-flow levels.

GEX values are $ billions per 1% move, to 3 significant figures, never in exponent
form (Pine's str.tonumber can't read 1e-05). Files written:

    gex/latest.txt     this run's lines, for every underlying
    gex/history.txt    snapshots, oldest first: paste this into the indicator. It keeps
                       the first run of each day plus the newest run, and stays under
                       38,000 characters (Pine strings stop at 40,960).

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
UNDERLYINGS = {
    "SPX": "_SPX", "SPY": "SPY",      # ES / MES
    "NDX": "_NDX", "QQQ": "QQQ",      # NQ / MNQ
    "RUT": "_RUT", "IWM": "IWM",      # RTY / M2K
    "DIA": "DIA", "DJX": "_DJX",      # YM / MYM
    "GLD": "GLD",                     # GC / MGC
    "SLV": "SLV",                     # SI / SIL
    "USO": "USO",                     # CL / MCL
    "UNG": "UNG",                     # NG
    "TLT": "TLT",                     # ZB / UB
}
# The first line also counts the second's options, moved onto its scale.
COMBINE = {"SPX": "SPY", "NDX": "QQQ", "RUT": "IWM", "DIA": "DJX"}
BUCKET = {"SPX": 5.0, "NDX": 25.0, "RUT": 5.0, "SPY": 1.0, "QQQ": 1.0, "IWM": 1.0, "DIA": 1.0,
          "DJX": 1.0, "GLD": 1.0, "SLV": 0.5, "USO": 1.0, "UNG": 0.5, "TLT": 1.0}
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

MAX_DAYS = 60        # expirations included
WALL_BAND = 0.05     # walls searched within +-5% of spot
LADDER_BAND = 0.02   # every strike bucket within +-2% of spot goes in the ladder
MIN_SHARE = 0.01     # ...unless its GEX is under 1% of the biggest one there (noise)
TOP_LEVELS = 8       # older snapshots in history keep only their 8 biggest strikes
N_VOLUME = 8         # options-flow levels: the 8 busiest strikes by today's volume
KEEP_DAYS = 40
MAX_HISTORY_CHARS = 38_000   # Pine strings stop at 40,960 characters
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
    """Spot price and one row per contract: strike, type, years to expiry, gamma, OI, IV and
    today's volume. Contracts with no open interest are kept when they traded today."""
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
        vol = float(o.get("volume") or 0)
        if oi <= 0 and vol <= 0:
            continue
        rows.append({
            "strike": int(k) / 1000.0,
            "cp": cp,
            "t": max(days, 1.0 / 24) / 365.0,
            "gamma": float(o.get("gamma") or 0),
            "oi": oi,
            "iv": float(o.get("iv") or 0),
            "vol": vol,
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


def rescale(rows: list[dict], ratio: float) -> list[dict]:
    """ETF contracts on the index's scale: strike x ratio, gamma / ratio, OI and volume / ratio.
    gamma x OI x spot^2 then gives the same dollar gamma as on the ETF's own scale, and
    volume counts in index-sized contracts."""
    return [dict(r, strike=r["strike"] * ratio, gamma=r["gamma"] / ratio, oi=r["oi"] / ratio,
                 vol=r.get("vol", 0.0) / ratio) for r in rows]


def volume_by_strike(spot: float, rows: list[dict], bucket: float = 0.0) -> list[tuple[float, float, float]]:
    """Today's options volume at the busiest strikes within LADDER_BAND of spot: the
    options-flow levels. (strike, call volume, put volume), biggest N_VOLUME, by strike."""
    out: dict[float, list[float]] = {}
    for r in rows:
        if r.get("vol", 0) <= 0 or abs(r["strike"] / spot - 1) > LADDER_BAND:
            continue
        k = round(r["strike"] / bucket) * bucket if bucket > 0 else r["strike"]
        cv = out.setdefault(k, [0.0, 0.0])
        cv[0 if r["cp"] == "C" else 1] += r["vol"]
    top = sorted(out.items(), key=lambda kv: kv[1][0] + kv[1][1], reverse=True)[:N_VOLUME]
    return sorted((k, c, p) for k, (c, p) in top)


def gex_by_strike(spot: float, rows: list[dict], bucket: float = 0.0) -> dict[float, float]:
    out: dict[float, float] = {}
    for r in rows:
        gamma = r["gamma"] if r["gamma"] > 0 else bs_gamma(spot, r["strike"], r["t"], r["iv"])
        k = round(r["strike"] / bucket) * bucket if bucket > 0 else r["strike"]
        out[k] = out.get(k, 0.0) + contract_gex(gamma, r["oi"], spot, r["cp"])
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


def levels(spot: float, rows: list[dict], bucket: float = 0.0) -> dict:
    profile = gex_by_strike(spot, rows, bucket)
    near = {k: g for k, g in profile.items() if abs(k / spot - 1) <= WALL_BAND}
    if not near:
        raise ValueError("no strikes near spot")
    cw = max(near, key=lambda k: near[k])
    pw = min(near, key=lambda k: near[k])
    ladder = [(k, g) for k, g in sorted(profile.items()) if abs(k / spot - 1) <= LADDER_BAND]
    biggest = max((abs(g) for _, g in ladder), default=0.0)
    ladder = [(k, g) for k, g in ladder if biggest > 0 and abs(g) >= MIN_SHARE * biggest]
    return {
        "spot": spot,
        "flip": gamma_flip(spot, rows, profile),
        "cw": cw if near[cw] > 0 else None,
        "pw": pw if near[pw] < 0 else None,
        "total": sum(profile.values()) / 1e9,
        "ladder": [(k, g / 1e9) for k, g in ladder],
        "volume": volume_by_strike(spot, rows, bucket),
    }


def fmt_num(x: float | None) -> str:
    if x is None:
        return "na"
    return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-9 else str(int(round(x)))


def fmt_gex(x: float, sig: int = 3) -> str:
    """Signed, `sig` significant figures, plain decimals: +10.9, -4.8, +0.0312, +0.00004."""
    if x == 0 or not math.isfinite(x):
        return "+0"
    digits = max(0, min(9, sig - 1 - math.floor(math.log10(abs(x)))))
    s = f"{x:+.{digits}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def line(stamp: str, name: str, lv: dict, src: str = "") -> str:
    ks = ",".join(f"{fmt_num(k)}:{fmt_gex(g)}" for k, g in lv["ladder"])
    vs = ",".join(f"{fmt_num(k)}:{round(c)}/{round(p)}" for k, c, p in lv.get("volume", []) if round(c) + round(p) > 0)
    return (f"{stamp} {name} spot={lv['spot']:.2f} flip={fmt_num(lv['flip'])} cw={fmt_num(lv['cw'])} "
            f"pw={fmt_num(lv['pw'])} total={fmt_gex(lv['total'])} k={ks}" + (f" v={vs}" if vs else "")
            + (f" src={src}" if src else ""))


# -------------------------------------------------------------------- files --
def compact(ln: str, n: int = TOP_LEVELS) -> str:
    """The same snapshot with only its n biggest strikes and no volume levels, for older
    runs in history."""
    out = []
    for p in ln.split(" "):
        if p.startswith("v="):
            continue
        if p.startswith("k="):
            pairs = [kv.split(":") for kv in p[2:].split(",") if ":" in kv]
            top = sorted(pairs, key=lambda kv: abs(float(kv[1])), reverse=True)[:n]
            p = "k=" + ",".join(f"{k}:{g}" for k, g in sorted(top, key=lambda kv: float(kv[0])))
        out.append(p)
    return " ".join(out)


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
    # The first run of each day (it applies all that day) for the last KEEP_DAYS days,
    # plus each underlying's newest run. Forecast lines (FC:) keep only the newest.
    first_of_day: dict[tuple[str, str], str] = {}
    newest_run: dict[str, str] = {}
    for stamp, und in sorted(keyed):
        first_of_day.setdefault((stamp[:10], und), stamp)
        newest_run[und] = stamp
    days = sorted({stamp[:10] for stamp, _ in keyed})[-KEEP_DAYS:]
    kept = sorted((ln for (stamp, und), ln in keyed.items()
                   if stamp[:10] in days and newest_run[und] == stamp
                   or stamp[:10] in days and first_of_day[(stamp[:10], und)] == stamp and not und.startswith("FC:")),
                  key=lambda s: (s.split(" ")[0], s.split(" ")[1]))
    # Only each underlying's newest run keeps its full ladder; older runs keep their
    # biggest strikes, which is all the signals use.
    newest: dict[str, str] = {}
    for ln in kept:
        stamp, und = ln.split(" ")[:2]
        newest[und] = max(newest.get(und, ""), stamp)
    kept = [ln if ln.split(" ")[0] == newest[ln.split(" ")[1]] else compact(ln) for ln in kept]
    # Drop the oldest runs until the file fits in one Pine string.
    while kept and len("\n".join(kept)) + 1 > MAX_HISTORY_CHARS:
        oldest = kept[0].split(" ")[0]
        kept = [ln for ln in kept if ln.split(" ")[0] != oldest]
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
    out_lines, failures, chains = [], [], {}
    for name, symbol in UNDERLYINGS.items():
        try:
            if args.file:
                with open(os.path.join(args.file, f"{name}.json")) as f:
                    payload = json.load(f)
            else:
                payload = fetch(symbol)
            chains[name] = parse_chain(payload, now)
        except Exception as e:  # one bad feed shouldn't stop the others
            failures.append(f"{name}: {type(e).__name__}: {e}")
    for name in UNDERLYINGS:
        if name not in chains:
            continue
        try:
            spot, rows = chains[name]
            src = ""
            etf = COMBINE.get(name)
            if etf in chains and chains[etf][0] > 0:
                rows = rows + rescale(chains[etf][1], spot / chains[etf][0])
                src = f"{name}+{etf}"
            out_lines.append(line(stamp, name, levels(spot, rows, BUCKET.get(name, 0.0)), src))
        except Exception as e:
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
