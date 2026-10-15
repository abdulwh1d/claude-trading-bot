"""Small persistent state: what was last analyzed, and which daily jobs already ran."""
from __future__ import annotations

import json
import os
from pathlib import Path

import config


class State:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.STATE_FILE)
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, so a crash can't leave half a file
