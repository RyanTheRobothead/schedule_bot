from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_bot.invocation import InvocationError, parse_invocation, parse_lead, parse_repeat
from schedule_bot.recurrence import (
    RecurrenceError,
    catch_up,
    period_label,
    post_at,
    repeat_label,
    upcoming_post,
    validate,
)

CHI = ZoneInfo("America/Chicago")
SIX_PM = time(18)


def local(epoch):
    return datetime.fromtimestamp(epoch, CHI).replace(tzinfo=None)


def test_default_week_posts_thursday_evening():
    assert local(post_at(date(2026, 10, 26), 4, SIX_PM, CHI)) == datetime(2026, 10, 22, 18)


def test_post_time_kept_across_dst():
    # Period starting Mon Nov 2 posts Sat Oct 31 (CDT) / Thu Nov 5 for the next week (CST): both 6pm.
    assert local(post_at(date(2026, 11, 2), 2, SIX_PM, CHI)) == datetime(2026, 10, 31, 18)
    assert local(post_at(date(2026, 11, 9), 4, SIX_PM, CHI)) == datetime(2026, 11, 5, 18)


def test_catch_up_skips_finished_periods_but_keeps_one_under_way():
    mon = date(2026, 10, 5)
    assert catch_up(mon, 7, 1, date(2026, 10, 20)) == date(2026, 10, 19)  # two missed weeks skipped
    assert catch_up(mon, 3, 2, date(2026, 10, 20)) == date(2026, 10, 19)
    assert catch_up(mon, 7, 1, date(2026, 10, 5)) == mon


def test_upcoming_post_is_strictly_in_the_future():
    now = datetime(2026, 10, 22, 18, 1, tzinfo=CHI).timestamp()  # just after Thursday's post
    assert local(upcoming_post(date(2026, 10, 26), 4, SIX_PM, 1, now, CHI)) == datetime(2026, 10, 29, 18)


@pytest.mark.parametrize(
    "period_days, weeks, lead",
    [(14, 1, 4), (7, 1, 8), (7, 0, 4), (7, 9, 4), (26, 4, 4), (7, 1, -1)],
)
def test_validation(period_days, weeks, lead):
    with pytest.raises(RecurrenceError):
        validate(period_days, weeks, lead)


def test_validation_ok():
    validate(7, 1, 7)
    validate(14, 2, 0)


def test_labels():
    assert repeat_label(1) == "every week" and repeat_label(3) == "every 3 weeks"
    assert period_label(date(2026, 10, 26), 7) == "Mon–Sun"
    assert period_label(date(2026, 10, 30), 3) == "Fri–Sun"
    assert period_label(date(2026, 10, 31), 1) == "Sat"
    assert period_label(date(2026, 10, 26), 14) == "14 days from Mon"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("every week", (1, "")),
        ("weekly fri-sun", (1, "fri-sun")),
        ("every 2 weeks next 2 weeks", (2, "next 2 weeks")),
        ("every other week", (2, "")),
        ("biweekly", (2, "")),
        ("every friday", (1, "friday")),
        ("every weekend", (1, "weekend")),
        ("every thursday", (1, "thursday")),
        ("next week", (None, "next week")),
        ("weekend", (None, "weekend")),
    ],
)
def test_parse_repeat(text, expected):
    assert parse_repeat(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("4d", (4, None)), ("1w", (7, None)), ("2 days", (2, None)), ("3d@9am", (3, time(9))), ("0d@6pm", (0, time(18)))],
)
def test_parse_lead(text, expected):
    assert parse_lead(text) == expected


def test_recurring_invocation():
    inv = parse_invocation('<@1> every week "Game night" <@&5> lead=3d@7pm nag=12h')
    assert (inv.repeat_weeks, inv.date_text, inv.lead_days, inv.lead_time, inv.nag_seconds) == (
        1, "", 3, time(19), 43200,
    )  # fmt: skip
    assert parse_invocation("<@1> recurring").list_recurring
    assert parse_invocation("<@1> list repeating polls").list_recurring


def test_lead_without_repeat_is_an_error():
    with pytest.raises(InvocationError):
        parse_invocation("<@1> next week lead=3d")
