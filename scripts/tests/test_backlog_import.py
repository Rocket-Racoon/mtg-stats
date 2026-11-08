"""Tests for the backlog import script (S-1.2.4). No network: GitHub is a recording fake."""

# The pyproject per-file-ignores glob is relative to apps/api, so it does not cover scripts/tests.
# ruff: noqa: S101

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import backlog_import as bi
import pytest

REPO = "Rocket-Racoon/mtg-stats"
ISSUES_PATH = f"repos/{REPO}/issues"
LABELS_PATH = f"repos/{REPO}/labels"
MILESTONES_PATH = f"repos/{REPO}/milestones"
PLATFORMS = {"api", "web", "mobile", "shared", "infra"}
PRIORITIES = {"must", "should", "could", "wont"}
POINTS = {str(n) for n in range(1, 9)}
DECISIONS_EPICS = {"E-8": 2, "E-9": 3, "E-10": 4, "E-11": 5}
OPEN_QUESTIONS_HEADER = "| ID | Question | Raised in | Blocks | Status |\n|---|---|---|---|---|\n"

Write = tuple[str, str, Any]  # method, path, payload


# --------------------------------------------------------------------------- fake GitHub


class FakeGitHub(bi.GitHub):
    """Serves canned repo state through ``api`` and records every write call."""

    def __init__(self) -> None:
        super().__init__(REPO)
        self.labels: list[dict[str, Any]] = []
        self.milestones: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.parents: dict[int, int] = {}  # child number -> parent number
        self.writes: list[Write] = []
        self.preexisting: set[int] = set()
        self.created_milestones: dict[int, int] = {}  # phase -> milestone number
        self._next = 1000

    # -- GitHub API surface -----------------------------------------------------------
    def api(self, path: str, *, method: str = "GET", payload: Any = None) -> Any:
        base, _, query = path.partition("?")
        base = base.removeprefix(f"repos/{REPO}/")
        if method != "GET":
            self.writes.append((method, path, payload))
            return self._write(base, payload)
        params = dict(p.split("=") for p in query.split("&") if p)
        page, per_page = int(params.get("page", 1)), int(params.get("per_page", 30))
        rows = self._read(base)
        return rows[(page - 1) * per_page : page * per_page]

    def _read(self, base: str) -> list[Any]:
        if base == "labels":
            return self.labels
        if base == "milestones":
            return self.milestones
        if base == "issues":
            return self.issues
        if m := re.fullmatch(r"issues/(\d+)/sub_issues", base):
            return [i for i in self.issues if self.parents.get(i["number"]) == int(m.group(1))]
        raise AssertionError(f"unexpected GET {base}")

    def parent_of(self, number: int) -> int | None:
        return self.parents.get(number)

    def _write(self, base: str, payload: Any) -> Any:
        self._next += 1
        if base == "labels":
            return {"name": payload["name"]}
        if base == "milestones":
            phase = int(payload["title"].split()[1])
            self.created_milestones[phase] = self._next
            return {"number": self._next, "title": payload["title"]}
        if base == "issues":
            return {
                "number": self._next,
                "id": self._next + 10**6,
                "title": payload["title"],
                "state": "open",
                "labels": [{"name": n} for n in payload["labels"]],
            }
        return {}

    # -- seeding ----------------------------------------------------------------------
    def issue(self, number: int) -> dict[str, Any]:
        return next(i for i in self.issues if i["number"] == number)

    def add_issue(
        self, title: str, labels: list[str], *, closed: bool = False, **extra: Any
    ) -> dict[str, Any]:
        number = 1 + len(self.issues)
        issue = {
            "number": number,
            "id": number + 10**6,
            "title": title,
            "state": "closed" if closed else "open",
            "labels": [{"name": n} for n in labels],
            **extra,
        }
        self.issues.append(issue)
        self.preexisting.add(number)
        return issue

    def seed_all(self, items: list[bi.Item]) -> dict[str, int]:
        """Everything already imported: labels, milestones, issues, decisions and links."""
        self.labels = [{"name": n} for n in sorted({lb for i in items for lb in i.labels})]
        self.milestones = [{"number": p + 1, "title": f"Phase {p}"} for p in range(6)]
        numbers: dict[str, int] = {}
        for item in items:
            issue = self.add_issue(item.issue_title, item.labels, closed=item.wont)
            numbers[item.id] = issue["number"]
        for epic in (i for i in items if i.kind == "epic" and i.phase in bi.DECISIONS_PHASES):
            d = bi.decisions_item(epic)
            numbers[d.id] = self.add_issue(d.title, d.labels)["number"]
            self.parents[numbers[d.id]] = numbers[epic.id]
        for item in items:
            if item.parent:
                self.parents[numbers[item.id]] = numbers[item.parent]
        return numbers

    def remove(self, numbers: set[int]) -> None:
        self.issues = [i for i in self.issues if i["number"] not in numbers]
        self.parents = {c: p for c, p in self.parents.items() if c not in numbers}
        self.preexisting -= numbers

    # -- inspection -------------------------------------------------------------------
    def posts(self, path: str) -> list[Any]:
        return [w[2] for w in self.writes if w[0] == "POST" and w[1] == path]

    def link_writes(self) -> list[Write]:
        return [w for w in self.writes if w[1].endswith("/sub_issues")]


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def items() -> list[bi.Item]:
    return bi.parse_backlog()


