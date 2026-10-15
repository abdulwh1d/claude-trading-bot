"""Plain data shapes shared by the real broker (Alpaca) and the offline simulator."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ClockInfo:
    now: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass
class Bar:
    t: datetime  # bar open time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class NewsItem:
    id: str
    headline: str
    summary: str
    source: str
    created_at: datetime
    url: str | None = None
    content: str = ""


@dataclass
class OrderInfo:
    id: str
    symbol: str
    side: str            # "buy" | "sell"
    type: str            # "market" | "stop" | "limit" | ...
    qty: float
    status: str
    stop_price: float | None = None
    order_class: str = ""


@dataclass
class PositionInfo:
    symbol: str
    qty: float           # signed: long > 0, short < 0
    avg_entry_price: float
    market_value: float = 0.0
    unrealized_pl: float = 0.0


@dataclass
class Inputs:
    """What the analyst reads for one symbol. Also journaled: 'what the system read'."""
    symbol: str
    price: float
    price_time: datetime
    market_data: str
    news_items: list[str]
    news_ids: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)
    atr: float | None = None
