"""One-time, resumable import of the markdown backlog into GitHub Issues (S-1.2.4).

Parses ``docs/backlog/E*.md`` into epics, features and stories and makes sure that each one
exists as a GitHub issue titled ``[<ID>] <title>``, with its labels and milestone, linked as a
sub-issue (stories under their feature, features under their epic). It also creates one
"Decisions" issue for each epic whose milestone is Phase 2 to 5.

The script is safe to run again after an interruption:

* issues are matched by the ID in their title, and only missing issues are created;
* an existing issue is never edited, relabelled, reopened or closed;
* missing labels, milestones and sub-issue links are added, and existing ones are left alone.

It runs as a dry run by default and only writes with ``--apply``. Standard library only; all
GitHub calls go through ``gh api``, so ``gh`` must be authenticated with access to the repo.

Usage::

    python scripts/backlog_import.py                 # dry run: report what is missing
    python scripts/backlog_import.py --apply         # create what is missing
"""

# ruff: noqa: T201 - a command-line script reports progress with print

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKLOG_DIR = REPO_ROOT / "docs" / "backlog"
SNAPSHOT_DATE = "2026-11-07"
DECISIONS_PHASES = range(2, 6)

EPIC_RE = re.compile(r"^# (E-\d+) — (.+)$")
FEATURE_RE = re.compile(r"^## (F-\d+\.\d+) — (.+)$")
STORY_RE = re.compile(r"^#### (S-\d+\.\d+\.\d+) — (.+)$")
OPEN_QUESTIONS_RE = re.compile(r"^## Open questions\s*$")
# Matches both "[S-1.1.1] Title" (the form used at import) and "S-1.1.1 — Title".
TITLE_ID_RE = re.compile(r"^\[?([EFS]-\d+(?:\.\d+)*)\]?(?:\s|$)")
DECISIONS_TITLE_RE = re.compile(r"^Decisions: (E-\d+) ")
FIELD_ROW_RE = re.compile(
    r"^\|\s*(Must|Should|Could|Won't)\s*\|\s*(\d+)\s*\|([^|]*)\|\s*(\d)\s*\|([^|]*)\|\s*$"
)
EPIC_PHASE_RE = re.compile(r"^\|\s*(?:\*\*)?Phase\(s\)(?:\*\*)?\s*\|\s*(\d)")
# The feature's phase field line, in any of the backlog's spellings: "**Phase** 0 · …",
# "**Phase:** 1 · …" or "Phase 1 · …". Prose that merely mentions a phase must not match.
FEATURE_PHASE_RE = re.compile(r"^\*{0,2}Phase\*{0,2}:?\*{0,2}\s*(\d)")
MILESTONE_RE = re.compile(r"^Phase (\d)\b")

LABEL_COLORS = {
    "type": "5319e7",
    "phase": "1d76db",
    "platform": "0e8a16",
    "priority": "d93f0b",
    "points": "fbca04",
}


@dataclass
class Item:
    """One backlog item (epic, feature or story) parsed from the markdown."""

    id: str
    kind: str  # "epic" | "feature" | "story"
    title: str
    body: str
    source: str
    phase: int
    labels: list[str]
    parent: str | None = None
    wont: bool = False
    open_questions: str = ""

    @property
    def issue_title(self) -> str:
        return f"[{self.id}] {self.title}"


@dataclass
class Plan:
    """Everything the import found missing, reported in dry runs and applied with --apply."""

    labels: list[str] = field(default_factory=list)
    milestones: list[int] = field(default_factory=list)
    issues: list[Item] = field(default_factory=list)
    decisions: list[Item] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- parsing


def footer(source: str) -> str:
    return (
        f"\n\n---\nImported from `docs/backlog/{source}` (snapshot {SNAPSHOT_DATE}). "
        "After import, GitHub Issues is the source of truth."
    )


