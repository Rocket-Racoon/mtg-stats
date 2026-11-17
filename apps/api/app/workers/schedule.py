"""The schedule registry: canonical ``<owner>.<job>`` name -> UTC schedule (S-1.9.3).

A domain schedules a job in two steps: export the job from its ``services/__init__.py`` and add
it to :data:`app.workers.registry.JOBS`, then add one line to :data:`SCHEDULES` here. Schedules
are UTC; arq evaluates them on the UTC clock (``WorkerSettings.timezone``).

Exactly once across workers: every worker enqueues each occurrence under the job id
``<name>:<scheduled time in ms>`` (arq's ``unique`` cron), and arq refuses an id whose job or
result key exists. The result is therefore kept (see :data:`SCHEDULE_KEEP_RESULT_SECONDS`): with
arq's default ``keep_result=0`` a worker that was slow to wake up could enqueue an occurrence
again after another worker had already run it and deleted its result.

Scheduled jobs follow S-1.9.2's rules: each cron job runs through :func:`wrap_job` and takes its
timeout and ``max_tries`` from its ``JobSpec`` via :func:`arq_options`, so a
:class:`~app.core.jobs.RetryableJobError` is retried under the same occurrence id (arq keeps a
cron job's in-progress key for 60 s, so a cron retry waits at least that long). Caveat: a long job
whose retries run past its next occurrence can overlap it, because the next occurrence has a new
id. That cannot happen to the heartbeat.

The scheduler runs only when ``SCHEDULER_ENABLED`` is true and ``APP_ENV`` is not ``test``.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any, cast

from arq.cron import CronJob, cron, next_cron
from arq.typing import OptionType, WeekdayOptionType

from app.core.config import Settings
from app.core.jobs import KEEP_RESULT_SECONDS
from app.workers.registry import JOBS, arq_options, wrap_job

logger = logging.getLogger(__name__)

CRON_MICROSECOND = 123_456
"""arq's default microsecond, pinned. It is part of every occurrence's time and so of its job id
(``<name>:<ms>``): changing it changes every id, and in a rolling deploy workers with different
values would each run the same occurrence once. Never change it without stopping all workers."""

SCHEDULE_KEEP_RESULT_SECONDS = KEEP_RESULT_SECONDS
"""One day: far longer than the seconds in which a lagging worker can still see an occurrence."""


@dataclass(frozen=True)
class Schedule:
    """A UTC schedule, with the field meanings of :func:`arq.cron.cron` (``None`` = every)."""

    month: OptionType = None
    day: OptionType = None
    weekday: WeekdayOptionType = None
    hour: OptionType = None
    minute: OptionType = None
    second: OptionType = 0

    def next_run(self, after: datetime) -> datetime:
        """Return the next run time strictly after ``after``, using arq's own logic."""
        return next_cron(
            after,
            month=self.month,
            day=self.day,
            weekday=self.weekday,
            hour=self.hour,
            minute=self.minute,
            second=self.second,
            microsecond=CRON_MICROSECOND,
        )

    def describe(self) -> str:
        """Return the set fields as ``minute=0,5,10 hour=3``; ``every minute`` if none is set."""
        return _describe(self)


def _describe(fields: Any) -> str:
    """Describe the cron fields of a :class:`Schedule` or an arq ``CronJob``."""
    parts = []
    for field in ("month", "day", "weekday", "hour", "minute", "second"):
        value = getattr(fields, field)
        if value is None or (field == "second" and value == 0):
            continue
        shown = sorted(value) if isinstance(value, set | list | tuple) else [value]
        parts.append(f"{field}={','.join(str(v) for v in shown)}")
    return " ".join(parts) or "every minute"


SCHEDULES: Mapping[str, Schedule] = {
    "identity.cleanup_refresh_tokens": Schedule(hour=4, minute=15),
    "system.heartbeat": Schedule(minute=set(range(0, 60, 5))),
    "system.purge_job_runs": Schedule(hour=4, minute=45),
}

_INTERVAL_REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)
_INTERVAL_MAX_RUNS = 3_000
_INTERVAL_MIN_SPAN = timedelta(days=8)
_INTERVAL_MAX_SPAN = timedelta(days=366)


