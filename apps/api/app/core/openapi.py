"""OpenAPI conventions (S-1.5.1): operation IDs and a uniqueness check.

Operation ID rule: ``<domain>_<function>``, where ``<function>`` is the endpoint function name
(``route.name``). There is no API version in IDs; a v2 handler needs a distinct function name.

``<domain>`` is derived module-first, so it is the real owning domain and cannot be changed by
a tag:

1. the endpoint's module is ``app.<domain>.…`` with ``<domain>`` one of the architecture §4.1
   domains (``app.decks.router`` gives ``decks``);
2. a route in ``app.workflows.…`` (workflows own no domain) maps its first tag, slugged: a domain
   name is used as is, and a backlog name from architecture §4.1 (``auth`` and ``accounts`` are
   ``identity``, and so on) maps to its domain, so ``POST /auth/register`` is
   ``identity_register``;
3. otherwise (core, workers, tests, probes, and workflow routes whose tag maps to nothing), the
   route's first tag (an ``Enum`` tag uses its ``.value``), slugged to snake_case, but only if it
   is itself one of those domains;
4. otherwise ``system``.

FastAPI calls the generator with an effective route context for routes added through
``include_router`` (0.162+), not always a real ``APIRoute``. The code here only relies on the
duck-typed ``tags``, ``endpoint`` and ``name`` attributes that both provide.
"""

import re
import warnings
from collections import defaultdict
from collections.abc import Iterator
from enum import Enum
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

SYSTEM_DOMAIN = "system"
DOMAINS = frozenset(
    {
        "system",
        "identity",
        "playgroups",
        "cards",
        "decks",
        "brackets",
        "games",
        "stats",
        "collection",
        "leagues",
    }
)
BACKLOG_ALIASES = {
    "auth": "identity",
    "accounts": "identity",
    "prices": "cards",
    "purchases": "collection",
    "dashboard": "stats",
    "activity": "stats",
    "meta": "stats",
}
"""Backlog names that architecture 4.1 maps to a domain (``players`` is ambiguous: left out)."""
_MODULE_RE = re.compile(r"^app\.(?P<domain>[a-z_][a-z0-9_]*)(?:\.|$)")
_DUPLICATE_WARNING = "Duplicate Operation ID"


class DuplicateOperationIdError(ValueError):
    """Two or more routes share an operation ID."""


def _slug(value: str) -> str:
    return re.sub(r"\W+", "_", value.strip().lower()).strip("_")


def _first_tag_slug(route: APIRoute) -> str:
    """The route's first tag as snake_case (an ``Enum`` tag uses its ``.value``), else ``""``."""
    if not route.tags:
        return ""
    first = route.tags[0]
    return _slug(str(first.value if isinstance(first, Enum) else first))


def route_domain(route: APIRoute) -> str:
    """Return the domain a route belongs to (module package, workflow tag, tag, else ``system``)."""
    match = _MODULE_RE.match(getattr(route.endpoint, "__module__", "") or "")
    if match and match["domain"] in DOMAINS:
        return match["domain"]
    tag = _first_tag_slug(route)
    if match and match["domain"] == "workflows" and tag in BACKLOG_ALIASES:
        return BACKLOG_ALIASES[tag]
    if tag in DOMAINS:
        return tag
    return SYSTEM_DOMAIN


def generate_operation_id(route: APIRoute) -> str:
    """Build the ``<domain>_<function>`` operation ID for ``route``."""
    return f"{route_domain(route)}_{route.name}"


def _iter_operations(app: FastAPI) -> Iterator[tuple[str, str]]:
    """Yield ``(operation_id, "METHOD /full/path")``, one per HTTP method, for every API route."""
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        route: Any = context
        for method in sorted(route.methods or []):
            yield route.unique_id, f"{method} {route.path}"


def collect_operation_ids(app: FastAPI) -> dict[str, list[str]]:
    """Map each operation ID to the ``METHOD path`` operations that use it (full paths)."""
    found: dict[str, list[str]] = defaultdict(list)
    for op_id, label in _iter_operations(app):
        found[op_id].append(label)
    return dict(found)


def assert_unique_operation_ids(app: FastAPI) -> None:
    """Raise :class:`DuplicateOperationIdError` naming both routes on a shared operation ID.

    Fails closed: FastAPI's own duplicate warning while building the schema also counts, unless
    the collector already found duplicates. This rebuilds the app's cached OpenAPI schema, so
    call it at build or export time, never on a serving app.
    """
    problems = [
        f"operationId {op_id!r} used by: {' and '.join(routes)}"
        for op_id, routes in sorted(collect_operation_ids(app).items())
        if len(routes) > 1
    ]
    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.openapi()
    if not problems:
        problems.extend(str(w.message) for w in caught if _DUPLICATE_WARNING in str(w.message))
    if problems:
        raise DuplicateOperationIdError("duplicate operation IDs: " + "; ".join(problems))
