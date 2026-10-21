from datetime import date

import pytest

from schedule_bot.dates import DateRangeError, next_week, parse_date_range

TUE = date(2026, 10, 20)


def d(month, day, year=2026):
    return date(year, month, day)


@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 10, 19), (d(10, 26), d(11, 1))),  # Monday -> the following Monday
        (TUE, (d(10, 26), d(11, 1))),
        (date(2026, 10, 25), (d(10, 26), d(11, 1))),  # Sunday -> tomorrow
    ],
)
def test_next_week(today, expected):
    assert next_week(today) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", (d(10, 26), d(11, 1))),
        ("next week", (d(10, 26), d(11, 1))),
        ("this week", (d(10, 20), d(10, 25))),
        ("weekend", (d(10, 24), d(10, 25))),
        ("next weekend", (d(10, 31), d(11, 1))),
        ("next 10 days", (d(10, 20), d(10, 29))),
        ("2 weeks", (d(10, 26), d(11, 8))),
        ("fri-sun", (d(10, 23), d(10, 25))),
        ("Friday", (d(10, 23), d(10, 23))),
        ("next fri", (d(10, 30), d(10, 30))),
        ("sat to tue", (d(10, 24), d(10, 27))),
        ("oct 30 - nov 3", (d(10, 30), d(11, 3))),
        ("Oct 25-31", (d(10, 25), d(10, 31))),
        ("Oct 28-3", (d(10, 28), d(11, 3))),
        ("11/1-11/7", (d(11, 1), d(11, 7))),
        ("11/1 to 11/7", (d(11, 1), d(11, 7))),
        ("2026-11-01..2026-11-07", (d(11, 1), d(11, 7))),
        ("2026-11-01 to 2026-11-07", (d(11, 1), d(11, 7))),
        ("from November 2 through November 8", (d(11, 2), d(11, 8))),
        ("tomorrow", (d(10, 21), d(10, 21))),
        ("10/18-10/24", (d(10, 20), d(10, 24))),  # already-past days are clipped off
    ],
)
def test_parse_date_range(text, expected):
    assert parse_date_range(text, TUE) == expected


def test_year_rollover():
    today = date(2026, 12, 20)
    assert parse_date_range("dec 28 - jan 3", today) == (d(12, 28), d(1, 3, 2027))
    assert parse_date_range("jan 4-10", today) == (d(1, 4, 2027), d(1, 10, 2027))


def test_dmy_order():
    assert parse_date_range("1/11-7/11", TUE, "DMY") == (d(11, 1), d(11, 7))


@pytest.mark.parametrize(
    "text",
    [
        "next 40 days",  # too long
        "nov 5 - oct 30 2026",  # end before start
        "2026-01-01..2026-01-05",  # in the past
        "flibbertigibbet",
        "a to b to c",
    ],
)
def test_parse_date_range_errors(text):
    with pytest.raises(DateRangeError):
        parse_date_range(text, TUE)


def test_startup_warmup_phrase_never_crashes_unexpectedly():
    # bot.setup_hook parses this to warm up dateparser, suppressing only DateRangeError
    for day in range(1, 32):
        try:
            parse_date_range("jan 1 - jan 2", date(2027, 1, day))
        except DateRangeError:
            pass
