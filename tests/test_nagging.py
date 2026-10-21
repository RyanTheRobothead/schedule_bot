from datetime import datetime, time
from zoneinfo import ZoneInfo

from schedule_bot.nagging import first_nag_at, next_nag_at

CHI = ZoneInfo("America/Chicago")
SIX_PM = time(18, 0)
DAY = 86400


def ts(*args):
    return datetime(*args, tzinfo=CHI).timestamp()


def local(epoch):
    return datetime.fromtimestamp(epoch, CHI).replace(tzinfo=None)


def test_off():
    assert first_nag_at(ts(2026, 10, 20, 9), 0, SIX_PM, CHI) is None


def test_first_nag_same_evening_when_posted_in_the_morning():
    assert local(first_nag_at(ts(2026, 10, 20, 9), DAY, SIX_PM, CHI)) == datetime(2026, 10, 20, 18)


def test_first_nag_waits_a_day_when_posted_just_before():
    assert local(first_nag_at(ts(2026, 10, 20, 17, 55), DAY, SIX_PM, CHI)) == datetime(2026, 10, 21, 18)


def test_every_two_days_first_nag_at_least_a_day_out():
    assert local(first_nag_at(ts(2026, 10, 20, 9), 2 * DAY, SIX_PM, CHI)) == datetime(2026, 10, 21, 18)


def test_sub_day_intervals_and_no_time_stay_relative():
    created = ts(2026, 10, 20, 9)
    assert first_nag_at(created, 12 * 3600, SIX_PM, CHI) == created + 12 * 3600
    assert first_nag_at(created, DAY, None, CHI) == created + DAY
    assert next_nag_at(created, created + 60, 12 * 3600, SIX_PM, CHI) == created + 60 + 12 * 3600


def test_wall_time_kept_across_dst_fall_back():
    # America/Chicago falls back on Sun Nov 1 2026; that "day" is 25 hours long.
    sat = ts(2026, 10, 31, 18)
    sun = next_nag_at(sat, sat + 30, DAY, SIX_PM, CHI)
    assert local(sun) == datetime(2026, 11, 1, 18)
    assert sun - sat == 25 * 3600


def test_missed_slots_are_skipped_not_replayed():
    scheduled = ts(2026, 10, 20, 18)
    now = ts(2026, 10, 23, 12)  # bot was down for a few days
    assert local(next_nag_at(scheduled, now, DAY, SIX_PM, CHI)) == datetime(2026, 10, 23, 18)
