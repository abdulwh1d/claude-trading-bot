from datetime import datetime, timedelta, timezone

import config
from conftest import make_thesis
from tradebot.analyst import AnalysisResult
from tradebot.costs import CostLedger
from tradebot.executor import PendingTrades
from tradebot.journal import Journal
from tradebot.loop import Context, protect_positions, run_cycle, should_analyze
from tradebot.models import ClockInfo, NewsItem, OrderInfo, PositionInfo
from tradebot.risk import Account
from tradebot.state import State

NOW = datetime(2026, 3, 3, 15, 0, tzinfo=timezone.utc)   # 10:00 New York time, a Tuesday


class StubBroker:
    def __init__(self, price=180.0):
        self.now, self.price, self.is_open = NOW, price, True
        self.pos: dict[str, PositionInfo] = {}
        self.orders: list[OrderInfo] = []
        self.news_items: dict[str, list[NewsItem]] = {}
        self.submitted, self.stops, self.canceled, self.closed = [], [], [], []
        self.flattened = False

    def clock(self):
        return ClockInfo(self.now, self.is_open, self.now + timedelta(days=1), self.now + timedelta(hours=6))

    def latest_trade(self, symbol):
        return self.price, self.now - timedelta(minutes=1)

    def bars(self, symbol, timeframe, start, end=None):
        return []

    def news(self, symbol, start, limit=8, include_content=False):
        return self.news_items.get(symbol, [])

    def positions(self):
        return list(self.pos.values())

    def open_orders(self, symbol=None):
        return [o for o in self.orders if symbol is None or o.symbol == symbol]

    def account(self):
        held = {s: p.qty for s, p in self.pos.items()}
        pending = {o.symbol for o in self.orders} - set(held)
        return Account(100_000.0, 0.0, len(held) + len(pending), held, pending)

    def submit_entry(self, symbol, side, qty, stop, take_profit=None, client_order_id=None):
        self.submitted.append((symbol, side, qty, stop, take_profit))
        self.pos[symbol] = PositionInfo(symbol, qty, self.price)
        self.orders.append(OrderInfo(f"stop-{symbol}", symbol, "sell", "stop", qty, "new", stop))
        return f"order-{symbol}"

    def place_stop(self, symbol, qty, side, stop):
        self.stops.append((symbol, qty, side, stop))
        self.orders.append(OrderInfo(f"new-stop-{symbol}", symbol, side, "stop", qty, "new", stop))
        return f"new-stop-{symbol}"

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        self.orders = [o for o in self.orders if o.id != order_id]

    def close_position(self, symbol):
        self.closed.append(symbol)
        self.pos.pop(symbol, None)
        self.orders = [o for o in self.orders if o.symbol != symbol]
        return f"close-{symbol}"

    def flatten_all(self):
        self.flattened = True
        self.pos.clear()
        self.orders.clear()


class ScriptedAnalyst:
    model, effort = "claude-opus-5-5", "medium"

    def __init__(self, decide):
        self.decide, self.calls = decide, []

    def analyze(self, symbol, market_data, news_items, position_note="none", brief=None, when=None):
        self.calls.append(symbol)
        thesis = self.decide(symbol)
        if isinstance(thesis, Exception):
            return AnalysisResult(None, self.model, self.effort, error=str(thesis))
        return AnalysisResult(thesis, self.model, self.effort, "end_turn", {"input_tokens": 1, "output_tokens": 1}, 0.0)


def context(broker, decide):
    return Context(broker=broker, analyst=ScriptedAnalyst(decide), journal=Journal(), state=State(),
                   ledger=CostLedger(), pending=PendingTrades(), now=lambda: broker.now)


def hold(symbol):
    return make_thesis(symbol=symbol, action="hold", confidence="low", stop_loss=180.0)


def buy_aapl(symbol):
    return make_thesis(symbol=symbol) if symbol == "AAPL" else hold(symbol)


def test_kill_switch_flattens_and_stops():
    broker = StubBroker()
    ctx = context(broker, hold)
    config.KILL_FILE.write_text("stop")
    assert run_cycle(ctx) is False
    assert broker.flattened and ctx.analyst.calls == []
    assert any(e["event"] == "kill_switch" for e in ctx.journal.events())


def test_every_hold_is_journaled():
    ctx = context(StubBroker(), hold)
    assert run_cycle(ctx)
    rows = ctx.journal.decisions()
    assert [r["symbol"] for r in rows] == config.WATCHLIST
    assert all(not r["approved"] and r["risk_reason"] == "Model recommends no trade." for r in rows)
    assert all(r["price"] == 180.0 and r["model"] == "claude-opus-5-5" for r in rows)   # what it read


def test_buy_is_sized_and_never_stacked():
    broker = StubBroker()
    ctx = context(broker, buy_aapl)
    run_cycle(ctx)
    assert broker.submitted == [("AAPL", "buy", 27, 170.0, 200.0)]
    # 15 minutes later a new headline arrives and the model says "buy" again.
    broker.now += timedelta(minutes=15)
    broker.news_items["AAPL"] = [NewsItem("9", "Apple again", "", "wire", broker.now - timedelta(minutes=5))]
    run_cycle(ctx)
    assert len(broker.submitted) == 1
    last = ctx.journal.decisions()[-1]
    assert last["symbol"] == "AAPL" and "No adding" in last["risk_reason"]


