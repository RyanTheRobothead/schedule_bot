"""Parsing of human-friendly date ranges ("next week", "fri-sun", "10/25-10/31", ...)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import dateparser

MAX_DAYS = 25  # Discord select menus hold at most 25 options.

WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}  # fmt: skip

_ISO = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_SEPARATOR = re.compile(r"\s*(?:\.\.|–|—|\s-\s|\bto\b|\bthrough\b|\bthru\b|\buntil\b|\btill\b)\s*")


class DateRangeError(ValueError):
    """Raised when a date range can't be understood or isn't allowed."""


def next_week(today: date) -> tuple[date, date]:
    """The next full Monday-Sunday week (never the current one)."""
    start = today + timedelta(days=7 - today.weekday())
    return start, start + timedelta(days=6)


def parse_date_range(text: str, today: date, date_order: str = "MDY", clamp: bool = True) -> tuple[date, date]:
    """Turn free text into an inclusive (start, end) range.

    An empty string means the next calendar week. With ``clamp``, days already
    past are dropped from the start; without it the range is returned as named
    (e.g. "this week" starts on Monday), which recurring polls need.
    """
    text = " ".join(text.lower().replace(",", " ").split())
    text = re.sub(r"^(?:from|for|between|on)\s+", "", text)

    start, end = _parse_keyword_range(text, today) or _parse_explicit_range(text, today, date_order)

    if end < start:
        raise DateRangeError("The end date is before the start date.")
    if end < today:
        raise DateRangeError("That date range is entirely in the past.")
    if clamp:
        start = max(start, today)
    if (end - start).days + 1 > MAX_DAYS:
        raise DateRangeError(f"Date ranges can be at most {MAX_DAYS} days long.")
    return start, end


def _parse_keyword_range(text: str, today: date) -> tuple[date, date] | None:
    if text in ("", "next week"):
        return next_week(today)
    if text == "this week":
        return today - timedelta(days=today.weekday()), today + timedelta(days=6 - today.weekday())
    if text in ("weekend", "this weekend"):
        if today.weekday() == 6:
            return today, today
        saturday = today + timedelta(days=5 - today.weekday())
        return saturday, saturday + timedelta(days=1)
    if text == "next weekend":
        monday, _ = next_week(today)
        return monday + timedelta(days=5), monday + timedelta(days=6)
    if m := re.fullmatch(r"(?:the\s+)?(?:next\s+)?(\d+)\s+days?", text):
        return today, today + timedelta(days=int(m[1]) - 1)
    if m := re.fullmatch(r"(?:the\s+)?(?:next\s+)?(\d+)\s+weeks?", text):
        monday, _ = next_week(today)
        return monday, monday + timedelta(days=7 * int(m[1]) - 1)
    return None


def _parse_explicit_range(text: str, today: date, date_order: str) -> tuple[date, date]:
    iso = _ISO.findall(text)
    parts = [p for p in _SEPARATOR.split(text) if p]
    if len(parts) == 1 and not iso and text.count("-") == 1:
        parts = [p.strip() for p in text.split("-")]

    if len(parts) == 1:
        day = _parse_date(parts[0], today, date_order, today)
        return day, day
    if len(parts) == 2:
        start = _parse_date(parts[0], today, date_order, today)
        end = _parse_date(parts[1], start, date_order, today)
        return start, end
    raise DateRangeError(f"I couldn't understand the date range `{text}`.")


def _parse_date(piece: str, ref: date, date_order: str, today: date) -> date:
    """Parse one end of a range. Dates without a year land on or after ``ref``-ish."""
    piece = piece.strip()

    # Bare weekday: the next occurrence on/after ref. "next fri" means Friday of next week.
    if m := re.fullmatch(r"(this\s+|next\s+)?([a-z]+)", piece):
        if m[2] in WEEKDAYS:
            weekday = WEEKDAYS[m[2]]
            if m[1] and m[1].startswith("next"):
                monday, _ = next_week(today)
                return monday + timedelta(days=weekday)
            return ref + timedelta(days=(weekday - ref.weekday()) % 7)

    # Bare day of month, e.g. the "31" in "oct 25-31".
    if piece.isdigit() and 1 <= int(piece) <= 31:
        day_num = int(piece)
        candidate = ref
        for _ in range(3):
            try:
                result = candidate.replace(day=day_num)
            except ValueError:
                result = None
            if result and result >= ref:
                return result
            candidate = (candidate.replace(day=1) + timedelta(days=32)).replace(day=1)
        raise DateRangeError(f"I couldn't understand the date `{piece}`.")

    parsed = dateparser.parse(
        piece,
        languages=["en"],
        settings={
            "RELATIVE_BASE": datetime.combine(ref, datetime.min.time()),
            "DATE_ORDER": date_order,
            "PREFER_DATES_FROM": "current_period",
            "PREFER_DAY_OF_MONTH": "first",
        },
    )
    if parsed is None:
        raise DateRangeError(f"I couldn't understand the date `{piece}`.")
    result = parsed.date()

    # No explicit year: assume the next occurrence rather than one long past
    # (e.g. "jan 3" in December, or the end of "dec 28 - jan 3").
    if not re.search(r"\d{4}", piece):
        grace = timedelta(days=7) if ref == today else timedelta(0)
        while result < ref - grace:
            try:
                result = result.replace(year=result.year + 1)
            except ValueError:  # Feb 29
                result = result.replace(year=result.year + 1, day=28)
    return result
