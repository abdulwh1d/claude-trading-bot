"""AlpacaBroker against a local fake Alpaca API, through the real alpaca-py client.

Checks that every call the bot makes hits the right endpoint with the right
parameters, and that Alpaca-shaped responses parse into what the bot expects.
Skipped when alpaca-py isn't installed.
"""
import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("alpaca")

from alpaca.data.enums import DataFeed  # noqa: E402
from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.historical.news import NewsClient  # noqa: E402
from alpaca.trading.client import TradingClient  # noqa: E402

from tradebot.broker import AlpacaBroker  # noqa: E402

T = "2026-03-03T15:00:00Z"
ASSET = "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415"
OPEN = {"new", "accepted", "held", "pending_new", "partially_filled"}


def order(oid, symbol="AAPL", side="buy", type_="market", status="new", order_class="oto", stop=None, qty="27"):
    return {"id": oid, "client_order_id": f"c-{oid[:8]}", "created_at": T, "updated_at": T, "submitted_at": T,
            "filled_at": None, "expired_at": None, "canceled_at": None, "failed_at": None, "replaced_at": None,
            "replaced_by": None, "replaces": None, "asset_id": ASSET, "symbol": symbol, "asset_class": "us_equity",
            "notional": None, "qty": qty, "filled_qty": "0", "filled_avg_price": None, "order_class": order_class,
            "order_type": type_, "type": type_, "side": side, "time_in_force": "gtc", "limit_price": None,
            "stop_price": stop, "status": status, "extended_hours": False, "legs": None, "trail_percent": None,
            "trail_price": None, "hwm": None, "position_intent": None}


def position(symbol, qty, side, avg="180.00"):
    return {"asset_id": ASSET, "symbol": symbol, "exchange": "NASDAQ", "asset_class": "us_equity",
            "asset_marginable": True, "avg_entry_price": avg, "qty": qty, "side": side, "market_value": "4900.00",
            "cost_basis": "4860.00", "unrealized_pl": "40.00", "unrealized_plpc": "0.008",
            "unrealized_intraday_pl": "40.00", "unrealized_intraday_plpc": "0.008", "current_price": "181.48",
            "lastday_price": "179.00", "change_today": "0.01", "qty_available": "0"}


