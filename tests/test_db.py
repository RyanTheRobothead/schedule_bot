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
