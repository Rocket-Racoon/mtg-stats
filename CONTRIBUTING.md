# Contributing

Setup and the git hooks are in the [README](README.md), and the verify gate is in [docs/getting-started.md](docs/getting-started.md#7-run-the-verify-gate). This file covers how a change gets onto `main`.

## The merge convention

**GitHub does not enforce any of this.** The repository is private on GitHub Free, which has no branch protection or required checks for private repositories (decision I1 in [the decisions log](docs/backlog/OPEN-QUESTIONS.md)). These rules hold because everyone, people and agents, follows them. The [process guard](#the-process-guard) only makes a breach visible after the fact.

1. **All work goes through a pull request.** Branch from `main` as `s-<id>-<slug>` (for example `s-6.4.2-seat-drag`), one story per pull request.
2. **Merge only when all of these hold:**
   - the `api` and `ts` checks are green on the pull request's final head commit;
   - `code-reviewer` has reviewed the diff, and its findings are fixed or filed as follow-up issues.
3. **Never push directly to `main`, never force-push `main`, and never delete `main`.**
4. **Never bypass the pre-commit hooks** with `--no-verify`.

## Required checks

CI (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`. Its two jobs are the required checks, and both report their status on the pull request and on the commit:

| Check | What it runs                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------- |
| `api` | The API half of the verify gate (steps 1-6) against Postgres and Redis, then an Alembic round-trip |
| `ts`  | The TypeScript half of the verify gate (steps 7-12), then the web production build                 |

A red or still-running check means don't merge. Fix it on the branch and push again.

## Pull requests

The [pull request template](.github/pull_request_template.md) asks for:

- the story ID and `Closes #<issue>`;
- how each acceptance criterion is tested;
- the verify-gate checklist, including "pre-commit hooks ran, no `--no-verify`";
- behavior changes, migration notes, screenshots for UI changes, and deviations from the story.

Merge with a merge commit (`gh pr merge --merge --match-head-commit <sha>`), so the merge lands only if nobody pushed after the checks ran.

## The process guard

`.github/workflows/process-guard.yml` runs on every push to `main`. It fails, and opens an issue titled `Process bypass: <short SHA>` naming the commit, when:

- a commit on `main` isn't part of a pull request merged into `main`;
- a pull request was merged while `api` or `ts` was red, still running or missing (a check that turned green only after the merge counts too);
- `main` was force-pushed.

All the commits of one push that came from no merged pull request share one issue, named by the newest of them. A commit of a stacked pull request counts as merged when the pull request merged into `main` contains it. If GitHub can't be asked (an API error or a rate limit), the run fails and says the push couldn't be verified; re-run it.

It can't tell whether `code-reviewer` reviewed the diff. It also can't catch a push that changes or removes the guard itself (its workflow or `scripts/process_guard.py`), because that push's own copy is what runs. If it opens an issue, review the commit, fix anything wrong forward through a pull request, then close the issue with a note on what was done.