@pytest.fixture(scope="module")
def by_id(items: list[bi.Item]) -> dict[str, bi.Item]:
    return {i.id: i for i in items}


@pytest.fixture
def full_repo(items: list[bi.Item]) -> tuple[FakeGitHub, dict[str, int]]:
    gh = FakeGitHub()
    return gh, gh.seed_all(items)


def run_apply(gh: FakeGitHub, items: list[bi.Item]) -> bi.Plan:
    plan, issues, milestones = bi.build_plan(items, gh)
    bi.apply_plan(plan, gh, issues, milestones)
    return plan


def writes_to_existing_issues(gh: FakeGitHub) -> list[Write]:
    """Write calls aimed at a pre-existing issue, other than POSTs to /sub_issues."""
    bad = []
    for method, path, payload in gh.writes:
        m = re.search(r"/issues/(\d+)(/.*)?$", path)
        if not m or int(m.group(1)) not in gh.preexisting:
            continue
        if method == "POST" and m.group(2) == "/sub_issues":
            continue
        bad.append((method, path, payload))
    return bad


# --------------------------------------------------------------------------- AC1, AC2: parser


class TestParser:
    def test_backlog_has_11_epics_62_features_374_stories(self, items: list[bi.Item]) -> None:
        assert Counter(i.kind for i in items) == {"epic": 11, "feature": 62, "story": 374}

    def test_backlog_has_six_wont_stories(self, items: list[bi.Item]) -> None:
        assert sum(1 for i in items if i.wont) == 6
        assert all(i.kind == "story" for i in items if i.wont)

    def test_ids_are_unique(self, items: list[bi.Item]) -> None:
        ids = [i.id for i in items]
        assert len(ids) == len(set(ids))

    def test_duplicate_ids_in_the_backlog_are_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("E01-a.md", "E02-b.md"):
            (tmp_path / name).write_text(
                "# E-1 — Epic\n\n| Phase(s) | 0 |\n\n## F-1.1 — Feat\n\nPhase 0 · x\n",
                encoding="utf-8",
            )
        monkeypatch.setattr(bi, "BACKLOG_DIR", tmp_path)
        with pytest.raises(ValueError, match="duplicate"):
            bi.parse_backlog()

    def test_story_parent_is_feature_with_matching_prefix(
        self, items: list[bi.Item], by_id: dict[str, bi.Item]
    ) -> None:
        for story in (i for i in items if i.kind == "story"):
            assert story.parent is not None
            assert by_id[story.parent].kind == "feature"
            assert story.id.removeprefix("S-").rsplit(".", 1)[0] == story.parent.removeprefix("F-")

    def test_feature_parent_is_epic_with_matching_prefix(
        self, items: list[bi.Item], by_id: dict[str, bi.Item]
    ) -> None:
        for feature in (i for i in items if i.kind == "feature"):
            assert feature.parent is not None
            assert by_id[feature.parent].kind == "epic"
            assert feature.id.removeprefix("F-").split(".")[0] == feature.parent.removeprefix("E-")

    def test_epics_have_no_parent(self, items: list[bi.Item]) -> None:
        assert all(i.parent is None for i in items if i.kind == "epic")

    def test_issue_title_starts_with_id(self, items: list[bi.Item]) -> None:
        for item in items:
            assert item.issue_title.startswith(f"[{item.id}] ")
            match = bi.TITLE_ID_RE.match(item.issue_title)
            assert match is not None
            assert match.group(1) == item.id

    def test_story_labels_have_one_type_phase_priority_points_and_a_platform(
        self, items: list[bi.Item]
    ) -> None:
        for story in (i for i in items if i.kind == "story"):
            prefixes = Counter(lb.split(":")[0] for lb in story.labels)
            assert story.labels.count("type:story") == 1, story.id
            assert prefixes["type"] == 1, story.id
            assert prefixes["phase"] == 1, story.id
            assert prefixes["priority"] == 1, story.id
            assert prefixes["points"] == 1, story.id
            assert prefixes["platform"] >= 1, story.id
            assert f"phase:{story.phase}" in story.labels

    def test_wont_stories_map_to_priority_wont(self, items: list[bi.Item]) -> None:
        for story in (i for i in items if i.kind == "story"):
            assert ("priority:wont" in story.labels) == story.wont, story.id

    def test_label_set_is_within_readme_taxonomy(self, items: list[bi.Item]) -> None:
        allowed = (
            {"type:epic", "type:feature", "type:story"}
            | {f"phase:{n}" for n in range(6)}
            | {f"platform:{p}" for p in PLATFORMS}
            | {f"priority:{p}" for p in PRIORITIES}
            | {f"points:{p}" for p in POINTS}
        )
        used = {lb for i in items for lb in i.labels}
        assert used <= allowed, used - allowed

    @pytest.mark.parametrize(
        ("feature_id", "phase"), [("F-2.1", 0), ("F-3.2", 1), ("F-4.1", 1), ("F-2.6", 5)]
    )
    def test_feature_phase_comes_from_the_field_line(
        self, by_id: dict[str, bi.Item], feature_id: str, phase: int
    ) -> None:
        assert by_id[feature_id].phase == phase
        assert f"phase:{phase}" in by_id[feature_id].labels

    def test_feature_phase_is_at_most_min_phase_of_its_active_stories(
        self, items: list[bi.Item]
    ) -> None:
        # F-7.2 is the one documented exception: its field line says "Phase 2", but S-7.2.8
        # (mobile chart kit) is deliberately phase 1 because F-4.6 needs it (E07, decision X3).
        violations = set()
        for feature in (i for i in items if i.kind == "feature"):
            active = [s.phase for s in items if s.parent == feature.id and not s.wont]
            if active and feature.phase > min(active):
                violations.add(feature.id)
        assert violations == {"F-7.2"}

    @pytest.mark.parametrize(
        ("epic_id", "phase"), [("E-1", 0), ("E-7", 1), ("E-8", 2), ("E-11", 5)]
    )
    def test_epic_phase(self, by_id: dict[str, bi.Item], epic_id: str, phase: int) -> None:
        assert by_id[epic_id].phase == phase
        assert f"phase:{phase}" in by_id[epic_id].labels

    def test_feature_phase_regex_ignores_prose_mentioning_a_phase(self) -> None:
        assert bi.FEATURE_PHASE_RE.match("Depends on Phase 5 work") is None
        assert bi.FEATURE_PHASE_RE.match("**Phase** 0 · x")
        assert bi.FEATURE_PHASE_RE.match("**Phase:** 1 · x")


