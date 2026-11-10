"""The schedule registry: canonical ``<owner>.<job>`` name -> UTC schedule (S-1.9.3).

A domain schedules a job in two steps: export the job from its ``services/__init__.py`` and add
it to :data:`app.workers.registry.JOBS`, then add one line to :data:`SCHEDULES` here. Schedules
are UTC; arq evaluates them on the UTC clock (``WorkerSettings.timezone``).

Exactly once across workers: every worker enqueues each occurrence under the job id
``<name>:<scheduled time in ms>`` (arq's ``unique`` cron), and arq refuses an id whose job or
result key exists. The result is therefore kept (see :data:`SCHEDULE_KEEP_RESULT_SECONDS`): with
arq's default ``keep_result=0`` a worker that was slow to wake up could enqueue an occurrence
again after another worker had already run it and deleted its result.

The scheduler runs only when ``SCHEDULER_ENABLED`` is true and ``APP_ENV`` is not ``test``.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from arq.cron import CronJob, cron, next_cron
from arq.typing import OptionType, WeekdayOptionType

from app.core.config import Settings
from app.core.jobs import KEEP_RESULT_SECONDS
from app.workers.registry import JOBS, _with_log_context

logger = logging.getLogger(__name__)

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
        )

    def describe(self) -> str:
        """Return the set fields as ``minute=0,5,10 hour=3``; ``every minute`` if none is set."""
        parts = []
        for field in ("month", "day", "weekday", "hour", "minute"):
            value = getattr(self, field)
            if value is None:
                continue
            shown = sorted(value) if isinstance(value, set | list | tuple) else [value]
            parts.append(f"{field}={','.join(str(v) for v in shown)}")
        return " ".join(parts) or "every minute"


SCHEDULES: Mapping[str, Schedule] = {
    "system.heartbeat": Schedule(minute=set(range(0, 60, 5))),
}


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
            cast(Any, _with_log_context(name, JOBS[name])),
            name=name,
            month=schedule.month,
            day=schedule.day,
            weekday=schedule.weekday,
            hour=schedule.hour,
            minute=schedule.minute,
            second=schedule.second,
            unique=True,
            keep_result=SCHEDULE_KEEP_RESULT_SECONDS,
            max_tries=1,
        )
        for name, schedule in SCHEDULES.items()
    ]


def log_schedule(settings: Settings, now: datetime | None = None) -> None:
    """Log each scheduled job with its next UTC run time, or once that the scheduler is off."""
    reason = scheduler_disabled_reason(settings)
    if reason is not None:
        logger.info("scheduler disabled (%s): no scheduled jobs will run", reason)
        return
    current = now or datetime.now(UTC)
    for name, schedule in SCHEDULES.items():
        next_run = schedule.next_run(current).astimezone(UTC).isoformat()
        logger.info(
            "scheduled job %s schedule=[%s] (UTC) next_run=%s",
            name,
            schedule.describe(),
            next_run,
        )
