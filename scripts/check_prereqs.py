"""Prerequisite check (S-1.1.4): fail when a required dev tool is missing or too old.

Standard library only, and it starts on Python 3.8 or newer (an old interpreter gets the named
``python: required >=3.12`` FAIL line instead of a traceback). Run from the repository root
(any directory inside it also works):

    pnpm prereqs                                          # runs the line below
    uv run --no-project python scripts/check_prereqs.py   # when pnpm is the missing tool
    python scripts/check_prereqs.py                       # when uv itself is not installed yet

Checked tools: Docker, uv, Python, Node, pnpm and Docker Compose. Output is one line per tool.
A failing line names the tool, the required version and the version found (``not found`` when
it is missing).
Exit code 0 means every tool is fine and 1 means at least one is missing or too old.

Where the required versions come from:

* Python: ``requires-python`` in ``apps/api/pyproject.toml``.
* Node and pnpm: ``engines`` in the root ``package.json``.
* uv: the repo pins uv 0.10.12 (``apps/api/Dockerfile``, ``.github/workflows/ci.yml``), so the
  floor is the 0.10 series.
* Docker: ``healthcheck.start_interval`` in ``infra/docker-compose.yml`` needs Docker Engine 25.0.
* Docker Compose: the long-form ``env_file`` with ``required:`` in ``infra/docker-compose.yml``
  needs Compose 2.24. It is probed with ``docker compose version``.
"""

# ruff: noqa: T201 - a command-line script reports its result with print
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional  # noqa: UP035 - runtime alias, see Lookup

ROOT = Path(__file__).resolve().parent.parent

# Floors that come from this script rather than a repo manifest (see the module docstring).
DOCKER_MINIMUM = ">=25.0"
COMPOSE_MINIMUM = ">=2.24"
UV_MINIMUM = ">=0.10"

# Evaluated at runtime, so no ``X | None`` here: this module must import on Python 3.8.
Lookup = Callable[[str], Optional[str]]  # noqa: UP045
"""Returns the raw version text a tool prints (``docker --version`` and so on), or ``None``
when the tool is missing or could not be run. The argument is the tool name: ``docker``, ``uv``,
``python``, ``node``, ``pnpm`` or ``compose``."""

_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")
_COMPARATOR_RE = re.compile(r"^(>=|<=|==|=|>|<)?\s*(\d+(?:\.\d+)*)$")

_VERSION_COMMANDS: dict[str, list[str]] = {
    "docker": ["docker", "--version"],
    "uv": ["uv", "--version"],
    "node": ["node", "--version"],
    "pnpm": ["pnpm", "--version"],
    "compose": ["docker", "compose", "version"],
}


@dataclass(frozen=True)
class Requirement:
    """One tool and the version range it must satisfy, for example ``>=24 <25``."""

    tool: str
    spec: str
    source: str = ""


