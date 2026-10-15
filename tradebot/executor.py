"""Step 6: execute on paper, with the stop attached.

Approved trades go to the broker with the stop loss attached to the order
itself, so the protection exists even if this program crashes a second later.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config


def take_profit_price(side: str, price: float, stop: float) -> float | None:
    """Broker-held target at TAKE_PROFIT_R times the risk (Reddit tip: bracket orders). None = stop only."""
    if not config.TAKE_PROFIT_R:
        return None
    distance = abs(price - stop) * config.TAKE_PROFIT_R
    return round(price + distance if side == "buy" else price - distance, 2)


def execute(broker, thesis, verdict, price: float) -> str:
    """Send an approved verdict to the broker and return the order id."""
    if verdict.intent in ("close_long", "close_short"):
        return broker.close_position(thesis.symbol)
    side = "buy" if verdict.intent == "open_long" else "sell"
    stop = round(float(thesis.stop_loss), 2)
    return broker.submit_entry(thesis.symbol, side, verdict.qty, stop, take_profit_price(side, price, stop))


class PendingTrades:
    """The article's stricter first phase: approved trades wait here until you sign off."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.PENDING_FILE)

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def add(self, thesis, verdict, price: float, when: datetime | None = None) -> str:
        pid = uuid.uuid4().hex[:8]
        when = when or datetime.now(timezone.utc)
        self._append({"id": pid, "status": "pending", "time": when.isoformat(), "symbol": thesis.symbol,
                      "thesis": thesis.model_dump(), "qty": verdict.qty, "intent": verdict.intent,
                      "price": price})
        return pid

    def resolve(self, pid: str, status: str, **extra) -> None:
        self._append({"id": pid, "status": status, "time": datetime.now(timezone.utc).isoformat(), **extra})

    def open_items(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        items: dict[str, dict] = {}
        for row in self._rows():
            if row["status"] == "pending":
                items[row["id"]] = row
            else:
                items.pop(row["id"], None)
        expiry = timedelta(minutes=config.PENDING_EXPIRY_MINUTES)
        return [r for r in items.values() if now - datetime.fromisoformat(r["time"]) <= expiry]
