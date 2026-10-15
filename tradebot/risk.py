"""Step 5: the risk engine, in plain code.

This is the most important code in the system, and there is no AI in it at all.
It takes the thesis and the account state and applies hard rules written in
advance (config.py). It decides whether a trade happens and how big it is.
No model touches this layer. Ever.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config

_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Account:
    equity: float
    day_pnl: float
    open_positions: int
    held: dict[str, float] = field(default_factory=dict)  # symbol -> signed shares (long > 0)
    pending: set[str] = field(default_factory=set)        # symbols with an entry order not yet filled
    blocked: bool = False

    @property
    def start_equity(self) -> float:
        return self.equity - self.day_pnl


@dataclass(frozen=True)
class Verdict:
    approved: bool
    qty: int
    reason: str
    intent: str | None = None  # open_long | close_long | open_short | close_short

    def __iter__(self):
        # Lets you write `approved, qty, reason = check_trade(...)` exactly as in the article.
        return iter((self.approved, self.qty, self.reason))


def check_trade(thesis, account: Account, price: float, expected_symbol: str | None = None) -> Verdict:
    """Return (approved, qty, reason). Pure code. The model never touches this."""
    symbol = thesis.symbol.upper()
    action = thesis.action.lower()          # enum casing isn't guaranteed: compare case-insensitively
    confidence = thesis.confidence.lower()

    if account.blocked:
        return Verdict(False, 0, "Account is blocked from trading.")
    # Added: a thesis for a different ticker than the one analyzed (an injected headline can
    # try this) would otherwise be sized with the wrong stock's price.
    if expected_symbol and symbol != expected_symbol.upper():
        return Verdict(False, 0, f"Thesis is for {symbol} but {expected_symbol.upper()} was analyzed.")
    if symbol not in config.ALLOWED_SYMBOLS:
        return Verdict(False, 0, f"{symbol} is not on the allowed list.")
    if action == "hold":
        return Verdict(False, 0, "Model recommends no trade.")
    if not price or price <= 0:
        return Verdict(False, 0, "No valid price to size against.")

    held = account.held.get(symbol, 0.0)

    # Added: exits reduce risk, so they are allowed even after the daily loss limit,
    # and need less confidence than entries.
    if (action == "sell" and held > 0) or (action == "buy" and held < 0):
        if _RANK.get(confidence, -1) < _RANK[config.EXIT_CONFIDENCE]:
            return Verdict(False, 0, f"Exits need {config.EXIT_CONFIDENCE} confidence or better.")
        intent = "close_long" if held > 0 else "close_short"
        return Verdict(True, int(abs(held)), f"Exit approved: closing {abs(held):g} shares.", intent)

    if action == "sell" and not config.ALLOW_SHORTS:
        return Verdict(False, 0, "Shorting is disabled and there is no position to sell.")

    # Everything below opens a new position.
    if account.day_pnl <= -config.MAX_DAILY_LOSS_PCT * account.start_equity:
        return Verdict(False, 0, "Daily loss limit hit. No new trades today.")
    # Added: the article sizes each order, not each position, so a repeated "buy AAPL"
    # every 15 minutes would keep adding 5% chunks. One position per symbol.
    if held != 0 or symbol in account.pending:
        return Verdict(False, 0, f"Already holding or buying {symbol}. No adding to positions.")
    if account.open_positions >= config.MAX_OPEN_POSITIONS:
        return Verdict(False, 0, "Maximum open positions reached.")
    if _RANK.get(confidence, -1) < _RANK[config.ENTRY_CONFIDENCE]:
        return Verdict(False, 0, f"Only {config.ENTRY_CONFIDENCE}-confidence theses trade.")

    stop = float(thesis.stop_loss)
    stop_distance = abs(price - stop)
    if stop <= 0 or stop_distance <= 0:
        return Verdict(False, 0, "Invalid stop loss.")
    if action == "buy" and stop >= price:
        return Verdict(False, 0, "Stop loss must be below entry for a buy.")
    # Added: the article only checks buys. A short's stop must sit above entry.
    if action == "sell" and stop <= price:
        return Verdict(False, 0, "Stop loss must be above entry for a short.")
    stop_pct = stop_distance / price
    if stop_pct < config.MIN_STOP_DISTANCE_PCT:
        return Verdict(False, 0, f"Stop is only {stop_pct:.2%} away (minimum {config.MIN_STOP_DISTANCE_PCT:.1%}).")
    if stop_pct > config.MAX_STOP_DISTANCE_PCT:
        return Verdict(False, 0, f"Stop is {stop_pct:.1%} away (maximum {config.MAX_STOP_DISTANCE_PCT:.0%}).")

    qty_by_risk = (config.MAX_RISK_PER_TRADE * account.equity) / stop_distance
    qty_by_size = (config.MAX_POSITION_PCT * account.equity) / price
    qty = int(min(qty_by_risk, qty_by_size))
    if qty < 1:
        return Verdict(False, 0, "Position too small after risk limits.")
    intent = "open_long" if action == "buy" else "open_short"
    return Verdict(True, qty, "Approved by risk engine.", intent)
