"""Step 9: run the loop, around the clock.

During the session: every 15 minutes, check the kill switch, protect open
positions, and for each symbol with something new, get inputs -> thesis ->
risk check -> order -> journal. Fifteen-minute cycles are deliberate: a model
call takes seconds, and the edge here is discipline and coverage, not speed.

Outside the session the same loop does the rest of the 24/7 work: the
after-close review, the overnight research batch, and the pre-market brief.
"""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import config

from .broker import PROTECTIVE_TYPES
from .costs import CostLedger
from .executor import PendingTrades, execute
from .journal import Journal
from .killswitch import kill_switch_engaged
from .market_data import get_inputs, market_context
from .overnight import brief_text, collect_overnight, load_brief, submit_overnight
from .premarket import run_premarket
from .review import run_review
from .risk import Verdict, check_trade
from .state import State
from .timeutil import et_date, utcnow

log = logging.getLogger("bot")


@dataclass
class Context:
    broker: Any
    analyst: Any
    journal: Journal = field(default_factory=Journal)
    state: State = field(default_factory=State)
    ledger: CostLedger = field(default_factory=CostLedger)
    pending: PendingTrades = field(default_factory=PendingTrades)
    client: Any = None        # Anthropic client for the batch and the review (None in the simulator)
    edgar: Any = None         # SEC filings (None if SEC_USER_AGENT isn't set)
    now: Callable[[], datetime] = utcnow

    def position_note(self, symbol: str) -> str:
        """What the analyst is told about the current position, so "sell" means something."""
        pos = next((p for p in self.broker.positions() if p.symbol == symbol), None)
        if pos is None:
            return "none"
        side = "long" if pos.qty > 0 else "short"
        note = f"{side} {abs(pos.qty):g} shares at an average {pos.avg_entry_price:.2f}, unrealized {pos.unrealized_pl:+.2f} USD"
        stops = [o.stop_price for o in self.broker.open_orders(symbol) if o.type in PROTECTIVE_TYPES and o.stop_price]
        if stops:
            note += f"; protective stop at {stops[0]:.2f}"
        return note


def should_analyze(state: State, symbol: str, inputs, now: datetime, force: bool = False) -> tuple[bool, str]:
    """Only pay for a model call when something changed. The article re-analyzes every symbol every
    15 minutes: about 130 Opus calls a day for 5 symbols, mostly on identical inputs."""
    if force:
        return True, "flagged before the open"
    last = state.get("last_analysis", {}).get(symbol)
    if not last:
        return True, "first look"
    last_time = datetime.fromisoformat(last["time"])
    if et_date(last_time) != et_date(now):
        return True, "first look today"
    new = set(inputs.news_ids) - set(last.get("news_ids", []))
    if new:
        return True, f"{len(new)} new headline(s)"
    if last.get("price") and abs(inputs.price / last["price"] - 1) >= config.REANALYZE_MOVE_PCT:
        return True, "price moved"
    if now - last_time >= timedelta(minutes=config.REANALYZE_MAX_AGE_MIN):
        return True, "last thesis is getting old"
    return False, "nothing new"


def _remember(state: State, symbol: str, inputs, now: datetime) -> None:
    seen = state.get("last_analysis", {})
    seen[symbol] = {"time": now.isoformat(), "price": inputs.price, "news_ids": inputs.news_ids}
    state.set("last_analysis", seen)


