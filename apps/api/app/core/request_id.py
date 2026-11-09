"""Request-ID middleware (S-1.4.5): pure ASGI, outermost, so every response carries the ID."""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import REQUEST_ID_HEADER
from app.core.logging import request_id_var

SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
logger = logging.getLogger("app.access")


def resolve_request_id(raw: str | None) -> str:
    """Return ``raw`` if it is 1-128 safe characters, else a fresh UUID4 hex."""
    if raw is not None and SAFE_REQUEST_ID.fullmatch(raw):
        return raw
    return uuid.uuid4().hex


class RequestIDMiddleware:
    """Bind the request ID to the logging context, echo it in ``X-Request-ID``, log the request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one HTTP request."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        raw = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), None)
        request_id = resolve_request_id(raw)
        token = request_id_var.set(request_id)
        status = 0
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self._app(scope, receive, send_with_id)
        finally:
            ms = round((time.perf_counter() - start) * 1000, 1)
            logger.info("%s %s -> %s (%sms)", scope["method"], scope["path"], status, ms)
            request_id_var.reset(token)
