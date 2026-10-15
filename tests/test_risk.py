import pytest

import config
from conftest import make_thesis
from tradebot.risk import Account, check_trade


def acct(**kw):
    base = dict(equity=100_000.0, day_pnl=0.0, open_positions=0)
    base.update(kw)
    return Account(**base)


def test_article_worked_example():
    # $100,000 account, buy at $180 with a $170 stop: 1% risk allows 100 shares,
    # 5% size allows 27, the engine takes 27.
    approved, qty, reason = check_trade(make_thesis(), acct(), 180.0)
    assert (approved, qty) == (True, 27)


def test_daily_loss_limit_blocks_new_trades():
    v = check_trade(make_thesis(), acct(equity=97_000.0, day_pnl=-3_000.0), 180.0)
    assert not v.approved and "Daily loss limit" in v.reason


def test_exits_still_allowed_after_daily_loss_limit():
    a = acct(equity=97_000.0, day_pnl=-3_000.0, open_positions=1, held={"AAPL": 27})
    v = check_trade(make_thesis(action="sell", confidence="medium", stop_loss=180.0), a, 175.0)
    assert v.approved and v.intent == "close_long" and v.qty == 27


def test_exit_needs_at_least_medium_confidence():
    v = check_trade(make_thesis(action="sell", confidence="low"), acct(held={"AAPL": 27}, open_positions=1), 175.0)
    assert not v.approved


@pytest.mark.parametrize("symbol", ["TSLA", "GME"])
def test_symbol_not_on_allowed_list(symbol):
    v = check_trade(make_thesis(symbol=symbol), acct(), 180.0)
    assert not v.approved and "not on the allowed list" in v.reason


def test_thesis_for_a_different_symbol_than_analyzed():
    # e.g. an injected headline convinced the model to talk about NVDA while analyzing AAPL
    v = check_trade(make_thesis(symbol="NVDA"), acct(), 180.0, expected_symbol="AAPL")
    assert not v.approved and "AAPL was analyzed" in v.reason


def test_hold_never_trades():
    assert not check_trade(make_thesis(action="hold"), acct(), 180.0).approved


def test_only_high_confidence_opens():
    v = check_trade(make_thesis(confidence="medium"), acct(), 180.0)
    assert not v.approved and "high-confidence" in v.reason


def test_max_open_positions():
    v = check_trade(make_thesis(), acct(open_positions=config.MAX_OPEN_POSITIONS), 180.0)
    assert not v.approved and "Maximum open positions" in v.reason


def test_no_adding_to_an_existing_position():
    # The article's loop would buy another 5% every 15 minutes while the model keeps saying buy.
    v = check_trade(make_thesis(), acct(open_positions=1, held={"AAPL": 27}), 180.0)
    assert not v.approved and "No adding" in v.reason


def test_no_adding_while_an_entry_is_in_flight():
    v = check_trade(make_thesis(), acct(open_positions=1, pending={"AAPL"}), 180.0)
    assert not v.approved


@pytest.mark.parametrize("stop", [180.0, 185.0])
def test_buy_stop_must_be_below_entry(stop):
    assert not check_trade(make_thesis(stop_loss=stop), acct(), 180.0).approved


def test_stop_distance_bounds():
    assert "only" in check_trade(make_thesis(stop_loss=179.9), acct(), 180.0).reason      # 0.06% away
    assert "maximum" in check_trade(make_thesis(stop_loss=120.0), acct(), 180.0).reason   # 33% away


def test_sell_with_no_position_is_not_a_short_by_default():
    v = check_trade(make_thesis(action="sell", stop_loss=190.0), acct(), 180.0)
    assert not v.approved and "Shorting is disabled" in v.reason


def test_shorts_when_enabled_need_a_stop_above_entry(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_SHORTS", True)
    bad = check_trade(make_thesis(action="sell", stop_loss=170.0), acct(), 180.0)
    assert not bad.approved and "above entry" in bad.reason
    good = check_trade(make_thesis(action="sell", stop_loss=190.0), acct(), 180.0)
    assert good.approved and good.intent == "open_short" and good.qty == 27


def test_position_too_small():
    v = check_trade(make_thesis(stop_loss=850.0), acct(equity=1_000.0), 900.0)
    assert not v.approved and "too small" in v.reason


def test_casing_does_not_matter():
    v = check_trade(make_thesis(action="BUY", confidence="High", symbol="aapl"), acct(), 180.0, expected_symbol="AAPL")
    assert v.approved and v.qty == 27


def test_blocked_account():
    assert not check_trade(make_thesis(), acct(blocked=True), 180.0).approved
