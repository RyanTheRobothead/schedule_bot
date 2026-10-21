"""SQLite persistence. Small and synchronous: a friend group's polls are tiny."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, time as dtime
from pathlib import Path

from .models import Poll, Recurrence, Status, UserResponse

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
    created_at   REAL NOT NULL,
    nag_time     TEXT,
    recurrence_id INTEGER REFERENCES recurrences(id)
);
CREATE TABLE IF NOT EXISTS recurrences (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL,
    channel_id     INTEGER NOT NULL,
    creator_id     INTEGER NOT NULL,
    title          TEXT NOT NULL,
    period_start   TEXT NOT NULL,
    period_days    INTEGER NOT NULL,
    interval_weeks INTEGER NOT NULL,
    lead_days      INTEGER NOT NULL,
    post_time      TEXT NOT NULL,
    nag_interval   INTEGER NOT NULL,
    nag_time       TEXT,
    user_ids       TEXT NOT NULL DEFAULT '',
    role_ids       TEXT NOT NULL DEFAULT '',
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL
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
        self._migrate()

    def _migrate(self) -> None:
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(polls)")}
        if "nag_time" not in columns:  # added in 0.2
            with self.conn:
                self.conn.execute("ALTER TABLE polls ADD COLUMN nag_time TEXT")
        if "recurrence_id" not in columns:  # added in 0.3
            with self.conn:
                self.conn.execute("ALTER TABLE polls ADD COLUMN recurrence_id INTEGER REFERENCES recurrences(id)")

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
        next_nag_at: float | None = None,
        nag_time: dtime | None = None,
        created_at: float | None = None,
        recurrence_id: int | None = None,
    ) -> Poll:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO polls (guild_id, channel_id, creator_id, title, start_date, end_date,"
                " nag_interval, next_nag_at, created_at, nag_time, recurrence_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    channel_id,
                    creator_id,
                    title,
                    start.isoformat(),
                    end.isoformat(),
                    nag_interval,
                    next_nag_at,
                    time.time() if created_at is None else created_at,
                    nag_time.strftime("%H:%M") if nag_time else None,
                    recurrence_id,
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

    def poll_by_message(self, message_id: int) -> Poll | None:
        row = self.conn.execute("SELECT * FROM polls WHERE message_id = ?", (message_id,)).fetchone()
        return _row_to_poll(row) if row else None

    def open_polls_for_recurrence(self, recurrence_id: int) -> list[Poll]:
        rows = self.conn.execute(
            "SELECT * FROM polls WHERE recurrence_id = ? AND closed = 0 AND message_id IS NOT NULL", (recurrence_id,)
        )
        return [_row_to_poll(r) for r in rows]

    def recurrence_has_poll(self, recurrence_id: int, end: date) -> bool:
        """Whether this period's poll exists already. Keyed on the end date, since the start can be clamped."""
        row = self.conn.execute(
            "SELECT 1 FROM polls WHERE recurrence_id = ? AND end_date = ?", (recurrence_id, end.isoformat())
        ).fetchone()
        return row is not None

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

    # --- recurrences ---------------------------------------------------------

    def create_recurrence(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        title: str,
        period_start: date,
        period_days: int,
        interval_weeks: int,
        lead_days: int,
        post_time: dtime,
        nag_interval: int,
        nag_time: dtime | None,
        user_ids: set[int],
        role_ids: set[int],
    ) -> Recurrence:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO recurrences (guild_id, channel_id, creator_id, title, period_start, period_days,"
                " interval_weeks, lead_days, post_time, nag_interval, nag_time, user_ids, role_ids, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    channel_id,
                    creator_id,
                    title,
                    period_start.isoformat(),
                    period_days,
                    interval_weeks,
                    lead_days,
                    post_time.strftime("%H:%M"),
                    nag_interval,
                    nag_time.strftime("%H:%M") if nag_time else None,
                    ",".join(map(str, sorted(user_ids))),
                    ",".join(map(str, sorted(role_ids))),
                    time.time(),
                ),
            )
        return self.get_recurrence(cur.lastrowid)

    def get_recurrence(self, recurrence_id: int) -> Recurrence | None:
        row = self.conn.execute("SELECT * FROM recurrences WHERE id = ?", (recurrence_id,)).fetchone()
        return _row_to_recurrence(row) if row else None

    def active_recurrences(self, guild_id: int | None = None) -> list[Recurrence]:
        if guild_id is None:
            rows = self.conn.execute("SELECT * FROM recurrences WHERE active = 1 ORDER BY id")
        else:
            rows = self.conn.execute(
                "SELECT * FROM recurrences WHERE active = 1 AND guild_id = ? ORDER BY id", (guild_id,)
            )
        return [_row_to_recurrence(r) for r in rows]

    def set_recurrence_period(self, recurrence_id: int, period_start: date) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE recurrences SET period_start = ? WHERE id = ?", (period_start.isoformat(), recurrence_id)
            )

    def deactivate_recurrence(self, recurrence_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE recurrences SET active = 0 WHERE id = ?", (recurrence_id,))

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

    def replace_response(self, poll_id: int, user_id: int, response: UserResponse) -> None:
        self.add_participant(poll_id, user_id)
        with self.conn:
            self.conn.execute("DELETE FROM defaults WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))
            self.conn.execute("DELETE FROM overrides WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))
            if response.default is not None:
                self.conn.execute(
                    "INSERT INTO defaults (poll_id, user_id, status) VALUES (?, ?, ?)",
                    (poll_id, user_id, int(response.default)),
                )
            self.conn.executemany(
                "INSERT INTO overrides (poll_id, user_id, day, status) VALUES (?, ?, ?, ?)",
                [(poll_id, user_id, d.isoformat(), int(s)) for d, s in response.overrides.items()],
            )

    def previous_answer(self, guild_id: int, user_id: int, before_poll_id: int) -> tuple[Poll, UserResponse] | None:
        """The user's answer to the most recent earlier poll in this server they responded to."""
        row = self.conn.execute(
            "SELECT * FROM polls WHERE guild_id = ? AND id < ? AND ("
            " EXISTS (SELECT 1 FROM defaults d WHERE d.poll_id = polls.id AND d.user_id = ?)"
            " OR EXISTS (SELECT 1 FROM overrides o WHERE o.poll_id = polls.id AND o.user_id = ?)"
            ") ORDER BY id DESC LIMIT 1",
            (guild_id, before_poll_id, user_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_poll(row), self.response(row["id"], user_id)

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
        nag_time=dtime.fromisoformat(row["nag_time"]) if row["nag_time"] else None,
        recurrence_id=row["recurrence_id"],
    )


def _ids(text: str) -> set[int]:
    return {int(i) for i in text.split(",") if i}


def _row_to_recurrence(row: sqlite3.Row) -> Recurrence:
    return Recurrence(
        id=row["id"],
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        creator_id=row["creator_id"],
        title=row["title"],
        period_start=date.fromisoformat(row["period_start"]),
        period_days=row["period_days"],
        interval_weeks=row["interval_weeks"],
        lead_days=row["lead_days"],
        post_time=dtime.fromisoformat(row["post_time"]),
        nag_interval=row["nag_interval"],
        nag_time=dtime.fromisoformat(row["nag_time"]) if row["nag_time"] else None,
        user_ids=_ids(row["user_ids"]),
        role_ids=_ids(row["role_ids"]),
        active=bool(row["active"]),
    )
