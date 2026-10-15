"""Step 7: journal every decision.

A trading system without a journal cannot improve, because you can never
reconstruct why it did what it did. Every thesis is logged, holds included,
with the risk verdict, the order, and what the system read. The theses the
system rejected are how you find out whether your risk rules are filtering out
losers or filtering out winners (the scorecard measures exactly that).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a half-written line from a crash shouldn't break the report
    return rows


class Journal:
    def __init__(self, path: Path | None = None, events_path: Path | None = None):
        self.path = Path(path or config.JOURNAL_FILE)
        self.events_path = Path(events_path or config.EVENTS_FILE)

    def decision(self, *, symbol, thesis, approved, qty, reason, intent=None, order_id=None,
                 pending_id=None, phase="session", inputs=None, analysis=None,
                 when: datetime | None = None) -> dict:
        when = when or datetime.now(timezone.utc)
        entry = {
            "time": when.isoformat(),
            "phase": phase,                       # session | premarket | shadow
            "symbol": symbol,
            "thesis": thesis.model_dump() if thesis is not None else None,
            "approved": approved,
            "qty": qty,
            "risk_reason": reason,
            "intent": intent,
            "order_id": str(order_id) if order_id else None,
            "pending_id": pending_id,
            # What the system read. The article promises this record; its code leaves it out.
            "price": inputs.price if inputs else None,
            "price_time": inputs.price_time.isoformat() if inputs else None,
            "news_ids": inputs.news_ids if inputs else [],
            "headlines": inputs.headlines if inputs else [],
            # Which model and how much thinking, so effort levels can be compared later.
            "model": analysis.model if analysis else None,
            "effort": analysis.effort if analysis else None,
            "stop_reason": analysis.stop_reason if analysis else None,
            "usage": analysis.usage if analysis else None,
            "cost_usd": analysis.cost_usd if analysis else 0.0,
            "analyst_error": analysis.error if analysis else None,
        }
        _append(self.path, entry)
        return entry

    def event(self, kind: str, when: datetime | None = None, **data) -> None:
        when = when or datetime.now(timezone.utc)
        _append(self.events_path, {"time": when.isoformat(), "event": kind, **data})

    def decisions(self) -> list[dict]:
        return _read(self.path)

    def events(self) -> list[dict]:
        return _read(self.events_path)

    def last_entry_stop(self, symbol: str) -> float | None:
        """Stop price of the most recent executed entry for a symbol."""
        for row in reversed(self.decisions()):
            if (row.get("symbol") == symbol and row.get("approved") and row.get("order_id")
                    and row.get("intent") in ("open_long", "open_short") and row.get("thesis")):
                return float(row["thesis"]["stop_loss"])
        return None


# The article's function, kept for anyone following along with it.
def journal(thesis, approved, qty, reason, order_id=None):
    return Journal().decision(symbol=thesis.symbol, thesis=thesis, approved=approved, qty=qty,
                              reason=reason, order_id=order_id)
