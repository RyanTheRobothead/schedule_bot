"""When reminders fire.

Intervals that are a whole number of days can be pinned to a wall-clock time
("daily at 6pm"); those step in local time so DST doesn't shift them. Anything
else (``nag=12h``) is simply relative to the previous reminder.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo

DAY = 86400
# Don't send the first reminder within this long of the poll being posted.
FIRST_NAG_GRACE = timedelta(hours=6)


def uses_time_of_day(interval: int, nag_time: time | None) -> bool:
    return nag_time is not None and interval > 0 and interval % DAY == 0


def _at(day, nag_time: time, tz: tzinfo) -> datetime:
    return datetime.combine(day, nag_time, tzinfo=tz)


def first_nag_at(created: float, interval: int, nag_time: time | None, tz: tzinfo) -> float | None:
    """Epoch seconds of a new poll's first reminder, or None when nagging is off."""
    if not interval:
        return None
    if not uses_time_of_day(interval, nag_time):
        return created + interval
    # e.g. daily at 18:00: posted at 09:00 -> 18:00 today; posted at 17:55 -> 18:00 tomorrow.
    # Every 2 days: the first is at least a day away.
    earliest = datetime.fromtimestamp(created, tz) + max(FIRST_NAG_GRACE, timedelta(seconds=interval - DAY))
    candidate = _at(earliest.date(), nag_time, tz)
    if candidate < earliest:
        candidate = _at(earliest.date() + timedelta(days=1), nag_time, tz)
    return candidate.timestamp()


def next_nag_at(scheduled: float, now: float, interval: int, nag_time: time | None, tz: tzinfo) -> float:
    """The reminder after one that was due at ``scheduled`` (and handled at ``now``)."""
    if not uses_time_of_day(interval, nag_time):
        return now + interval
    step = timedelta(days=interval // DAY)
    day = datetime.fromtimestamp(scheduled, tz).date() + step
    # If the bot was down for a while, skip the missed slots rather than catching up.
    while _at(day, nag_time, tz).timestamp() <= now:
        day += step
    return _at(day, nag_time, tz).timestamp()
