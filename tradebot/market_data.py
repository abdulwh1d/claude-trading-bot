"""get_inputs: recent price bars from the broker plus headlines.

Keep the inputs tight. Give the model the specific, recent information it
needs, not an entire day of noise. Arithmetic (returns, ranges, ATR) is done
here in code; models are not reliable at arithmetic under pressure.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import config

from .models import Bar, Inputs, NewsItem
from .timeutil import ET, et_date


def _pct(a: float, b: float) -> float:
    return a / b - 1 if b else 0.0


def atr(bars: list[Bar], n: int = 14) -> float | None:
    """Average true range over the last n completed sessions."""
    if len(bars) < n + 1:
        return None
    window = bars[-(n + 1):]
    true_ranges = [max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
                   for prev, cur in zip(window, window[1:])]
    return sum(true_ranges) / n


def format_market_data(symbol: str, price: float, price_time: datetime, daily: list[Bar], now: datetime,
                       context_line: str | None = None) -> tuple[str, float | None]:
    today = et_date(now)
    history = [b for b in daily if et_date(b.t) < today]          # completed sessions only
    today_bar = next((b for b in daily if et_date(b.t) == today), None)
    lines = [f"{symbol} last trade: {price:.2f} at {price_time.astimezone(ET):%Y-%m-%d %H:%M} ET "
             f"({config.DATA_FEED.upper()} feed)"]
    if history:
        lines.append(f"Change since prior close ({history[-1].close:.2f}): {_pct(price, history[-1].close):+.2%}")
    if today_bar:
        lines.append(f"Today so far: open {today_bar.open:.2f}, high {today_bar.high:.2f}, low {today_bar.low:.2f}")
    closes = [b.close for b in history]
    for n in (5, 20):
        if len(closes) >= n:
            lines.append(f"Return since the close {n} sessions ago: {_pct(price, closes[-n]):+.2%}")
    recent = history[-20:]
    if recent:
        lines.append(f"{len(recent)}-session range: {min(b.low for b in recent):.2f} to {max(b.high for b in recent):.2f}")
    atr14 = atr(history)
    if atr14:
        lines.append(f"ATR(14): {atr14:.2f} ({atr14 / price:.2%} of price)")
    if today_bar and recent:
        avg_vol = sum(b.volume for b in recent) / len(recent)
        if avg_vol:
            lines.append(f"Volume so far today vs 20-session average: {today_bar.volume / avg_vol:.2f}x")
    if closes:
        lines.append("Last 10 closes, oldest first: " + ", ".join(f"{c:.2f}" for c in closes[-10:]))
    if context_line:
        lines.append(context_line)
    return "\n".join(lines), atr14


def format_news(item: NewsItem, max_chars: int = 600) -> str:
    summary = " ".join((item.summary or "").split())
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "..."
    stamp = item.created_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
    return f"[news id {item.id} | {stamp} | {item.source}]\n{item.headline}\n{summary}".strip()


def daily_history(broker, symbol: str, now: datetime) -> list[Bar]:
    return broker.bars(symbol, "1Day", start=now - timedelta(days=int(config.DAILY_BARS * 1.6) + 5))


def market_context(broker, now: datetime) -> str | None:
    """One line of market context: SPY's move today."""
    try:
        price, _ = broker.latest_trade("SPY")
        history = [b for b in daily_history(broker, "SPY", now) if et_date(b.t) < et_date(now)]
        if history:
            return f"Market context: SPY {_pct(price, history[-1].close):+.2%} since prior close"
    except Exception:
        return None
    return None


def get_inputs(broker, symbol: str, now: datetime, context_line: str | None = None) -> Inputs:
    price, price_time = broker.latest_trade(symbol)
    daily = daily_history(broker, symbol, now)
    news = broker.news(symbol, start=now - timedelta(hours=config.NEWS_LOOKBACK_HOURS),
                       limit=config.MAX_NEWS_ITEMS)
    market_data, atr14 = format_market_data(symbol, price, price_time, daily, now, context_line)
    return Inputs(
        symbol=symbol, price=price, price_time=price_time, market_data=market_data,
        news_items=[format_news(n) for n in news], news_ids=[n.id for n in news],
        headlines=[n.headline for n in news], atr=atr14,
    )
