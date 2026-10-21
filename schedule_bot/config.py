"""Configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from .invocation import parse_duration


def _default_db_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(state) / "schedule_bot" / "schedule_bot.db"


@dataclass
class Config:
    token: str
    db_path: Path
    tz: tzinfo
    date_order: str = "MDY"
    default_nag_seconds: int = 86400
    housekeeping_minutes: float = 5

    @classmethod
    def from_env(cls) -> Config:
        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            raise SystemExit("DISCORD_TOKEN is not set.")
        tz_name = os.environ.get("SCHEDULE_BOT_TZ")
        return cls(
            token=token,
            db_path=Path(os.environ.get("SCHEDULE_BOT_DB") or _default_db_path()),
            tz=ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo,
            date_order=os.environ.get("SCHEDULE_BOT_DATE_ORDER", "MDY").upper(),
            default_nag_seconds=parse_duration(os.environ.get("SCHEDULE_BOT_NAG", "daily")),
        )

    def today(self) -> date:
        return datetime.now(self.tz).date()