def schedule_interval(schedule: Schedule) -> timedelta:
    """Return the longest gap between two consecutive runs of ``schedule``.

    "Interval" for S-1.9.4's stale check is the longest gap, so an irregular schedule (hour=3
    and 15, say) is only called stale when it has missed more than twice its widest gap. It is
    measured with arq's own ``next_cron`` from a fixed date, over up to a year of runs but at
    most :data:`_INTERVAL_MAX_RUNS` of them. A dense schedule that hits that cap before covering
    :data:`_INTERVAL_MIN_SPAN` (every minute on weekdays 09-17, say) raises ``ValueError`` rather
    than understate its gap and make the job look stale every night.
    """
    gaps = []
    previous = schedule.next_run(_INTERVAL_REFERENCE)
    end = _INTERVAL_REFERENCE + _INTERVAL_MAX_SPAN
    for _ in range(_INTERVAL_MAX_RUNS):
        current = schedule.next_run(previous)
        gaps.append(current - previous)
        previous = current
        if current >= end:
            return max(gaps)
    if previous - _INTERVAL_REFERENCE >= _INTERVAL_MIN_SPAN:
        return max(gaps)
    raise ValueError(
        f"schedule has more than {_INTERVAL_MAX_RUNS} runs in under {_INTERVAL_MIN_SPAN.days} "
        "days; its longest gap cannot be measured, so the stale check would be wrong"
    )


@cache
def _scheduled_intervals() -> tuple[tuple[str, timedelta], ...]:
    return tuple((name, schedule_interval(schedule)) for name, schedule in SCHEDULES.items())


def scheduled_intervals() -> dict[str, timedelta]:
    """Return each scheduled job's name and its :func:`schedule_interval` (measured once)."""
    return dict(_scheduled_intervals())


def scheduler_disabled_reason(settings: Settings) -> str | None:
    """Return why scheduled jobs do not run (``APP_ENV=test`` or ``SCHEDULER_ENABLED=false``)."""
    if settings.app_env == "test":
        return "APP_ENV=test"
    if not settings.scheduler_enabled:
        return "SCHEDULER_ENABLED=false"
    return None


def build_cron_jobs(settings: Settings) -> list[CronJob]:
    """Return the arq cron jobs for :data:`SCHEDULES`; empty when the scheduler is disabled."""
    if scheduler_disabled_reason(settings) is not None:
        return []
    return [
        cron(
            cast(Any, wrap_job(name, JOBS[name])),
            name=name,
            month=schedule.month,
            day=schedule.day,
            weekday=schedule.weekday,
            hour=schedule.hour,
            minute=schedule.minute,
            second=schedule.second,
            microsecond=CRON_MICROSECOND,
            unique=True,
            keep_result=SCHEDULE_KEEP_RESULT_SECONDS,
            **arq_options(JOBS[name]),
        )
        for name, schedule in SCHEDULES.items()
    ]


def log_schedule(
    settings: Settings, now: datetime | None = None, cron_jobs: Sequence[CronJob] | None = None
) -> None:
    """Log each scheduled job with its next UTC run time, or once that the scheduler is off.

    The worker passes the ``cron_jobs`` it actually built, so the log shows what arq will run;
    without them the jobs are read from :data:`SCHEDULES`. Next run times come from arq's
    ``next_cron`` and no job's own state is changed.
    """
    reason = scheduler_disabled_reason(settings)
    if reason is not None:
        logger.info("scheduler disabled (%s): no scheduled jobs will run", reason)
        return
    current = now or datetime.now(UTC)
    entries: list[tuple[str, Any]] = (
        [(job.name, job) for job in cron_jobs] if cron_jobs is not None else list(SCHEDULES.items())
    )
    for name, fields in entries:
        next_run = (
            next_cron(
                current,
                month=fields.month,
                day=fields.day,
                weekday=fields.weekday,
                hour=fields.hour,
                minute=fields.minute,
                second=fields.second,
                microsecond=CRON_MICROSECOND,
            )
            .astimezone(UTC)
            .isoformat(timespec="milliseconds")
        )
        logger.info(
            "scheduled job %s schedule=[%s] (UTC) next_run=%s", name, _describe(fields), next_run
        )