def protect_positions(ctx: Context) -> None:
    """Every open position must have a stop at the broker, and no exit order may outlive its position."""
    orders = ctx.broker.open_orders()
    positions = ctx.broker.positions()
    held = {p.symbol for p in positions}

    # Orphans: exit orders left behind after their position closed (say, a target filled while a
    # re-attached stop was still open). A sell stop with no shares behind it would open a short.
    for symbol in {o.symbol for o in orders} - held:
        mine = [o for o in orders if o.symbol == symbol]
        if any(o.type == "market" for o in mine):
            continue   # an entry is still in flight; its legs are supposed to be waiting
        for o in mine:
            ctx.broker.cancel_order(o.id)
            ctx.journal.event("orphan_order_canceled", symbol=symbol, order_id=o.id, type=o.type)

    for pos in positions:
        exit_side = "sell" if pos.qty > 0 else "buy"
        covered = sum(o.qty for o in orders
                      if o.symbol == pos.symbol and o.side == exit_side and o.type in PROTECTIVE_TYPES)
        missing = abs(pos.qty) - covered
        if missing <= 0:
            continue
        stop = ctx.journal.last_entry_stop(pos.symbol)
        if stop is None:   # opened outside the bot: warn once a day instead of every cycle
            warned, today = ctx.state.get("unprotected_warned", {}), str(et_date(ctx.now()))
            if warned.get(pos.symbol) != today:
                ctx.journal.event("unprotected_position", symbol=pos.symbol, qty=pos.qty,
                                  note="No journaled stop: opened outside the bot? Protect it by hand.")
                ctx.state.set("unprotected_warned", {**warned, pos.symbol: today})
            continue
        price, _ = ctx.broker.latest_trade(pos.symbol)
        if (pos.qty > 0 and price <= stop) or (pos.qty < 0 and price >= stop):
            order_id = ctx.broker.close_position(pos.symbol)
            ctx.journal.event("stop_breached_closed", symbol=pos.symbol, stop=stop, price=price, order_id=order_id)
            continue
        # Cancel the old exit legs (a lone target) so the new stop isn't racing an order it can't see.
        for o in orders:
            if o.symbol == pos.symbol and o.side == exit_side:
                ctx.broker.cancel_order(o.id)
        order_id = ctx.broker.place_stop(pos.symbol, abs(pos.qty), exit_side, stop)
        ctx.journal.event("protective_stop_placed", symbol=pos.symbol, qty=abs(pos.qty), stop=stop, order_id=order_id)


def process_symbol(ctx: Context, symbol: str, account, now: datetime, context_line: str | None,
                   force: bool = False, phase: str = "session") -> dict | None:
    inputs = get_inputs(ctx.broker, symbol, now, context_line)
    go, why = should_analyze(ctx.state, symbol, inputs, now, force)
    if not go:
        log.debug("%s: skipped (%s)", symbol, why)
        return None
    if now - inputs.price_time > timedelta(minutes=config.STALE_PRICE_MINUTES):
        ctx.journal.event("stale_price", symbol=symbol, price_time=inputs.price_time.isoformat())
        return None
    if ctx.ledger.over_budget(now):
        if ctx.state.get("budget_hit") != str(et_date(now)):
            ctx.state.set("budget_hit", str(et_date(now)))
            ctx.journal.event("budget_hit", limit_usd=config.MAX_DAILY_LLM_USD)
        return None   # no thesis means no trade

    brief = brief_text(load_brief(symbol, str(et_date(now))))
    result = ctx.analyst.analyze(symbol, inputs.market_data, inputs.news_items,
                                 position_note=ctx.position_note(symbol), brief=brief, when=now)
    _remember(ctx.state, symbol, inputs, now)
    thesis = result.thesis
    if thesis is None:
        verdict = Verdict(False, 0, f"No thesis ({result.error}). No trade.")
    else:
        verdict = check_trade(thesis, account, inputs.price, expected_symbol=symbol)

    order_id = pending_id = None
    reason = verdict.reason
    if verdict.approved:
        opening = verdict.intent in ("open_long", "open_short")
        if config.MANUAL_APPROVAL and opening:
            pending_id = ctx.pending.add(thesis, verdict, inputs.price, now)
            reason += f" Waiting for your approval: python bot.py approve {pending_id}"
        else:
            try:
                order_id = execute(ctx.broker, thesis, verdict, inputs.price)
                if opening:   # keep this cycle's later checks honest
                    account.open_positions += 1
                    account.pending.add(symbol)
                else:
                    account.held.pop(symbol, None)
                    account.open_positions = max(0, account.open_positions - 1)
            except Exception as exc:
                reason += f" ORDER FAILED: {exc}"
                ctx.journal.event("order_error", symbol=symbol, error=repr(exc))
    entry = ctx.journal.decision(symbol=symbol, thesis=thesis, approved=verdict.approved, qty=verdict.qty,
                                 reason=reason, intent=verdict.intent, order_id=order_id,
                                 pending_id=pending_id, phase=phase, inputs=inputs, analysis=result, when=now)
    action = thesis.action if thesis else "none"
    log.info("%s %s (%s) -> %s%s", symbol, action, why, reason, f" order {order_id}" if order_id else "")
    return entry


