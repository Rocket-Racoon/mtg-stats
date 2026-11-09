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
    (RSA, EC, OPENSSH, ENCRYPTED, ...). Checked in every text file.

``inline-password``
    A connection string with an inline password, ``scheme://[user]:<password>@host``. The user
    part may be empty (``redis://:<pw>@host``), and the ``@`` must be followed by a host character,
    so prose such as ``user:pass@`` alone is ignored. Every match on a line is checked. Not a
    finding when the password is a placeholder: empty, ``CHANGE_ME``, or starting with ``$``,
    ``{``, ``<`` or ``%`` (variable interpolation or template). Checked in every text file.

``secret-assignment``
    An assignment of a literal to a name ending in ``_SECRET``, ``_KEY``, ``_TOKEN`` or
    ``_PASSWORD``. The suffix match is case-insensitive (a lowercase ``secret_key`` counts too), so
    fixtures should use ``CHANGE_ME`` or the allow marker. How a line is read depends on the file:

    * env-like files (``.env*``, ``*.env``, ``*.ini``, ``*.cfg``) and YAML: ``NAME=value`` or
      ``NAME: value`` anywhere on the line; the value may be quoted or bare (a ``# comment`` after
      it is ignored);
    * TOML, JSON, Python and TS/JS: ``NAME = "value"``, ``NAME: str = "value"``,
      ``"NAME": "value"``, ``x["NAME"] = "value"``; the value must be a quoted string (Python
      ``f``/``b``/``r``/``u`` prefixes are accepted). An unquoted right-hand side is an expression
      (``settings.JWT_SECRET``, ``os.environ["X_TOKEN"]``), not a literal. A quote needs a
      matching closing quote, so ``"X_KEY="`` (the quote closes an enclosing string) is not an
      assignment;
    * every other text file (``.sh``, ``Dockerfile``, ``.md``, ``.txt``, ``.ps1``, ``.npmrc``,
      extensionless files, ...): a line-anchored env-style form only,
      ``^\\s*(export |ENV |$env:)?NAME\\s*[=:]\\s*value``, with bare or quoted values.

    Not a finding: an empty value, ``CHANGE_ME``, or a placeholder or reference (starts with
    ``$``, ``{``, ``<``, ``!``, ``*``, ``&``, ``|``, ``>`` or ``%``; or contains ``${``, ``{{`` or
    ``%(``). ``==`` is a comparison, not an assignment. Every match on a line is checked.

Skipped: binary files (a NUL byte; UTF-16 files with a BOM are decoded first), gitlinks
(submodules), tracked symlinks in the default mode, and any line ending in
``# secrets-check: allow`` (``//`` is accepted as the comment marker too).

Staged mode reads the staged blobs through one ``git cat-file --batch`` process.
"""

from __future__ import annotations

import argparse
import io
import posixpath
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ALLOW_SUFFIXES = ("# secrets-check: allow", "// secrets-check: allow")

PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
INLINE_PASSWORD_RE = re.compile(r"://[^\s:/@'\"`]*:(?P<pw>[^\s@'\"`/]*)@(?=[\w.\[%-])")

_NAME = r"(?P<name>[A-Za-z_][A-Za-z0-9_]*_(?:SECRET|KEY|TOKEN|PASSWORD))"
_VALUE = r"(?P<val>(?:[fFbBrRuU]{1,2})?(?:\"[^\"]*\"?|'[^']*'?|`[^`]*`?)|[^\s,;]*)"
_PREFIX = r"(?:^|[^A-Za-z0-9_])[\"'`]?" + _NAME + r"\b[\"'`]?\]?\s*"
ASSIGN_EQ_RE = re.compile(_PREFIX + r"(?::\s*[^=:#\"'`]+?)?=(?![=>~])\s*" + _VALUE, re.IGNORECASE)
ASSIGN_COLON_RE = re.compile(_PREFIX + r":\s*" + _VALUE, re.IGNORECASE)
FALLBACK_RE = re.compile(
    r"^\s*(?:export\s+|ENV\s+|\$env:)?" + _NAME + r"\s*(?:=(?!=)|:)\s*" + _VALUE,
    re.IGNORECASE,
)

ENV_LIKE_SUFFIXES = (".env", ".ini", ".cfg")
YAML_SUFFIXES = (".yml", ".yaml")
QUOTED_ONLY_SUFFIXES = (".toml", ".json", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
PLACEHOLDER_STARTS = ("$", "{", "<", "!", "*", "&", "|", ">", "%")
PLACEHOLDER_PARTS = ("${", "{{", "%(")
STRING_PREFIX_RE = re.compile(r"^[fFbBrRuU]{1,2}(?=[\"'`])")
GITLINK_MODE = "160000"
SYMLINK_MODE = "120000"


@dataclass(frozen=True)
class Finding:
    """One rule violation. ``line`` is 0 for file-level findings."""

    path: str
    line: int
    rule: str

    def render(self) -> str:
        """Format as ``path:line: rule`` (or ``path: rule``). Never includes a value."""
        return f"{self.path}:{self.line}: {self.rule}" if self.line else f"{self.path}: {self.rule}"


@dataclass(frozen=True)
class Entry:
    """A file to scan: its path, git mode and blob id."""

    path: str
    mode: str
    sha: str


def is_forbidden_env_file(path: str) -> bool:
    """Return True for a ``.env`` or ``.env.*`` path other than ``.env.example``."""
    base = posixpath.basename(path.replace("\\", "/"))
    return base != ".env.example" and (base == ".env" or base.startswith(".env."))


def _file_kind(path: str) -> str:
    """Classify a path: ``env``, ``yaml``, ``quoted`` (literal must be quoted) or ``fallback``."""
    base = posixpath.basename(path.replace("\\", "/")).lower()
    if base == ".env" or base.startswith(".env.") or base.endswith(ENV_LIKE_SUFFIXES):
        return "env"
    if base.endswith(YAML_SUFFIXES):
        return "yaml"
    if base.endswith(QUOTED_ONLY_SUFFIXES):
        return "quoted"
    return "fallback"


def _is_placeholder(value: str) -> bool:
    return (
        value in ("", "CHANGE_ME")
        or value.startswith(PLACEHOLDER_STARTS)
        or any(part in value for part in PLACEHOLDER_PARTS)
    )


def _literal(raw: str, kind: str) -> str | None:
    """Extract the literal from a value token, or None when it is not a literal."""
    raw = STRING_PREFIX_RE.sub("", raw.strip())
    if raw[:1] in ("'", '"', "`"):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else None
    if kind == "quoted":
        return None
    return "" if raw.startswith("#") else raw.rstrip(",")


def _secret_assignment(line: str, kind: str) -> bool:
    patterns = (FALLBACK_RE,) if kind == "fallback" else (ASSIGN_EQ_RE, ASSIGN_COLON_RE)
    value_kind = "env" if kind == "fallback" else kind
    for pattern in patterns:
        for match in pattern.finditer(line):
            value = _literal(match.group("val"), value_kind)
            if value is not None and not _is_placeholder(value):
                return True
    return False


def decode_text(data: bytes) -> str | None:
    """Decode file bytes to text, or return None for a binary file."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    if b"\0" in data:
        return None
    return data.decode("utf-8-sig", "replace")


def scan_text(path: str, text: str) -> list[Finding]:
    """Scan one file's text and return the findings (excluding the tracked-env-file rule)."""
    findings: list[Finding] = []
    kind = _file_kind(path)
    for number, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.rstrip("\r")
        if line.rstrip().endswith(ALLOW_SUFFIXES):
            continue
        if PRIVATE_KEY_RE.search(line):
            findings.append(Finding(path, number, "private-key"))
        if any(not _is_placeholder(m.group("pw")) for m in INLINE_PASSWORD_RE.finditer(line)):
            findings.append(Finding(path, number, "inline-password"))
        if _secret_assignment(line, kind):
            findings.append(Finding(path, number, "secret-assignment"))
    return findings


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        input=stdin,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _entries(repo: Path, *, staged: bool) -> list[Entry]:
    """List the files to scan. Staged mode uses ACMRT without rename detection."""
    entries: dict[str, Entry] = {}
    if staged:
        out = _git(
            repo,
            *("diff", "--cached", "--raw", "--no-abbrev", "--no-renames"),
            *("--diff-filter=ACMRT", "-z"),
        )
        parts = out.decode("utf-8", "replace").split("\0")
        for meta, path in zip(parts[0::2], parts[1::2], strict=False):
            fields = meta.lstrip(":").split()
            entries[path] = Entry(path, fields[1], fields[3])
    else:
        out = _git(repo, "ls-files", "-s", "-z")
        for record in out.decode("utf-8", "replace").split("\0"):
            if not record:
                continue
            meta, _, path = record.partition("\t")
            mode, sha, _stage = meta.split()
            entries[path] = Entry(path, mode, sha)
    return list(entries.values())


def _staged_blobs(repo: Path, entries: list[Entry]) -> dict[str, bytes]:
    """Read the blobs of ``entries`` with a single ``git cat-file --batch`` process."""
    wanted = [e for e in entries if e.mode != GITLINK_MODE]
    request = "".join(f"{e.sha}\n" for e in wanted).encode()
    out = _git(repo, "cat-file", "--batch", stdin=request)
    blobs: dict[str, bytes] = {}
    pos = 0
    for entry in wanted:
        end = out.index(b"\n", pos)
        header = out[pos:end].split()
        pos = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            blobs[entry.path] = out[pos : pos + size]
            pos += size + 1
    return blobs


def check_repo(repo: Path, *, staged: bool = False) -> list[Finding]:
    """Run every rule over tracked (or staged) files of ``repo``."""
    findings: list[Finding] = []
    entries = _entries(repo, staged=staged)
    blobs = _staged_blobs(repo, entries) if staged else {}
    for entry in entries:
        path = entry.path
        if is_forbidden_env_file(path):
            findings.append(Finding(path, 0, "tracked-env-file"))
        if entry.mode == GITLINK_MODE:
            continue
        data: bytes | None
        if staged:
            data = blobs.get(path)
        else:
            file = repo / path
            skip = entry.mode == SYMLINK_MODE or file.is_symlink() or not file.is_file()
            data = None if skip else file.read_bytes()
        text = None if data is None else decode_text(data)
        if text is not None:
            findings.extend(scan_text(path, text))
    return findings


def _utf8(stream: object) -> None:
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def main(argv: Sequence[str] | None = None, repo: Path | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    _utf8(sys.stdout)
    _utf8(sys.stderr)
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
    sys.stdout.flush()
    if findings:
        sys.stderr.write(
            f"secrets-check: {len(findings)} finding(s). "
            "Remove the secret, or mark a false positive with '# secrets-check: allow'.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
