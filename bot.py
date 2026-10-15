#!/usr/bin/env python3
"""A 24/7 paper-trading system: Claude writes the thesis, plain code decides and sizes,
Alpaca's paper account executes with the stop attached, and a journal records everything.

Start here:   python bot.py check
Try offline:  python bot.py simulate
Run it:       python watchdog.py          (restarts the bot if it crashes)
Stop it:      create a file named KILL    (or: python bot.py kill)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=value lines). Real environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()   # before importing config, which reads SEC_USER_AGENT

import config  # noqa: E402

log = logging.getLogger("bot")


def setup_logging(verbose: bool = False) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(config.LOG_DIR / "bot.log", maxBytes=5_000_000, backupCount=5,
                                       encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    for h in (file_handler, console):
        h.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def make_context():
    from importlib.util import find_spec

    missing = [name for name in ("anthropic", "alpaca") if find_spec(name) is None]
    if missing:
        raise SystemExit(f"Missing package ({', '.join(missing)}). Run: pip install -r requirements.txt")

    from anthropic import Anthropic

    from tradebot.analyst import Analyst
    from tradebot.broker import AlpacaBroker
    from tradebot.filings import Edgar
    from tradebot.loop import Context

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY in .env first.")
    broker = AlpacaBroker()
    client = Anthropic()
    edgar = Edgar() if config.SEC_USER_AGENT else None
    return Context(broker=broker, analyst=Analyst(client), client=client, edgar=edgar)


# ---------------------------------------------------------------------------- commands
def cmd_check(args) -> None:
    """Step 1: confirm both keys work and that this is a paper account."""
    ctx = make_context()
    acct = ctx.broker.trading.get_account()
    clock = ctx.broker.clock()
    status = getattr(acct.status, "value", acct.status)
    print(f"Alpaca: {'PAPER' if config.PAPER else 'LIVE'} account {acct.account_number}, status {status}, "
          f"equity ${float(acct.equity):,.2f}, market {'open' if clock.is_open else 'closed'} "
          f"(next open {clock.next_open:%Y-%m-%d %H:%M} UTC)")
    price, when = ctx.broker.latest_trade("SPY")
    print(f"Market data ({config.DATA_FEED}): SPY {price:.2f} at {when:%Y-%m-%d %H:%M} UTC")
    for model in (config.ANALYST_MODEL, config.SUMMARIZER_MODEL):
        info = ctx.client.models.retrieve(model)
        print(f"Anthropic: {info.display_name} ({info.id}) available")
    print("SEC filings:", "on" if ctx.edgar else "off (set SEC_USER_AGENT in .env to turn on)")
    print("Kill switch:", "ENGAGED (delete the KILL file to run)" if config.KILL_FILE.exists() else "clear")


def cmd_analyze(args) -> None:
    """One thesis and its risk verdict, without placing an order. Journaled as a shadow decision."""
    from tradebot.loop import market_context
    from tradebot.market_data import get_inputs
    from tradebot.overnight import brief_text, load_brief
    from tradebot.risk import Verdict, check_trade
    from tradebot.timeutil import et_date

    ctx = make_context()
    if args.effort:
        ctx.analyst.effort = args.effort
    now = ctx.broker.clock().now
    account = ctx.broker.account()
    for symbol in args.symbols:
        symbol = symbol.upper()
        inputs = get_inputs(ctx.broker, symbol, now, market_context(ctx.broker, now))
        result = ctx.analyst.analyze(symbol, inputs.market_data, inputs.news_items, ctx.position_note(symbol),
                                     brief_text(load_brief(symbol, str(et_date(now)))), when=now)
        verdict = (check_trade(result.thesis, account, inputs.price, symbol) if result.thesis
                   else Verdict(False, 0, f"No thesis: {result.error}"))
        ctx.journal.decision(symbol=symbol, thesis=result.thesis, approved=verdict.approved, qty=verdict.qty,
                             reason="(dry run, no order) " + verdict.reason, intent=verdict.intent,
                             phase="shadow", inputs=inputs, analysis=result, when=now)
        print(f"\n{symbol} @ {inputs.price:.2f}  [{result.model}, effort {result.effort}, "
              f"${result.cost_usd:.4f}, {result.seconds:.1f}s]")
        if result.thesis:
            print(json.dumps(result.thesis.model_dump(), indent=2))
        print("Risk engine:", "APPROVED" if verdict.approved else "rejected", f"qty={verdict.qty}", "-", verdict.reason)


def cmd_compare_effort(args) -> None:
    """Same inputs, several effort levels. Compare them against the journal before paying for more thinking."""
    from tradebot.analyst import Analyst
    from tradebot.loop import market_context
    from tradebot.market_data import get_inputs
    from tradebot.risk import check_trade

    ctx = make_context()
    now = ctx.broker.clock().now
    account = ctx.broker.account()
    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    for symbol in [s.upper() for s in args.symbols]:
        inputs = get_inputs(ctx.broker, symbol, now, market_context(ctx.broker, now))
        print(f"\n{symbol} @ {inputs.price:.2f}")
        for level in levels:
            analyst = Analyst(ctx.client, effort=level, ledger=ctx.ledger)
            r = analyst.analyze(symbol, inputs.market_data, inputs.news_items, ctx.position_note(symbol), when=now)
            t = r.thesis
            verdict = check_trade(t, account, inputs.price, symbol) if t else None
            ctx.journal.decision(symbol=symbol, thesis=t, approved=bool(verdict and verdict.approved),
                                 qty=verdict.qty if verdict else 0,
                                 reason=f"(effort comparison, no order) {verdict.reason if verdict else r.error}",
                                 phase="shadow", inputs=inputs, analysis=r, when=now)
            summary = f"{t.action}/{t.confidence} stop {t.stop_loss:.2f}" if t else f"error: {r.error}"
            print(f"  effort {level:<6} {summary:<32} out {r.usage.get('output_tokens', 0):>6} tok  "
                  f"${r.cost_usd:.4f}  {r.seconds:.1f}s")
    print("\nThese are journaled as shadow decisions; `python bot.py scorecard` compares effort levels once scored.")


def cmd_once(args) -> None:
    from tradebot.loop import run_cycle
    ok = run_cycle(make_context())
    print("cycle done" if ok else "kill switch engaged")


def cmd_run(args) -> None:
    from tradebot.loop import run_forever
    try:
        run_forever(make_context())
    except KeyboardInterrupt:
        log.info("Stopped by keyboard. Open positions keep their broker-side stops.")


def cmd_overnight(args) -> None:
    import time as _time

    from tradebot.overnight import collect_overnight, submit_overnight
    from tradebot.timeutil import et_date

    ctx = make_context()
    clock = ctx.broker.clock()
    session = str(et_date(clock.next_open if not clock.is_open else clock.now))
    batch_id = submit_overnight(ctx, session, clock.now)
    print(f"Submitted batch {batch_id} for session {session}" if batch_id else "Nothing to summarize.")
    while args.wait and batch_id and not collect_overnight(ctx):
        print("waiting for the batch...")
        _time.sleep(60)
    if batch_id and args.wait:
        print(f"Briefs saved in {config.BRIEFS_DIR / session}")


def cmd_premarket(args) -> None:
    from tradebot.premarket import run_premarket
    ctx = make_context()
    print("Wrote", run_premarket(ctx, ctx.broker.clock()))


def cmd_review(args) -> None:
    from tradebot.review import run_review
    from tradebot.scorecard import to_markdown
    from tradebot.timeutil import et_date

    ctx = make_context()
    session = args.session or ctx.state.get("last_session_seen") or str(et_date(ctx.broker.clock().now))
    print(to_markdown(run_review(ctx, session)))


def cmd_scorecard(args) -> None:
    from tradebot import scorecard
    from tradebot.broker import AlpacaBroker
    from tradebot.costs import CostLedger
    from tradebot.journal import Journal

    broker = AlpacaBroker()
    try:
        equity = broker.portfolio_history()
    except Exception:
        equity = None
    report = scorecard.build_report(Journal().decisions(), lambda s, start: broker.bars(s, "15Min", start),
                                    CostLedger().rows(), equity)
    path = scorecard.write(report)
    print(scorecard.to_markdown(report))
    print(f"(saved to {path})")


def cmd_pending(args) -> None:
    from tradebot.executor import PendingTrades
    items = PendingTrades().open_items()
    if not items:
        print("No trades waiting for approval.")
    for p in items:
        t = p["thesis"]
        print(f"{p['id']}  {p['time'][:16]}  {p['symbol']} {t['action']} x{p['qty']} @~{p['price']:.2f} "
              f"stop {t['stop_loss']:.2f}  | {t['entry_reason'][:100]}")


def cmd_approve(args) -> None:
    """Re-check the pending trade against fresh prices and the current account, then execute."""
    from tradebot.executor import PendingTrades, execute
    from tradebot.risk import check_trade
    from tradebot.thesis import TradeThesis

    ctx = make_context()
    item = next((p for p in PendingTrades().open_items() if p["id"] == args.id), None)
    if item is None:
        raise SystemExit("No such pending trade (or it expired).")
    if not ctx.broker.clock().is_open:
        raise SystemExit("The market is closed; approve during the session.")
    thesis = TradeThesis.model_validate(item["thesis"])
    price, _ = ctx.broker.latest_trade(thesis.symbol)
    verdict = check_trade(thesis, ctx.broker.account(), price, thesis.symbol)
    if not verdict.approved:
        ctx.pending.resolve(args.id, "rejected_on_recheck", reason=verdict.reason)
        raise SystemExit(f"Re-check failed: {verdict.reason}")
    order_id = execute(ctx.broker, thesis, verdict, price)
    ctx.pending.resolve(args.id, "approved", order_id=order_id)
    ctx.journal.event("manual_approval", pending_id=args.id, symbol=thesis.symbol, qty=verdict.qty, order_id=order_id)
    print(f"Submitted {thesis.symbol} x{verdict.qty}, order {order_id}")


def cmd_reject(args) -> None:
    from tradebot.executor import PendingTrades
    PendingTrades().resolve(args.id, "rejected_by_you")
    print("Rejected.")


def cmd_kill(args) -> None:
    from tradebot.killswitch import engage
    engage("python bot.py kill")
    print(f"KILL file created at {config.KILL_FILE}.")
    try:
        from tradebot.broker import AlpacaBroker
        AlpacaBroker().flatten_all()
        print("Open orders canceled and positions closed (market orders queue if the market is closed).")
    except (SystemExit, Exception) as exc:
        print(f"Could not reach Alpaca ({exc}); a running bot will flatten within {config.KILL_POLL_SECONDS}s.")


def cmd_simulate(args) -> None:
    from tradebot.sim import simulate
    simulate(days=args.days, seed=args.seed, kill_day=args.kill_day)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="confirm keys, paper account, data and models").set_defaults(func=cmd_check)
    p = sub.add_parser("analyze", help="one thesis + risk verdict, no order")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.set_defaults(func=cmd_analyze)
    p = sub.add_parser("compare-effort", help="same inputs at several effort levels")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--levels", default="medium,high")
    p.set_defaults(func=cmd_compare_effort)
    sub.add_parser("once", help="run one cycle now").set_defaults(func=cmd_once)
    sub.add_parser("run", help="run 24/7 (use watchdog.py to auto-restart)").set_defaults(func=cmd_run)
    p = sub.add_parser("overnight", help="submit the overnight research batch now")
    p.add_argument("--wait", action="store_true")
    p.set_defaults(func=cmd_overnight)
    sub.add_parser("premarket", help="write the pre-market brief now").set_defaults(func=cmd_premarket)
    p = sub.add_parser("review", help="after-close review: scorecard, daily CSV, one hypothesis")
    p.add_argument("--session", help="YYYY-MM-DD (default: last session the bot saw)")
    p.set_defaults(func=cmd_review)
    sub.add_parser("scorecard", help="how accurate has the analyst been?").set_defaults(func=cmd_scorecard)
    sub.add_parser("pending", help="trades waiting for approval (MANUAL_APPROVAL)").set_defaults(func=cmd_pending)
    p = sub.add_parser("approve")
    p.add_argument("id")
    p.set_defaults(func=cmd_approve)
    p = sub.add_parser("reject")
    p.add_argument("id")
    p.set_defaults(func=cmd_reject)
    sub.add_parser("kill", help="emergency stop: cancel orders, close positions").set_defaults(func=cmd_kill)
    p = sub.add_parser("simulate", help="offline run against a fake market (no keys needed)")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--kill-day", type=int, default=None, help="pull the kill switch on this day (0-based)")
    p.set_defaults(func=cmd_simulate)

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
