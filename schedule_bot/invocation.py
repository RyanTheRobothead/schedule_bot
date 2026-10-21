"""Parsing the text of an ``@schedule_bot ...`` message."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time

_MENTION = re.compile(r"<@[!&]?\d+>|@everyone|@here")
_TITLE = re.compile(r"\"([^\"]+)\"|“([^”]+)”")
_NAG = re.compile(r"\bnag\s*[=:]\s*(\S+)")
_NO_NAG = re.compile(r"\b(?:no[\s-]?nag|nag\s+off)\b")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_TIME_OF_DAY = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")
_LEAD = re.compile(r"\blead\s*[=:]\s*(\S+)")
_LEAD_DAYS = re.compile(r"(\d+)\s*(d|days?|w|weeks?)?")
_WEEKDAY_WORD = r"mon(?:day)?|tues?(?:day)?|wed(?:nesday)?|thu(?:rs?)?(?:day)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?"
_REPEAT = re.compile(
    r"\b(?:(?P<weekly>weekly)|(?P<biweekly>bi-?weekly|fortnightly)"
    r"|every\s+(?:(?P<other>other\s+week)|(?P<n>\d+)\s+weeks?|(?P<week>week)|(?P<what>weekend|" + _WEEKDAY_WORD + r")))\b"
)
_LIST_RECURRING = re.compile(r"(?:list\s+)?(?:recurring|repeating|recurrences)(?:\s+polls)?")
_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")

DEFAULT_TITLE = "Game night"
MIN_NAG_SECONDS = 15 * 60


class InvocationError(ValueError):
    pass


@dataclass
class Invocation:
    title: str = DEFAULT_TITLE
    nag_seconds: int | None = None  # None means "use the configured default"
    nag_time: time | None = None  # None means "use the configured default"
    date_text: str = ""
    help: bool = False
    repeat_weeks: int | None = None  # set for recurring polls
    lead_days: int | None = None  # None means the default
    lead_time: time | None = None  # None means the default
    list_recurring: bool = False


def parse_duration(text: str) -> int:
    """'12h' -> 43200, 'daily' -> 86400, 'off' -> 0."""
    text = text.lower().strip()
    if text in ("off", "no", "none", "false", "0", "never"):
        return 0
    if text in ("daily", "on", "yes", "true"):
        return 86400
    if text == "hourly":
        return 3600
    if text == "weekly":
        return 604800
    m = _DURATION.fullmatch(text)
    if not m:
        raise InvocationError(f"I couldn't understand the nag interval `{text}` (try `nag=12h` or `nag=off`).")
    seconds = int(float(m[1]) * _UNIT_SECONDS[m[2][0]])
    if 0 < seconds < MIN_NAG_SECONDS:
        raise InvocationError("Nagging more often than every 15 minutes would be rude.")
    return seconds


def parse_time_of_day(text: str) -> time:
    """'18:00', '6pm', '6:30pm', '9am' -> a time."""
    m = _TIME_OF_DAY.fullmatch(text.lower().strip())
    if not m:
        raise InvocationError(f"I couldn't understand the time `{text}` (try `6pm` or `18:00`).")
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3]:
        if not 1 <= hour <= 12:
            raise InvocationError(f"`{text}` isn't a valid time.")
        hour = hour % 12 + (12 if m[3] == "pm" else 0)
    if hour > 23 or minute > 59:
        raise InvocationError(f"`{text}` isn't a valid time.")
    return time(hour, minute)


def parse_nag(text: str) -> tuple[int, time | None]:
    """A per-poll ``nag=`` value: '12h', 'off', '6pm' (daily at 6pm), 'daily@6pm', '2d@18:00'.

    The time is None when none was given.
    """
    text = text.lower().strip()
    if "@" in text:
        interval, at = text.split("@", 1)
        return parse_duration(interval), parse_time_of_day(at)
    if re.search(r"am$|pm$|:", text):
        return 86400, parse_time_of_day(text)
    return parse_duration(text), None


def parse_lead(text: str) -> tuple[int, time | None]:
    """How far ahead a recurring poll goes out: '4d', '1w', '2d@9am', '0d@6pm' (on the first day)."""
    text = text.lower().strip()
    days_text, _, at = text.partition("@")
    m = _LEAD_DAYS.fullmatch(days_text.strip())
    if not m:
        raise InvocationError(f"I couldn't understand `lead={text}` (try `lead=4d` or `lead=2d@9am`).")
    days = int(m[1]) * (7 if m[2] and m[2].startswith("w") else 1)
    return days, parse_time_of_day(at) if at else None


def parse_repeat(text: str) -> tuple[int | None, str]:
    """Pull a repeat phrase out of the date text: ('every 2 weeks fri-sun') -> (2, 'fri-sun').

    'every friday' / 'every weekend' repeat weekly and leave 'friday' / 'weekend' as the dates.
    """
    m = _REPEAT.search(text.lower())
    if not m:
        return None, text
    rest = (text[: m.start()] + " " + text[m.end() :]).strip()
    if m["n"]:
        weeks = int(m["n"])
    elif m["biweekly"] or m["other"]:
        weeks = 2
    else:
        weeks = 1
    if m["what"]:
        rest = f"{m['what']} {rest}".strip()
    return weeks, " ".join(rest.split())


def extract_mentions(text: str) -> tuple[set[int], set[int]]:
    """User and role ids mentioned in raw message text."""
    return {int(i) for i in _USER_MENTION.findall(text)}, {int(i) for i in _ROLE_MENTION.findall(text)}


def parse_invocation(content: str) -> Invocation:
    inv = Invocation()
    text = _MENTION.sub(" ", content)

    if m := _TITLE.search(text):
        inv.title = next(g for g in m.groups() if g).strip() or DEFAULT_TITLE
        text = text[: m.start()] + " " + text[m.end() :]

    if m := _NAG.search(text):
        inv.nag_seconds, inv.nag_time = parse_nag(m[1])
        text = text[: m.start()] + " " + text[m.end() :]
    elif m := _NO_NAG.search(text.lower()):
        inv.nag_seconds = 0
        text = text[: m.start()] + " " + text[m.end() :]

    if m := _LEAD.search(text):
        inv.lead_days, inv.lead_time = parse_lead(m[1])
        text = text[: m.start()] + " " + text[m.end() :]

    text = " ".join(text.split())
    if text.lower() in ("help", "?", "-h", "--help"):
        inv.help = True
        text = ""
    elif _LIST_RECURRING.fullmatch(text.lower()):
        inv.list_recurring = True
        text = ""
    inv.repeat_weeks, inv.date_text = parse_repeat(text)
    if inv.repeat_weeks is None and (inv.lead_days is not None):
        raise InvocationError("`lead=` only applies to repeating polls (e.g. `every week lead=3d`).")
    return inv
