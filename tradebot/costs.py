"""What every model call cost, and a hard daily cap enforced by code."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config

from .timeutil import et_date


def usage_dict(usage) -> dict:
    """Pull token counts off an Anthropic `Usage` object (or a plain dict)."""
    get = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
    return {
        "input_tokens": int(get("input_tokens", 0) or 0),
        "output_tokens": int(get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(get("cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens": int(get("cache_read_input_tokens", 0) or 0),
    }


def cost_usd(model: str, usage: dict, batch: bool = False) -> float:
    """Dollar cost of one call. Output tokens include thinking tokens, which are billed as output."""
    price = config.PRICING.get(model)
    if price is None:
        return 0.0
    per_in = price["input"] / 1e6
    per_out = price["output"] / 1e6
    total = (
        usage.get("input_tokens", 0) * per_in
        + usage.get("cache_creation_input_tokens", 0) * per_in * config.CACHE_WRITE_MULT
        + usage.get("cache_read_input_tokens", 0) * per_in * config.CACHE_READ_MULT
        + usage.get("output_tokens", 0) * per_out
    )
    return round(total * (config.BATCH_DISCOUNT if batch else 1.0), 6)


class CostLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.COSTS_FILE)

    def record(self, *, model: str, usage, kind: str, batch: bool = False, symbol: str | None = None,
               when: datetime | None = None) -> float:
        u = usage_dict(usage)
        cost = cost_usd(model, u, batch)
        when = when or datetime.now(timezone.utc)
        row = {"time": when.isoformat(), "model": model, "kind": kind, "batch": batch,
               "symbol": symbol, **u, "cost_usd": cost}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return cost

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def spent_on(self, day) -> float:
        """Total spend for one New York calendar day."""
        return round(sum(r["cost_usd"] for r in self.rows()
                         if et_date(datetime.fromisoformat(r["time"])) == day), 6)

    def total(self) -> float:
        return round(sum(r["cost_usd"] for r in self.rows()), 6)

    def over_budget(self, now: datetime) -> bool:
        return self.spent_on(et_date(now)) >= config.MAX_DAILY_LLM_USD
