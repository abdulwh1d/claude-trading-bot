"""Before the open: a pre-market brief that flags anything that changed overnight,
and candidate theses for the flagged symbols.

Candidates are journaled (phase "premarket") so they get scored like everything
else, but nothing trades before the open. Flagged symbols are re-analyzed in the
first session cycle with live prices.
"""
from __future__ import annotations

from pathlib import Path

import config

from .market_data import daily_history, get_inputs
from .overnight import brief_text, load_brief
from .timeutil import ET, et_date


def run_premarket(ctx, clock) -> Path:
    session_day = et_date(clock.next_open)
    session = str(session_day)
    now = clock.now
    lines = [f"# Pre-market brief for {session}", "",
             f"Written {now.astimezone(ET):%Y-%m-%d %H:%M} ET.", ""]
    flagged = []
    for symbol in config.WATCHLIST:
        try:
            brief = load_brief(symbol, session)
            price, _ = ctx.broker.latest_trade(symbol)
            history = [b for b in daily_history(ctx.broker, symbol, now) if et_date(b.t) < session_day]
            prior = history[-1].close if history else None
            gap = price / prior - 1 if prior else 0.0
            reasons = []
            if brief and brief.material:
                reasons.append("material overnight news or filing")
            if abs(gap) >= config.PREMARKET_GAP_FLAG_PCT:
                reasons.append(f"moved {gap:+.1%} since the close")
            lines += [f"## {symbol}" + ("  (FLAGGED: " + "; ".join(reasons) + ")" if reasons else ""), "",
                      f"- Last {price:.2f}" + (f", {gap:+.2%} vs prior close {prior:.2f}" if prior else ""),
                      (brief_text(brief) or "- No overnight brief yet.").strip(), ""]
            if reasons:
                flagged.append(symbol)
        except Exception as exc:
            lines += [f"## {symbol}", "", f"- Error: {exc!r}", ""]
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"premarket_{session}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    ctx.state.set("force_analyze", flagged)   # re-analyze these in the first cycle after the open

    if config.PREMARKET_THESES:
        for symbol in flagged:
            if ctx.ledger.over_budget(now):
                break
            try:
                inputs = get_inputs(ctx.broker, symbol, now)
                result = ctx.analyst.analyze(symbol, inputs.market_data, inputs.news_items,
                                             position_note=ctx.position_note(symbol),
                                             brief=brief_text(load_brief(symbol, session)), when=now)
                ctx.journal.decision(symbol=symbol, thesis=result.thesis, approved=False, qty=0,
                                     reason="Pre-market candidate. Nothing trades before the open.",
                                     phase="premarket", inputs=inputs, analysis=result, when=now)
            except Exception as exc:
                ctx.journal.event("premarket_error", symbol=symbol, error=repr(exc))
    ctx.journal.event("premarket_done", session=session, flagged=flagged, report=str(path))
    return path
