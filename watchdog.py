#!/usr/bin/env python3
"""Keeps the bot running on your own computer: restarts it when it crashes, stops for good
when the kill switch is pulled or the bot exits cleanly. Gives up after repeated crashes so
a human looks at the log instead of the bot crash-looping all night.

Usage:  python watchdog.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone

import config

MAX_CRASHES_PER_HOUR = 6


def note(event: str, **data) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
    with config.EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[watchdog {row['time'][:19]}] {event} {data if data else ''}", flush=True)


def main() -> int:
    crashes: list[float] = []
    while True:
        if config.KILL_FILE.exists():
            note("watchdog_stopped", reason="KILL file present")
            return 0
        note("bot_started")
        code = subprocess.call([sys.executable, str(config.ROOT / "bot.py"), "run"], cwd=config.ROOT)
        if code == 0:
            note("watchdog_stopped", reason="bot exited cleanly")
            return 0
        now = time.time()
        crashes = [t for t in crashes if now - t < 3600] + [now]
        note("bot_crashed", exit_code=code, crashes_last_hour=len(crashes))
        if len(crashes) > MAX_CRASHES_PER_HOUR:
            note("watchdog_gave_up", reason=f"more than {MAX_CRASHES_PER_HOUR} crashes in an hour; check logs/bot.log")
            return 1
        deadline = time.monotonic() + min(300, 15 * 2 ** (len(crashes) - 1))
        while time.monotonic() < deadline and not config.KILL_FILE.exists():
            time.sleep(config.KILL_POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
