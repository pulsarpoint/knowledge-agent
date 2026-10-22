"""Schedules run one target (a named job, e.g. the Jira pull) on a cron timetable in a time zone, or (Emit event only)
once at a time. This module holds the timetable rules; scheduler.py claims and runs due schedules, schedules_api.py
edits them."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

from croniter import CroniterBadDateError, croniter

MIN_INTERVAL = timedelta(minutes=5)
CHECKED_FIRES = 11  # the gaps between the next 11 fire times are checked against MIN_INTERVAL
TOO_OFTEN = 'Schedules may run at most every 5 minutes.'
BAD_CRON = 'Use a cron expression with five fields, for example */15 * * * *.'
BAD_TIMEZONE = 'Choose a time zone from the list.'
PAST = 'Choose a time in the future.'
MISSING = 'Target no longer exists.'
PRESETS = {
    '*/15 * * * *': 'Every 15 minutes',
    '*/30 * * * *': 'Every 30 minutes',
    '0 * * * *': 'Every hour',
    '0 */6 * * *': 'Every 6 hours',
    '0 6 * * *': 'Every day at 06:00',
}


class ScheduleError(ValueError):
    """A cron expression or time zone a schedule may not use; the message is shown to the user."""


class TargetMissing(Exception):
    """The target's row (e.g. the Jira connection) no longer exists."""


def normal(cron: str) -> str:
    return ' '.join(cron.split())


def upcoming(cron: str, timezone: str, after: datetime, count: int) -> list[datetime]:
    """The next `count` fire times after `after`, computed in the schedule's time zone and returned in UTC."""
    times = croniter(normal(cron), after.astimezone(ZoneInfo(timezone)))
    return [times.get_next(datetime).astimezone(UTC) for _ in range(count)]


def next_after(cron: str, timezone: str, after: datetime) -> datetime:
    return upcoming(cron, timezone, after, 1)[0]


def validate(cron: str, timezone: str) -> None:
    expression = normal(cron)
    if len(expression.split(' ')) != 5 or not croniter.is_valid(expression):
        raise ScheduleError(BAD_CRON)
    if timezone not in available_timezones():
        raise ScheduleError(BAD_TIMEZONE)
    try:
        times = upcoming(expression, timezone, datetime.now(UTC), CHECKED_FIRES)
    except CroniterBadDateError:
        raise ScheduleError(BAD_CRON) from None
    if any(later - earlier < MIN_INTERVAL for earlier, later in zip(times, times[1:])):
        raise ScheduleError(TOO_OFTEN)


def run_time(run_at: datetime, timezone: str, now: datetime, kept: datetime | None = None) -> datetime:
    """When a one-time schedule runs, to the minute: a time without an offset is read in the schedule's time zone. It
    must be ahead, unless it is the time the schedule already has (kept), e.g. when a finished schedule is renamed."""
    if timezone not in available_timezones():
        raise ScheduleError(BAD_TIMEZONE)
    aware = run_at if run_at.tzinfo else run_at.replace(tzinfo=ZoneInfo(timezone))
    aware = aware.astimezone(UTC).replace(second=0, microsecond=0)
    if aware <= now and aware != kept:
        raise ScheduleError(PAST)
    return aware


def describe(cron: str) -> str:
    """Plain words for a preset; any other expression is shown as written."""
    expression = normal(cron)
    return PRESETS.get(expression, expression)
