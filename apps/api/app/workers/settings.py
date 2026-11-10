"""arq ``WorkerSettings`` (S-1.9.1), built from the environment.

Importing this module reads settings, so ``REDIS_URL`` must be set. Run the worker with
``python -m app.workers`` (it picks the right event loop and signal handling on Windows);
``arq app.workers.settings.WorkerSettings`` works on Linux and macOS.

Tuning:

- ``job_timeout`` 300 s: the default budget of one run; a longer job registers its own timeout.
- ``max_tries`` 5: a job cancelled by shutdown or a crash is retried, so jobs must be idempotent.
- ``job_completion_wait``: on SIGTERM/SIGINT the worker stops taking jobs, waits up to
  ``WORKER_SHUTDOWN_GRACE_SECONDS`` (30) for running jobs, then cancels them; a cancelled job
  stays queued and is retried by the next worker.
- ``keep_result``: see :data:`app.core.jobs.KEEP_RESULT_SECONDS`.
"""

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.jobs import KEEP_RESULT_SECONDS, deserialize_job, serialize_job
from app.workers.registry import build_functions

JOB_TIMEOUT_SECONDS = 300
MAX_TRIES = 5
MAX_JOBS = 10
HEALTH_CHECK_INTERVAL_SECONDS = 30

_settings = get_settings()


class WorkerSettings:
    """Settings class consumed by arq (``create_worker(WorkerSettings)``)."""

    functions = build_functions()
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    queue_name = _settings.worker_queue_name
    job_serializer = staticmethod(serialize_job)
    job_deserializer = staticmethod(deserialize_job)
    max_jobs = MAX_JOBS
    job_timeout = JOB_TIMEOUT_SECONDS
    max_tries = MAX_TRIES
    keep_result = KEEP_RESULT_SECONDS
    job_completion_wait = _settings.worker_shutdown_grace_seconds
    health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
