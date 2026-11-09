"""OpenAPI conventions (S-1.5.1): operation IDs and a uniqueness check.

Operation ID rule: ``<domain>_<function>`` where ``<function>`` is the endpoint function name
(``route.name``). ``<domain>`` is the route's first tag when it has one; otherwise the domain
package of the endpoint's module (``app.<domain>.router`` gives ``<domain>``); otherwise
``system`` (core and system routes, and anything declared outside a domain package).
"""

import re
from collections import defaultdict

from fastapi import FastAPI
from fastapi.routing import APIRoute

SYSTEM_DOMAIN = "system"
_NON_DOMAIN_PACKAGES = frozenset({"core", "workflows", "workers", "scripts", "main"})
_MODULE_RE = re.compile(r"^app\.(?P<domain>[a-z_][a-z0-9_]*)(?:\.|$)")


def _slug(value: str) -> str:
    return re.sub(r"\W+", "_", value.strip().lower()).strip("_")


def route_domain(route: APIRoute) -> str:
    """Return the domain a route belongs to (first tag, else module package, else ``system``)."""
    if route.tags:
        tag = _slug(str(route.tags[0]))
        if tag:
            return tag
    match = _MODULE_RE.match(getattr(route.endpoint, "__module__", "") or "")
    if match and match["domain"] not in _NON_DOMAIN_PACKAGES:
        return match["domain"]
    return SYSTEM_DOMAIN


def generate_operation_id(route: APIRoute) -> str:
    """Build the ``<domain>_<function>`` operation ID for ``route``."""
    return f"{route_domain(route)}_{route.name}"


def collect_operation_ids(app: FastAPI) -> dict[str, list[str]]:
    """Map each operation ID to the ``METHOD path`` routes that use it."""
    found: dict[str, list[str]] = defaultdict(list)
    for route in app.routes:
        if isinstance(route, APIRoute):
            label = f"{','.join(sorted(route.methods or []))} {route.path}"
            found[route.unique_id].append(label)
    return dict(found)


def assert_unique_operation_ids(app: FastAPI) -> None:
    """Raise ``ValueError`` naming both routes when two share an operation ID."""
    problems = [
        f"operationId {op_id!r} used by: {' and '.join(routes)}"
        for op_id, routes in sorted(collect_operation_ids(app).items())
        if len(routes) > 1
    ]
    if problems:
        raise ValueError("duplicate operation IDs: " + "; ".join(problems))
