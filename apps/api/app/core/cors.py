"""CORS that is completely silent for origins that are not allowed."""

from collections.abc import Sequence

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class OriginGatedCORSMiddleware:
    """Delegate to ``CORSMiddleware`` only when the request ``Origin`` is allowed.

    Starlette's ``CORSMiddleware`` with ``allow_credentials=True`` still adds
    ``access-control-allow-credentials`` (and answers preflights with 400 plus CORS headers)
    for disallowed origins. AC4 of S-1.4.1 requires no CORS headers at all, so requests with
    no ``Origin`` or a disallowed one go straight to the app, untouched.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        self._app = app
        self._allowed = frozenset(allowed_origins)
        self._cors = CORSMiddleware(
            app,
            allow_origins=list(allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route the request through CORS handling only for allowed origins."""
        if scope["type"] == "http":
            for key, value in scope["headers"]:
                if key == b"origin":
                    if value.decode("latin-1") in self._allowed:
                        await self._cors(scope, receive, send)
                        return
                    break
        await self._app(scope, receive, send)
