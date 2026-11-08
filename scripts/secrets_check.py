"""Secrets check (S-1.3.3): fail when a secret or a tracked env file is about to be committed.

Standard library only. Run from anywhere inside the repository.

    python scripts/secrets_check.py            # scan every tracked file (verify, CI)
    python scripts/secrets_check.py --staged   # scan the staged content (pre-commit hook)

Output is one ``path:line: <rule>`` line per finding (``path: <rule>`` for the file-level rule).
The matched value is never printed. Exit code 0 means clean, 1 means findings, 2 means the
script could not run (for example, not a git repository).

Rules
-----
``tracked-env-file``
    A tracked path whose basename is ``.env`` or starts with ``.env.``, except ``.env.example``.

``private-key``
    A line containing a private key header, ``-----BEGIN [<kind> ]PRIVATE KEY-----``
    (RSA, EC, OPENSSH, ENCRYPTED, ...).

``inline-password``
    A connection string with an inline password, ``scheme://user:<password>@host`` (the ``@`` must
    be followed by a host character, so prose such as ``user:pass@`` alone is ignored). Not a
    finding when the password is a placeholder: empty, ``CHANGE_ME``, or starting with ``$``, ``{``,
    ``<`` or ``%`` (variable interpolation or template).

``secret-assignment``
    An assignment of a literal to a name ending in ``_SECRET``, ``_KEY``, ``_TOKEN`` or
    ``_PASSWORD`` (case-insensitive), in env-like files (``.env*``, ``*.env``, ``*.ini``,
    ``*.cfg``), YAML, TOML, Python, and TypeScript/JavaScript. Recognised forms are
    ``NAME=value``, ``NAME: value``, ``NAME = "value"``, ``NAME: str = "value"``,
    ``"NAME": "value"`` and ``x["NAME"] = "value"``. A literal is:

    * env-like, YAML and INI files: any value, quoted or not (trailing ``# comment`` ignored);
    * TOML, Python, TS/JS: a quoted string only. An unquoted right-hand side is an expression
      (``settings.JWT_SECRET``, ``os.environ["X_TOKEN"]``, ``getenv(...)``), not a literal.
    * a quoted literal needs a matching closing quote: ``"X_KEY="`` (the quote closes an enclosing
      string) is not an assignment.

    Not a finding: an empty value, ``CHANGE_ME``, or a value that is a placeholder or reference
    (starts with ``$``, ``{``, ``<``, ``!``, ``*``, ``&``, ``|``, ``>`` or ``%``; or contains
    ``${``, ``{{`` or ``%(``). Comparisons (``==``) are not assignments.

Skipped: binary files (a NUL byte), and any line ending in ``# secrets-check: allow`` (``//`` is
accepted as the comment marker too).
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ALLOW_SUFFIXES = ("# secrets-check: allow", "// secrets-check: allow")

PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
INLINE_PASSWORD_RE = re.compile(r"://[^\s:/@'\"`]+:(?P<pw>[^\s@'\"`/]*)@(?=[\w.\[%-])")

_NAME = r"(?P<name>[A-Za-z_][A-Za-z0-9_]*_(?:SECRET|KEY|TOKEN|PASSWORD))"
_PREFIX = r"(?:^|[^A-Za-z0-9_])[\"'`]?" + _NAME + r"\b[\"'`]?\]?\s*"
ASSIGN_EQ_RE = re.compile(
    _PREFIX + r"(?::\s*[^=:#\"'`]+?)?=(?![=>~])\s*(?P<val>.*)$", re.IGNORECASE
)
ASSIGN_COLON_RE = re.compile(_PREFIX + r":\s*(?P<val>.*)$", re.IGNORECASE)

ENV_LIKE_SUFFIXES = (".env", ".ini", ".cfg")
YAML_SUFFIXES = (".yml", ".yaml")
QUOTED_ONLY_SUFFIXES = (".toml", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
PLACEHOLDER_STARTS = ("$", "{", "<", "!", "*", "&", "|", ">", "%")
PLACEHOLDER_PARTS = ("${", "{{", "%(")


@dataclass(frozen=True)
class Finding:
    """One rule violation. ``line`` is 0 for file-level findings."""

    path: str
    line: int
    rule: str

    def render(self) -> str:
        """Format as ``path:line: rule`` (or ``path: rule``). Never includes a value."""
        return f"{self.path}:{self.line}: {self.rule}" if self.line else f"{self.path}: {self.rule}"


def is_forbidden_env_file(path: str) -> bool:
    """Return True for a ``.env`` or ``.env.*`` path other than ``.env.example``."""
    base = posixpath.basename(path.replace("\\", "/"))
    return base != ".env.example" and (base == ".env" or base.startswith(".env."))


def _file_kind(path: str) -> str | None:
    """Classify a path as ``env``, ``yaml``, ``quoted`` (literal must be quoted) or None."""
    base = posixpath.basename(path.replace("\\", "/")).lower()
    if base == ".env" or base.startswith(".env.") or base.endswith(ENV_LIKE_SUFFIXES):
        return "env"
    if base.endswith(YAML_SUFFIXES):
        return "yaml"
    if base.endswith(QUOTED_ONLY_SUFFIXES):
        return "quoted"
    return None


def _is_placeholder(value: str) -> bool:
    return (
        value in ("", "CHANGE_ME")
        or value.startswith(PLACEHOLDER_STARTS)
        or any(part in value for part in PLACEHOLDER_PARTS)
    )


def _literal(raw: str, kind: str) -> str | None:
    """Extract the literal from a right-hand side, or None when it is not a literal."""
    raw = raw.strip()
    if raw[:1] in ("'", '"', "`"):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else None
    if kind == "quoted":
        return None
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip().rstrip(",").strip()


def _secret_assignment(line: str, kind: str) -> bool:
    for pattern in (ASSIGN_EQ_RE, ASSIGN_COLON_RE):
        match = pattern.search(line)
        if match is None:
            continue
        value = _literal(match.group("val"), kind)
        if value is not None and not _is_placeholder(value):
            return True
    return False


def scan_text(path: str, text: str) -> list[Finding]:
    """Scan one file's text and return the findings (excluding the tracked-env-file rule)."""
    findings: list[Finding] = []
    kind = _file_kind(path)
    for number, line in enumerate(text.splitlines(), start=1):
        if line.rstrip().endswith(ALLOW_SUFFIXES):
            continue
        if PRIVATE_KEY_RE.search(line):
            findings.append(Finding(path, number, "private-key"))
        if any(not _is_placeholder(m.group("pw")) for m in INLINE_PASSWORD_RE.finditer(line)):
            findings.append(Finding(path, number, "inline-password"))
        if kind is not None and _secret_assignment(line, kind):
            findings.append(Finding(path, number, "secret-assignment"))
    return findings


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _paths(repo: Path, *, staged: bool) -> list[str]:
    if staged:
        out = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        out = _git(repo, "ls-files", "-z")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _content(repo: Path, path: str, *, staged: bool) -> bytes | None:
    if staged:
        return _git(repo, "show", f":{path}")
    file = repo / path
    return file.read_bytes() if file.is_file() else None


def check_repo(repo: Path, *, staged: bool = False) -> list[Finding]:
    """Run every rule over tracked (or staged) files of ``repo``."""
    findings: list[Finding] = []
    for path in _paths(repo, staged=staged):
        if is_forbidden_env_file(path):
            findings.append(Finding(path, 0, "tracked-env-file"))
        data = _content(repo, path, staged=staged)
        if data is None or b"\0" in data:
            continue
        findings.extend(scan_text(path, data.decode("utf-8", "replace")))
    return findings


def main(argv: Sequence[str] | None = None, repo: Path | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Fail on committed secrets and env files.")
    parser.add_argument("--staged", action="store_true", help="scan staged content only")
    args = parser.parse_args(argv)
    try:
        root = repo or Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        findings = check_repo(root, staged=args.staged)
    except (subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"secrets-check: cannot run git: {exc.__class__.__name__}\n")
        return 2
    for finding in findings:
        sys.stdout.write(finding.render() + "\n")
    if findings:
        sys.stderr.write(
            f"secrets-check: {len(findings)} finding(s). "
            "Remove the secret, or mark a false positive with '# secrets-check: allow'.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
