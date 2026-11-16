"""Seed registry for the dev database (S-1.3.4).

Domains register seed functions by calling :func:`register_seeder` from their own
``services/__init__.py``. The reset command imports each domain's services package and then
calls :func:`run_seeders`. A seeder runs in the caller's session and never commits.
"""

from collections.abc import Awaitable, Callable
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "Seeder",
    "clear_seeders",
    "register_seeder",
    "registered_seeders",
    "restore_seeders",
    "run_seeders",
    "snapshot_seeders",
]

Seeder = Callable[[AsyncSession], Awaitable[None]]

_SEEDERS: dict[str, Seeder] = {}


def register_seeder(name: str, fn: Seeder) -> None:
    """Register ``fn`` under ``name``; raise ``ValueError`` if the name is already taken."""
    if name in _SEEDERS:
        raise ValueError(f"seeder {name!r} is already registered")
    _SEEDERS[name] = fn


def registered_seeders() -> list[str]:
    """Return the registered seeder names in registration order."""
    return list(_SEEDERS)


def clear_seeders() -> None:
    """Remove every registered seeder (for tests)."""
    _SEEDERS.clear()


def snapshot_seeders() -> object:
    """Return an opaque copy of the registry, to pass to :func:`restore_seeders` (for tests)."""
    return dict(_SEEDERS)


def restore_seeders(snapshot: object) -> None:
    """Put the registry back to a :func:`snapshot_seeders` state (for tests)."""
    saved = cast("dict[str, Seeder]", snapshot)
    _SEEDERS.clear()
    _SEEDERS.update(saved)


async def run_seeders(session: AsyncSession) -> int:
    """Call each registered seeder in order with ``session``; return how many ran."""
    snapshot = list(_SEEDERS.values())
    for fn in snapshot:
        await fn(session)
    return len(snapshot)
