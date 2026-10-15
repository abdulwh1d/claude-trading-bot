"""Alpaca: account state, market data, news, and orders (Steps 1 and 6).

Everything else in the bot talks to this class (or to the simulator's fake
broker, which has the same methods), so no other module imports alpaca-py.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

import config

from .models import Bar, ClockInfo, NewsItem, OrderInfo, PositionInfo
from .risk import Account
from .timeutil import as_utc

PROTECTIVE_TYPES = {"stop", "stop_limit", "trailing_stop"}
_DONE = {"canceled", "filled", "expired", "rejected", "done_for_day", "replaced"}


def _value(x):
    """Alpaca enums -> plain strings."""
    return getattr(x, "value", x)


class AlpacaBroker:
    def __init__(self):
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.news import NewsClient
        from alpaca.trading.client import TradingClient

        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (paper keys) in .env first.")
        if not config.PAPER:
            from .golive import assert_live_allowed
            assert_live_allowed()
        # paper=True is the most important line in this entire build.
        self.trading = TradingClient(key, secret, paper=config.PAPER)
        self.data = StockHistoricalDataClient(key, secret)
        self.news_client = NewsClient(key, secret)
        self.feed = DataFeed(config.DATA_FEED)

    # ------------------------------------------------------------------ state
    def clock(self) -> ClockInfo:
        c = self.trading.get_clock()
        return ClockInfo(now=as_utc(c.timestamp), is_open=bool(c.is_open),
                         next_open=as_utc(c.next_open), next_close=as_utc(c.next_close))

    def positions(self) -> list[PositionInfo]:
        out = []
        for p in self.trading.get_all_positions():
            qty = abs(float(p.qty)) * (-1 if _value(p.side) == "short" else 1)
            out.append(PositionInfo(p.symbol, qty, float(p.avg_entry_price),
                                    float(p.market_value or 0), float(p.unrealized_pl or 0)))
        return out

    def open_orders(self, symbol: str | None = None) -> list[OrderInfo]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500,
                               symbols=[symbol] if symbol else None)
        return [OrderInfo(id=str(o.id), symbol=o.symbol, side=_value(o.side), type=_value(o.type or o.order_type),
                          qty=float(o.qty or 0), status=_value(o.status),
                          stop_price=float(o.stop_price) if o.stop_price else None,
                          order_class=_value(o.order_class) or "")
                for o in self.trading.get_orders(filter=req)]

    def account(self) -> Account:
        acct = self.trading.get_account()
        held = {p.symbol: p.qty for p in self.positions()}
        # An open order on a symbol we don't hold is an entry still in flight.
        pending = {o.symbol for o in self.open_orders()} - set(held)
        equity = float(acct.equity)
        return Account(
            equity=equity,
            day_pnl=equity - float(acct.last_equity),   # last_equity = equity at the previous close
            open_positions=len(held) + len(pending),
            held=held,
            pending=pending,
            blocked=bool(acct.trading_blocked or acct.account_blocked),
        )

    # ------------------------------------------------------------------- data
    def latest_trade(self, symbol: str) -> tuple[float, datetime]:
        from alpaca.data.requests import StockLatestTradeRequest

        trade = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.feed))[symbol]
        return float(trade.price), as_utc(trade.timestamp)

    def bars(self, symbol: str, timeframe: str, start: datetime, end: datetime | None = None) -> list[Bar]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf = TimeFrame.Day if timeframe == "1Day" else TimeFrame(15, TimeFrameUnit.Minute)
        barset = self.data.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=tf, start=start, end=end, feed=self.feed))
        return [Bar(as_utc(b.timestamp), b.open, b.high, b.low, b.close, b.volume)
                for b in barset.data.get(symbol, [])]

    def news(self, symbol: str, start: datetime, limit: int = config.MAX_NEWS_ITEMS,
             include_content: bool = False) -> list[NewsItem]:
        from alpaca.data.requests import NewsRequest

        newsset = self.news_client.get_news(NewsRequest(
            symbols=symbol, start=start, limit=limit, sort="desc", include_content=include_content))
        return [NewsItem(id=str(n.id), headline=n.headline, summary=n.summary or "", source=n.source,
                         created_at=as_utc(n.created_at), url=n.url, content=n.content or "")
                for n in newsset.data.get("news", [])]

    def portfolio_history(self) -> list[tuple[datetime, float]]:
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        h = self.trading.get_portfolio_history(GetPortfolioHistoryRequest(period="1A", timeframe="1D"))
        return [(datetime.fromtimestamp(t, tz=timezone.utc), float(e))
                for t, e in zip(h.timestamp, h.equity) if e]

    # ----------------------------------------------------------------- orders
    def submit_entry(self, symbol: str, side: str, qty: int, stop_price: float,
                     take_profit: float | None = None, client_order_id: str | None = None) -> str:
        """Market entry with the stop attached to the order itself (OTO), or stop + target (bracket),
        so the protection exists even if this program crashes a second later."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            # The article uses DAY. Alpaca users report the attached legs take the entry's time in
            # force, so a DAY stop can expire at the close and leave the position unprotected
            # overnight. Alpaca's own OTO and bracket examples use GTC. As a backstop,
            # protect_positions() in loop.py re-attaches any stop that goes missing.
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET if take_profit else OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)) if take_profit else None,
            client_order_id=client_order_id or f"bot-{uuid.uuid4().hex[:20]}",
        )
        return str(self.trading.submit_order(order_data=order).id)

    def place_stop(self, symbol: str, qty: float, side: str, stop_price: float) -> str:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest

        order = StopOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                 time_in_force=TimeInForce.GTC, stop_price=round(stop_price, 2))
        return str(self.trading.submit_order(order_data=order).id)

    def cancel_order(self, order_id: str, wait_seconds: float = 5) -> None:
        """Cancel and wait for it to land: a canceled order keeps its shares reserved until it's done."""
        self.trading.cancel_order_by_id(order_id)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if _value(self.trading.get_order_by_id(order_id).status) in _DONE:
                return
            time.sleep(0.25)

    def close_position(self, symbol: str) -> str:
        # The attached stop/target legs reserve the shares, so cancel them first or the close is rejected.
        for o in self.open_orders(symbol):
            self.trading.cancel_order_by_id(o.id)
        deadline = time.monotonic() + 10
        while self.open_orders(symbol) and time.monotonic() < deadline:
            time.sleep(0.5)
        return str(self.trading.close_position(symbol).id)

    def flatten_all(self) -> None:
        """Cancel every open order, then close every position."""
        self.trading.close_all_positions(cancel_orders=True)
