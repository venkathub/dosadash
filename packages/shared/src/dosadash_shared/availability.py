"""Time-based availability (IST): business hours + per-item schedules.

Shared between the core API (menu/checkout enforcement) and the AI service
(the order agent must refuse orders while closed — one implementation, no
drift). Sources of truth are the Phase 2 admin settings
(`Settings.business_hours`) and `MenuItem.schedule` — both
`{day: {start, end}}` with HH:MM windows validated at write time
(dosadash_shared.admin). Missing day key = closed / not served that day;
None/empty = always open / always served. Windows with end < start span
midnight.

`now_ist` is a module function so tests can freeze time via monkeypatch.
"""

from datetime import datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def now_ist() -> datetime:
    return datetime.now(IST)


def _parse(hhmm: str) -> dt_time:
    hours, minutes = hhmm.split(":")
    return dt_time(int(hours), int(minutes))


def _in_window(window: dict[str, Any], now_time: dt_time) -> bool:
    start, end = _parse(window["start"]), _parse(window["end"])
    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end  # overnight window (e.g. 18:00–02:00)


def _windows_say_yes(windows: dict[str, Any] | None, now: datetime | None) -> bool:
    if not windows:
        return True
    at = now or now_ist()
    window = windows.get(WEEKDAYS[at.weekday()])
    return bool(window) and _in_window(window, at.time())


def is_open(business_hours: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Is the kitchen within business hours? No hours configured = always open."""
    return _windows_say_yes(business_hours, now)


def item_on_schedule(schedule: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Is this dish being served right now? No schedule = always served."""
    return _windows_say_yes(schedule, now)
