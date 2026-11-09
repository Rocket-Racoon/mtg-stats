"""Error envelope, ``DomainError`` hierarchy and exception handling (S-1.4.5).

Every error response is ``{"error": {"code", "message", "details", "request_id"}}``.
Services raise :class:`DomainError` subclasses, never FastAPI's ``HTTPException``.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_request_id

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"
INTERNAL_ERROR_MESSAGE = "An internal error occurred."


class ErrorBody(BaseModel):
    """The ``error`` object of every error response."""

    code: str = Field(description="Stable snake_case code; clients switch on this.")
    message: str = Field(description="Human-readable text; never switch on it.")
    details: dict[str, Any] | None = Field(description="Extra structured information, or null.")
    request_id: str = Field(description="Matches the X-Request-ID header and the logs.")


class ErrorResponse(BaseModel):
    """Envelope of every error response."""

    error: ErrorBody


class DomainError(Exception):
    """Base class for errors a service raises on purpose; carries a code and HTTP status."""

    code: str = "domain_error"
    status_code: int = 400
    default_message: str = "The operation was rejected."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class NotFoundError(DomainError):
    """The requested resource does not exist (or is hidden from the caller)."""

    code = "not_found"
    status_code = 404
    default_message = "Resource not found."


class ConflictError(DomainError):
    """The operation conflicts with the current state."""

    code = "conflict"
    status_code = 409
    default_message = "The request conflicts with the current state."


class PermissionDeniedError(DomainError):
    """The caller is authenticated but not allowed."""

    code = "forbidden"
    status_code = 403
    default_message = "You do not have permission to do this."


class AuthenticationError(DomainError):
    """The caller is not authenticated."""

    code = "unauthenticated"
    status_code = 401
    default_message = "Authentication is required."


class RateLimitedError(DomainError):
    """Too many requests."""

    code = "rate_limited"
    status_code = 429
    default_message = "Too many requests."


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}

# Declared on the app so OpenAPI (and the generated client) type every error body.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse, "description": HTTPStatus(status).phrase}
    for status in (401, 403, 404, 409, 422, 429, 500)
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an envelope response using the current request ID."""
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, details=details, request_id=get_request_id() or ""
        )
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    fields = [
        {"path": ".".join(str(part) for part in err["loc"]), "message": str(err["msg"])}
        for err in exc.errors()
    ]
    return {"fields": fields}


async def _domain_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _http_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "Error"
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else phrase
    return error_response(exc.status_code, code, message, headers=dict(exc.headers or {}))


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return error_response(
        422, "validation_error", "Request validation failed.", _validation_details(exc)
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register envelope handlers for domain, HTTP and validation errors."""
    app.add_exception_handler(DomainError, _domain_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)


class UnhandledErrorMiddleware:
    """Turn any unhandled exception into the 500 envelope, inside CORS and request-ID handling.

    Starlette's ``ServerErrorMiddleware`` sits outside all user middleware, so a 500 it builds
    would carry neither CORS headers nor ``X-Request-ID``. Catching here, as the innermost
    user middleware, keeps both. The traceback is logged (never returned).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app; on an exception answer 500 unless the response already started."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, tracking_send)
        except Exception:
            logger.exception("Unhandled exception")
            if started:
                raise
            response = error_response(500, "internal_error", INTERNAL_ERROR_MESSAGE)
            await response(scope, receive, send)
