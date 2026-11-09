"""CORS that is completely silent for origins that are not allowed."""

from collections.abc import Sequence

from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class OriginGatedCORSMiddleware:
    """Delegate to ``CORSMiddleware`` only when the request ``Origin`` is allowed.

    Starlette's ``CORSMiddleware`` with ``allow_credentials=True`` still adds
    ``access-control-allow-credentials`` (and answers preflights with 400 plus CORS headers)
    for disallowed origins. AC4 of S-1.4.1 requires no CORS headers at all, so requests with
    no ``Origin`` or a disallowed one go straight to the app, untouched. Every HTTP response
    still carries ``Vary: Origin``, collapsed into a single header of unique tokens (or just
    ``*``), so shared caches never serve one origin's response to another.
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
            expose_headers=["X-Request-ID"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route the request through CORS handling only for allowed origins."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_vary(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                tokens: dict[str, str] = {}
                for line in headers.getlist("vary"):
                    for token in line.split(","):
                        if token.strip():
                            tokens.setdefault(token.strip().lower(), token.strip())
                tokens.setdefault("origin", "Origin")
                if "vary" in headers:
                    del headers["vary"]
                headers["Vary"] = "*" if "*" in tokens else ", ".join(tokens.values())
            await send(message)

        origin = next((v for k, v in scope["headers"] if k == b"origin"), None)
        if origin is not None and origin.decode("latin-1") in self._allowed:
            await self._cors(scope, receive, send_with_vary)
        else:
            await self._app(scope, receive, send_with_vary)
