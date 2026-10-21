"""Turning individual responses into a per-day breakdown and ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import Status, UserResponse


@dataclass
class DaySummary:
    day: date
    by_status: dict[Status, list[int]] = field(default_factory=lambda: {s: [] for s in Status})
    no_answer: list[int] = field(default_factory=list)  # answered the poll, but not for this day

    def count(self, status: Status) -> int:
        return len(self.by_status[status])

    @property
    def can_make_it(self) -> int:
        return self.count(Status.PREFERRED) + self.count(Status.AVAILABLE)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_status.values()) + len(self.no_answer)

    @property
    def rank_key(self) -> tuple:
        # Anybody being unavailable outweighs everything else; then fewest maybes,
        # then most enthusiasm, then earliest date.
        return (
            self.count(Status.UNAVAILABLE),
            self.count(Status.TENTATIVE) + len(self.no_answer),
            -self.count(Status.PREFERRED),
            self.day,
        )


def summarize(days: list[date], responses: dict[int, UserResponse]) -> list[DaySummary]:
    """Per-day breakdown in date order. Users who haven't answered at all are excluded."""
    summaries = []
    for day in days:
        summary = DaySummary(day)
        for user_id, response in responses.items():
            if not response.has_answered:
                continue
            status = response.status_on(day)
            if status is None:
                summary.no_answer.append(user_id)
            else:
                summary.by_status[status].append(user_id)
        summaries.append(summary)
    return summaries


def rank(summaries: list[DaySummary]) -> list[DaySummary]:
    """Best days first. Days where nobody can make it are dropped."""
    return sorted((s for s in summaries if s.can_make_it > 0), key=lambda s: s.rank_key)
