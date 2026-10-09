"""Cron rules: five fields, a known time zone, at most every 5 minutes, fire times in the schedule's time zone."""
from datetime import UTC, datetime

import pytest

from knowledge_admin.schedules import (BAD_CRON, BAD_TIMEZONE, TOO_OFTEN, ScheduleError, describe, next_after,
                                       upcoming, validate)


@pytest.mark.parametrize('cron', ['*/15 * * * *', '*/5 * * * *', '0 6 * * *', '0 7 * * 1-5', ' 0  *  * * * '])
def test_valid(cron):
    validate(cron, 'Europe/Belgrade')


@pytest.mark.parametrize('cron,message', [
    ('', BAD_CRON), ('* * * *', BAD_CRON), ('0 0 * * * *', BAD_CRON), ('61 * * * *', BAD_CRON), ('every hour', BAD_CRON),
    ('*/4 * * * *', TOO_OFTEN), ('* * * * *', TOO_OFTEN), ('0-4 * * * *', TOO_OFTEN), ('0,3 9 * * *', TOO_OFTEN),
])
def test_invalid(cron, message):
    with pytest.raises(ScheduleError, match=message.replace('*', r'\*').replace('.', r'\.')):
        validate(cron, 'UTC')


@pytest.mark.parametrize('timezone', ['Mars/Base', '', 'utc+2', '../etc/passwd'])
def test_unknown_time_zone(timezone):
    with pytest.raises(ScheduleError) as error:
        validate('0 * * * *', timezone)
    assert str(error.value) == BAD_TIMEZONE


def test_fire_times_follow_daylight_saving_time():
    # Belgrade leaves summer time on 2026-10-25: 09:00 local is 07:00 UTC before and 08:00 UTC after.
    first = next_after('0 9 * * *', 'Europe/Belgrade', datetime(2026, 10, 23, 12, tzinfo=UTC))
    assert first == datetime(2026, 10, 24, 7, tzinfo=UTC)
    assert next_after('0 9 * * *', 'Europe/Belgrade', first) == datetime(2026, 10, 25, 8, tzinfo=UTC)


def test_upcoming_is_ascending_utc():
    runs = upcoming('*/15 * * * *', 'UTC', datetime(2026, 10, 9, 10, 7, tzinfo=UTC), 3)
    assert runs == [datetime(2026, 10, 9, 10, 15, tzinfo=UTC), datetime(2026, 10, 9, 10, 30, tzinfo=UTC),
                    datetime(2026, 10, 9, 10, 45, tzinfo=UTC)]
    assert all(run.tzinfo == UTC for run in runs)


def test_describe_presets_and_custom():
    assert describe('*/15 * * * *') == 'Every 15 minutes'
    assert describe(' 0  6 * * * ') == 'Every day at 06:00'
    assert describe('0 7 * * 1-5') == '0 7 * * 1-5'
