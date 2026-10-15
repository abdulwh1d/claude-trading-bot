"""The gate in front of real money.

Live trading stays off until a human writes a GO-LIVE file with the exact
sentence below AND the journal shows at least MIN_PAPER_SESSIONS sessions of
paper decisions. Changing config.PAPER alone is not enough.
"""
from __future__ import annotations

from datetime import datetime

import config

from .journal import Journal
from .timeutil import et_date

PHRASE = "I have reviewed the paper results and accept the risk of trading real money."
MIN_PAPER_SESSIONS = 20


def assert_live_allowed() -> None:
    if not config.GO_LIVE_FILE.exists() or PHRASE not in config.GO_LIVE_FILE.read_text(encoding="utf-8"):
        raise SystemExit(f"Live trading is locked. Create a file named GO-LIVE containing:\n  {PHRASE}")
    sessions = {et_date(datetime.fromisoformat(r["time"])) for r in Journal().decisions() if r.get("phase") == "session"}
    if len(sessions) < MIN_PAPER_SESSIONS:
        raise SystemExit(f"Live trading is locked: the journal has {len(sessions)} paper sessions; "
                         f"{MIN_PAPER_SESSIONS} are required first.")
