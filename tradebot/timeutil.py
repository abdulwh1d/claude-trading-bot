"""New York time helpers. US market sessions are defined in Eastern time."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")  # on Windows this needs the `tzdata` package (in requirements.txt)
SESSION_OPEN = time(9, 30)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC (Alpaca's convention) and normalize to UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def et_date(dt: datetime) -> date:
    return as_utc(dt).astimezone(ET).date()


def session_open_utc(day: date) -> datetime:
    return datetime.combine(day, SESSION_OPEN, tzinfo=ET).astimezone(timezone.utc)