@dataclass(frozen=True)
class Finding:
    """The result for one tool: ``found`` is ``None`` when the tool is missing."""

    tool: str
    required: str
    found: str | None
    ok: bool
    message: str


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return the first dotted number in ``text`` as a tuple (``v24.1.0`` gives ``(24, 1, 0)``)."""
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _compare(found: tuple[int, ...], bound: tuple[int, ...]) -> int:
    """Compare two versions, padding the shorter with zeros. Returns -1, 0 or 1."""
    width = max(len(found), len(bound))
    a = found + (0,) * (width - len(found))
    b = bound + (0,) * (width - len(bound))
    return (a > b) - (a < b)


def satisfies(version: tuple[int, ...], spec: str) -> bool:
    """Return True when ``version`` meets every comparator in ``spec``.

    ``spec`` is comparators separated by spaces or commas: ``>=3.12``, ``>=24 <25``,
    ``>=11.1.3,<12``. Supported operators are ``>=``, ``<=``, ``>``, ``<``, ``=`` and ``==``;
    a bare number means ``>=``. An upper bound written with fewer parts than the version is
    compared padded (``<25`` rejects ``25.0.0`` and accepts ``24.9``). Raises ``ValueError`` on
    a comparator it does not understand.
    """
    tokens = [t for t in re.split(r"[\s,]+", spec.strip()) if t]
    if not tokens:
        raise ValueError(f"empty version spec: {spec!r}")
    # Join a detached operator with its number (">= 3.12" arrives as two tokens).
    comparators: list[str] = []
    for token in tokens:
        if comparators and comparators[-1] in {">=", "<=", ">", "<", "=", "=="}:
            comparators[-1] += token
        else:
            comparators.append(token)
    for comparator in comparators:
        match = _COMPARATOR_RE.match(comparator)
        if match is None:
            raise ValueError(f"unsupported version comparator: {comparator!r}")
        operator = match.group(1) or ">="
        bound = tuple(int(p) for p in match.group(2).split("."))
        order = _compare(version, bound)
        passed = {
            ">=": order >= 0,
            "<=": order <= 0,
            ">": order > 0,
            "<": order < 0,
            "=": order == 0,
            "==": order == 0,
        }[operator]
        if not passed:
            return False
    return True


def check_prerequisites(requirements: Sequence[Requirement], lookup: Lookup) -> list[Finding]:
    """Check every requirement against the versions ``lookup`` reports; one finding per tool.

    ``lookup(tool)`` returns the tool's raw version text, or ``None`` when it is missing. A tool
    is not ok when it is missing, when its output holds no version number, or when the version
    does not satisfy the requirement. Every message names the tool, the required version and the
    version found. Pure apart from the calls to ``lookup``.
    """
    findings: list[Finding] = []
    for requirement in requirements:
        raw = lookup(requirement.tool)
        found_version = None if raw is None else parse_version(raw)
        found_text = None if found_version is None else ".".join(map(str, found_version))
        required = requirement.spec
        if raw is None:
            reason = "not found"
            ok = False
        elif found_version is None:
            reason = f"version not recognised in {raw.strip()!r}"
            ok = False
        else:
            ok = satisfies(found_version, required)
            reason = f"found {found_text}" + ("" if ok else " (too old or out of range)")
        message = f"{requirement.tool}: required {required}, {reason}"
        findings.append(Finding(requirement.tool, required, found_text, ok, message))
    return findings


def _read_python_spec(pyproject: Path) -> str:
    """Return ``project.requires-python``; works on Pythons without ``tomllib`` (before 3.11).

    ``tomllib`` is imported here, not at module level, so an old interpreter can still import
    this script and report itself as too old. Without it a regex reads the one line needed.
    """
    text = pyproject.read_text(encoding="utf-8")
    try:
        import tomllib  # noqa: PLC0415 - lazy: absent before Python 3.11
    except ImportError:
        match = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if match is None:
            raise ValueError(f"requires-python not found in {pyproject}") from None
        return match.group(1)
    return str(tomllib.loads(text)["project"]["requires-python"])


def load_requirements(root: Path = ROOT) -> list[Requirement]:
    """Read the required versions from the repo's own pins (see the module docstring)."""
    python_spec = _read_python_spec(root / "apps/api/pyproject.toml")
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    engines: dict[str, str] = package["engines"]
    return [
        Requirement("docker", DOCKER_MINIMUM, "scripts/check_prereqs.py"),
        Requirement("uv", UV_MINIMUM, "scripts/check_prereqs.py"),
        Requirement("python", python_spec, "apps/api/pyproject.toml requires-python"),
        Requirement("node", engines["node"], "package.json engines.node"),
        Requirement("pnpm", engines["pnpm"], "package.json engines.pnpm"),
        Requirement("compose", COMPOSE_MINIMUM, "scripts/check_prereqs.py"),
    ]


def probe_tool(tool: str, cwd: Path = ROOT) -> str | None:
    """Run the real tool and return its version text, or ``None`` when it is missing or fails.

    Python is the interpreter running this script (under ``pnpm prereqs``, the one uv picked).
    """
    if tool == "python":
        return platform.python_version()
    command = _VERSION_COMMANDS[tool]
    executable = shutil.which(command[0])  # resolves pnpm.cmd and similar on Windows
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument lists, no user input
            [executable, *command[1:]],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or completed.stderr.strip() or None


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = ROOT,
    lookup: Lookup | None = None,
) -> int:
    """Print one line per tool and return the exit code: 0 when all pass, 1 otherwise."""
    del argv  # no options
    resolved: Lookup = lookup if lookup is not None else (lambda tool: probe_tool(tool, root))
    findings = check_prerequisites(load_requirements(root), resolved)
    for finding in findings:
        print(f"{'ok  ' if finding.ok else 'FAIL'} {finding.message}")
    failed = [f for f in findings if not f.ok]
    if failed:
        names = ", ".join(f.tool for f in failed)
        print(f"prereqs: FAILED ({names}). Install or upgrade (docs/getting-started.md)")
        return 1
    print("prereqs: all prerequisites met")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
