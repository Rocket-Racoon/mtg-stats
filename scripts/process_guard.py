"""Push-to-main bypass check (S-1.8.4, decision I1).

GitHub Free can't enforce branch protection on a private repository, so "merge only through a pull
request whose ``api`` and ``ts`` checks are green" is a convention. This script makes a breach of
it visible: the ``Process guard`` workflow runs it on every push to ``main``. For each commit the
push added it asks GitHub for the merged pull request the commit belongs to, then reads that pull
request's ``api`` and ``ts`` check runs on its head commit. It reports a bypass when:

- the push was a force-push;
- a commit is not part of a pull request merged into ``main``;
- a pull request was merged while ``api`` or ``ts`` was missing, still running or not green
  (a check that turned green only after the merge still counts as a bypass).

For each bypass it opens an issue titled ``Process bypass: <short SHA>`` (unless one with that
title already exists) and exits 1, so the workflow run fails.

What it can't see: whether ``code-reviewer`` reviewed the diff, and a direct push of a pull
request's own green head commit (GitHub then marks that pull request as merged itself).

Usage (from the workflow; ``gh`` reads ``GH_TOKEN``)::

    python3 scripts/process_guard.py --repo OWNER/NAME --before SHA --after SHA --forced false
"""

# ruff: noqa: T201 - a command-line script reports its result with print

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

REQUIRED_CHECKS = ("api", "ts")
BASE_BRANCH = "main"
ZERO_SHA = "0" * 40
ISSUE_TITLE_PREFIX = "Process bypass: "
# GitHub may need a moment to link a just-pushed commit to its pull request.
ASSOCIATION_ATTEMPTS = 3
ASSOCIATION_DELAY_S = 10.0


class Api(Protocol):
    """The GitHub REST calls this script needs (``gh api`` in production, a fake in tests)."""

    def __call__(self, path: str, *, method: str = "GET", payload: Any = None) -> Any: ...


@dataclass(frozen=True)
class Bypass:
    """One breach of the convention, named by the commit that landed on ``main``."""

    sha: str
    reason: str

    @property
    def title(self) -> str:
        return f"{ISSUE_TITLE_PREFIX}{self.sha[:7]}"


