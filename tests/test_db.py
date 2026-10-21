from datetime import date, timedelta

from schedule_bot.db import Database
from schedule_bot.models import Status
from schedule_bot.render import panel_text, poll_embed

D = [date(2026, 10, 26) + timedelta(days=i) for i in range(7)]


def make_poll(db):
    return db.create_poll(
        guild_id=1,
        channel_id=2,
        creator_id=10,
        title="Game night",
        start=D[0],
        end=D[-1],
        nag_interval=86400,
        participants={10, 11, 12},
    )


def test_three_interaction_flow():
    """'Default available, except Tuesday unavailable and Friday preferred.'"""
    db = Database(":memory:")
    poll = make_poll(db)
    db.set_default(poll.id, 11, Status.AVAILABLE)
    db.set_days_for_status(poll.id, 11, Status.UNAVAILABLE, [D[1]])
    db.set_days_for_status(poll.id, 11, Status.PREFERRED, [D[4]])

    r = db.response(poll.id, 11)
    assert [r.status_on(d) for d in D] == [
        Status.AVAILABLE, Status.UNAVAILABLE, Status.AVAILABLE, Status.AVAILABLE,
        Status.PREFERRED, Status.AVAILABLE, Status.AVAILABLE,
    ]  # fmt: skip


def test_day_moves_between_statuses_and_deselects():
    db = Database(":memory:")
    poll = make_poll(db)
    db.set_days_for_status(poll.id, 11, Status.UNAVAILABLE, [D[1], D[2]])
    db.set_days_for_status(poll.id, 11, Status.PREFERRED, [D[2]])
    assert db.response(poll.id, 11).overrides == {D[1]: Status.UNAVAILABLE, D[2]: Status.PREFERRED}
    db.set_days_for_status(poll.id, 11, Status.UNAVAILABLE, [])
    assert db.response(poll.id, 11).overrides == {D[2]: Status.PREFERRED}


def test_outsiders_join_and_render():
    db = Database(":memory:")
    poll = make_poll(db)
    db.set_default(poll.id, 99, Status.PREFERRED)
    responses = db.responses(poll.id)
    assert set(responses) == {10, 11, 12, 99}

    embed = poll_embed(poll, responses)
    assert "Responded (1/4)" in [f.name for f in embed.fields]
    assert "Best options" in embed.fields[0].name
    assert len(panel_text(poll, responses[99])) < 2000


def test_close():
    db = Database(":memory:")
    poll = make_poll(db)
    assert [p.id for p in db.open_polls()] == []  # no message yet
    db.set_message_id(poll.id, 555)
    assert [p.id for p in db.open_polls()] == [poll.id]
    db.close_poll(poll.id)
    assert db.open_polls() == []


def test_mention_limit():
    from schedule_bot.render import mentions

    assert mentions([3, 1, 2]) == "<@1> <@2> <@3>"
    assert mentions(range(100), 2) == "<@0> <@1> and 98 more"


def test_breakdown_labels_who_is_out_or_maybe():
    from schedule_bot.render import breakdown_lines
    from schedule_bot.scoring import summarize
    from schedule_bot.models import UserResponse

    responses = {1: UserResponse(Status.AVAILABLE), 2: UserResponse(Status.UNAVAILABLE), 3: UserResponse(Status.TENTATIVE)}
    (line,) = breakdown_lines(summarize(D[:1], responses), None)
    assert line.endswith("— out: <@2> · maybe: <@3>")


V0_1_POLLS = """
CREATE TABLE polls (
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
"""


def test_migrates_v0_1_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(V0_1_POLLS)
    conn.execute(
        "INSERT INTO polls (guild_id, channel_id, message_id, creator_id, title, start_date, end_date,"
        " nag_interval, next_nag_at, created_at) VALUES (1, 2, 3, 4, 'Old', '2026-10-26', '2026-11-01', 86400, 5, 6)"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    (poll,) = db.open_polls()
    assert poll.title == "Old" and poll.nag_time is None and poll.recurrence_id is None
    assert db.active_recurrences() == []
    Database(path)  # reopening an already-migrated database is fine


def test_nag_time_round_trip():
    from datetime import time

    db = Database(":memory:")
    poll = db.create_poll(
        guild_id=1, channel_id=2, creator_id=3, title="T", start=D[0], end=D[-1],
        nag_interval=86400, nag_time=time(18, 30), next_nag_at=123.0, participants={3},
    )  # fmt: skip
    assert (poll.nag_time, poll.next_nag_at) == (time(18, 30), 123.0)


def test_same_as_last_time():
    from schedule_bot.models import UserResponse, carry_over

    db = Database(":memory:")
    first = make_poll(db)  # Mon Oct 26 - Sun Nov 1
    db.set_default(first.id, 11, Status.AVAILABLE)
    db.set_days_for_status(first.id, 11, Status.UNAVAILABLE, [D[1]])  # Tuesday
    db.set_days_for_status(first.id, 11, Status.PREFERRED, [D[4]])  # Friday
    other_guild = db.create_poll(
        guild_id=999, channel_id=2, creator_id=10, title="Elsewhere", start=D[0], end=D[-1],
        nag_interval=0, participants={11},
    )  # fmt: skip
    db.set_default(other_guild.id, 11, Status.UNAVAILABLE)
    second = db.create_poll(
        guild_id=1, channel_id=2, creator_id=10, title="Next", start=D[-1] + timedelta(days=1),
        end=D[-1] + timedelta(days=7), nag_interval=0, participants={11},
    )  # fmt: skip

    assert db.previous_answer(1, 12, second.id) is None
    prev_poll, prev_resp = db.previous_answer(1, 11, second.id)
    assert prev_poll.id == first.id

    db.replace_response(second.id, 11, carry_over(prev_poll.days, prev_resp, second.days))
    r = db.response(second.id, 11)
    assert r.default == Status.AVAILABLE
    assert {d.strftime("%a"): s for d, s in r.overrides.items()} == {"Tue": Status.UNAVAILABLE, "Fri": Status.PREFERRED}


def test_carry_over_latest_weekday_wins():
    from schedule_bot.models import UserResponse, carry_over

    two_weeks = [D[0] + timedelta(days=i) for i in range(14)]
    prev = UserResponse(Status.AVAILABLE, {two_weeks[1]: Status.UNAVAILABLE, two_weeks[9]: Status.TENTATIVE})
    # Tue of week 1 unavailable, Tue of week 2 tentative -> tentative; Wed of week 2 had no override
    new = carry_over(two_weeks, prev, [D[0] + timedelta(days=14 + i) for i in range(7)])
    assert list(new.overrides.values()) == [Status.TENTATIVE]


def test_nag_label():
    from datetime import time

    from schedule_bot.render import nag_label

    assert nag_label(86400, time(18)) == "daily at 6pm"
    assert nag_label(172800, time(9, 30)) == "every 2 days at 9:30am"
    assert nag_label(43200, time(18)) == "every 12h"
    assert nag_label(0, time(18)) == "off"
