from datetime import time

import pytest

from schedule_bot.invocation import (
    DEFAULT_TITLE,
    InvocationError,
    extract_mentions,
    parse_duration,
    parse_invocation,
    parse_nag,
    parse_time_of_day,
)


def test_bare_mention():
    inv = parse_invocation("<@123>")
    assert (inv.title, inv.nag_seconds, inv.date_text, inv.help) == (DEFAULT_TITLE, None, "", False)


def test_full_invocation():
    inv = parse_invocation('<@!123> fri-sun "Bob\'s one-shot" <@&456> <@789> nag=12h')
    assert inv.title == "Bob's one-shot"
    assert inv.nag_seconds == 12 * 3600
    assert inv.date_text == "fri-sun"


def test_no_nag_and_smart_quotes():
    inv = parse_invocation("<@1> “Board games” next weekend no nag")
    assert inv.title == "Board games"
    assert inv.nag_seconds == 0
    assert inv.date_text == "next weekend"


def test_help():
    assert parse_invocation("<@1> help").help


@pytest.mark.parametrize(
    "text, seconds",
    [("off", 0), ("daily", 86400), ("12h", 43200), ("2d", 172800), ("90m", 5400), ("1 week", 604800)],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["5m", "soon"])
def test_parse_duration_errors(text):
    with pytest.raises(InvocationError):
        parse_duration(text)


@pytest.mark.parametrize(
    "text, expected",
    [("18:00", time(18)), ("6pm", time(18)), ("6:30pm", time(18, 30)), ("12am", time(0)), ("12pm", time(12)), ("9", time(9))],
)
def test_parse_time_of_day(text, expected):
    assert parse_time_of_day(text) == expected


@pytest.mark.parametrize("text", ["13pm", "25:00", "6:75", "noonish"])
def test_parse_time_of_day_errors(text):
    with pytest.raises(InvocationError):
        parse_time_of_day(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12h", (43200, None)),
        ("off", (0, None)),
        ("9am", (86400, time(9))),
        ("daily@6pm", (86400, time(18))),
        ("2d@18:30", (172800, time(18, 30))),
    ],
)
def test_parse_nag(text, expected):
    assert parse_nag(text) == expected


def test_nag_time_in_invocation():
    inv = parse_invocation("<@1> next week nag=2d@7pm")
    assert (inv.nag_seconds, inv.nag_time, inv.date_text) == (172800, time(19), "next week")


def test_extract_mentions():
    assert extract_mentions("<@1> <@!2> <@&3> @everyone") == ({1, 2}, {3})
