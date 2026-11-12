Closes #<!-- issue number -->

**Story:** S-<!-- id, for example S-1.8.4 -->

## Summary

<!-- What changed and why, in a few lines. -->

## Acceptance criteria → tests

| AC  | Tests |
| --- | ----- |
| 1.  |       |

## Verify gate

- [ ] `pnpm verify` is green locally, or the steps that couldn't run here are named below
- [ ] CI `api` and `ts` are green on the final head commit
- [ ] `code-reviewer` reviewed the diff, and its findings are fixed or filed as follow-ups
- [ ] Pre-commit hooks ran on every commit, with no `--no-verify`
- [ ] The TS client is regenerated (`pnpm client:generate`) if the API changed
- [ ] No secrets, `.env` files or coverage artifacts are committed

## Behavior changes

<!-- What users or other code will notice. Write "None" if nothing. -->

## Migration notes

<!-- New Alembic revisions, data migrations and their downgrade, new env settings or deploy steps. Write "None" if nothing. -->

## Screenshots

<!-- For UI changes: before and after, web and mobile as relevant. Write "No UI changes" otherwise. -->

## Deviations

<!-- Anything that differs from the story or the docs, and why. Write "None" if nothing. -->
