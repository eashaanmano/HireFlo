"""Working hours for multi-day drives, and their conversion into solver windows.

A drive's interviews may only run inside working hours: for example 09:00-18:00 local time with lunch 13:00-14:00,
on up to three working days, skipping Sundays. `windows()` turns that into the [start, end) minute intervals the
CP-SAT model uses as the domain of each start variable, so an interview never crosses lunch, closing time or the
night, and a round that doesn't fit in one day carries on the next morning.

The hours chosen when a round is scheduled are stored in the `schedule.generated` event payload, so later
re-plans and next-round placements of the same drive respect them without a schema change.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.timeutil import ensure_utc
from ..models import Event

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class WorkHoursError(ValueError):
    pass


def _mins(hhmm: str) -> int:
    if hhmm.strip() == "24:00":
        return 24 * 60  # end of day
    m = _HHMM.match(hhmm.strip())
    if not m:
        raise WorkHoursError(f"'{hhmm}' is not a time like 09:00.")
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_breaks(text: str | list[str] | None) -> list[str]:
    """'13:00-14:00, 16:00-16:15' or a list of such strings -> normalised list."""
    if not text:
        return []
    parts = text if isinstance(text, list) else re.split(r"[,;]", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        a, sep, b = p.partition("-")
        if not sep:
            raise WorkHoursError(f"Break '{p}' should look like 13:00-14:00.")
        if _mins(b) <= _mins(a):
            raise WorkHoursError(f"Break '{p}' ends before it starts.")
        out.append(f"{a.strip()}-{b.strip()}")
    return out


@dataclass(frozen=True)
class WorkingHours:
    day_start: str = "09:00"
    day_end: str = "18:00"
    breaks: tuple[str, ...] = field(default_factory=tuple)
    max_days: int = 3
    skip_sundays: bool = True
    utc_offset_min: int = 330

    def __post_init__(self) -> None:
        if _mins(self.day_end) <= _mins(self.day_start):
            raise WorkHoursError("The working day must end after it starts.")
        if not 1 <= self.max_days <= 14:
            raise WorkHoursError("A drive can run over 1 to 14 days.")
        parse_breaks(list(self.breaks))

    # ------------------------------------------------------------ (de)serialisation
    @classmethod
    def from_settings(cls) -> WorkingHours:
        s = get_settings()
        return cls(day_start=s.work_day_start, day_end=s.work_day_end, breaks=tuple(parse_breaks(s.work_breaks)),
                   max_days=s.schedule_max_days, skip_sundays=s.schedule_skip_sundays,
                   utc_offset_min=s.display_utc_offset_min)

    @classmethod
    def from_payload(cls, data: dict | None) -> WorkingHours | None:
        if not data:
            return None
        try:
            return cls(day_start=data["day_start"], day_end=data["day_end"], breaks=tuple(data.get("breaks") or ()),
                       max_days=int(data.get("max_days", 3)), skip_sundays=bool(data.get("skip_sundays", True)),
                       utc_offset_min=int(data.get("utc_offset_min", get_settings().display_utc_offset_min)))
        except (KeyError, TypeError, ValueError):
            return None

    def to_payload(self) -> dict[str, object]:
        d = asdict(self)
        d["breaks"] = list(self.breaks)
        return d

    # ------------------------------------------------------------ windows
    @property
    def tz(self) -> timezone:
        return timezone(timedelta(minutes=self.utc_offset_min))

    def _day_pieces(self) -> list[tuple[int, int]]:
        """Open periods of one working day, in minutes after local midnight."""
        pieces = [(_mins(self.day_start), _mins(self.day_end))]
        for br in self.breaks:
            a, _, b = br.partition("-")
            bs, be = _mins(a), _mins(b)
            nxt = []
            for ps, pe in pieces:
                if be <= ps or bs >= pe:
                    nxt.append((ps, pe))
                    continue
                if bs > ps:
                    nxt.append((ps, bs))
                if be < pe:
                    nxt.append((be, pe))
            pieces = nxt
        return pieces

    def working_days(self, origin: datetime, extra_days: int = 0) -> list[date]:
        """Local dates with working time left at or after `origin` (today counts only if time is left)."""
        origin = ensure_utc(origin)  # type: ignore[assignment]
        local = origin.astimezone(self.tz)
        end_today = datetime.combine(local.date(), time(0), self.tz) + timedelta(minutes=_mins(self.day_end))
        d = local.date() if local < end_today else local.date() + timedelta(days=1)
        out: list[date] = []
        while len(out) < self.max_days + extra_days:
            if not (self.skip_sundays and d.weekday() == 6):
                out.append(d)
            d += timedelta(days=1)
        return out

    def windows(self, origin: datetime, extra_days: int = 0) -> list[tuple[int, int]]:
        """[start, end) minute windows relative to `origin`, clipped so none starts before it."""
        origin = ensure_utc(origin)  # type: ignore[assignment]
        out = []
        for d in self.working_days(origin, extra_days):
            midnight = datetime.combine(d, time(0), self.tz)
            for ps, pe in self._day_pieces():
                ws = (midnight + timedelta(minutes=ps) - origin).total_seconds() / 60
                we = (midnight + timedelta(minutes=pe) - origin).total_seconds() / 60
                ws, we = max(0, int(ws) + (ws > int(ws))), int(we)  # ceil start, floor end
                if we > ws:
                    out.append((ws, we))
        return out

    def describe(self) -> str:
        br = f", breaks {', '.join(self.breaks)}" if self.breaks else ""
        sun = ", no Sundays" if self.skip_sundays else ""
        return f"{self.day_start}-{self.day_end}{br}, up to {self.max_days} day(s){sun}"


def drive_hours(db: Session, drive_id: int) -> WorkingHours:
    """The hours used when this drive was last scheduled, else the defaults from settings."""
    ev = db.scalar(select(Event).where(Event.drive_id == drive_id, Event.type == "schedule.generated")
                   .order_by(Event.id.desc()).limit(1))
    hours = WorkingHours.from_payload((ev.payload or {}).get("hours") if ev else None)
    return hours or WorkingHours.from_settings()


def local_date(dt: datetime, hours: WorkingHours) -> date:
    return ensure_utc(dt).astimezone(hours.tz).date()  # type: ignore[union-attr]
