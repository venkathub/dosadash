"""Time-based availability (IST): business hours + per-item schedules.

Shared between the core API (menu/checkout enforcement) and the AI service
(the order agent must refuse orders while closed — one implementation, no
drift). Sources of truth are the Phase 2 admin settings
(`Settings.business_hours`) and `MenuItem.schedule`.

Two schedule shapes are accepted per weekday (validated at write time in
dosadash_shared.admin):

- legacy single window:  ``{day: {"start": "06:00", "end": "12:00"}}``
- multi-window (Phase 11): ``{day: [{"start": "06:00", "end": "11:30"},
  {"start": "17:00", "end": "22:00"}]}`` — a tiffin-centre dish like dosa
  serves at breakfast AND dinner but not lunch.

Missing day key = closed / not served that day; None/empty = always open /
always served. Windows with end < start span midnight.

`now_ist` is a module function so tests can freeze time via monkeypatch.
It also honours the ``DOSADASH_NOW_IST`` env var (ISO datetime) so the live
eval harness can pin the clock for schedule-gated dishes — the variable is
never set in production compose, where behaviour is byte-identical.
"""

import os
from datetime import datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_CLOCK_ENV = "DOSADASH_NOW_IST"


def now_ist() -> datetime:
    pinned = os.environ.get(_CLOCK_ENV)
    if pinned:
        at = datetime.fromisoformat(pinned)
        return at if at.tzinfo else at.replace(tzinfo=IST)
    return datetime.now(IST)


def _parse(hhmm: str) -> dt_time:
    hours, minutes = hhmm.split(":")
    return dt_time(int(hours), int(minutes))


def _in_window(window: dict[str, Any], now_time: dt_time) -> bool:
    start, end = _parse(window["start"]), _parse(window["end"])
    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end  # overnight window (e.g. 18:00–02:00)


def _day_windows(entry: Any) -> list[dict[str, Any]]:
    """Normalize a weekday entry to a list of windows (legacy dict or list)."""
    if not entry:
        return []
    if isinstance(entry, list):
        return [w for w in entry if w]
    return [entry]


def _windows_say_yes(windows: dict[str, Any] | None, now: datetime | None) -> bool:
    if not windows:
        return True
    at = now or now_ist()
    today = _day_windows(windows.get(WEEKDAYS[at.weekday()]))
    return any(_in_window(w, at.time()) for w in today)


def is_open(business_hours: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Is the kitchen within business hours? No hours configured = always open."""
    return _windows_say_yes(business_hours, now)


def item_on_schedule(schedule: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Is this dish being served right now? No schedule = always served."""
    return _windows_say_yes(schedule, now)


def _fmt_12h(hhmm: str) -> str:
    """'06:00' → '6 AM', '11:30' → '11:30 AM', '12:00' → '12 PM'."""
    t = _parse(hhmm)
    meridiem = "AM" if t.hour < 12 else "PM"
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {meridiem}" if t.minute else f"{hour} {meridiem}"


def _window_text(window: dict[str, Any]) -> str:
    """One window as e.g. '6–11:30 AM' (shared meridiem collapsed) or '5 PM–10 PM'."""
    start, end = _fmt_12h(window["start"]), _fmt_12h(window["end"])
    s_num, s_mer = start.rsplit(" ", 1)
    e_num, e_mer = end.rsplit(" ", 1)
    if s_mer == e_mer:
        return f"{s_num}–{e_num} {e_mer}"
    return f"{start}–{end}"


def serving_windows_text(
    schedule: dict[str, Any] | None, now: datetime | None = None
) -> str | None:
    """Human-readable serving windows for menu badges and agent hints.

    None (always served) → None. If every configured day shares the same
    windows → '6–11:30 AM & 5–10 PM'. Otherwise today's windows are shown
    ('today 11:30 AM–4 PM'); a day with no entry → 'not served today'.
    """
    if not schedule:
        return None
    at = now or now_ist()
    per_day = [_day_windows(schedule.get(d)) for d in WEEKDAYS if d in schedule]
    uniform = len(per_day) == len(WEEKDAYS) and all(w == per_day[0] for w in per_day)
    today = _day_windows(schedule.get(WEEKDAYS[at.weekday()]))
    if uniform:
        return " & ".join(_window_text(w) for w in per_day[0])
    if not today:
        return "not served today"
    return "today " + " & ".join(_window_text(w) for w in today)
