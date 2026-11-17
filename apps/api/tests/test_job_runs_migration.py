"""The job_runs migration (S-1.9.4) on a throwaway database, never on ``mtg_stats*``.

Synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.migration_support import alembic_config, temporary_database

JOB_RUNS_REVISION = "4a01b3f4681e"
"""The revision that creates ``job_runs`` (pinned)."""

_INSERT = (
    "INSERT INTO job_runs (id, job_name, job_id, started_at, finished_at, status, error_summary)"
    " VALUES (gen_random_uuid(), 'j', 'id', now(), now(), :status, :error)"
)


def _parent() -> str:
    revision = ScriptDirectory.from_config(alembic_config()).get_revision(JOB_RUNS_REVISION)
    if revision is None:
        pytest.fail(f"revision {JOB_RUNS_REVISION} not found")
    assert isinstance(revision.down_revision, str)
    return revision.down_revision


def test_upgrade_creates_job_runs_with_its_columns_and_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            insp = inspect(engine)
            columns = {c["name"]: c for c in insp.get_columns("job_runs")}
            indexes = {tuple(i["column_names"]) for i in insp.get_indexes("job_runs")}
        finally:
            engine.dispose()
    assert set(columns) == {
        "id",
        "job_name",
        "job_id",
        "started_at",
        "finished_at",
        "status",
        "error_summary",
    }
    assert all(not c["nullable"] for c in columns.values())
    assert ("job_name", "status", "finished_at") in indexes
    assert ("finished_at",) in indexes


def test_the_database_enforces_the_status_list_and_the_two_kilobyte_error_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(text(_INSERT), {"status": "success", "error": "é" * 1024})
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(text(_INSERT), {"status": "success", "error": "é" * 1025})
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(text(_INSERT), {"status": "bogus", "error": ""})
        finally:
            engine.dispose()


def test_downgrade_removes_job_runs_and_upgrade_can_be_applied_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, "head")
        command.downgrade(config, _parent())
        engine = create_engine(url)
        try:
            after_downgrade = set(inspect(engine).get_table_names())
            command.upgrade(config, "head")
            after_upgrade = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    assert "job_runs" not in after_downgrade
    assert "job_runs" in after_upgrade


def test_head_has_no_model_drift_after_the_job_runs_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch):
        config = alembic_config()
        command.upgrade(config, "head")
        command.check(config)
