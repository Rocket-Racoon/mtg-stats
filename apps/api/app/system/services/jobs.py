"""Background jobs owned by the ``system`` domain (architecture §7)."""

from typing import Any


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job: returns ``"pong"``. Idempotent and side-effect free.

    It gives the worker a registered job from day one and the end-to-end tests a real target.
    """
    return "pong"
