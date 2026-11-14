"""Time helpers. All datetimes in DriveOps are timezone-aware UTC."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; normalise anything read back from the database."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def minutes_between(a: datetime, b: datetime) -> float:
    return (ensure_utc(b) - ensure_utc(a)).total_seconds() / 60.0  # type: ignore[operator]


def add_minutes(dt: datetime, mins: float) -> datetime:
    return dt + timedelta(minutes=mins)
