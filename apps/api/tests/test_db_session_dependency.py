"""Unit tests for the request-scoped session dependency (commit, rollback, close)."""

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import db
from app.core.db import AppSession, SessionDep, after_commit


class _SpySession(AppSession):
    created: list["_SpySession"] = []  # noqa: RUF012
    commits = 0
    rollbacks = 0
    closed = False

    async def commit(self) -> None:
        self.commits += 1
        await super().commit()

    async def rollback(self) -> None:
        self.rollbacks += 1
        await super().rollback()

    async def close(self) -> None:
        self.closed = True
        await super().close()


@pytest.fixture
def spy_sessions(db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> list[_SpySession]:
    """Point the dependency at the test engine and record every session it hands out."""
    created: list[_SpySession] = []

    class Recording(_SpySession):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            created.append(self)

    factory = async_sessionmaker(db_engine, class_=Recording, expire_on_commit=False)
    monkeypatch.setattr(db, "get_sessionmaker", lambda: factory)
    return created


def _app(events: list[str]) -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    async def ok(session: SessionDep) -> dict[str, bool]:
        after_commit(session, lambda: events.append("committed"))
        await session.execute(text("select 1"))
        return {"ok": True}

    @app.get("/fail")
    async def fail(session: SessionDep) -> None:
        after_commit(session, lambda: events.append("committed"))
        await session.execute(text("select 1"))
        raise HTTPException(status_code=409)

    return app


async def _get(app: FastAPI, path: str) -> int:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return (await client.get(path)).status_code


async def test_success_commits_runs_callbacks_and_closes_the_session(
    spy_sessions: list[_SpySession],
) -> None:
    events: list[str] = []
    assert await _get(_app(events), "/ok") == 200
    (session,) = spy_sessions
    assert (session.commits, session.rollbacks) == (1, 0)
    assert events == ["committed"]
    assert session.closed
    assert not session.in_transaction()


async def test_error_rolls_back_drops_callbacks_and_closes_the_session(
    spy_sessions: list[_SpySession],
) -> None:
    events: list[str] = []
    assert await _get(_app(events), "/fail") == 409
    (session,) = spy_sessions
    assert (session.commits, session.rollbacks) == (0, 1)
    assert events == []
    assert session.closed
    assert not session.in_transaction()
