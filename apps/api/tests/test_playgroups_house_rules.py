"""``/playgroups/{id}/house-rules``: saving, reading, validation and role checks (S-2.5.4)."""

import httpx
import pytest
from sqlalchemy import text

from app.core.db import AppSession
from tests.playgroup_support import join, make_user

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"


async def _group(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(URL, json={"name": "Friday Night"}, headers=headers)
    return str(response.json()["id"])


async def test_a_new_playgroup_has_empty_never_edited_house_rules(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    response = await client.get(f"{URL}/{group}/house-rules", headers=owner)
    assert response.status_code == 200
    assert response.json() == {"text": "", "edited_by": None, "edited_at": None}


async def test_owner_saves_and_a_member_reads_with_editor_and_time(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, member_player)
    saved = await client.put(
        f"{URL}/{group}/house-rules", json={"text": "No proxies"}, headers=owner
    )
    assert saved.status_code == 200
    seen = (await client.get(f"{URL}/{group}/house-rules", headers=member)).json()
    assert seen["text"] == "No proxies"
    assert seen["edited_by"]["player_id"] == str(owner_player)
    assert seen["edited_by"]["display_name"]
    assert seen["edited_at"].endswith(("Z", "+00:00"))


async def test_an_admin_can_save_and_a_plain_member_gets_403(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await join(app_schema, group, member_player)
    assert (
        await client.put(f"{URL}/{group}/house-rules", json={"text": "a"}, headers=admin)
    ).status_code == 200
    denied = await client.put(f"{URL}/{group}/house-rules", json={"text": "b"}, headers=member)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert (await client.get(f"{URL}/{group}/house-rules", headers=member)).json()["text"] == "a"


async def test_non_and_former_members_get_404(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, former_player, "admin", left=True)
    for headers in (stranger, former):
        assert (await client.get(f"{URL}/{group}/house-rules", headers=headers)).status_code == 404
        put = await client.put(f"{URL}/{group}/house-rules", json={"text": "x"}, headers=headers)
        assert put.status_code == 404
    assert (await client.get(f"{URL}/not-a-uuid/house-rules", headers=owner)).status_code == 404


async def test_text_is_stored_verbatim_with_html_and_crlf(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    raw = "  <script>alert(1)</script>\r\n<b>bold</b> &amp;\n\tend  "
    await client.put(f"{URL}/{group}/house-rules", json={"text": raw}, headers=owner)
    response = await client.get(f"{URL}/{group}/house-rules", headers=owner)
    assert response.json()["text"] == raw


async def test_length_limit_counts_characters_not_bytes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    ok = await client.put(f"{URL}/{group}/house-rules", json={"text": "é😀" * 2500}, headers=owner)
    assert ok.status_code == 200
    over = await client.put(f"{URL}/{group}/house-rules", json={"text": "x" * 5001}, headers=owner)
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("bad", ["a\x00b", "a\x1bb", "\ud800"])
async def test_control_characters_are_422(
    client: httpx.AsyncClient, app_schema: AppSession, bad: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    content = (
        '{"text": "' + bad.encode("unicode_escape").decode().replace("\\\\", "\\") + '"}'
    ).encode()
    response = await client.put(
        f"{URL}/{group}/house-rules",
        content=content,
        headers={**owner, "Content-Type": "application/json"},
    )
    assert response.status_code == 422


async def test_non_string_text_is_422(client: httpx.AsyncClient, app_schema: AppSession) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    for body in ({"text": None}, {"text": 5}, {}):
        assert (
            await client.put(f"{URL}/{group}/house-rules", json=body, headers=owner)
        ).status_code == 422


async def test_empty_or_blank_text_clears_but_keeps_the_editor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await client.put(f"{URL}/{group}/house-rules", json={"text": "rules"}, headers=owner)
    cleared = await client.put(f"{URL}/{group}/house-rules", json={"text": " \n "}, headers=admin)
    body = cleared.json()
    assert body["text"] == ""
    assert body["edited_by"]["player_id"] == str(admin_player)
    assert body["edited_at"] is not None


async def test_editor_who_left_the_playgroup_is_still_named(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await client.put(f"{URL}/{group}/house-rules", json={"text": "r"}, headers=admin)
    await app_schema.execute(
        text("UPDATE memberships SET left_at = now() WHERE player_id = :p"), {"p": admin_player}
    )
    await app_schema.commit()
    body = (await client.get(f"{URL}/{group}/house-rules", headers=owner)).json()
    assert body["edited_by"]["player_id"] == str(admin_player)