def gh_api(path: str, *, method: str = "GET", payload: Any = None) -> Any:
    """Calls ``gh api`` and returns the parsed JSON body (None for an empty body)."""
    cmd = ["gh", "api", "-X", method, path]
    if payload is not None:
        cmd += ["--input", "-"]
    result = subprocess.run(  # noqa: S603 - fixed executable, arguments are not shell-parsed
        cmd,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {method} {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pushed_commits(api: Api, repo: str, before: str, after: str) -> list[str]:
    """SHAs the push added to ``main``, oldest first.

    A push that created the branch (``before`` is all zeros) has nothing to compare against, so
    only its head commit is checked.
    """
    if before == ZERO_SHA:
        return [after]
    shas: list[str] = []
    page = 1
    while True:
        data = api(f"repos/{repo}/compare/{before}...{after}?per_page=100&page={page}")
        batch = [c["sha"] for c in data.get("commits", [])]
        shas.extend(batch)
        if len(batch) < 100:
            return shas
        page += 1


def merged_pull_request(
    api: Api, repo: str, sha: str, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any] | None:
    """The pull request merged into ``main`` that this commit belongs to, or None."""
    for attempt in range(ASSOCIATION_ATTEMPTS):
        pulls = api(f"repos/{repo}/commits/{sha}/pulls") or []
        merged: list[dict[str, Any]] = [
            p
            for p in pulls
            if p.get("merged_at") and (p.get("base") or {}).get("ref") == BASE_BRANCH
        ]
        if merged:
            # A commit can sit in more than one merged pull request; the first merge brought it in.
            return min(merged, key=lambda p: _parse_time(p["merged_at"]))
        if attempt < ASSOCIATION_ATTEMPTS - 1:
            sleep(ASSOCIATION_DELAY_S)
    return None


def _check_runs(api: Api, repo: str, sha: str) -> Iterator[dict[str, Any]]:
    page = 1
    while True:
        data = api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
        runs = data.get("check_runs", [])
        yield from runs
        if len(runs) < 100:
            return
        page += 1


def red_checks(api: Api, repo: str, pull: dict[str, Any]) -> list[str]:
    """Each required check that wasn't green when the pull request merged, with its state."""
    merged_at = _parse_time(pull["merged_at"])
    latest: dict[str, dict[str, Any]] = {}
    for run in _check_runs(api, repo, pull["head"]["sha"]):
        name = run.get("name")
        if name in REQUIRED_CHECKS and (name not in latest or run["id"] > latest[name]["id"]):
            latest[name] = run
    problems: list[str] = []
    for name in REQUIRED_CHECKS:
        check = latest.get(name)
        if check is None:
            problems.append(f"`{name}` never ran")
        elif check.get("status") != "completed" or not check.get("completed_at"):
            problems.append(f"`{name}` was still {check.get('status') or 'pending'}")
        elif check.get("conclusion") != "success":
            problems.append(f"`{name}` was {check.get('conclusion') or 'not green'}")
        elif _parse_time(check["completed_at"]) > merged_at:
            problems.append(f"`{name}` turned green only after the merge")
    return problems


def find_bypasses(
    api: Api,
    repo: str,
    before: str,
    after: str,
    *,
    forced: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Bypass]:
    """Every breach of the merge convention in one push to ``main``."""
    if forced:
        return [Bypass(after, f"`{BASE_BRANCH}` was force-pushed (previous head `{before}`).")]
    bypasses: list[Bypass] = []
    checked: set[int] = set()
    for sha in pushed_commits(api, repo, before, after):
        pull = merged_pull_request(api, repo, sha, sleep)
        if pull is None:
            bypasses.append(
                Bypass(sha, f"Commit is not part of a pull request merged into `{BASE_BRANCH}`.")
            )
            continue
        if pull["number"] in checked:
            continue
        checked.add(pull["number"])
        problems = red_checks(api, repo, pull)
        if problems:
            landed = pull.get("merge_commit_sha") or sha
            bypasses.append(
                Bypass(
                    landed,
                    f"Pull request #{pull['number']} was merged while a required check was not "
                    f"green: {'; '.join(problems)}.",
                )
            )
    return bypasses


def issue_body(repo: str, bypass: Bypass, run_url: str) -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    lines = [
        f"Commit [`{bypass.sha}`]({server}/{repo}/commit/{bypass.sha}) landed on "
        f"`{BASE_BRANCH}` outside the merge convention (decision I1).",
        "",
        f"**Why:** {bypass.reason}",
        "",
        "The convention (CONTRIBUTING.md): every change goes through a pull request, and it "
        "merges only when `api` and `ts` are green and `code-reviewer` has reviewed the diff.",
        "",
        "Review the commit, then fix it forward through a pull request if anything is wrong. "
        "Close this issue with a note on what was done.",
    ]
    if run_url:
        lines += ["", f"Found by [this workflow run]({run_url})."]
    return "\n".join(lines) + "\n"


def issue_exists(api: Api, repo: str, title: str) -> bool:
    """True when an issue (open or closed) already has exactly this title."""
    query = f'repo:{repo} is:issue in:title "{title}"'
    data = api(f"search/issues?q={quote(query, safe='')}&per_page=100")
    return any(item.get("title") == title for item in data.get("items", []))


def open_issues(api: Api, repo: str, bypasses: list[Bypass], run_url: str) -> list[str]:
    """Opens one issue per bypass, skipping titles that already have one. Returns new titles."""
    opened: list[str] = []
    for bypass in bypasses:
        if bypass.title in opened or issue_exists(api, repo, bypass.title):
            continue
        api(
            f"repos/{repo}/issues",
            method="POST",
            payload={"title": bypass.title, "body": issue_body(repo, bypass, run_url)},
        )
        opened.append(bypass.title)
    return opened


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main(argv: list[str] | None = None, api: Api = gh_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--repo", required=True, help="OWNER/NAME")
    parser.add_argument("--before", required=True, help="main's head before the push")
    parser.add_argument("--after", required=True, help="main's head after the push")
    parser.add_argument("--forced", default="false", help="'true' for a force-push")
    args = parser.parse_args(argv)

    bypasses = find_bypasses(
        api, args.repo, args.before, args.after, forced=args.forced.strip().lower() == "true"
    )
    if not bypasses:
        message = (
            "### Process guard: passed\n\n"
            "Every commit came from a merged pull request whose required checks were green."
        )
        print(message)
        _summary(message)
        return 0

    opened = open_issues(api, args.repo, bypasses, _run_url())
    lines = ["### Process guard: BYPASS", ""]
    for bypass in bypasses:
        lines.append(f"- `{bypass.sha[:7]}`: {bypass.reason}")
    opened_text = ", ".join(opened) if opened else "none (each already has an issue)"
    lines += ["", f"Issues opened: {opened_text}"]
    message = "\n".join(lines)
    print(message, file=sys.stderr)
    _summary(message)
    return 1


if __name__ == "__main__":
    sys.exit(main())
