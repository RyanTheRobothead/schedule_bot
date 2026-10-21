"""Recurring polls: which period comes next and when its poll goes out.

A recurrence is a date range (e.g. Mon–Sun, or Fri–Sun) that shifts forward
``interval_weeks`` weeks at a time. Each period's poll is posted ``lead_days``
before the period starts, at ``post_time`` local time.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

from .dates import MAX_DAYS

DEFAULT_LEAD_DAYS = 4  # a Mon–Sun week's poll goes out Thursday, leaving the weekend to answer
DEFAULT_POST_TIME = time(18, 0)
MAX_INTERVAL_WEEKS = 8

_WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class RecurrenceError(ValueError):
    pass


def validate(period_days: int, interval_weeks: int, lead_days: int) -> None:
    if not 1 <= interval_weeks <= MAX_INTERVAL_WEEKS:
        raise RecurrenceError(f"Polls can repeat every 1 to {MAX_INTERVAL_WEEKS} weeks.")
    if period_days > MAX_DAYS:
        raise RecurrenceError(f"Each poll can cover at most {MAX_DAYS} days.")
    if period_days > 7 * interval_weeks:
        raise RecurrenceError(
            f"A {period_days}-day poll can't repeat {repeat_label(interval_weeks)}; they'd overlap."
        )
    if not 0 <= lead_days <= 7 * interval_weeks:
        raise RecurrenceError(
            f"Polls repeating {repeat_label(interval_weeks)} can go out at most {7 * interval_weeks} days ahead."
        )


def post_at(period_start: date, lead_days: int, post_time: time, tz: tzinfo) -> float:
    """Epoch seconds at which the poll for the period starting ``period_start`` goes out."""
    return datetime.combine(period_start - timedelta(days=lead_days), post_time, tzinfo=tz).timestamp()


def next_period(period_start: date, interval_weeks: int) -> date:
    return period_start + timedelta(weeks=interval_weeks)


def catch_up(period_start: date, period_days: int, interval_weeks: int, today: date) -> date:
    """Skip periods that are already over. One that's under way is kept (and posted late)."""
    while period_start + timedelta(days=period_days - 1) < today:
        period_start = next_period(period_start, interval_weeks)
    return period_start


def upcoming_post(
    period_start: date, lead_days: int, post_time: time, interval_weeks: int, now: float, tz: tzinfo
) -> float:
    """The next post time strictly after ``now`` (for "next poll …" labels)."""
    while post_at(period_start, lead_days, post_time, tz) <= now:
        period_start = next_period(period_start, interval_weeks)
    return post_at(period_start, lead_days, post_time, tz)


def repeat_label(interval_weeks: int) -> str:
    return "every week" if interval_weeks == 1 else f"every {interval_weeks} weeks"


def period_label(period_start: date, period_days: int) -> str:
    """e.g. 'Mon–Sun', 'Fri–Sun', 'Sat', '14 days from Mon'."""
    first = _WEEKDAY[period_start.weekday()]
    if period_days == 1:
        return first
    if period_days <= 7:
        return f"{first}–{_WEEKDAY[(period_start.weekday() + period_days - 1) % 7]}"
    return f"{period_days} days from {first}"