def session_cycle(ctx: Context, clock, watchlist=None) -> None:
    now = clock.now
    ctx.state.set("last_session_seen", str(et_date(now)))
    try:
        protect_positions(ctx)
    except Exception as exc:
        ctx.journal.event("protect_error", error=repr(exc))
    account = ctx.broker.account()
    # Trades waiting for manual approval count as entries in flight.
    waiting = {p["symbol"] for p in ctx.pending.open_items(now)} - set(account.held) - account.pending
    account.pending |= waiting
    account.open_positions += len(waiting)
    context_line = market_context(ctx.broker, now)
    forced = set(ctx.state.get("force_analyze", []))
    for symbol in watchlist or config.WATCHLIST:
        try:
            process_symbol(ctx, symbol, account, now, context_line, force=symbol in forced)
        except Exception as exc:   # one bad symbol must not stop the others
            ctx.journal.event("symbol_error", symbol=symbol, error=repr(exc), trace=traceback.format_exc()[-1500:])
            log.exception("%s failed", symbol)
    if forced:
        ctx.state.set("force_analyze", [])


def _job(ctx: Context, name: str, fn) -> bool:
    """Run one off-hours job; a failure is logged and never blocks the jobs after it."""
    try:
        fn()
        return True
    except Exception as exc:
        ctx.journal.event(f"{name}_error", error=repr(exc), trace=traceback.format_exc()[-1500:])
        log.exception("%s failed", name)
        return False


def off_hours(ctx: Context, clock) -> None:
    now = clock.now
    last_session = ctx.state.get("last_session_seen")
    if last_session and ctx.state.get("review_done") != last_session:
        _job(ctx, "review", lambda: run_review(ctx, last_session))
        ctx.state.set("review_done", last_session)   # even on failure: `python bot.py review` can redo it
    if ctx.client is None:
        return   # the overnight batch and pre-market brief need the Anthropic API
    session = str(et_date(clock.next_open))
    if (ctx.state.get("overnight_session") != session
            and now >= clock.next_open - timedelta(hours=config.OVERNIGHT_SUBMIT_HOURS_BEFORE_OPEN)):
        _job(ctx, "overnight_submit", lambda: submit_overnight(ctx, session, now))   # retried next cycle on failure
    _job(ctx, "overnight_collect", lambda: collect_overnight(ctx))
    if (ctx.state.get("premarket_done") != session
            and now >= clock.next_open - timedelta(minutes=config.PREMARKET_MINUTES_BEFORE_OPEN)):
        if _job(ctx, "premarket", lambda: run_premarket(ctx, clock)):
            ctx.state.set("premarket_done", session)


def run_cycle(ctx: Context, watchlist=None) -> bool:
    """One pass of the loop. Returns False when the kill switch has fired."""
    if kill_switch_engaged(ctx.broker, ctx.journal):
        log.warning("Kill switch engaged: orders canceled, positions closed. Stopping.")
        return False
    clock = ctx.broker.clock()
    if clock.is_open:
        session_cycle(ctx, clock, watchlist)
    else:
        off_hours(ctx, clock)
    return True


def sleep_until_next_cycle(started: float) -> None:
    """Sleep out the cycle, checking the KILL file every few seconds instead of every 15 minutes."""
    deadline = started + config.CYCLE_MINUTES * 60
    while time.monotonic() < deadline:
        if config.KILL_FILE.exists():
            return
        time.sleep(min(config.KILL_POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def run_forever(ctx: Context) -> None:
    log.info("Bot running (paper=%s). Create a file named KILL to stop everything.", config.PAPER)
    while True:
        started = time.monotonic()
        try:
            if not run_cycle(ctx):
                return
        except Exception as exc:   # one bad cycle shouldn't end the loop; the watchdog covers hard crashes
            ctx.journal.event("cycle_error", error=repr(exc), trace=traceback.format_exc()[-2000:])
            log.exception("Cycle failed")
        sleep_until_next_cycle(started)
