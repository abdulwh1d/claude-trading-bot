from types import SimpleNamespace

import pytest

import config
from conftest import make_thesis
from tradebot.executor import execute, take_profit_price
from tradebot.risk import Verdict


class RecordingBroker:
    def __init__(self):
        self.calls = []

    def submit_entry(self, *args):
        self.calls.append(("submit_entry", args))
        return "order-1"

    def close_position(self, symbol):
        self.calls.append(("close_position", symbol))
        return "order-2"


def test_take_profit_at_two_r():
    assert take_profit_price("buy", 180.0, 170.0) == 200.0
    assert take_profit_price("sell", 180.0, 190.0) == 160.0


def test_stop_only_when_take_profit_disabled(monkeypatch):
    monkeypatch.setattr(config, "TAKE_PROFIT_R", None)
    assert take_profit_price("buy", 180.0, 170.0) is None


def test_execute_entry_sends_stop_and_target():
    b = RecordingBroker()
    execute(b, make_thesis(stop_loss=170.004), Verdict(True, 27, "ok", "open_long"), 180.0)
    assert b.calls == [("submit_entry", ("AAPL", "buy", 27, 170.0, 200.0))]


def test_execute_exit_closes_the_position():
    b = RecordingBroker()
    execute(b, make_thesis(action="sell"), Verdict(True, 27, "ok", "close_long"), 180.0)
    assert b.calls == [("close_position", "AAPL")]


def test_alpaca_order_is_oto_or_bracket_with_gtc_stop():
    """Build the order with the real alpaca-py request models (no network)."""
    pytest.importorskip("alpaca")
    from alpaca.trading.enums import OrderClass, TimeInForce

    from tradebot.broker import AlpacaBroker

    sent = []
    broker = AlpacaBroker.__new__(AlpacaBroker)          # skip __init__: no keys, no network
    broker.trading = SimpleNamespace(submit_order=lambda order_data: sent.append(order_data) or SimpleNamespace(id="x"))

    broker.submit_entry("AAPL", "buy", 27, 170.0, None)
    oto = sent[-1]
    assert oto.order_class == OrderClass.OTO and oto.time_in_force == TimeInForce.GTC
    assert oto.stop_loss.stop_price == 170.0 and oto.take_profit is None and oto.qty == 27

    broker.submit_entry("AAPL", "buy", 27, 170.0, 200.0)
    bracket = sent[-1]
    assert bracket.order_class == OrderClass.BRACKET and bracket.take_profit.limit_price == 200.0
