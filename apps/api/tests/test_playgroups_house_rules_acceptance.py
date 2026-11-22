"""Acceptance tests for the playgroup house rules note (S-2.5.4, API part).

Written from the story's acceptance criteria: who may save, the 5,000 character limit, text
stored and returned exactly as sent, and 403 for a plain member.
"""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.db import AppSession
from tests.factories import GuestPlayerFactory, create
from tests.playgroup_support import join, make_user
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
URL = "/api/v1/playgroups"
NO_RULES = {"text": "", "edited_by": None, "edited_at": None}


def _rules(group: str) -> str:
    return f"{URL}/{group}/house-rules"


async def _group(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(URL, json={"name": "Friday Night"}, headers=headers)
    assert response.status_code == 201
    return str(response.json()["id"])


async def _put(
    client: httpx.AsyncClient, group: str, headers: dict[str, str], body: Any
) -> httpx.Response:
    return await client.put(_rules(group), json=body, headers=headers)


async def _call(
    client: httpx.AsyncClient,
    method: str,
    group: str,
    headers: dict[str, str] | None,
    body: Any = None,
) -> httpx.Response:
    if method == "get":
        return await client.get(_rules(group), headers=headers)
    return await client.put(_rules(group), json=body, headers=headers)


async def _stored(session: AppSession, group: str) -> str:
    row = await session.execute(
        text("SELECT house_rules_text FROM playgroups WHERE id = :g"), {"g": group}
    )
    return str(row.scalar_one())


async def _state(session: AppSession, group: str) -> tuple[Any, ...]:
    row = await session.execute(
        text(
            "SELECT house_rules_text, house_rules_edited_by, house_rules_edited_at "
            "FROM playgroups WHERE id = :g"
        ),
        {"g": group},
    )
    return tuple(row.one())


def _without_request_id(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body["error"].pop("request_id", None)
    return body


async def _raw_put(
    client: httpx.AsyncClient, group: str, headers: dict[str, str], raw_body: str
) -> httpx.Response:
    return await client.put(
        _rules(group),
        content=raw_body.encode(),
        headers={**headers, "Content-Type": "application/json"},
    )


# --- Criterion 1: owner or admin saves, members see it with editor and date ------------------


async def test_owner_save_is_read_identically_by_owner_admin_and_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await join(app_schema, group, member_player)
    before = datetime.now(UTC) - timedelta(seconds=5)
    saved = await _put(client, group, owner, {"text": "No proxies.\nBe nice."})
    assert saved.status_code == 200
    reads = [(await client.get(_rules(group), headers=h)).json() for h in (owner, admin, member)]
    assert reads[0] == reads[1] == reads[2] == saved.json()
    assert reads[0]["text"] == "No proxies.\nBe nice."
    assert reads[0]["edited_by"]["player_id"] == str(owner_player)
    edited_at = datetime.fromisoformat(reads[0]["edited_at"])
    assert edited_at.tzinfo is not None
    assert before <= edited_at <= datetime.now(UTC) + timedelta(seconds=5)


async def test_admin_save_is_attributed_to_the_admin_and_seen_by_all(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await join(app_schema, group, member_player)
    saved = await _put(client, group, admin, {"text": "Admin rules"})
    assert saved.status_code == 200
    for headers in (owner, admin, member):
        body = (await client.get(_rules(group), headers=headers)).json()
        assert body["text"] == "Admin rules"
        assert body["edited_by"]["player_id"] == str(admin_player)


async def test_edited_by_carries_the_current_display_name_of_the_saver(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group = await _group(client, owner)
    name = (
        await app_schema.execute(
            text("SELECT display_name FROM players WHERE id = :p"), {"p": owner_player}
        )
    ).scalar_one()
    saved = (await _put(client, group, owner, {"text": "r"})).json()
    assert saved["edited_by"] == {"player_id": str(owner_player), "display_name": name}


async def test_client_sent_editor_and_time_are_not_honoured(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, other_player, _ = await make_user(app_schema)
    group = await _group(client, owner)
    response = await _put(
        client,
        group,
        owner,
        {
            "text": "r",
            "edited_at": "2001-01-01T00:00:00Z",
            "edited_by": {"player_id": str(other_player), "display_name": "Mallory"},
        },
    )
    # Ignoring or rejecting the extra fields are both acceptable; obeying them is not.
    assert response.status_code in (200, 422)
    body = (await client.get(_rules(group), headers=owner)).json()
    if response.status_code == 200:
        assert body["edited_by"]["player_id"] == str(owner_player)
        assert not body["edited_at"].startswith("2001")
    else:
        assert body == NO_RULES


async def test_a_second_save_by_someone_else_replaces_text_and_attribution(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    first = (await _put(client, group, owner, {"text": "first"})).json()
    second = (await _put(client, group, admin, {"text": "second"})).json()
    assert first["edited_by"]["player_id"] == str(owner_player)
    assert second["text"] == "second"
    assert second["edited_by"]["player_id"] == str(admin_player)
    assert datetime.fromisoformat(second["edited_at"]) >= datetime.fromisoformat(first["edited_at"])
    assert (await client.get(_rules(group), headers=owner)).json() == second


async def test_exactly_5000_characters_are_accepted_and_round_trip(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    body = "a" * 5000
    saved = await _put(client, group, owner, {"text": body})
    assert saved.status_code == 200
    assert saved.json()["text"] == body
    assert (await client.get(_rules(group), headers=owner)).json()["text"] == body
    assert await _stored(app_schema, group) == body


async def test_5001_characters_are_422_and_change_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    first = (await _put(client, group, owner, {"text": "keep me"})).json()
    over = await _put(client, group, admin, {"text": "x" * 5001})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "validation_error"
    after = (await client.get(_rules(group), headers=owner)).json()
    assert after == first
    assert after["edited_by"]["player_id"] == str(owner_player)


@pytest.mark.parametrize("unit", ["\u00e9", "\U0001f600", "e\u0301", "\u6f22"])
async def test_the_limit_counts_characters_not_bytes(
    client: httpx.AsyncClient, app_schema: AppSession, unit: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    chars = unit * (5000 // len(unit))
    assert len(chars) == 5000
    saved = await _put(client, group, owner, {"text": chars})
    assert saved.status_code == 200
    assert saved.json()["text"] == chars
    assert await _stored(app_schema, group) == chars
    assert (await _put(client, group, owner, {"text": chars + "x"})).status_code == 422


async def test_crlf_line_breaks_count_as_two_characters_each(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    """Ambiguity: 5,000 CRLF pairs are 10,000 characters. Counting what was sent (no newline
    normalisation) is the least surprising reading, so this is 422; 2,500 pairs are accepted."""
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    assert (await _put(client, group, owner, {"text": "x" + "\r\n" * 5000})).status_code == 422
    ok = await _put(client, group, owner, {"text": "x" + "\r\n" * 2499})
    assert ok.status_code == 200
    assert ok.json()["text"] == "x" + "\r\n" * 2499


async def test_empty_text_clears_the_rules_and_records_who_and_when(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    first = (await _put(client, group, owner, {"text": "rules"})).json()
    cleared = await _put(client, group, admin, {"text": ""})
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["text"] == ""
    assert body["edited_by"]["player_id"] == str(admin_player)
    assert datetime.fromisoformat(body["edited_at"]) >= datetime.fromisoformat(first["edited_at"])
    assert (await client.get(_rules(group), headers=owner)).json() == body


async def test_whitespace_only_text_is_stored_as_empty(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    """Ambiguity: criterion 2 says text is shown as sent, but whitespace-only text is a clear.
    The documented behaviour (the schema description says so) is that it becomes ``""``."""
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    for blank in ("   ", "\n\n", "\t \r\n"):
        response = await _put(client, group, owner, {"text": blank})
        assert response.status_code == 200
        assert response.json()["text"] == ""
        assert await _stored(app_schema, group) == ""


async def test_the_savers_later_rename_shows_the_new_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group = await _group(client, owner)
    await _put(client, group, owner, {"text": "r"})
    await app_schema.execute(
        text("UPDATE players SET display_name = 'Renamed Ren' WHERE id = :p"), {"p": owner_player}
    )
    await app_schema.commit()
    body = (await client.get(_rules(group), headers=owner)).json()
    assert body["edited_by"] == {"player_id": str(owner_player), "display_name": "Renamed Ren"}


async def test_an_editor_who_left_is_still_named_with_the_current_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await _put(client, group, admin, {"text": "r"})
    await app_schema.execute(
        text("UPDATE memberships SET left_at = now() WHERE player_id = :p"), {"p": admin_player}
    )
    await app_schema.execute(
        text("UPDATE players SET display_name = 'Gone Gary' WHERE id = :p"), {"p": admin_player}
    )
    await app_schema.commit()
    body = (await client.get(_rules(group), headers=owner)).json()
    assert body["text"] == "r"
    assert body["edited_by"] == {"player_id": str(admin_player), "display_name": "Gone Gary"}


async def test_a_merged_editor_is_shown_as_the_survivor(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await _put(client, group, admin, {"text": "r"})
    _, survivor, _ = await make_user(app_schema)
    await app_schema.execute(
        text("UPDATE players SET merged_into_id = :s WHERE id = :p"),
        {"s": survivor, "p": admin_player},
    )
    await app_schema.commit()
    body = (await client.get(_rules(group), headers=owner)).json()
    assert body["edited_by"]["player_id"] == str(survivor)


async def test_house_rules_of_another_playgroup_are_unaffected_and_unreadable(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner_a = await make_user(app_schema)
    _, _, owner_b = await make_user(app_schema)
    group_a = await _group(client, owner_a)
    group_b = await _group(client, owner_b)
    await _put(client, group_a, owner_a, {"text": "A rules"})
    await _put(client, group_b, owner_b, {"text": "B rules"})
    assert (await client.get(_rules(group_a), headers=owner_a)).json()["text"] == "A rules"
    assert (await client.get(_rules(group_b), headers=owner_b)).json()["text"] == "B rules"
    assert (await client.get(_rules(group_b), headers=owner_a)).status_code == 404
    assert (await _put(client, group_b, owner_a, {"text": "hijack"})).status_code == 404
    assert await _stored(app_schema, group_b) == "B rules"


# --- Criterion 2: stored and returned exactly as sent ----------------------------------------

HOSTILE = [
    "<script>alert(1)</script>",
    "<b>bold</b>",
    "<img src=x onerror=alert(1)>",
    "&lt;script&gt; &amp; &quot; &#60;",
    "javascript:alert(document.cookie)",
    "[link](http://x) **bold** # heading\n- item\n> quote\n`code`",
    "{{ template }} {% if x %}y{% endif %} ${process.env.SECRET} #{ruby}",
    "'; DROP TABLE playgroups; -- \" OR '1'='1'; \\ \\' %s %(x)s :p",
    "  leading and trailing  ",
    "\n\nblank lines around\n\n",
    "col1\tcol2\t\tcol3",
    "unix\nbreaks\nonly",
    "windows\r\nbreaks\r\nonly",
    "old mac\rbreaks\ronly",
    "mixed\r\n\n\r\r\nend",
    "emoji \U0001f600\U0001f468\u200d\U0001f469\u200d\U0001f467 "
    "\U0001f3f3\ufe0f\u200d\U0001f308 combining e\u0301 a\u0308\u0323",
    "rtl \u200f\u202e text \u200e\u202c \u0645\u0631\u062d\u0628\u0627 \u05e9\u05dc\u05d5\u05dd",
    "zero\u200bwidth\u00a0nbsp\u2028line sep\u2029para sep",
    "x" * 4999 + "\n",
    "word " * 999 + "word",
]


@pytest.mark.parametrize("raw", HOSTILE, ids=[f"hostile-{i}" for i in range(len(HOSTILE))])
async def test_hostile_and_formatted_text_round_trips_byte_for_byte(
    client: httpx.AsyncClient, app_schema: AppSession, raw: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    saved = await _put(client, group, owner, {"text": raw})
    assert saved.status_code == 200
    assert saved.json()["text"].encode() == raw.encode()
    read = await client.get(_rules(group), headers=owner)
    assert read.json()["text"].encode() == raw.encode()
    assert (await _stored(app_schema, group)).encode() == raw.encode()


async def test_one_very_long_line_with_html_round_trips(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    raw = "<b>" * 1666 + "zz"
    assert len(raw) == 5000
    assert (await _put(client, group, owner, {"text": raw})).json()["text"] == raw
    assert (await client.get(_rules(group), headers=owner)).json()["text"] == raw


async def test_responses_are_json_so_nothing_is_served_as_html(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    put = await _put(client, group, owner, {"text": "<script>alert(1)</script>"})
    get = await client.get(_rules(group), headers=owner)
    for response in (put, get):
        assert response.headers["content-type"].split(";")[0] == "application/json"
        assert json.loads(response.content)["text"] == "<script>alert(1)</script>"


@pytest.mark.parametrize("raw_json", ['"a\\u0000b"', '"\\u0000"', '"\\u0000' + "x" * 100 + '"'])
async def test_nul_is_422_never_500(
    client: httpx.AsyncClient, app_schema: AppSession, raw_json: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    response = await _raw_put(client, group, owner, '{"text": ' + raw_json + "}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert (await client.get(_rules(group), headers=owner)).json() == NO_RULES


@pytest.mark.parametrize("escape", ["\\u0001", "\\u001b", "\\u007f", "\\u0085", "\\ud800"])
async def test_other_control_characters_and_lone_surrogates_are_422(
    client: httpx.AsyncClient, app_schema: AppSession, escape: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    response = await _raw_put(client, group, owner, '{"text": "a' + escape + 'b"}')
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_the_database_row_holds_exactly_the_sent_text_editor_and_time(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group = await _group(client, owner)
    raw = "  <i>x</i>\r\n&amp;\r  "
    await _put(client, group, owner, {"text": raw})
    stored, editor, edited_at = await _state(app_schema, group)
    assert stored == raw
    assert editor == owner_player
    assert edited_at is not None
    assert edited_at.tzinfo is not None


# --- Criterion 3: roles ----------------------------------------------------------------------


async def test_a_plain_member_put_is_403_forbidden_and_changes_nothing(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, member_player)
    await _put(client, group, owner, {"text": "owner text"})
    before = await _state(app_schema, group)
    denied = await _put(client, group, member, {"text": "member text"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert await _state(app_schema, group) == before


async def test_a_plain_member_cannot_clear_the_rules_but_can_read_them(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, member_player)
    await _put(client, group, owner, {"text": "owner text"})
    assert (await _put(client, group, member, {"text": ""})).status_code == 403
    read = await client.get(_rules(group), headers=member)
    assert read.status_code == 200
    assert read.json()["text"] == "owner text"


async def test_a_plain_member_sending_an_oversized_text_is_403_not_422(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    """Ambiguity: a member's 6,000 characters could be 403 or 422. Authorisation first is the
    least surprising order (it leaks nothing and honours the 403 criterion)."""
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, member_player)
    assert (await _put(client, group, member, {"text": "x" * 6000})).status_code == 403


async def test_a_demoted_admin_gets_403_and_a_re_promoted_one_can_save(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    assert (await _put(client, group, admin, {"text": "as admin"})).status_code == 200
    await app_schema.execute(
        text("UPDATE memberships SET role = 'member' WHERE player_id = :p"), {"p": admin_player}
    )
    await app_schema.commit()
    denied = await _put(client, group, admin, {"text": "as demoted"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert (await client.get(_rules(group), headers=admin)).json()["text"] == "as admin"
    await app_schema.execute(
        text("UPDATE memberships SET role = 'admin' WHERE player_id = :p"), {"p": admin_player}
    )
    await app_schema.commit()
    assert (await _put(client, group, admin, {"text": "promoted again"})).status_code == 200


async def test_a_member_promoted_through_the_api_can_save(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, member_player, member = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, member_player)
    assert (await _put(client, group, member, {"text": "no"})).status_code == 403
    promote = await client.patch(
        f"{URL}/{group}/members/{member_player}", json={"role": "admin"}, headers=owner
    )
    assert promote.status_code == 200
    assert (await _put(client, group, member, {"text": "yes"})).status_code == 200


@pytest.mark.parametrize("method", ["get", "put"])
async def test_a_former_admin_gets_the_non_member_answer(
    client: httpx.AsyncClient, app_schema: AppSession, method: str
) -> None:
    _, _, owner = await make_user(app_schema)
    _, former_player, former = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, former_player, "admin", left=True)
    got = await _call(client, method, group, former, {"text": "x"})
    ref = await _call(client, method, group, stranger, {"text": "x"})
    assert got.status_code == 404
    assert _without_request_id(got) == _without_request_id(ref)


@pytest.mark.parametrize("method", ["get", "put"])
async def test_non_members_get_the_same_404_as_an_unknown_playgroup(
    client: httpx.AsyncClient, app_schema: AppSession, method: str
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    _, _, other_owner = await make_user(app_schema)
    group = await _group(client, owner)
    other_group = await _group(client, other_owner)
    unknown = "00000000-0000-7000-8000-00000000dead"
    reference = await _call(client, method, unknown, stranger, {"text": "x"})
    assert reference.status_code == 404
    assert reference.json()["error"]["code"] == "not_found"
    for actual_group, headers in (
        (group, stranger),
        (group, other_owner),
        (other_group, stranger),
    ):
        response = await _call(client, method, actual_group, headers, {"text": "x"})
        assert response.status_code == 404
        assert _without_request_id(response) == _without_request_id(reference)


async def test_a_non_member_sending_6000_characters_gets_404_not_422(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group = await _group(client, owner)
    response = await _put(client, group, stranger, {"text": "x" * 6000})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_a_non_member_sending_a_malformed_body_gets_404_not_422(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    _, _, stranger = await make_user(app_schema)
    group = await _group(client, owner)
    assert (await _put(client, group, stranger, {"text": None})).status_code == 404


async def test_a_non_uuid_playgroup_id_is_404_for_both_routes(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    for method in ("get", "put"):
        response = await _call(client, method, "not-a-uuid", owner, {"text": "x"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("method", ["get", "put"])
async def test_unauthenticated_requests_are_401(
    client: httpx.AsyncClient, app_schema: AppSession, method: str
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    response = await _call(client, method, group, None, {"text": "x"})
    assert response.status_code == 401
    bad = await _call(client, method, group, {"Authorization": "Bearer nonsense"}, {"text": "x"})
    assert bad.status_code == 401
    assert await _stored(app_schema, group) == ""


async def test_a_guest_member_cannot_read_without_credentials(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    guest = await create(app_schema, GuestPlayerFactory)
    await app_schema.commit()
    await join(app_schema, group, guest.id)
    assert (await client.get(_rules(group))).status_code == 401


# --- Malformed bodies ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [{}, {"text": None}, {"text": 5}, {"text": True}, {"text": ["a"]}, {"text": {"a": 1}}, ["x"]],
    ids=["empty", "null", "int", "bool", "list-text", "dict-text", "list-body"],
)
async def test_malformed_bodies_are_422_and_change_nothing(
    client: httpx.AsyncClient, app_schema: AppSession, body: Any
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    await _put(client, group, owner, {"text": "keep"})
    before = await _state(app_schema, group)
    response = await _put(client, group, owner, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await _state(app_schema, group) == before


async def test_a_non_json_or_broken_or_missing_body_is_422(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    plain = await client.put(
        _rules(group), content=b"just text", headers={**owner, "Content-Type": "text/plain"}
    )
    assert plain.status_code == 422
    assert (await _raw_put(client, group, owner, '{"text": ')).status_code == 422
    assert (await client.put(_rules(group), headers=owner)).status_code == 422


async def test_extra_fields_are_ignored_or_rejected_but_never_applied(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    response = await _put(client, group, owner, {"text": "ok", "name": "Hacked"})
    assert response.status_code in (200, 422)
    name = (
        await app_schema.execute(text("SELECT name FROM playgroups WHERE id = :g"), {"g": group})
    ).scalar_one()
    assert name == "Friday Night"


async def test_the_422_details_mention_the_text_field(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    error = (await _put(client, group, owner, {"text": "x" * 5001})).json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"]
    assert "text" in json.dumps(error["details"])


async def test_other_methods_on_the_route_are_405(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, _, owner = await make_user(app_schema)
    group = await _group(client, owner)
    assert (await client.post(_rules(group), json={"text": "x"}, headers=owner)).status_code == 405
    assert (await client.delete(_rules(group), headers=owner)).status_code == 405
    assert (await client.patch(_rules(group), json={"text": "x"}, headers=owner)).status_code == 405


# --- Query count and OpenAPI -----------------------------------------------------------------


async def test_get_query_count_does_not_grow_with_members_or_playgroups(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, _, owner = await make_user(app_schema)
    _, admin_player, admin = await make_user(app_schema)
    group = await _group(client, owner)
    await join(app_schema, group, admin_player, "admin")
    await _put(client, group, admin, {"text": "rules"})
    await client.get(_rules(group), headers=owner)  # warm up
    with count_statements() as small:
        assert (await client.get(_rules(group), headers=owner)).status_code == 200
    for _ in range(8):
        _, extra_player, _ = await make_user(app_schema)
        await join(app_schema, group, extra_player)
        _, _, extra_owner = await make_user(app_schema)
        await _group(client, extra_owner)
    with count_statements() as large:
        assert (await client.get(_rules(group), headers=owner)).status_code == 200
    assert large.count == small.count


async def test_the_openapi_document_lists_both_routes_and_the_put_says_verbatim_plain_text(
    api_app: FastAPI,
) -> None:
    operations = api_app.openapi()["paths"]["/api/v1/playgroups/{playgroup_id}/house-rules"]
    assert {"get", "put"} <= set(operations)
    description = " ".join(operations["put"]["description"].lower().split())
    assert "exactly as sent" in description or "verbatim" in description
    assert "plain text" in description


async def test_the_openapi_schema_declares_the_5000_limit_and_required_fields(
    api_app: FastAPI,
) -> None:
    schemas = api_app.openapi()["components"]["schemas"]
    update = schemas["HouseRulesUpdate"]
    assert update["properties"]["text"]["maxLength"] == 5000
    assert update["required"] == ["text"]
    assert set(schemas["HouseRulesOut"]["required"]) == {"text", "edited_by", "edited_at"}


async def test_the_state_helper_type_uuid_is_a_uuid(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, owner_player, owner = await make_user(app_schema)
    group = await _group(client, owner)
    await _put(client, group, owner, {"text": "r"})
    _, editor, _ = await _state(app_schema, group)
    assert isinstance(editor, UUID)
    assert editor == owner_player
