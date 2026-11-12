"""S-1.8.4 AC3: the push-to-main bypass check, driven through a fake GitHub REST API."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import process_guard
import pytest
from process_guard import find_bypasses, main, open_issues

REPO = "owner/mtg-stats"
BEFORE = "b" * 40
AFTER = "a" * 40
ZERO = "0" * 40
MERGED = "2026-11-10T12:00:00Z"
BEFORE_MERGE = "2026-11-10T11:00:00Z"
AFTER_MERGE = "2026-11-10T12:30:00Z"


def sha(n: int) -> str:
    return f"{n:x}".ljust(40, "c")


def run(
    name: str,
    *,
    id: int = 1,
    status: str = "completed",
    conclusion: str | None = "success",
    completed_at: str | None = BEFORE_MERGE,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "completed_at": completed_at,
    }


def green() -> list[dict[str, Any]]:
    return [run("api", id=1), run("ts", id=2)]


def pull(
    number: int,
    *,
    head: str | None = None,
    merge: str | None = None,
    merged_at: str | None = MERGED,
    base: str = "main",
) -> dict[str, Any]:
    return {
        "number": number,
        "merged_at": merged_at,
        "base": {"ref": base},
        "head": {"sha": head or sha(1000 + number)},
        "merge_commit_sha": merge,
    }


class FakeGitHub:
    """Serves canned GitHub REST responses and records every call."""

    def __init__(self) -> None:
        self.commits: list[str] = []
        self.pulls: dict[str, list[dict[str, Any]]] = {}
        self.pulls_after_attempt: dict[str, int] = {}
        self.checks: dict[str, list[dict[str, Any]]] = {}
        self.existing_titles: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []
        self.pull_lookups: dict[str, int] = {}

    def __call__(self, path: str, *, method: str = "GET", payload: Any = None) -> Any:
        self.calls.append((method, path))
        base = path.split("?", maxsplit=1)[0]
        if method == "POST":
            assert base == f"repos/{REPO}/issues"
            self.created.append(payload)
            return {"number": len(self.created)}
        if base.startswith(f"repos/{REPO}/compare/"):
            return {"commits": [{"sha": s} for s in self.commits]}
        if base.endswith("/pulls"):
            s = base.split("/")[-2]
            self.pull_lookups[s] = self.pull_lookups.get(s, 0) + 1
            if self.pull_lookups[s] <= self.pulls_after_attempt.get(s, 0):
                return []
            return self.pulls.get(s, [])
        if base.endswith("/check-runs"):
            s = base.split("/")[-2]
            return {"check_runs": self.checks.get(s, [])}
        if base == "search/issues":
            query = unquote(path.split("q=")[1].split("&", maxsplit=1)[0])
            return {"items": [{"title": t} for t in self.existing_titles if f'"{t}"' in query]}
        raise AssertionError(f"unexpected call {method} {path}")

    def merged_pr(self, commit: str, number: int, checks: list[dict[str, Any]], **kw: Any) -> None:
        p = pull(number, **kw)
        self.pulls.setdefault(commit, []).append(p)
        self.checks[p["head"]["sha"]] = checks


def nosleep(_: float) -> None:
    return None


def check(gh: FakeGitHub, *, forced: bool = False, before: str = BEFORE) -> list[Any]:
    return find_bypasses(gh, REPO, before, AFTER, forced=forced, sleep=nosleep)


def test_green_merged_pull_request_is_not_a_bypass() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, green())
    assert check(gh) == []


def test_direct_push_with_no_associated_pull_request_is_a_bypass() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    bypasses = check(gh)
    assert [b.sha for b in bypasses] == [sha(1)]
    assert bypasses[0].title == f"Process bypass: {sha(1)[:7]}"


def test_unmerged_pull_request_does_not_make_a_commit_legitimate() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.pulls[sha(1)] = [pull(7, merged_at=None)]
    assert [b.sha for b in check(gh)] == [sha(1)]


def test_pull_request_merged_into_another_branch_does_not_count() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, green(), base="develop")
    assert [b.sha for b in check(gh)] == [sha(1)]


def test_association_is_retried_until_github_links_the_pull_request() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, green())
    gh.pulls_after_attempt[sha(1)] = 1
    assert check(gh) == []
    assert gh.pull_lookups[sha(1)] == 2


def test_association_that_never_appears_is_a_bypass_after_retries() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    assert len(check(gh)) == 1
    assert gh.pull_lookups[sha(1)] > 1


@pytest.mark.parametrize("name", ["api", "ts"])
def test_failed_required_check_is_a_bypass(name: str) -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    checks = [r if r["name"] != name else run(name, id=9, conclusion="failure") for r in green()]
    gh.merged_pr(sha(1), 7, checks, merge=sha(1))
    bypasses = check(gh)
    assert [b.sha for b in bypasses] == [sha(1)]
    assert name in bypasses[0].reason


@pytest.mark.parametrize("name", ["api", "ts"])
def test_in_progress_required_check_is_a_bypass(name: str) -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    pending = run(name, status="in_progress", conclusion=None, completed_at=None)
    checks = [r if r["name"] != name else pending for r in green()]
    gh.merged_pr(sha(1), 7, checks, merge=sha(1))
    assert len(check(gh)) == 1


@pytest.mark.parametrize("name", ["api", "ts"])
def test_missing_required_check_is_a_bypass(name: str) -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, [r for r in green() if r["name"] != name], merge=sha(1))
    assert len(check(gh)) == 1


def test_pull_request_with_no_check_runs_at_all_is_a_bypass() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, [], merge=sha(1))
    assert len(check(gh)) == 1


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "skipped", "neutral"])
def test_non_success_conclusion_is_a_bypass(conclusion: str) -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    checks = [run("api", id=1, conclusion=conclusion), run("ts", id=2)]
    gh.merged_pr(sha(1), 7, checks, merge=sha(1))
    assert len(check(gh)) == 1


def test_check_that_went_green_only_after_the_merge_is_a_bypass() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    checks = [run("api", id=1, completed_at=AFTER_MERGE), run("ts", id=2)]
    gh.merged_pr(sha(1), 7, checks, merge=sha(1))
    assert len(check(gh)) == 1


def test_rerun_that_passed_supersedes_an_earlier_failure() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    checks = [run("api", id=1, conclusion="failure"), run("api", id=5), run("ts", id=2)]
    gh.merged_pr(sha(1), 7, checks)
    assert check(gh) == []


def test_latest_run_wins_when_a_later_rerun_failed() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    checks = [run("api", id=1), run("api", id=5, conclusion="failure"), run("ts", id=2)]
    gh.merged_pr(sha(1), 7, checks, merge=sha(1))
    assert len(check(gh)) == 1


def test_other_check_runs_are_ignored() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, [*green(), run("lint", id=3, conclusion="failure")])
    assert check(gh) == []


def test_pull_request_with_many_commits_is_checked_once() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1), sha(2), sha(3)]
    p = pull(7, merge=sha(3))
    for s in gh.commits:
        gh.pulls[s] = [p]
    gh.checks[p["head"]["sha"]] = [run("api", id=1, conclusion="failure"), run("ts", id=2)]
    bypasses = check(gh)
    assert len(bypasses) == 1
    assert bypasses[0].sha == sha(3)
    head = p["head"]["sha"]
    assert sum(1 for _, path in gh.calls if f"commits/{head}/check-runs" in path) == 1


def test_one_bad_commit_among_good_ones_is_reported_alone() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1), sha(2)]
    gh.merged_pr(sha(1), 7, green())
    assert [b.sha for b in check(gh)] == [sha(2)]


def test_force_push_is_a_bypass_naming_the_new_head() -> None:
    gh = FakeGitHub()
    bypasses = check(gh, forced=True)
    assert [b.sha for b in bypasses] == [AFTER]
    assert bypasses[0].title == f"Process bypass: {AFTER[:7]}"


def test_branch_creation_push_checks_only_the_head_commit() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1), sha(2)]
    bypasses = check(gh, before=ZERO)
    assert [b.sha for b in bypasses] == [AFTER]
    assert not any("/compare/" in path for _, path in gh.calls)


def test_branch_creation_push_of_a_green_merged_head_is_clean() -> None:
    gh = FakeGitHub()
    gh.merged_pr(AFTER, 7, green())
    assert check(gh, before=ZERO) == []


def test_issue_has_exact_short_sha_title_and_full_sha_in_body() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    opened = open_issues(gh, REPO, check(gh), "https://example.test/run/1")
    assert opened == [f"Process bypass: {sha(1)[:7]}"]
    assert len(gh.created) == 1
    assert gh.created[0]["title"] == f"Process bypass: {sha(1)[:7]}"
    assert sha(1) in gh.created[0]["body"]
    assert "https://example.test/run/1" in gh.created[0]["body"]


def test_no_duplicate_issue_when_one_with_the_title_exists() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.existing_titles = [f"Process bypass: {sha(1)[:7]}"]
    assert open_issues(gh, REPO, check(gh), "") == []
    assert gh.created == []


def test_an_issue_with_a_different_title_does_not_suppress_a_new_one() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.existing_titles = ["Process bypass: 1234567"]
    assert len(open_issues(gh, REPO, check(gh), "")) == 1


def test_one_issue_per_bypassing_commit() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1), sha(2)]
    open_issues(gh, REPO, check(gh), "")
    assert [c["title"] for c in gh.created] == [
        f"Process bypass: {sha(1)[:7]}",
        f"Process bypass: {sha(2)[:7]}",
    ]


def args(**kw: str) -> list[str]:
    values = {"repo": REPO, "before": BEFORE, "after": AFTER, "forced": "false", **kw}
    return [x for k, v in values.items() for x in (f"--{k}", v)]


def test_main_returns_0_and_opens_no_issue_for_a_clean_push() -> None:
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.merged_pr(sha(1), 7, green())
    assert main(args(), gh) == 0
    assert gh.created == []


def test_main_returns_1_and_opens_the_issue_on_a_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_guard, "ASSOCIATION_DELAY_S", 0.0)
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    assert main(args(), gh) == 1
    assert [c["title"] for c in gh.created] == [f"Process bypass: {sha(1)[:7]}"]


def test_main_still_returns_1_when_the_issue_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_guard, "ASSOCIATION_DELAY_S", 0.0)
    gh = FakeGitHub()
    gh.commits = [sha(1)]
    gh.existing_titles = [f"Process bypass: {sha(1)[:7]}"]
    assert main(args(), gh) == 1
    assert gh.created == []


def test_main_treats_forced_true_as_a_bypass() -> None:
    gh = FakeGitHub()
    assert main(args(forced="true"), gh) == 1
    assert [c["title"] for c in gh.created] == [f"Process bypass: {AFTER[:7]}"]


def test_main_requires_its_arguments() -> None:
    with pytest.raises(SystemExit) as exc:
        main([], FakeGitHub())
    assert exc.value.code == 2
