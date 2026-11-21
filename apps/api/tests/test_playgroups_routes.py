"""``/playgroups`` routes: shapes, validation, error codes and role checks (S-2.4.1)."""

import json

import httpx
import pytest

from app.core.db import AppSession
from tests.playgroup_support import join, make_user

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"


async def _create(
    client: httpx.AsyncClient, headers: dict[str, str], **body: object
) -> httpx.Response:
    return await client.post(URL, json={"name": "Friday Night", **body}, headers=headers)


async def test_creating_returns_201_with_the_owner_role(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers, name="  Friday Night  ", description="Commander")
    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["description"], body["role"]) == (
        "Friday Night",
        "Commander",
        "owner",
    )


@pytest.mark.parametrize(
    "body",
    [
        {"name": "ab"},
        {"name": "  ab  "},
        {"name": "x" * 51},
        {"name": "Valid", "description": "d" * 501},
        {"name": None},
        {},
    ],
)
async def test_invalid_create_bodies_are_422(
    client: httpx.AsyncClient, app_schema: AppSession, body: dict[str, object]
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await client.post(URL, json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_boundary_lengths_are_accepted(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    assert (await _create(client, headers, name="abc", description="d" * 500)).status_code == 201
    assert (await _create(client, headers, name="x" * 50)).status_code == 201


async def test_routes_need_a_token(client: httpx.AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.post(URL, json={"name": "Friday"})).status_code == 401


async def test_owner_renames_and_a_member_gets_403(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group_id = (await _create(client, owner)).json()["id"]
    await join(app_schema, group_id, member_player, "member")

    denied = await client.patch(f"{URL}/{group_id}", json={"name": "Hijack"}, headers=member)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    done = await client.patch(
        f"{URL}/{group_id}", json={"name": "Renamed", "description": "d"}, headers=owner
    )
    assert done.status_code == 200
    assert (done.json()["name"], done.json()["description"]) == ("Renamed", "d")
    seen = await client.get(f"{URL}/{group_id}", headers=member)
    assert (seen.json()["name"], seen.json()["role"]) == ("Renamed", "member")


async def test_an_admin_can_edit(client: httpx.AsyncClient, app_schema: AppSession) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group_id = (await _create(client, owner)).json()["id"]
    await join(app_schema, group_id, admin_player, "admin")
    done = await client.patch(f"{URL}/{group_id}", json={"description": "By admin"}, headers=admin)
    assert done.status_code == 200
    assert done.json()["description"] == "By admin"


@pytest.mark.parametrize("body", [{"name": None}, {"description": None}, {"name": "ab"}])
async def test_invalid_patch_bodies_are_422(
    client: httpx.AsyncClient, app_schema: AppSession, body: dict[str, object]
) -> None:
    _, _, owner = await make_user(app_schema)
    group_id = (await _create(client, owner)).json()["id"]
    response = await client.patch(f"{URL}/{group_id}", json=body, headers=owner)
    assert response.status_code == 422


async def test_a_non_member_gets_404_on_every_playgroup_route(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group_id = (await _create(client, owner)).json()["id"]
    for response in (
        await client.get(f"{URL}/{group_id}", headers=stranger),
        await client.patch(f"{URL}/{group_id}", json={"name": "Nope"}, headers=stranger),
        await client.get(f"{URL}/00000000-0000-7000-8000-000000000000", headers=stranger),
        await client.get(f"{URL}/not-a-uuid", headers=stranger),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_the_list_pages_with_a_cursor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    for i in range(3):
        await _create(client, headers, name=f"Group {i}")
    first = (await client.get(URL, params={"limit": 2}, headers=headers)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    second = (
        await client.get(URL, params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers)
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    assert {i["role"] for i in first["items"] + second["items"]} == {"owner"}


async def _send(
    client: httpx.AsyncClient, method: str, url: str, body: dict[str, str], headers: dict[str, str]
) -> httpx.Response:
    """Send ``body`` as ASCII-escaped JSON, so a lone surrogate reaches the server intact."""
    return await client.request(
        method,
        url,
        content=json.dumps(body),
        headers={**headers, "Content-Type": "application/json"},
    )


BAD_NAMES = ["abc\x00", "ab\x00c", "ab\x07c", "ab\nc", "ab\x85c", "ab\ud800c"]
BAD_DESCRIPTIONS = ["a\x00b", "a\x07b", "a\x85b", "a\x1fb", "a\ud800b"]


@pytest.mark.parametrize("name", BAD_NAMES)
async def test_control_characters_in_a_name_are_422_on_post_and_patch(
    client: httpx.AsyncClient, app_schema: AppSession, name: str
) -> None:
    _, _, headers = await make_user(app_schema)
    group_id = (await _create(client, headers)).json()["id"]
    for response in (
        await _send(client, "POST", URL, {"name": name}, headers),
        await _send(client, "PATCH", f"{URL}/{group_id}", {"name": name}, headers),
    ):
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert error["details"]["fields"][0]["loc"][-1] == "name"


@pytest.mark.parametrize("description", BAD_DESCRIPTIONS)
async def test_control_characters_in_a_description_are_422_on_post_and_patch(
    client: httpx.AsyncClient, app_schema: AppSession, description: str
) -> None:
    _, _, headers = await make_user(app_schema)
    group_id = (await _create(client, headers)).json()["id"]
    for response in (
        await _send(client, "POST", URL, {"name": "Friday", "description": description}, headers),
        await _send(client, "PATCH", f"{URL}/{group_id}", {"description": description}, headers),
    ):
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert error["details"]["fields"][0]["loc"][-1] == "description"


async def test_a_description_keeps_newlines_carriage_returns_and_tabs(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    text = "line one\r\nline two\n\tindented"
    created = await _create(client, headers, description=text)
    assert created.status_code == 201
    assert created.json()["description"] == text


async def test_a_name_padded_with_control_whitespace_is_trimmed_then_accepted(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, headers = await make_user(app_schema)
    response = await _create(client, headers, name="\x85 Friday \x85")
    assert response.status_code == 201
    assert response.json()["name"] == "Friday"
