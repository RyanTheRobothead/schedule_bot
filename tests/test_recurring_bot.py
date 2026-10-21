"""Drives ScheduleBot's recurring-poll logic against fake Discord objects (no network)."""

import asyncio
import itertools
from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord
import pytest

from schedule_bot import bot as bot_module
from schedule_bot.bot import ScheduleBot
from schedule_bot.config import Config
from schedule_bot.invocation import parse_invocation

CHI = ZoneInfo("America/Chicago")
_ids = itertools.count(1000)


class FakeConfig(Config):
    def today(self):
        return datetime.fromtimestamp(bot_module.time.time(), CHI).date()


class FakeChannel:
    def __init__(self, member_ids=(11, 12), fail=None):
        self.id = 2
        self.sent = []
        self.fail = fail
        members = {uid: SimpleNamespace(id=uid, bot=False) for uid in member_ids}
        self.members = list(members.values())
        self.guild = SimpleNamespace(id=1, get_member=members.get, get_role=lambda rid: None)

    def __str__(self):
        return "fake"

    async def send(self, content=None, **kwargs):
        if self.fail:
            raise self.fail
        message = SimpleNamespace(id=next(_ids), content=content, **kwargs)
        self.sent.append(message)
        return message


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(bot_module.time, "time", lambda: now["t"])

    def set_to(*args):
        now["t"] = datetime(*args, tzinfo=CHI).timestamp()

    return set_to


def make_bot(channel, monkeypatch):
    bot = ScheduleBot(FakeConfig(token="x", db_path=":memory:", tz=CHI))
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel)
    return bot


def create(bot, channel, text):
    inv = parse_invocation(text)
    author = SimpleNamespace(id=11)
    return asyncio.run(bot._create_recurrence(channel, author, inv, [], []))


def polls(bot):
    return bot.db.conn.execute("SELECT start_date, end_date, recurrence_id FROM polls ORDER BY id").fetchall()


def run(bot, rec_id):
    asyncio.run(bot._run_recurrence(bot.db.get_recurrence(rec_id)))


def test_posts_once_when_due_and_advances(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)  # Tuesday
    rec = create(bot, channel, "<@1> every week")
    assert rec.period_start == date(2026, 10, 26)

    run(bot, rec.id)  # not due until Thu 6pm
    assert polls(bot) == []

    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)
    run(bot, rec.id)
    assert [tuple(r) for r in polls(bot)] == [("2026-10-26", "2026-11-01", rec.id)]
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 11, 2)
    assert "Repeats every week, next poll Thu Oct 29 at 6pm" in channel.sent[0].embed.footer.text


def test_crash_between_post_and_advance_does_not_double_post(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)
    bot.db.set_recurrence_period(rec.id, date(2026, 10, 26))  # as if the advance never happened
    run(bot, rec.id)
    assert len(polls(bot)) == 1 and len(channel.sent) == 1
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 11, 2)


def test_transient_error_retries_next_time(clock, monkeypatch):
    channel = FakeChannel(fail=discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "boom"))
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)
    assert polls(bot) == [] and bot.db.get_recurrence(rec.id).period_start == date(2026, 10, 26)
    channel.fail = None
    run(bot, rec.id)
    assert len(polls(bot)) == 1


def test_lost_channel_stops_the_recurrence(clock, monkeypatch):
    channel = FakeChannel(fail=discord.Forbidden(SimpleNamespace(status=403, reason="no"), "no"))
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)
    assert polls(bot) == [] and not bot.db.get_recurrence(rec.id).active


def test_missed_weeks_are_skipped_and_week_under_way_is_posted(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 11, 11, 9)  # bot was down for weeks; now Wed of the Nov 9 week
    run(bot, rec.id)
    assert [tuple(r)[:2] for r in polls(bot)] == [("2026-11-11", "2026-11-15")]  # clamped to today
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 11, 16)


def test_this_week_repeats_monday_to_sunday(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)  # Tuesday
    rec = create(bot, channel, "<@1> every week this week")
    assert (rec.period_start, rec.period_days) == (date(2026, 10, 19), 7)
    run(bot, rec.id)  # under way, so posted straight away for the rest of the week
    assert [tuple(r)[:2] for r in polls(bot)] == [("2026-10-20", "2026-10-25")]


def test_custom_lead_and_cadence(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every 2 weeks fri-sun lead=1d@9am")
    assert (rec.period_start, rec.period_days, rec.interval_weeks, rec.lead_days, rec.post_time) == (
        date(2026, 10, 23), 3, 2, 1, time(9),
    )  # fmt: skip
    clock(2026, 10, 22, 9, 1)
    run(bot, rec.id)
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 11, 6)


def test_invitees_who_left_stop_the_recurrence(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    member = SimpleNamespace(id=99, bot=False)
    inv = parse_invocation("<@1> every week")
    rec = asyncio.run(bot._create_recurrence(channel, SimpleNamespace(id=11), inv, [member], []))
    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)  # user 99 isn't in the guild anymore
    assert polls(bot) == [] and not bot.db.get_recurrence(rec.id).active
    assert "Stopped repeating" in channel.sent[0].content


