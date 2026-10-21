"""Parsing the text of an ``@schedule_bot ...`` message."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MENTION = re.compile(r"<@[!&]?\d+>|@everyone|@here")
_TITLE = re.compile(r"\"([^\"]+)\"|“([^”]+)”")
_NAG = re.compile(r"\bnag\s*[=:]\s*(\S+)")
_NO_NAG = re.compile(r"\b(?:no[\s-]?nag|nag\s+off)\b")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}

DEFAULT_TITLE = "Game night"
MIN_NAG_SECONDS = 15 * 60


class InvocationError(ValueError):
    pass


@dataclass
class Invocation:
    title: str = DEFAULT_TITLE
    nag_seconds: int | None = None  # None means "use the configured default"
    date_text: str = ""
    help: bool = False


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


def parse_invocation(content: str) -> Invocation:
    inv = Invocation()
    text = _MENTION.sub(" ", content)

    if m := _TITLE.search(text):
        inv.title = next(g for g in m.groups() if g).strip() or DEFAULT_TITLE
        text = text[: m.start()] + " " + text[m.end() :]

    if m := _NAG.search(text):
        inv.nag_seconds = parse_duration(m[1])
        text = text[: m.start()] + " " + text[m.end() :]
    elif m := _NO_NAG.search(text.lower()):
        inv.nag_seconds = 0
        text = text[: m.start()] + " " + text[m.end() :]

    text = " ".join(text.split())
    if text.lower() in ("help", "?", "-h", "--help"):
        inv.help = True
        text = ""
    inv.date_text = text
    return inv
