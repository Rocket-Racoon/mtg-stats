"""Test helpers for ``job_runs`` (S-1.9.4): a factory for finished job runs."""

from datetime import UTC, datetime, timedelta

import factory

from app.core.ids import uuid7
from app.system.models import JobRun
from tests.factories import BaseFactory


class JobRunFactory(BaseFactory):
    """A finished successful run of ``system.heartbeat`` that ended now; override any field."""

    class Meta:
        model = JobRun

    id = factory.LazyFunction(uuid7)
    job_name = "system.heartbeat"
    job_id = factory.Sequence(lambda n: f"job-{n}")
    started_at = factory.LazyFunction(lambda: datetime.now(UTC) - timedelta(seconds=1))
    finished_at = factory.LazyFunction(lambda: datetime.now(UTC))
    status = "success"
    error_summary = ""