def test_empty_list_has_no_view_key(monkeypatch):
    # InteractionResponse.send_message(view=None) raises, so the key must be absent
    bot = make_bot(FakeChannel(), monkeypatch)
    assert "view" not in bot._recurring_list(999)


def test_concurrent_runs_post_once(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 10, 22, 18, 2)

    async def both():
        await asyncio.gather(bot._run_recurrence(rec), bot._run_recurrence(rec))

    asyncio.run(both())
    assert len(polls(bot)) == 1


def test_stopped_recurrence_does_not_post(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    bot.db.deactivate_recurrence(rec.id)
    clock(2026, 10, 22, 18, 2)
    asyncio.run(bot._run_recurrence(rec))  # stale object still says active
    assert polls(bot) == []


class FakeResponse:
    def __init__(self, log):
        self.log = log

    async def send_message(self, content=None, **kwargs):
        self.log.append(("send", content, kwargs))

    async def edit_message(self, **kwargs):
        self.log.append(("edit", kwargs.get("content"), kwargs))


class FakeFollowup:
    def __init__(self, log):
        self.log = log

    async def send(self, content=None, **kwargs):
        self.log.append(("followup", content, kwargs))


def interaction(user_id=11, moderator=False, message_content=None):
    log = []
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
        permissions=SimpleNamespace(manage_messages=moderator),
        message=SimpleNamespace(id=next(_ids), content=message_content) if message_content is not None else None,
        response=FakeResponse(log),
        followup=FakeFollowup(log),
        log=log,
    )


def test_skip_next_then_undo(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")

    it = interaction(message_content=bot_module.LIST_HEADER)
    asyncio.run(bot.skip_next(it, rec.id))
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 11, 2)
    (edit, notice) = it.log
    assert edit[0] == "edit" and "Nov 2 – Sun Nov 8" in edit[1]  # list re-rendered
    assert "skipped **Game night** for Mon Oct 26 – Sun Nov 1" in notice[1]
    assert "next poll goes out Thu Oct 29 at 6pm" in notice[1]

    clock(2026, 10, 22, 18, 2)  # Thursday's post time passes: nothing goes out for the skipped week
    run(bot, rec.id)
    assert polls(bot) == []

    undo = interaction(message_content="⏭️ skipped")
    asyncio.run(bot.undo_skip(undo, rec.id, date(2026, 10, 26)))
    assert "un-skipped" in undo.log[-1][1]
    assert [tuple(r)[:2] for r in polls(bot)] == [("2026-10-26", "2026-11-01")]  # already due, so posted

    again = interaction(message_content="⏭️ skipped")
    asyncio.run(bot.undo_skip(again, rec.id, date(2026, 10, 26)))
    assert again.log == [("send", "It's too late to undo that skip.", {"ephemeral": True})]


def test_skip_next_needs_creator_or_moderator(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    stranger = interaction(user_id=12, message_content=bot_module.LIST_HEADER)
    asyncio.run(bot.skip_next(stranger, rec.id))
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 10, 26)
    assert "Only <@11>" in stranger.log[0][1]
    asyncio.run(bot.skip_next(interaction(user_id=12, moderator=True, message_content=""), rec.id))
    assert bot.db.get_recurrence(rec.id).period_start == date(2026, 11, 2)


def test_skip_this_time_closes_the_posted_poll(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)
    (poll,) = bot.db.open_polls()

    edits = []

    async def fake_edit(p):
        edits.append(p)

    monkeypatch.setattr(bot, "_edit_poll_message", fake_edit)
    it = interaction()
    asyncio.run(bot.skip_poll(it, poll.id))
    skipped = bot.db.get_poll(poll.id)
    assert skipped.closed and skipped.skipped and skipped.next_nag_at is None
    assert "skipped **Game night** this time" in it.log[0][1]
    assert "next poll goes out Thu Oct 29 at 6pm, for Mon Nov 2 – Sun Nov 8" in it.log[0][1]
    assert edits[0].skipped
    assert bot._render_poll(skipped)[0].title == "⏭️ Game night (skipped this time)"
    assert bot.db.get_recurrence(rec.id).active  # only this one is skipped


def test_repeating_poll_has_skip_button(clock, monkeypatch):
    channel = FakeChannel()
    bot = make_bot(channel, monkeypatch)
    clock(2026, 10, 20, 12)
    rec = create(bot, channel, "<@1> every week")
    clock(2026, 10, 22, 18, 2)
    run(bot, rec.id)
    rows = channel.sent[0].view.to_components()
    assert [c["label"] for c in rows[1]["components"]] == [
        "Same as last time", "Fine-tune days", "Close poll", "Skip this time", "Stop repeating",
    ]  # fmt: skip
    async def list_components():  # views need a running event loop
        return bot._recurring_list(1)["view"].to_components()

    listing = asyncio.run(list_components())
    assert [c["label"] for c in listing[0]["components"]] == [f"#{rec.id}: skip Mon Nov 2 – Sun Nov 8", f"Stop #{rec.id}"]