def test_hijacked_analyst_cannot_trade():
    broker = StubBroker()
    run_cycle(context(broker, lambda s: make_thesis(symbol="TSLA")))
    assert broker.submitted == []


def test_identical_inputs_are_not_reanalyzed():
    broker = StubBroker()
    ctx = context(broker, hold)
    run_cycle(ctx)
    broker.now += timedelta(minutes=15)
    run_cycle(ctx)
    assert len(ctx.analyst.calls) == len(config.WATCHLIST)


def test_should_analyze_rules():
    state = State()
    inputs = type("I", (), {"news_ids": ["1"], "price": 100.0})()
    assert should_analyze(state, "AAPL", inputs, NOW)[0]                       # first look
    state.set("last_analysis", {"AAPL": {"time": NOW.isoformat(), "price": 100.0, "news_ids": ["1"]}})
    assert not should_analyze(state, "AAPL", inputs, NOW + timedelta(minutes=15))[0]
    inputs.price = 101.5
    assert should_analyze(state, "AAPL", inputs, NOW + timedelta(minutes=15))[1] == "price moved"
    inputs.price, inputs.news_ids = 100.0, ["1", "2"]
    assert "new headline" in should_analyze(state, "AAPL", inputs, NOW + timedelta(minutes=15))[1]
    inputs.news_ids = ["1"]
    assert should_analyze(state, "AAPL", inputs, NOW + timedelta(minutes=config.REANALYZE_MAX_AGE_MIN))[0]


def _journal_entry_with_stop(journal, stop=170.0):
    journal.decision(symbol="AAPL", thesis=make_thesis(stop_loss=stop), approved=True, qty=27, reason="ok",
                     intent="open_long", order_id="order-AAPL")


def test_missing_stop_is_reattached():
    broker = StubBroker()
    broker.pos["AAPL"] = PositionInfo("AAPL", 27, 180.0)
    broker.orders.append(OrderInfo("tp", "AAPL", "sell", "limit", 27, "new"))   # a lone target, no stop
    ctx = context(broker, hold)
    _journal_entry_with_stop(ctx.journal)
    protect_positions(ctx)
    assert broker.stops == [("AAPL", 27, "sell", 170.0)] and broker.canceled == ["tp"]


def test_breached_stop_closes_the_position():
    broker = StubBroker(price=165.0)
    broker.pos["AAPL"] = PositionInfo("AAPL", 27, 180.0)
    ctx = context(broker, hold)
    _journal_entry_with_stop(ctx.journal)
    protect_positions(ctx)
    assert broker.closed == ["AAPL"]


def test_orphan_exit_orders_are_canceled():
    broker = StubBroker()
    broker.orders.append(OrderInfo("old-stop", "MSFT", "sell", "stop", 10, "new", 400.0))
    protect_positions(context(broker, hold))
    assert broker.canceled == ["old-stop"]


def test_entry_in_flight_is_left_alone():
    broker = StubBroker()
    broker.orders += [OrderInfo("entry", "MSFT", "buy", "market", 10, "accepted"),
                      OrderInfo("leg", "MSFT", "sell", "stop", 10, "held", 400.0)]
    protect_positions(context(broker, hold))
    assert broker.canceled == []


def test_daily_budget_stops_model_calls():
    broker = StubBroker()
    ctx = context(broker, hold)
    ctx.ledger.record(model="claude-opus-5-5", usage={"output_tokens": 10_000_000}, kind="analyst", when=NOW)
    run_cycle(ctx)
    assert ctx.analyst.calls == [] and any(e["event"] == "budget_hit" for e in ctx.journal.events())


def test_stale_prices_are_not_traded():
    broker = StubBroker()
    broker.latest_trade = lambda s: (180.0, NOW - timedelta(hours=2))
    ctx = context(broker, buy_aapl)
    run_cycle(ctx)
    assert ctx.analyst.calls == [] and broker.submitted == []


def test_analyst_failure_is_a_journaled_no_trade():
    broker = StubBroker()
    ctx = context(broker, lambda s: RuntimeError("overloaded"))
    run_cycle(ctx)
    row = ctx.journal.decisions()[0]
    assert row["thesis"] is None and "No thesis" in row["risk_reason"] and broker.submitted == []


def test_manual_approval_queues_instead_of_ordering(monkeypatch):
    monkeypatch.setattr(config, "MANUAL_APPROVAL", True)
    broker = StubBroker()
    ctx = context(broker, buy_aapl)
    run_cycle(ctx)
    assert broker.submitted == []
    items = ctx.pending.open_items(NOW)
    assert len(items) == 1 and items[0]["qty"] == 27
    assert "approve" in ctx.journal.decisions()[0]["risk_reason"]
    # Next cycle: the queued trade counts as in flight, so it isn't queued twice.
    broker.now += timedelta(minutes=15)
    broker.news_items["AAPL"] = [NewsItem("9", "Apple again", "", "wire", broker.now)]
    run_cycle(ctx)
    assert len(ctx.pending.open_items(broker.now)) == 1


def test_closed_market_runs_no_session_cycle():
    broker = StubBroker()
    broker.is_open = False
    ctx = context(broker, buy_aapl)
    assert run_cycle(ctx) and ctx.analyst.calls == []