class FakeAlpaca(BaseHTTPRequestHandler):
    state: dict = {}

    def _send(self, code, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(code)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = None
        if self.headers.get("Content-Length"):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.loads(raw) if raw else None
        s = self.state
        s["log"].append((method, url.path, q, body))
        p = url.path
        if p == "/v2/clock":
            return self._send(200, {"timestamp": "2026-03-03T10:00:00.123456-05:00", "is_open": True,
                                    "next_open": "2026-03-04T09:30:00-05:00", "next_close": "2026-03-03T16:00:00-05:00"})
        if p == "/v2/account":
            return self._send(200, {"id": ASSET, "account_number": "PA1", "status": "ACTIVE", "currency": "USD",
                                    "cash": "95000", "equity": "100000", "last_equity": "99000",
                                    "trading_blocked": False, "account_blocked": False, "shorting_enabled": True})
        if p == "/v2/positions" and method == "GET":
            return self._send(200, s["positions"])
        if p == "/v2/positions" and method == "DELETE":
            out = [{"symbol": x["symbol"], "status": 200, "body": order(str(uuid.uuid4()), x["symbol"], "sell")}
                   for x in s["positions"]]
            s["positions"] = []
            return self._send(207, out)
        if p.startswith("/v2/positions/") and method == "DELETE":
            sym = p.rsplit("/", 1)[1]
            s["positions"] = [x for x in s["positions"] if x["symbol"] != sym]
            return self._send(200, order(str(uuid.uuid4()), sym, "sell"))
        if p == "/v2/orders" and method == "GET":
            syms = set(q.get("symbols", "").split(",")) - {""}
            return self._send(200, [o for o in s["orders"] if o["status"] in OPEN and (not syms or o["symbol"] in syms)])
        if p == "/v2/orders" and method == "POST":
            o = order(str(uuid.uuid4()), body["symbol"], body["side"], body["type"], order_class=body.get("order_class", "simple"),
                      stop=body.get("stop_price"), qty=str(body["qty"]))
            s["orders"].append(o)
            return self._send(200, o)
        if p.startswith("/v2/orders/"):
            oid = p.rsplit("/", 1)[1]
            o = next(o for o in s["orders"] if o["id"] == oid)
            if method == "DELETE":
                o["status"] = "canceled"
                return self._send(204)
            return self._send(200, o)
        if p == "/v2/account/portfolio/history":
            return self._send(200, {"timestamp": [1772427600, 1772514000], "equity": [99000.0, 100000.0],
                                    "profit_loss": [0.0, 1000.0], "profit_loss_pct": [0.0, 0.0101],
                                    "base_value": 99000.0, "timeframe": "1D", "cashflow": {}})
        if p == "/v2/stocks/trades/latest":
            trade = {"t": "2026-03-03T14:59:30Z", "x": "V", "p": 181.48, "s": 100, "c": ["@"], "i": 1, "z": "C"}
            return self._send(200, {"trades": {sym: trade for sym in q["symbols"].split(",")}})
        if p == "/v2/stocks/bars":
            bars = [{"t": "2026-03-02T05:00:00Z", "o": 179, "h": 182, "l": 178, "c": 181, "v": 1000, "n": 9, "vw": 180}]
            return self._send(200, {"bars": {sym: bars for sym in q["symbols"].split(",")}, "next_page_token": None})
        if p == "/v1beta1/news":
            return self._send(200, {"news": [{"id": 24843171, "headline": "Apple raises guidance", "author": "Newsdesk",
                                              "created_at": T, "updated_at": T, "summary": "Raised.", "content": "",
                                              "url": "https://example.com/a", "images": [], "symbols": ["AAPL"],
                                              "source": "benzinga"}], "next_page_token": None})
        return self._send(404, {"code": 40410000, "message": f"no route {method} {p}"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def log_message(self, *args):
        pass


@pytest.fixture
def broker():
    FakeAlpaca.state = {"log": [], "orders": [order(str(uuid.uuid4()), "AAPL", "sell", "stop", "new", "oto", "170.00")],
                        "positions": [position("AAPL", "27", "long"), position("MSFT", "-5", "short", "400.00")]}
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAlpaca)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    b = AlpacaBroker.__new__(AlpacaBroker)
    b.trading = TradingClient("key", "secret", paper=True, url_override=base)
    b.data = StockHistoricalDataClient("key", "secret", url_override=base)
    b.news_client = NewsClient("key", "secret", url_override=base)
    b.feed = DataFeed.IEX
    yield b
    server.shutdown()


def test_state_reads(broker):
    clock = broker.clock()
    assert clock.is_open and clock.now.tzinfo is not None
    assert clock.next_close == datetime(2026, 3, 3, 21, 0, tzinfo=timezone.utc)
    pos = {p.symbol: p.qty for p in broker.positions()}
    assert pos == {"AAPL": 27.0, "MSFT": -5.0}                       # shorts come back negative
    acct = broker.account()
    assert (acct.equity, acct.day_pnl, acct.open_positions) == (100_000.0, 1_000.0, 2)
    stop = broker.open_orders("AAPL")[0]
    assert (stop.type, stop.side, stop.stop_price) == ("stop", "sell", 170.0)


def test_market_data_and_news(broker):
    price, when = broker.latest_trade("AAPL")
    assert price == 181.48 and when.tzinfo is not None
    log = FakeAlpaca.state["log"]
    assert log[-1][2]["feed"] == "iex"
    bars = broker.bars("AAPL", "1Day", datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert bars[0].close == 181 and log[-1][2]["timeframe"] == "1Day"
    broker.bars("AAPL", "15Min", datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert log[-1][2]["timeframe"] == "15Min"
    news = broker.news("AAPL", datetime(2026, 3, 2, tzinfo=timezone.utc))
    assert news[0].id == "24843171" and news[0].headline == "Apple raises guidance"
    assert log[-1][1] == "/v1beta1/news" and log[-1][2]["symbols"] == "AAPL" and log[-1][2]["limit"] == "8"
    assert broker.portfolio_history()[-1][1] == 100_000.0


def test_orders(broker):
    broker.submit_entry("NVDA", "buy", 10, 115.004, 130.0)
    sent = FakeAlpaca.state["log"][-1]
    assert sent[:2] == ("POST", "/v2/orders")
    body = sent[3]
    assert body["order_class"] == "bracket" and body["time_in_force"] == "gtc" and body["type"] == "market"
    assert body["stop_loss"] == {"stop_price": 115.0} and body["take_profit"] == {"limit_price": 130.0}

    broker.place_stop("AAPL", 27, "sell", 170.0)
    body = FakeAlpaca.state["log"][-1][3]
    assert (body["type"], body["stop_price"], body["time_in_force"]) == ("stop", 170.0, "gtc")

    broker.close_position("AAPL")    # cancels the AAPL stops first, then closes
    calls = [(m, p) for m, p, _, _ in FakeAlpaca.state["log"]]
    first_delete = calls.index(next(c for c in calls if c[0] == "DELETE"))
    assert calls[first_delete][1].startswith("/v2/orders/") and ("DELETE", "/v2/positions/AAPL") in calls

    broker.flatten_all()
    method, path, query, _ = FakeAlpaca.state["log"][-1]
    assert (method, path) == ("DELETE", "/v2/positions") and query.get("cancel_orders") == "True"
    assert broker.positions() == []


def test_full_cycle_through_both_real_sdks(broker, monkeypatch):
    """One real session cycle: alpaca-py -> fake Alpaca, anthropic SDK -> fake Claude API."""
    anthropic = pytest.importorskip("anthropic")
    import re

    from tradebot.analyst import Analyst
    from tradebot.loop import Context, run_cycle

    class FakeClaude(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            symbol = re.search(r"Symbol: (\w+)", body["messages"][0]["content"]).group(1)
            thesis = {"symbol": symbol, "action": "Buy" if symbol == "NVDA" else "hold",
                      "confidence": "high" if symbol == "NVDA" else "low", "entry_reason": "test",
                      "stop_loss": 175.0 if symbol == "NVDA" else 181.48, "invalidation": "test", "sources": []}
            reply = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                "content": [{"type": "text", "text": json.dumps(thesis)}], "stop_reason": "end_turn",
                                "stop_sequence": None, "usage": {"input_tokens": 1000, "output_tokens": 500}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    claude = ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
    threading.Thread(target=claude.serve_forever, daemon=True).start()
    try:
        client = anthropic.Anthropic(api_key="test", base_url=f"http://127.0.0.1:{claude.server_port}", max_retries=0)
        ctx = Context(broker=broker, analyst=Analyst(client), client=None)
        assert run_cycle(ctx)
    finally:
        claude.shutdown()

    rows = {r["symbol"]: r for r in ctx.journal.decisions()}
    assert set(rows) == {"AAPL", "MSFT", "NVDA", "SPY", "QQQ"}
    nvda = rows["NVDA"]
    assert nvda["approved"] and nvda["qty"] == 27 and nvda["order_id"] and nvda["thesis"]["action"] == "buy"
    posted = [b for m, p, _, b in FakeAlpaca.state["log"] if (m, p) == ("POST", "/v2/orders")]
    assert len(posted) == 1 and posted[0]["symbol"] == "NVDA" and posted[0]["order_class"] == "bracket"
    assert posted[0]["stop_loss"]["stop_price"] == 175.0
    assert rows["AAPL"]["risk_reason"] == "Model recommends no trade."
    events = {e["event"] for e in ctx.journal.events()}
    assert "unprotected_position" in events          # the short MSFT position has no stop and no journaled entry
    assert ctx.ledger.total() == pytest.approx(5 * (1000 * 4 + 500 * 20) / 1e6)
