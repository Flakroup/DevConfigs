#!/usr/bin/env python3
"""Guard the hardening of every workflow under ``.github/workflows``.

Each workflow must declare a top-level ``permissions:`` block, so a change to the organisation's default
token scope cannot silently widen its jobs; every ``actions/checkout`` step must set
``persist-credentials: false``, so the token does not stay in ``.git/config`` for the steps after it; and
every action must be pinned to a full commit SHA with its tag in a trailing comment, since a tag can be
moved to other code. Plain text checks, no YAML library - CI needs nothing installed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+?)@(\S+)(.*)$")
PINNED = re.compile(r"^[0-9a-f]{40}$")
TAG_COMMENT = re.compile(r"^\s+#\s*v\d")
STEP = re.compile(r"^\s*-\s")


def problems(name: str, text: str) -> list[str]:
    """Hardening gaps of one workflow file; empty when it is hardened."""
    found = []
    lines = text.splitlines()
    if not any(line.startswith("permissions:") for line in lines):
        found.append(f"{name}: no top-level permissions block")
    for number, line in enumerate(lines, start=1):
        match = USES.match(line)
        if not match:
            continue
        action, ref, rest = match.groups()
        if not PINNED.match(ref):
            found.append(f"{name}:{number}: {action}@{ref} is not pinned to a commit SHA")
        elif not TAG_COMMENT.match(rest):
            found.append(f"{name}:{number}: {action} is pinned without its tag in a trailing comment")
        if action == "actions/checkout":
            step = []
            for following in lines[number:]:
                if STEP.match(following):
                    break
                step.append(following.strip())
            if "persist-credentials: false" not in step:
                found.append(f"{name}:{number}: actions/checkout keeps the token (no persist-credentials: false)")
    return found


def main() -> int:
    found = [problem for path in sorted(WORKFLOWS.glob("*.y*ml"))
             for problem in problems(path.name, path.read_text(encoding="utf-8"))]
    for problem in found:
        print(problem, file=sys.stderr)
    print("OK" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
