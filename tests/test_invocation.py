import pytest

from schedule_bot.invocation import DEFAULT_TITLE, InvocationError, parse_duration, parse_invocation


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
