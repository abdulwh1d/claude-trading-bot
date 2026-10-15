from datetime import date, datetime, time, timedelta

import pytest

from conftest import make_thesis
from tradebot.journal import Journal
from tradebot.models import Bar
from tradebot.scorecard import build_report, simulate_r, to_markdown, wilson
from tradebot.timeutil import ET


def day_bars(d: date, close: float, low: float | None = None) -> list[Bar]:
    bars = []
    for k in range(26):
        t = datetime.combine(d, time(9, 30), tzinfo=ET) + timedelta(minutes=15 * k)
        lo = low if (low is not None and k == 13) else close
        bars.append(Bar(t, close, close, lo, close, 1000))
    return bars


DAYS = [date(2026, 3, 2) + timedelta(days=i) for i in range(12) if (date(2026, 3, 2) + timedelta(days=i)).weekday() < 5]


def test_wilson_interval():
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)


def test_simulate_r_paths():
    flat = Bar(datetime(2026, 3, 3, 15, tzinfo=ET), 100, 101, 99, 100, 1)
    assert simulate_r("buy", 100, 95, [flat], 103) == (pytest.approx(0.6), False)          # held to the horizon
    stopped = Bar(flat.t, 100, 112, 94, 100, 1)                                            # both touched: stop first
    assert simulate_r("buy", 100, 95, [stopped], 103) == (-1.0, True)
    gap = Bar(flat.t, 90, 91, 89, 90, 1)                                                    # gapped through the stop
    assert simulate_r("buy", 100, 95, [gap], 90) == (-2.0, True)
    target = Bar(flat.t, 100, 111, 99, 110, 1)
    assert simulate_r("buy", 100, 95, [target], 110) == (2.0, False)


def test_report_scores_forward_and_dedupes():
    closes = [100, 101, 99, 102, 103, 104, 105, 106]
    bars = [b for d, c in zip(DAYS, closes) for b in day_bars(d, c)]
    j = Journal()
    t0 = datetime.combine(DAYS[0], time(10, 0), tzinfo=ET)

    def add(t, thesis, price, approved=False):
        row = j.decision(symbol="AAPL", thesis=thesis, approved=approved, qty=0, reason="", when=t)
        row["price"] = price
        return row

    rows = [
        add(t0, make_thesis(stop_loss=95.0), 100.0, approved=True),           # buy: next close 101 -> hit
        add(t0 + timedelta(hours=1), make_thesis(stop_loss=95.0), 100.0),    # same day, same action: deduped
        add(t0 + timedelta(days=1), make_thesis(action="sell", confidence="medium", stop_loss=105.0), 101.0),  # 99 next -> hit
        add(t0 + timedelta(days=2), make_thesis(action="hold", confidence="low", stop_loss=99.0), 99.0),       # 102 next
    ]
    report = build_report(rows, lambda symbol, start: [b for b in bars if b.t >= start])
    acc = report["accuracy"][1]
    assert report["scored_observations"] == 3
    assert acc["directional"]["n"] == 2 and acc["directional"]["hits"] == 2
    assert acc["buys"]["hits"] == 1 and acc["sells"]["hits"] == 1
    assert acc["always_buy_baseline"]["n"] == 3 and acc["always_buy_baseline"]["hits"] == 2
    assert acc["holds"]["n"] == 1 and acc["holds"]["big_moves_missed"] == 1          # 99 -> 102 is +3%
    assert acc["avg_r_approved"]["n"] == 1 and acc["avg_r_approved"]["avg_r"] == pytest.approx(0.2)
    assert "noise" in to_markdown(report)


def test_report_compares_against_spy():
    bars = [b for d, c in zip(DAYS, [100, 101, 102, 103, 104, 105]) for b in day_bars(d, c)]
    j = Journal()
    row = j.decision(symbol="AAPL", thesis=make_thesis(), approved=False, qty=0, reason="",
                     when=datetime.combine(DAYS[1], time(10, 0), tzinfo=ET))
    row["price"] = 101.0
    label = lambda d: datetime.combine(d, time(0, 0), tzinfo=ET)   # Alpaca-style daily labels
    equity = [(label(DAYS[0]), 100_000.0), (label(DAYS[1]), 100_500.0), (label(DAYS[3]), 101_000.0)]
    report = build_report([row], lambda s, start: [b for b in bars if b.t >= start], [{"cost_usd": 10.0}], equity)
    p = report["portfolio"]
    assert p["from"] == str(DAYS[0]) and p["return"] == pytest.approx(0.01)
    assert p["pnl_after_api_costs_usd"] == 990.0
    assert p["spy_buy_and_hold_return"] == pytest.approx(103 / 100 - 1)
