"""Step 8: install the kill switch.

Every automated system that touches money needs a way to stop instantly, from
anywhere, without thinking. Create a file named KILL in the bot's folder (by
hand, from a phone through any file-sync tool, or with `python bot.py kill`)
and the bot cancels open orders, closes every position, and stops.

The daily loss limit in the risk engine is the automatic brake. The kill
switch is the emergency brake. You need both.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import config


def kill_switch_engaged(broker, journal=None, kill_file: Path | None = None) -> bool:
    if Path(kill_file or config.KILL_FILE).exists():
        broker.flatten_all()   # cancel open orders, close every position
        if journal is not None:
            journal.event("kill_switch", detail="KILL file found: orders canceled, positions closed, bot stopped.")
        return True
    return False


def engage(reason: str = "manual", kill_file: Path | None = None) -> None:
    Path(kill_file or config.KILL_FILE).write_text(f"{datetime.now(timezone.utc).isoformat()} {reason}\n",
                                                   encoding="utf-8")