def clean(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    while text.endswith("---"):
        text = text[:-3].rstrip()
    return text


def story_labels(row: re.Match[str], phase: int) -> list[str]:
    priority, points, platforms = row.group(1), row.group(2), row.group(3)
    labels = ["type:story", f"phase:{phase}"]
    labels += [f"platform:{p.strip()}" for p in platforms.split(",") if p.strip()]
    labels += [f"priority:{priority.lower().replace(chr(39), '')}", f"points:{points}"]
    return labels


def parse_epic_file(path: Path) -> list[Item]:
    """Split one epic file into its epic, feature and story items."""
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[Item] = []
    sections: list[tuple[str, re.Match[str] | None, list[str]]] = []
    for line in lines:
        for kind, regex in (
            ("epic", EPIC_RE),
            ("feature", FEATURE_RE),
            ("story", STORY_RE),
            ("questions", OPEN_QUESTIONS_RE),
        ):
            match = regex.match(line)
            if match:
                sections.append((kind, match, []))
                break
        else:
            if sections:
                sections[-1][2].append(line)

    epic: Item | None = None
    feature: Item | None = None
    for kind, match, body_lines in sections:
        body = clean(body_lines)
        if kind == "questions":
            if epic is None:
                raise ValueError(f"{path.name}: 'Open questions' before the epic heading")
            epic.open_questions = body
            epic.body += f"\n\n## Open questions\n\n{body}"
            continue
        if match is None:
            raise AssertionError("heading match missing")
        item_id, title = match.group(1), match.group(2).strip()
        if kind == "epic":
            phase = next(
                (int(m.group(1)) for ln in body_lines if (m := EPIC_PHASE_RE.match(ln))), None
            )
            if phase is None:
                raise ValueError(f"{path.name}: {item_id} has no Phase(s) row")
            epic = Item(
                item_id, kind, title, body, path.name, phase, ["type:epic", f"phase:{phase}"]
            )
            items.append(epic)
        elif kind == "feature":
            phase_match = next(
                (m for ln in body_lines if (m := FEATURE_PHASE_RE.match(ln.strip()))), None
            )
            if epic is None or phase_match is None:
                raise ValueError(f"{path.name}: {item_id} has no epic or no phase line")
            phase = int(phase_match.group(1))
            feature = Item(
                item_id,
                kind,
                title,
                body,
                path.name,
                phase,
                ["type:feature", f"phase:{phase}"],
                parent=epic.id,
            )
            items.append(feature)
        else:
            row = next((m for ln in body_lines if (m := FIELD_ROW_RE.match(ln))), None)
            if feature is None or row is None:
                raise ValueError(f"{path.name}: {item_id} has no feature or no field row")
            phase = int(row.group(4))
            items.append(
                Item(
                    item_id,
                    kind,
                    title,
                    body,
                    path.name,
                    phase,
                    story_labels(row, phase),
                    parent=feature.id,
                    wont=row.group(1) == "Won't",
                )
            )
    return items


def parse_backlog() -> list[Item]:
    items: list[Item] = []
    for path in sorted(BACKLOG_DIR.glob("E*.md")):
        items += parse_epic_file(path)
    ids = [item.id for item in items]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate IDs in the backlog: {sorted(duplicates)}")
    return items


def open_register_questions(epic_id: str) -> list[str]:
    """Rows of OPEN-QUESTIONS.md raised in this epic whose status is still ``open``."""
    rows = []
    for line in (BACKLOG_DIR / "OPEN-QUESTIONS.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[4] != "open":
            continue
        raised_in = {r.strip() for r in cells[2].split(",")}
        if epic_id in raised_in:
            rows.append(f"- [ ] **{cells[0]}** {cells[1]} (blocks phase {cells[3]})")
    return rows


def decisions_item(epic: Item) -> Item:
    questions = open_register_questions(epic.id)
    epic_section = epic.open_questions
    if epic_section and not epic_section.lower().startswith("none open"):
        questions.append(epic_section)
    listing = (
        "\n".join(questions)
        if questions
        else "None open at import: every question raised for this epic is answered in the "
        "`docs/backlog/OPEN-QUESTIONS.md` decisions log."
    )
    body = (
        f"Answer these before Phase {epic.phase} starts, and record each answer in the "
        "decisions log (`docs/backlog/OPEN-QUESTIONS.md`) and, for dependencies, plan §8.\n\n"
        f"## Still-open questions\n\n{listing}\n\n"
        f"## Before Phase {epic.phase} starts\n\n"
        "- [ ] Re-read the epic's stories and confirm no new question has come up since import\n"
        "- [ ] Confirm any dependency the epic needs is approved "
        "(plan §8, ADR-0002, decisions log)\n"
        "- [ ] Close this issue once every question above is answered"
    )
    return Item(
        f"Decisions {epic.id}",
        "decisions",
        f"Decisions: {epic.id} {epic.title} (before Phase {epic.phase})",
        body,
        epic.source,
        epic.phase,
        [f"phase:{epic.phase}"],
        parent=epic.id,
    )


# --------------------------------------------------------------------------- GitHub


class GitHub:
    """Thin wrapper over ``gh api``."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def api(self, path: str, *, method: str = "GET", payload: Any = None) -> Any:
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

    def paginate(self, path: str) -> Iterator[Any]:
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self.api(f"{path}{sep}per_page=100&page={page}")
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def parent_of(self, number: int) -> int | None:
        """Number of the issue's parent, or None when it is not anyone's sub-issue."""
        try:
            parent = self.api(self.repo_path(f"issues/{number}/parent"))
        except RuntimeError as error:
            if "404" in str(error) or "Not Found" in str(error):
                return None
            raise
        return int(parent["number"])

    def repo_path(self, suffix: str) -> str:
        return f"repos/{self.repo}/{suffix}"


def index_milestones(gh: GitHub) -> dict[int, dict[str, Any]]:
    """Existing milestones keyed by phase number (titles start with "Phase <n>")."""
    milestones: dict[int, dict[str, Any]] = {}
    for milestone in gh.paginate(gh.repo_path("milestones?state=all")):
        match = MILESTONE_RE.match(milestone["title"])
        if match:
            phase = int(match.group(1))
            if phase in milestones:
                raise RuntimeError(f"two milestones claim Phase {phase}")
            milestones[phase] = milestone
    return milestones


def index_issues(gh: GitHub) -> dict[str, dict[str, Any]]:
    """Existing issues keyed by backlog ID (or "Decisions E-<n>"); duplicates abort the run."""
    issues: dict[str, dict[str, Any]] = {}
    for issue in gh.paginate(gh.repo_path("issues?state=all")):
        if "pull_request" in issue:
            continue
        if match := TITLE_ID_RE.match(issue["title"]):
            key = match.group(1)
        elif match := DECISIONS_TITLE_RE.match(issue["title"]):
            key = f"Decisions {match.group(1)}"
        else:
            continue
        if key in issues:
            raise RuntimeError(
                f"two issues claim {key}: #{issues[key]['number']} and #{issue['number']}"
            )
        issues[key] = issue
    return issues


def plan_links(
    items: list[Item], gh: GitHub, issues: dict[str, dict[str, Any]], plan: Plan
) -> None:
    """Add the missing sub-issue links to the plan. Only orphans are linked, never re-parented."""
    children: dict[str, list[str]] = {}
    for item in items:
        if item.parent:
            children.setdefault(item.parent, []).append(item.id)
    for parent_id, child_ids in children.items():
        linked: set[int] = set()
        if parent_id in issues:
            number = issues[parent_id]["number"]
            linked = {
                sub["number"] for sub in gh.paginate(gh.repo_path(f"issues/{number}/sub_issues"))
            }
        for child_id in child_ids:
            child = issues.get(child_id)
            if child is None:
                plan.links.append((parent_id, child_id))  # created by this run, so no parent yet
                continue
            if child["number"] in linked:
                continue
            current = gh.parent_of(child["number"])
            if current is None:
                plan.links.append((parent_id, child_id))
            else:
                plan.warnings.append(
                    f"{child_id} (#{child['number']}) is under #{current}, not under "
                    f"{parent_id} as in the snapshot; left alone"
                )


def build_plan(
    items: list[Item], gh: GitHub
) -> tuple[Plan, dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Compare the backlog with the repo and list what is missing."""
    plan = Plan()

    existing_labels = {label["name"] for label in gh.paginate(gh.repo_path("labels"))}
    wanted_labels = sorted({label for item in items for label in item.labels})
    plan.labels = [label for label in wanted_labels if label not in existing_labels]

    milestones = index_milestones(gh)
    plan.milestones = sorted({item.phase for item in items} - milestones.keys())

    issues = index_issues(gh)
    plan.issues = [item for item in items if item.id not in issues]
    for epic in (i for i in items if i.kind == "epic" and i.phase in DECISIONS_PHASES):
        decisions = decisions_item(epic)
        if decisions.id not in issues:
            plan.decisions.append(decisions)

    plan_links(items + plan.decisions, gh, issues, plan)

    for item_id, issue in issues.items():
        labels = {label["name"] for label in issue.get("labels", [])}
        if issue["state"] == "open" and "priority:wont" in labels:
            plan.warnings.append(
                f"{item_id} (#{issue['number']}) is labelled priority:wont but still open; "
                "close it as not planned"
            )
    return plan, issues, milestones


def apply_plan(
    plan: Plan, gh: GitHub, issues: dict[str, dict[str, Any]], milestones: dict[int, dict[str, Any]]
) -> None:
    for label in plan.labels:
        prefix = label.split(":", 1)[0]
        gh.api(
            gh.repo_path("labels"),
            method="POST",
            payload={"name": label, "color": LABEL_COLORS.get(prefix, "ededed")},
        )
        print(f"created label {label}")
    for phase in plan.milestones:
        milestones[phase] = gh.api(
            gh.repo_path("milestones"), method="POST", payload={"title": f"Phase {phase}"}
        )
        print(f"created milestone Phase {phase}")
    for item in plan.issues + plan.decisions:
        body = item.body + footer(item.source) if item.kind != "decisions" else item.body
        issue = gh.api(
            gh.repo_path("issues"),
            method="POST",
            payload={
                "title": item.issue_title if item.kind != "decisions" else item.title,
                "body": body,
                "labels": item.labels,
                "milestone": milestones[item.phase]["number"],
            },
        )
        issues[item.id] = issue
        print(f"created #{issue['number']} {issue['title']}")
        if item.wont:
            gh.api(
                gh.repo_path(f"issues/{issue['number']}"),
                method="PATCH",
                payload={"state": "closed", "state_reason": "not_planned"},
            )
            print(f"  closed #{issue['number']} as not planned (Won't)")
    for parent_id, child_id in plan.links:
        parent, child = issues[parent_id], issues[child_id]
        gh.api(
            gh.repo_path(f"issues/{parent['number']}/sub_issues"),
            method="POST",
            payload={"sub_issue_id": child["id"]},
        )
        print(f"linked #{child['number']} under #{parent['number']}")


def report(plan: Plan, items: list[Item]) -> None:
    kinds = {k: sum(1 for i in items if i.kind == k) for k in ("epic", "feature", "story")}
    print(f"backlog: {kinds['epic']} epics, {kinds['feature']} features, {kinds['story']} stories")
    print(f"missing labels: {len(plan.labels)} {plan.labels if plan.labels else ''}")
    print(
        f"missing milestones: {len(plan.milestones)} {plan.milestones if plan.milestones else ''}"
    )
    print(f"missing issues: {len(plan.issues)}")
    for item in plan.issues:
        print(f"  {item.issue_title}")
    print(f"missing Decisions issues: {len(plan.decisions)}")
    for item in plan.decisions:
        print(f"  {item.title}")
    print(f"missing sub-issue links: {len(plan.links)}")
    for parent_id, child_id in plan.links:
        print(f"  {child_id} under {parent_id}")
    print(f"warnings: {len(plan.warnings)}")
    for warning in plan.warnings:
        print(f"  {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--repo", default="Rocket-Racoon/mtg-stats", help="OWNER/NAME")
    parser.add_argument("--apply", action="store_true", help="create what is missing")
    args = parser.parse_args(argv)

    items = parse_backlog()
    gh = GitHub(args.repo)
    plan, issues, milestones = build_plan(items, gh)
    report(plan, items)
    if not args.apply:
        print("\ndry run: nothing was changed (pass --apply to create what is missing)")
        return 0
    apply_plan(plan, gh, issues, milestones)
    print("\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
