"""The site-admin role cannot be changed over HTTP (S-2.1.11 AC3).

The role changes only from the CLI (and ``create-admin``, S-2.1.1). The check walks the real app's
OpenAPI document: no operation may look like a grant or a revoke, and no request, query, header or
cookie input may carry ``is_site_admin``. ``GET /auth/me`` *returns* the flag, which is fine, so
only inputs are inspected there.
"""

import itertools
import re
from typing import Any

import pytest

from app.main import create_app

HTTP_METHODS = {"get", "put", "post", "patch", "delete", "options", "head", "trace"}
VERBS = {"grant", "revoke", "promote", "demote", "set", "assign", "make", "remove", "add"}
TARGETS = {"admin", "admins", "role", "roles", "siteadmin", "siteadmins"}
SPEC_FLAG = "is_site_admin"


def names_the_role_change(text: str) -> bool:
    """True when ``text`` combines a change verb with admin/role, or names a "site admin".

    ``GET /admin/card-sync/runs`` is a legitimate site-admin route (it has no verb), and
    ``/invites/{id}/revoke`` revokes an invite (no admin/role target), so neither is flagged.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    if "siteadmin" in tokens or any(
        a == "site" and b in {"admin", "admins"} for a, b in itertools.pairwise(tokens)
    ):
        return True
    return bool(VERBS & set(tokens)) and bool(TARGETS & set(tokens))


def _resolve(spec: dict[str, Any], schema: Any) -> Any:
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if ref:
        return spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    return schema


def _property_names(spec: dict[str, Any], schema: Any, seen: set[str] | None = None) -> set[str]:
    """Every property name reachable from ``schema`` (through refs, allOf/anyOf/oneOf, items)."""
    seen = set() if seen is None else seen
    if not isinstance(schema, dict):
        return set()
    ref = schema.get("$ref")
    if ref:
        if ref in seen:
            return set()
        seen.add(ref)
    node = _resolve(spec, schema)
    names: set[str] = set(node.get("properties", {}))
    children: list[Any] = list(node.get("properties", {}).values())
    for key in ("allOf", "anyOf", "oneOf"):
        children.extend(node.get(key, []))
    if "items" in node:
        children.append(node["items"])
    if isinstance(node.get("additionalProperties"), dict):
        children.append(node["additionalProperties"])
    for child in children:
        names |= _property_names(spec, child, seen)
    return names


def role_changing_operations(spec: dict[str, Any]) -> list[str]:
    """Describe every operation that looks like it grants/revokes the role or accepts the flag."""
    problems: list[str] = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            label = f"{method.upper()} {path}"
            for what, text in (
                ("path", path),
                ("operationId", operation.get("operationId", "")),
                ("summary", operation.get("summary", "")),
            ):
                if names_the_role_change(text):
                    problems.append(f"{label}: {what} {text!r} names the role")
            for parameter in [*item.get("parameters", []), *operation.get("parameters", [])]:
                if parameter.get("name", "").lower().replace("-", "_") == SPEC_FLAG:
                    problems.append(f"{label}: parameter {parameter['name']!r}")
            for media in operation.get("requestBody", {}).get("content", {}).values():
                if SPEC_FLAG in _property_names(spec, media.get("schema", {})):
                    problems.append(f"{label}: request body has {SPEC_FLAG}")
    return problems


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return create_app().openapi()


def test_no_operation_in_the_real_openapi_grants_or_revokes_the_site_admin_role(
    spec: dict[str, Any],
) -> None:
    assert role_changing_operations(spec) == []


def test_no_request_body_schema_in_the_real_openapi_has_an_is_site_admin_property(
    spec: dict[str, Any],
) -> None:
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            for media in operation.get("requestBody", {}).get("content", {}).values():
                names = _property_names(spec, media.get("schema", {}))
                assert SPEC_FLAG not in names, f"{method.upper()} {path}"


def test_only_response_schemas_mention_is_site_admin(spec: dict[str, Any]) -> None:
    """Every schema that has the flag is a response; none is used as an input."""
    carriers = {
        name
        for name, schema in spec["components"]["schemas"].items()
        if SPEC_FLAG in schema.get("properties", {})
    }
    assert "MeResponse" in carriers
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            for media in operation.get("requestBody", {}).get("content", {}).values():
                ref = media.get("schema", {}).get("$ref", "")
                assert ref.rsplit("/", 1)[-1] not in carriers, f"{method.upper()} {path}"


def test_the_guard_is_not_vacuous_get_auth_me_does_return_the_flag(spec: dict[str, Any]) -> None:
    response = spec["paths"]["/api/v1/auth/me"]["get"]["responses"]["200"]
    schema = response["content"]["application/json"]["schema"]
    assert SPEC_FLAG in _property_names(spec, schema)
    assert role_changing_operations(spec) == []  # and it is not flagged for returning it


def _doc(paths: dict[str, Any], schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"paths": paths, "components": {"schemas": schemas or {}}}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/site-admins/{id}",
        "/api/v1/users/{id}/site_admin",
        "/api/v1/admin/grant",
        "/api/v1/users/{id}/set-role",
        "/api/v1/users/{id}/make_admin",
    ],
)
def test_the_checker_flags_a_route_whose_path_names_the_role(path: str) -> None:
    found = role_changing_operations(_doc({path: {"post": {"operationId": "x_y"}}}))
    assert found


def test_the_checker_flags_an_operation_id_or_summary_naming_the_role() -> None:
    by_id = _doc({"/a": {"post": {"operationId": "identity_grant_site_admin"}}})
    by_summary = _doc({"/b": {"post": {"operationId": "identity_x", "summary": "Grant admin"}}})
    assert role_changing_operations(by_id)
    assert role_changing_operations(by_summary)


def test_the_checker_flags_a_request_body_with_the_flag_even_behind_a_ref() -> None:
    schemas = {
        "Patch": {"type": "object", "properties": {"is_site_admin": {"type": "boolean"}}},
        "Wrapper": {
            "type": "object",
            "properties": {"inner": {"$ref": "#/components/schemas/Patch"}},
        },
    }
    body = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Wrapper"}}}}
    doc = _doc({"/u/{id}": {"patch": {"operationId": "x_y", "requestBody": body}}}, schemas)
    assert role_changing_operations(doc)


def test_the_checker_flags_a_query_parameter_named_is_site_admin() -> None:
    doc = _doc(
        {
            "/u": {
                "get": {
                    "operationId": "x_y",
                    "parameters": [{"name": "is_site_admin", "in": "query"}],
                }
            }
        }
    )
    assert role_changing_operations(doc)


def test_the_checker_does_not_flag_a_response_that_returns_the_flag() -> None:
    schemas = {"Me": {"type": "object", "properties": {"is_site_admin": {"type": "boolean"}}}}
    responses = {
        "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Me"}}}}
    }
    doc = _doc(
        {"/api/v1/auth/me": {"get": {"operationId": "identity_me", "responses": responses}}},
        schemas,
    )
    assert role_changing_operations(doc) == []


@pytest.mark.parametrize(
    "path",
    ["/api/v1/admin/card-sync/runs", "/api/v1/invites/{invite_id}/revoke", "/api/v1/settings"],
)
def test_the_checker_does_not_flag_legitimate_admin_or_revoke_routes(path: str) -> None:
    doc = _doc(
        {
            path: {
                "get": {
                    "operationId": "cards_list_card_sync_runs",
                    "summary": "List card sync runs",
                }
            }
        }
    )
    assert role_changing_operations(doc) == []
