"""How accurate is the analyst? Measured forward, on the journal, never on past news.

The backtesting trap: a language model trained on enormous amounts of text
already knows how past events played out. Show it a 2025 headline and it
recommends the trade that worked, because it knows how the story ended. So this
scorecard only grades theses that were written before their outcome existed:
every journal entry is scored against prices that came after it.

It answers "does the decision rule have an edge?" It cannot answer that
quickly: 50 trades isn't a sample, it's noise, and the report says so.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import config

from .models import Bar
from .timeutil import ET, as_utc, et_date, session_open_utc

_OPEN, _CLOSE = time(9, 30), time(16, 0)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for a hit rate."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def regular_sessions(bars: list[Bar]) -> dict[date, list[Bar]]:
    """Group intraday bars by New York date, keeping regular hours (9:30-16:00) only."""
    days: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        local = b.t.astimezone(ET)
        if _OPEN <= local.time() < _CLOSE:
            days[local.date()].append(b)
    return {d: sorted(v, key=lambda b: b.t) for d, v in sorted(days.items())}


def simulate_r(side: str, entry: float, stop: float, path: list[Bar], final_close: float) -> tuple[float, bool]:
    """R-multiple of a hypothetical trade managed like the real orders: stop, optional target,
    otherwise exit at the horizon close. Stop is checked first inside a bar (pessimistic), and a
    gap through the stop fills at the open."""
    risk = entry - stop if side == "buy" else stop - entry
    target = None
    if config.TAKE_PROFIT_R:
        target = entry + config.TAKE_PROFIT_R * risk if side == "buy" else entry - config.TAKE_PROFIT_R * risk
    for b in path:
        if side == "buy":
            if b.low <= stop:
                return (min(stop, b.open) - entry) / risk, True
            if target and b.high >= target:
                return config.TAKE_PROFIT_R, False
        else:
            if b.high >= stop:
                return (entry - max(stop, b.open)) / risk, True
            if target and b.low <= target:
                return config.TAKE_PROFIT_R, False
    return ((final_close - entry) if side == "buy" else (entry - final_close)) / risk, False


def score_row(row: dict, days: dict[date, list[Bar]]) -> dict | None:
    """Forward outcome of one journal entry at each horizon (in full sessions after the decision)."""
    price = row.get("price")
    thesis = row.get("thesis")
    if not price or not thesis:
        return None
    t = as_utc(datetime.fromisoformat(row["time"]))
    action = thesis["action"].lower()
    stop = float(thesis.get("stop_loss") or 0)
    full_sessions = [d for d in days if session_open_utc(d) > t]
    side = None
    if action == "buy" and 0 < stop < price:
        side = "buy"
    elif action == "sell" and stop > price and row.get("intent") not in ("close_long",):
        side = "sell"   # a short idea; exits (closing a long) get no R-multiple
    out = {"returns": {}, "r": {}, "stop_hit": {}}
    for h in config.HORIZONS:
        if len(full_sessions) < h:
            continue
        end_day = full_sessions[h - 1]
        close = days[end_day][-1].close
        out["returns"][h] = close / price - 1
        if side:
            path = [b for d, bars in days.items() if d <= end_day for b in bars if b.t >= t]
            out["r"][h], out["stop_hit"][h] = simulate_r(side, price, stop, path, close)
    return out if out["returns"] else None


def _rate(rows: list[dict], h: int, predicate) -> dict:
    scored = [r for r in rows if h in r["outcome"]["returns"]]
    hits = sum(1 for r in scored if predicate(r))
    lo, hi = wilson(hits, len(scored))
    return {"n": len(scored), "hits": hits, "rate": hits / len(scored) if scored else None, "ci95": [lo, hi]}


def _hit(r: dict, h: int) -> bool:
    ret = r["outcome"]["returns"][h]
    return ret > 0 if r["action"] == "buy" else ret < 0


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _dedupe(rows: list[dict]) -> list[dict]:
    """One observation per symbol, day and action. Twenty identical 'hold AAPL' calls in one
    morning are one opinion, not twenty."""
    seen, out = set(), []
    for r in rows:
        key = (r["symbol"], et_date(datetime.fromisoformat(r["time"])), r["action"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def build_report(decisions: list[dict], fetch_bars, cost_rows: list[dict] | None = None,
                 equity: list[tuple[datetime, float]] | None = None) -> dict:
    """decisions: journal rows. fetch_bars(symbol, start) -> 15-minute bars from start to now."""
    cost_rows = cost_rows or []
    usable = [r for r in decisions if r.get("thesis") and r.get("price")]
    report: dict = {"generated": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "decisions_total": len(decisions),
                    "analyst_errors": sum(1 for r in decisions if r.get("analyst_error")),
                    "horizons": list(config.HORIZONS)}

    # Fetch prices once per symbol, from the first decision onward.
    first = defaultdict(lambda: None)
    for r in usable:
        t = as_utc(datetime.fromisoformat(r["time"]))
        if first[r["symbol"]] is None or t < first[r["symbol"]]:
            first[r["symbol"]] = t
    days_by_symbol = {s: regular_sessions(fetch_bars(s, t - timedelta(days=1))) for s, t in first.items()}

    rows = []
    for r in usable:
        outcome = score_row(r, days_by_symbol.get(r["symbol"], {}))
        if outcome:
            rows.append({**r, "action": r["thesis"]["action"].lower(),
                         "confidence": r["thesis"]["confidence"].lower(), "outcome": outcome})
    rows = _dedupe(rows)
    report["scored_observations"] = len(rows)

    directional = [r for r in rows if r["action"] in ("buy", "sell")]
    holds = [r for r in rows if r["action"] == "hold"]
    report["accuracy"] = {}
    for h in config.HORIZONS:
        acc = {
            "directional": _rate(directional, h, lambda r, h=h: _hit(r, h)),
            "buys": _rate([r for r in directional if r["action"] == "buy"], h, lambda r, h=h: _hit(r, h)),
            "sells": _rate([r for r in directional if r["action"] == "sell"], h, lambda r, h=h: _hit(r, h)),
            # Baseline: how often the stock simply went up from the same decision moments.
            "always_buy_baseline": _rate(rows, h, lambda r, h=h: r["outcome"]["returns"][h] > 0),
            "by_confidence": {c: _rate([r for r in directional if r["confidence"] == c], h,
                                       lambda r, h=h: _hit(r, h)) for c in ("low", "medium", "high")},
        }
        efforts = sorted({r.get("effort") for r in directional if r.get("effort")})
        if len(efforts) > 1:
            acc["by_effort"] = {e: _rate([r for r in directional if r.get("effort") == e], h,
                                         lambda r, h=h: _hit(r, h)) for e in efforts}
        # Did the risk rules filter out losers or winners? Compare hypothetical R of both groups.
        opening = [r for r in directional if h in r["outcome"]["r"]]
        approved = [r["outcome"]["r"][h] for r in opening if r.get("approved")]
        rejected = [r["outcome"]["r"][h] for r in opening if not r.get("approved")]
        acc["avg_r_approved"] = {"n": len(approved), "avg_r": _avg(approved)}
        acc["avg_r_rejected"] = {"n": len(rejected), "avg_r": _avg(rejected)}
        moves = [abs(r["outcome"]["returns"][h]) for r in holds if h in r["outcome"]["returns"]]
        acc["holds"] = {"n": len(moves), "avg_abs_move": _avg(moves),
                        "big_moves_missed": sum(1 for m in moves if m >= config.BIG_MOVE_PCT)}
        report["accuracy"][h] = acc

    total_cost = round(sum(r.get("cost_usd", 0) for r in cost_rows), 4)
    by_kind: dict[str, float] = defaultdict(float)
    for r in cost_rows:
        by_kind[r.get("kind", "?")] += r.get("cost_usd", 0)
    report["costs"] = {"total_usd": total_cost, "by_kind": {k: round(v, 4) for k, v in by_kind.items()},
                       "model_calls": len(cost_rows)}

    if equity and len(equity) >= 2 and usable:
        # Daily equity points are labeled with their trading day. Noon-shifting the UTC timestamp
        # gives the right day whether the label sits at midnight New York time or midnight UTC.
        points = [((as_utc(t) + timedelta(hours=12)).date(), e) for t, e in equity]
        first_day = et_date(min(as_utc(datetime.fromisoformat(r["time"])) for r in usable))
        before = [p for p in points if p[0] < first_day]
        series = before[-1:] + [p for p in points if p[0] >= first_day]   # anchor: the close before day one
        if len(series) >= 2:
            pnl = series[-1][1] - series[0][1]
            report["portfolio"] = {"from": str(series[0][0]), "to": str(series[-1][0]),
                                   "return": series[-1][1] / series[0][1] - 1, "pnl_usd": round(pnl, 2),
                                   "pnl_after_api_costs_usd": round(pnl - total_cost, 2)}
            spy_days = regular_sessions(fetch_bars("SPY", session_open_utc(series[0][0]) - timedelta(days=3)))
            spy = [bars[-1].close for d, bars in spy_days.items() if series[0][0] <= d <= series[-1][0]]
            if len(spy) >= 2:
                report["portfolio"]["spy_buy_and_hold_return"] = spy[-1] / spy[0] - 1
    return report


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _line(label: str, stat: dict) -> str:
    if not stat["n"]:
        return f"- {label}: no scored observations yet"
    lo, hi = stat["ci95"]
    noise = "  <- noise, not a result" if stat["n"] < config.MIN_SAMPLE else ""
    return f"- {label}: {_pct(stat['rate'])} ({stat['hits']}/{stat['n']}, 95% CI {_pct(lo)} to {_pct(hi)}){noise}"


def to_markdown(report: dict) -> str:
    out = ["# Scorecard", "",
           f"Generated {report['generated']}. {report['decisions_total']} journal entries, "
           f"{report['scored_observations']} scored observations (one per symbol, day and action), "
           f"{report['analyst_errors']} analyst errors.", "",
           "A hit = the price moved the way the thesis said, measured from the decision price to the close "
           f"{', '.join(str(h) for h in report['horizons'])} full session(s) later. Below "
           f"{config.MIN_SAMPLE} observations a hit rate is noise.", ""]
    for h, acc in report["accuracy"].items():
        out += [f"## {h} session(s) ahead", "",
                _line("Directional hit rate (buy + sell)", acc["directional"]),
                _line("Buys", acc["buys"]),
                _line("Sells", acc["sells"]),
                _line("Baseline: stock went up from the same moments (always-buy)", acc["always_buy_baseline"])]
        for c, stat in acc["by_confidence"].items():
            if stat["n"]:
                out.append(_line(f"Confidence {c}", stat))
        for e, stat in acc.get("by_effort", {}).items():
            out.append(_line(f"Effort {e}", stat))
        ap, rj = acc["avg_r_approved"], acc["avg_r_rejected"]
        fmt = lambda x: "n/a" if x is None else f"{x:+.2f}R"
        out.append(f"- Hypothetical average R: approved {fmt(ap['avg_r'])} (n={ap['n']}), "
                   f"rejected {fmt(rj['avg_r'])} (n={rj['n']})")
        if ap["avg_r"] is not None and rj["avg_r"] is not None and rj["avg_r"] > ap["avg_r"]:
            if min(ap["n"], rj["n"]) >= 10:
                out.append("  - Rejected ideas did better than approved ones: the rules may be filtering out winners.")
            else:
                out.append("  - Rejected ideas did better so far, but with this few trades that is noise.")
        hd = acc["holds"]
        if hd["n"]:
            out.append(f"- Holds: {hd['n']}, average move afterwards {_pct(hd['avg_abs_move'])}, "
                       f"{hd['big_moves_missed']} moved {config.BIG_MOVE_PCT:.0%} or more")
        out.append("")
    c = report["costs"]
    out += ["## Costs", "", f"- Model calls: {c['model_calls']}, total ${c['total_usd']:.2f} "
            + "(" + ", ".join(f"{k} ${v:.2f}" for k, v in c["by_kind"].items()) + ")"]
    if "portfolio" in report:
        p = report["portfolio"]
        out += ["", "## Paper account vs SPY", "",
                f"- {p['from']} to {p['to']}: account {_pct(p['return'])} (${p['pnl_usd']:,.2f}), "
                f"after API costs ${p['pnl_after_api_costs_usd']:,.2f}. Taxes not included.",
                f"- SPY buy-and-hold over the same days: {_pct(p.get('spy_buy_and_hold_return'))}"]
    return "\n".join(out) + "\n"


def write(report: dict, directory: Path | None = None) -> Path:
    directory = Path(directory or config.REPORTS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scorecard.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    path = directory / "scorecard.md"
    path.write_text(to_markdown(report), encoding="utf-8")
    return path
