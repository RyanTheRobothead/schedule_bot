"""Core data types shared across the bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from enum import IntEnum


class Status(IntEnum):
    UNAVAILABLE = 0
    TENTATIVE = 1
    AVAILABLE = 2
    PREFERRED = 3

    @property
    def emoji(self) -> str:
        return _EMOJI[self]

    @property
    def label(self) -> str:
        return self.name.capitalize()


_EMOJI = {
    Status.PREFERRED: "⭐",
    Status.AVAILABLE: "✅",
    Status.TENTATIVE: "❔",
    Status.UNAVAILABLE: "❌",
}

# Display order, best first.
STATUS_ORDER = (Status.PREFERRED, Status.AVAILABLE, Status.TENTATIVE, Status.UNAVAILABLE)

NO_ANSWER_EMOJI = "⬜"


@dataclass
class Poll:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    creator_id: int
    title: str
    start: date
    end: date
    nag_interval: int  # seconds; 0 disables nagging
    next_nag_at: float | None
    closed: bool
    nag_time: time | None = None  # wall-clock time for whole-day intervals

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"


@dataclass
class UserResponse:
    default: Status | None = None
    overrides: dict[date, Status] = field(default_factory=dict)

    def status_on(self, day: date) -> Status | None:
        return self.overrides.get(day, self.default)

    @property
    def has_answered(self) -> bool:
        return self.default is not None or bool(self.overrides)


def carry_over(
    previous_days: list[date], previous: UserResponse, new_days: list[date]
) -> UserResponse:
    """Map an earlier answer onto new days by weekday ("same as last time").

    If a weekday appeared more than once before, its latest occurrence wins.
    """
    by_weekday: dict[int, Status | None] = {}
    for day in sorted(previous_days):
        by_weekday[day.weekday()] = previous.overrides.get(day)
    overrides = {d: s for d in new_days if (s := by_weekday.get(d.weekday())) is not None}
    return UserResponse(previous.default, overrides)