# --------------------------------------------------------------------------- title matching


class TestTitleMatching:
    @pytest.mark.parametrize(
        ("title", "key"),
        [
            ("[S-1.1.1] Title", "S-1.1.1"),
            ("S-1.1.1 — Title", "S-1.1.1"),
            ("[F-2.6] Feature", "F-2.6"),
            ("[E-11] Epic", "E-11"),
        ],
    )
    def test_title_id_re_accepts_import_and_snapshot_forms(self, title: str, key: str) -> None:
        match = bi.TITLE_ID_RE.match(title)
        assert match is not None
        assert match.group(1) == key

    def test_title_id_re_rejects_decisions_title(self) -> None:
        assert bi.TITLE_ID_RE.match("Decisions: E-8 Player activity (before Phase 2)") is None

    def test_decisions_title_re_matches_decisions_form(self) -> None:
        match = bi.DECISIONS_TITLE_RE.match("Decisions: E-8 Player activity (before Phase 2)")
        assert match is not None
        assert match.group(1) == "E-8"

    def test_decisions_title_re_rejects_story_title(self) -> None:
        assert bi.DECISIONS_TITLE_RE.match("[S-1.1.1] Title") is None


# --------------------------------------------------------------------------- AC3: planning


class TestIdempotence:
    def test_nothing_is_planned_when_everything_exists(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        plan, _, _ = bi.build_plan(items, gh)
        assert plan.labels == []
        assert plan.milestones == []
        assert plan.issues == []
        assert plan.decisions == []
        assert plan.links == []
        assert plan.warnings == []

    def test_apply_plan_issues_zero_writes_when_everything_exists(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        run_apply(gh, items)
        assert gh.writes == []

    def test_unrelated_issues_and_pull_requests_are_ignored(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        gh.add_issue("Random bug report", [])
        gh.add_issue("[S-1.1.1] Title from a PR", [], pull_request={"url": "x"})
        plan, _, _ = bi.build_plan(items, gh)
        assert plan.issues == []

    def test_only_missing_items_are_planned_and_created_with_labels_and_milestone(
        self,
        items: list[bi.Item],
        by_id: dict[str, bi.Item],
        full_repo: tuple[FakeGitHub, dict[str, int]],
    ) -> None:
        gh, numbers = full_repo
        wont = next(i for i in items if i.wont)
        missing_ids = ["S-1.1.1", "S-1.1.2", wont.id, "F-2.1"]
        gh.remove({numbers[i] for i in missing_ids})
        plan = run_apply(gh, items)

        assert sorted(i.id for i in plan.issues) == sorted(missing_ids)
        assert plan.decisions == []
        created = gh.posts(ISSUES_PATH)
        assert sorted(p["title"] for p in created) == sorted(
            by_id[i].issue_title for i in missing_ids
        )
        for payload in created:
            item = next(i for i in items if i.issue_title == payload["title"])
            assert payload["labels"] == item.labels
            assert payload["milestone"] == item.phase + 1  # seeded milestone number

    def test_missing_items_are_linked_to_their_parent(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        gh.remove({numbers["S-1.1.1"]})
        plan = run_apply(gh, items)
        assert plan.links == [("F-1.1", "S-1.1.1")]
        links = gh.link_writes()
        assert len(links) == 1
        assert links[0][1] == f"{ISSUES_PATH}/{numbers['F-1.1']}/sub_issues"
        assert links[0][2]["sub_issue_id"] > 10**6

    def test_wont_story_is_closed_not_planned_and_only_new_issues_are_patched(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        wont = next(i for i in items if i.wont)
        gh.remove({numbers[wont.id], numbers["S-1.1.1"]})
        run_apply(gh, items)
        patches = [w for w in gh.writes if w[0] == "PATCH"]
        assert len(patches) == 1
        assert patches[0][2] == {"state": "closed", "state_reason": "not_planned"}
        assert int(patches[0][1].rsplit("/", 1)[1]) not in gh.preexisting

    def test_no_write_targets_a_preexisting_issue_except_sub_issue_links(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        wont = next(i for i in items if i.wont)
        gh.remove({numbers[i] for i in ("S-1.1.1", "F-1.2", wont.id)})
        run_apply(gh, items)
        assert gh.writes
        assert writes_to_existing_issues(gh) == []

    def test_existing_issue_in_the_wrong_state_is_never_edited(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        wont = next(i for i in items if i.wont)
        gh.issue(numbers[wont.id])["state"] = "open"
        run_apply(gh, items)
        assert [w for w in gh.writes if w[0] in {"PATCH", "PUT", "DELETE"}] == []

    def test_two_issues_claiming_the_same_id_raise_before_any_write(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        gh.add_issue("[S-1.1.1] A duplicate", [])
        with pytest.raises(RuntimeError, match=r"S-1\.1\.1"):
            run_apply(gh, items)
        assert gh.writes == []

    def test_two_decisions_issues_for_one_epic_raise_before_any_write(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        gh.add_issue("Decisions: E-8 Another (before Phase 2)", [])
        with pytest.raises(RuntimeError, match="E-8"):
            run_apply(gh, items)
        assert gh.writes == []

    def test_existing_child_without_parent_is_linked(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        del gh.parents[numbers["S-1.1.1"]]
        plan = run_apply(gh, items)
        assert plan.links == [("F-1.1", "S-1.1.1")]
        assert plan.warnings == []
        assert [w[1] for w in gh.writes] == [f"{ISSUES_PATH}/{numbers['F-1.1']}/sub_issues"]
        assert gh.writes[0][2] == {"sub_issue_id": numbers["S-1.1.1"] + 10**6}

    def test_existing_child_parented_elsewhere_is_not_linked_but_warned(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        gh.parents[numbers["S-1.1.1"]] = numbers["F-1.2"]
        plan = run_apply(gh, items)
        assert plan.links == []
        assert len(plan.warnings) == 1
        assert "S-1.1.1" in plan.warnings[0]
        assert gh.writes == []

    def test_open_issue_labelled_wont_warns_and_writes_nothing(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        wont = next(i for i in items if i.wont)
        gh.issue(numbers[wont.id])["state"] = "open"
        plan = run_apply(gh, items)
        assert len(plan.warnings) == 1
        assert wont.id in plan.warnings[0]
        assert gh.writes == []

    def test_closed_wont_issue_produces_no_warning(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        plan, _, _ = bi.build_plan(items, gh)
        assert plan.warnings == []

    def test_missing_labels_and_milestones_are_created_and_existing_left_alone(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        gh.labels = [lb for lb in gh.labels if lb["name"] not in {"points:8", "platform:infra"}]
        gh.milestones = [m for m in gh.milestones if m["title"] != "Phase 3"]
        plan = run_apply(gh, items)
        assert plan.labels == ["platform:infra", "points:8"]
        assert plan.milestones == [3]
        assert sorted(p["name"] for p in gh.posts(LABELS_PATH)) == ["platform:infra", "points:8"]
        assert [p["title"] for p in gh.posts(MILESTONES_PATH)] == ["Phase 3"]
        assert len(gh.writes) == 3

    def test_milestone_titles_with_a_suffix_are_matched_by_phase_number(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, _ = full_repo
        gh.milestones = [{"number": p + 1, "title": f"Phase {p} - Something"} for p in range(6)]
        plan, _, _ = bi.build_plan(items, gh)
        assert plan.milestones == []


class TestFullImportFromEmptyRepo:
    def test_every_item_is_created_with_its_labels_and_title(self, items: list[bi.Item]) -> None:
        gh = FakeGitHub()
        plan = run_apply(gh, items)
        created = gh.posts(ISSUES_PATH)
        assert len(created) == 11 + 62 + 374 + 4
        by_title = {p["title"]: p for p in created}
        assert len(by_title) == len(created)
        for item in items:
            assert by_title[item.issue_title]["labels"] == item.labels
        assert plan.milestones == [0, 1, 2, 3, 4, 5]

    def test_every_child_is_linked_under_its_parent(self, items: list[bi.Item]) -> None:
        gh = FakeGitHub()
        run_apply(gh, items)
        assert len(gh.link_writes()) == 62 + 374 + 4
        assert sum(1 for w in gh.writes if w[0] == "PATCH") == 6

    def test_epic_issues_are_in_their_phase_milestone(self, items: list[bi.Item]) -> None:
        gh = FakeGitHub()
        run_apply(gh, items)
        assert sorted(gh.created_milestones) == [0, 1, 2, 3, 4, 5]
        by_title = {p["title"]: p for p in gh.posts(ISSUES_PATH)}
        for epic in (i for i in items if i.kind == "epic"):
            assert by_title[epic.issue_title]["milestone"] == gh.created_milestones[epic.phase]


# --------------------------------------------------------------------------- AC4: Decisions


class TestDecisions:
    def test_decisions_planned_for_epics_in_phases_2_to_5_only(self, items: list[bi.Item]) -> None:
        plan, _, _ = bi.build_plan(items, FakeGitHub())
        assert sorted(d.parent or "" for d in plan.decisions) == sorted(DECISIONS_EPICS)

    def test_decisions_issue_is_titled_for_its_epic_and_parented_to_it(
        self, items: list[bi.Item], by_id: dict[str, bi.Item]
    ) -> None:
        plan, _, _ = bi.build_plan(items, FakeGitHub())
        for d in plan.decisions:
            assert d.parent is not None
            epic = by_id[d.parent]
            assert d.title.startswith(f"Decisions: {epic.id} ")
            assert bi.DECISIONS_TITLE_RE.match(d.title)
            assert bi.TITLE_ID_RE.match(d.title) is None
            assert d.phase == epic.phase
            assert (d.parent, d.id) in plan.links

    def test_applied_decisions_land_in_the_epics_phase_milestone(
        self, items: list[bi.Item]
    ) -> None:
        gh = FakeGitHub()
        run_apply(gh, items)
        decisions = [p for p in gh.posts(ISSUES_PATH) if p["title"].startswith("Decisions: ")]
        assert len(decisions) == 4
        for payload in decisions:
            match = bi.DECISIONS_TITLE_RE.match(payload["title"])
            assert match is not None
            phase = DECISIONS_EPICS[match.group(1)]
            assert payload["milestone"] == gh.created_milestones[phase]
            assert payload["labels"] == [f"phase:{phase}"]

    def test_only_the_missing_decisions_issue_is_created(
        self, items: list[bi.Item], full_repo: tuple[FakeGitHub, dict[str, int]]
    ) -> None:
        gh, numbers = full_repo
        gh.remove({numbers["Decisions E-9"]})
        plan = run_apply(gh, items)
        assert [d.parent for d in plan.decisions] == ["E-9"]
        assert plan.links == [("E-9", "Decisions E-9")]
        assert len(gh.posts(ISSUES_PATH)) == 1

    def test_body_lists_open_register_rows_naming_the_epic(
        self, by_id: dict[str, bi.Item], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "OPEN-QUESTIONS.md").write_text(
            OPEN_QUESTIONS_HEADER + "| Z9 | Which widget to use | E-8, E-9 | 2 | open |\n"
            "| Z8 | Already settled thing | E-8 | 2 | answered |\n"
            "| Z7 | Other epic question | E-10 | 4 | open |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(bi, "BACKLOG_DIR", tmp_path)
        body = bi.decisions_item(by_id["E-8"]).body
        assert "Z9" in body
        assert "Which widget to use" in body
        assert "Z8" not in body
        assert "Z7" not in body
        assert "None open" not in body
        assert "Z9" in bi.decisions_item(by_id["E-9"]).body
        assert "Z9" not in bi.decisions_item(by_id["E-10"]).body

    def test_body_says_none_open_when_no_open_questions(
        self, by_id: dict[str, bi.Item], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "OPEN-QUESTIONS.md").write_text(
            OPEN_QUESTIONS_HEADER + "| Z8 | Settled | E-8 | 2 | answered |\n", encoding="utf-8"
        )
        monkeypatch.setattr(bi, "BACKLOG_DIR", tmp_path)
        assert "None open" in bi.decisions_item(by_id["E-8"]).body

    def test_real_decisions_bodies_say_none_open(self, by_id: dict[str, bi.Item]) -> None:
        for epic_id in DECISIONS_EPICS:
            assert "None open" in bi.decisions_item(by_id[epic_id]).body


# --------------------------------------------------------------------------- CLI


class TestCli:
    def test_dry_run_makes_no_writes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gh = FakeGitHub()
        monkeypatch.setattr(bi, "GitHub", lambda _repo: gh)
        assert bi.main([]) == 0
        assert gh.writes == []
        out = capsys.readouterr().out
        assert "11 epics, 62 features, 374 stories" in out
        assert "dry run" in out

    def test_apply_writes_and_reports_what_it_created(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gh = FakeGitHub()
        monkeypatch.setattr(bi, "GitHub", lambda _repo: gh)
        assert bi.main(["--apply"]) == 0
        assert gh.writes
        assert "created #" in capsys.readouterr().out
