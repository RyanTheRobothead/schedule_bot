"""SQLite persistence. Small and synchronous: a friend group's polls are tiny."""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

from .models import Poll, Status, UserResponse

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    channel_id   INTEGER NOT NULL,
    message_id   INTEGER,
    creator_id   INTEGER NOT NULL,
    title        TEXT NOT NULL,
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    nag_interval INTEGER NOT NULL,
    next_nag_at  REAL,
    closed       INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS participants (
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS defaults (
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    status  INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS overrides (
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    status  INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id, day)
);
"""


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # --- polls -------------------------------------------------------------

    def create_poll(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        title: str,
        start: date,
        end: date,
        nag_interval: int,
        participants: set[int],
    ) -> Poll:
        now = time.time()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO polls (guild_id, channel_id, creator_id, title, start_date, end_date,"
                " nag_interval, next_nag_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    channel_id,
                    creator_id,
                    title,
                    start.isoformat(),
                    end.isoformat(),
                    nag_interval,
                    now + nag_interval if nag_interval else None,
                    now,
                ),
            )
            poll_id = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO participants (poll_id, user_id) VALUES (?, ?)",
                [(poll_id, uid) for uid in participants],
            )
        return self.get_poll(poll_id)

    def get_poll(self, poll_id: int) -> Poll | None:
        row = self.conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
        return _row_to_poll(row) if row else None

    def open_polls(self) -> list[Poll]:
        rows = self.conn.execute("SELECT * FROM polls WHERE closed = 0 AND message_id IS NOT NULL")
        return [_row_to_poll(r) for r in rows]

    def set_message_id(self, poll_id: int, message_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id))

    def set_next_nag(self, poll_id: int, when: float | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE polls SET next_nag_at = ? WHERE id = ?", (when, poll_id))

    def close_poll(self, poll_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE polls SET closed = 1, next_nag_at = NULL WHERE id = ?", (poll_id,))

    def delete_poll(self, poll_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM polls WHERE id = ?", (poll_id,))

    # --- participants & responses -----------------------------------------

    def participants(self, poll_id: int) -> set[int]:
        rows = self.conn.execute("SELECT user_id FROM participants WHERE poll_id = ?", (poll_id,))
        return {r["user_id"] for r in rows}

    def add_participant(self, poll_id: int, user_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO participants (poll_id, user_id) VALUES (?, ?)", (poll_id, user_id)
            )

    def set_default(self, poll_id: int, user_id: int, status: Status) -> None:
        self.add_participant(poll_id, user_id)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO defaults (poll_id, user_id, status) VALUES (?, ?, ?)",
                (poll_id, user_id, int(status)),
            )

    def set_days_for_status(self, poll_id: int, user_id: int, status: Status, days: list[date]) -> None:
        """Make ``days`` exactly the set of days overridden to ``status`` for this user.

        Days picked here are moved away from any other override status.
        """
        self.add_participant(poll_id, user_id)
        with self.conn:
            self.conn.execute(
                "DELETE FROM overrides WHERE poll_id = ? AND user_id = ? AND status = ?",
                (poll_id, user_id, int(status)),
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO overrides (poll_id, user_id, day, status) VALUES (?, ?, ?, ?)",
                [(poll_id, user_id, d.isoformat(), int(status)) for d in days],
            )

    def clear_overrides(self, poll_id: int, user_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM overrides WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))

    def response(self, poll_id: int, user_id: int) -> UserResponse:
        return self.responses(poll_id).get(user_id, UserResponse())

    def responses(self, poll_id: int) -> dict[int, UserResponse]:
        """Responses keyed by user, including an empty entry for every participant."""
        result = {uid: UserResponse() for uid in self.participants(poll_id)}
        for r in self.conn.execute("SELECT user_id, status FROM defaults WHERE poll_id = ?", (poll_id,)):
            result.setdefault(r["user_id"], UserResponse()).default = Status(r["status"])
        for r in self.conn.execute("SELECT user_id, day, status FROM overrides WHERE poll_id = ?", (poll_id,)):
            result.setdefault(r["user_id"], UserResponse()).overrides[date.fromisoformat(r["day"])] = Status(
                r["status"]
            )
        return result


def _row_to_poll(row: sqlite3.Row) -> Poll:
    return Poll(
        id=row["id"],
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        message_id=row["message_id"],
        creator_id=row["creator_id"],
        title=row["title"],
        start=date.fromisoformat(row["start_date"]),
        end=date.fromisoformat(row["end_date"]),
        nag_interval=row["nag_interval"],
        next_nag_at=row["next_nag_at"],
        closed=bool(row["closed"]),
    )
