"""Structured JSON logging with a shared ``contextvars`` context (S-1.4.5, decision A17).

Every log line is one JSON object: a UTC timestamp, level, logger, message and the ambient
context (``request_id`` or ``job_id``, plus ``game_id`` when a domain has set it). The context
is reusable by the arq worker through :func:`log_context`.
"""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
game_id_var: ContextVar[str | None] = ContextVar("game_id", default=None)
job_var: ContextVar[str | None] = ContextVar("job", default=None)

_CONTEXT_VARS = {
    "request_id": request_id_var,
    "job_id": job_id_var,
    "job": job_var,
    "game_id": game_id_var,
}
_HANDLER_MARK = "_mtg_stats_json_handler"
# Keys a caller may pass via ``extra={...}``; anything else is dropped.
_EXTRA_KEYS = ("method", "path", "status", "duration_ms", "game_id", "try", "outcome")
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def get_request_id() -> str | None:
    """Return the current request ID, or ``None`` outside a request."""
    return request_id_var.get()


def set_game_id(game_id: object | None) -> Token[str | None]:
    """Add ``game_id`` to every log line of the current context (domains call this)."""
    return game_id_var.set(None if game_id is None else str(game_id))


@contextmanager
def log_context(
    *,
    request_id: str | None = None,
    job_id: str | None = None,
    job: str | None = None,
    game_id: object | None = None,
) -> Iterator[None]:
    """Bind context values for the duration of a block, then restore the previous ones."""
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    for var, value in (
        (request_id_var, request_id),
        (job_id_var, job_id),
        (job_var, job),
        (game_id_var, None if game_id is None else str(game_id)),
    ):
        if value is not None:
            tokens.append((var, var.set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class JsonFormatter(logging.Formatter):
    """Render a record as a single-line JSON object including the logging context."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as one JSON line."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, var in _CONTEXT_VARS.items():
            value = var.get()
            if value is not None:
                payload[key] = value
        for key in _EXTRA_KEYS:
            if key in record.__dict__ and key not in payload:
                payload[key] = record.__dict__[key]
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Stream handler that looks up ``sys.stderr`` on every emit (survives stream swaps)."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        """Ignore assignments; the stream is always the live ``sys.stderr``."""


def configure_logging(level: int | str = logging.INFO) -> None:
    """Route all logging, uvicorn's included, through the JSON formatter. Idempotent."""
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]
    handler = _StderrHandler()
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # The request middleware writes its own access line (with the request ID).
    logging.getLogger("uvicorn.access").propagate = False
