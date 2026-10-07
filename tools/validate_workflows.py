#!/usr/bin/env python3
"""Guard the hardening of every workflow under ``.github/workflows``.

Each workflow must declare a top-level ``permissions:`` block and grant write access nowhere, so neither a
change to the organisation's default token scope nor a new job can widen what its jobs hold; every
``actions/checkout`` step must set ``persist-credentials: false``, so the token does not stay in
``.git/config`` for the steps after it; every action must be pinned to a full commit SHA with its tag in a
trailing comment, and the tag must still point at that commit (``git ls-remote``), so the comment cannot lie
about what runs; and a ``docker://`` image must be pinned to a ``sha256`` digest. Plain text checks, no YAML
library - CI needs nothing installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

USES = re.compile(r"""^(\s*)(?:-\s+)?uses:\s*(['"]?)([^\s'"]+)\2(.*)$""")
PINNED = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"docker://\S+@sha256:[0-9a-f]{64}")
TAG_COMMENT = re.compile(r"^\s+#\s*(\S+)")
WRITE = re.compile(r"^\s*(?:permissions:\s*write-all|[\w-]+:\s*write)\s*(?:#.*)?$")
NO_CREDENTIALS = re.compile(r"""^persist-credentials:\s*(['"]?)false\1\s*(?:#.*)?$""")

Resolver = Callable[[str, str], "str | None"]


def tag_commit(repository: str, tag: str) -> str | None:
    """The commit a tag of a GitHub repository points at, or None when the tag does not exist."""
    refs = subprocess.run(["git", "ls-remote", f"https://github.com/{repository}.git",
                           f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
                          capture_output=True, text=True, check=True).stdout
    found = {ref: sha for sha, ref in (line.split("\t") for line in refs.splitlines())}
    return found.get(f"refs/tags/{tag}^{{}}") or found.get(f"refs/tags/{tag}")


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def problems(name: str, text: str, resolve: Resolver = tag_commit) -> list[str]:
    """Hardening gaps of one workflow file; empty when it is hardened."""
    found = []
    lines = text.splitlines()
    if not any(line.startswith("permissions:") for line in lines):
        found.append(f"{name}: no top-level permissions block")
    for number, line in enumerate(lines, start=1):
        if WRITE.match(line):
            found.append(f"{name}:{number}: grants write access ({line.strip()})")
        match = USES.match(line)
        if not match:
            continue
        step_indent, _, reference, rest = match.groups()
        if reference.startswith("./"):
            continue
        if reference.startswith("docker://"):
            if not DIGEST.fullmatch(reference):
                found.append(f"{name}:{number}: {reference} is not pinned to a sha256 digest")
            continue
        action, _, ref = reference.rpartition("@")
        tag = TAG_COMMENT.match(rest)
        if not PINNED.fullmatch(ref):
            found.append(f"{name}:{number}: {reference} is not pinned to a commit SHA")
        elif not tag:
            found.append(f"{name}:{number}: {action} is pinned without its tag in a trailing comment")
        elif (commit := resolve("/".join(action.lower().split("/")[:2]), tag.group(1))) != ref:
            found.append(f"{name}:{number}: {action}@{ref} is not the commit of {tag.group(1)} ({commit})")
        if action.lower() == "actions/checkout":
            step = []
            for following in lines[number:]:
                if following.strip() and indent(following) <= len(step_indent):
                    break
                step.append(following.strip())
            if not any(NO_CREDENTIALS.match(entry) for entry in step):
                found.append(f"{name}:{number}: {action} keeps the token (no persist-credentials: false)")
    return found


def main(resolve: Resolver = tag_commit) -> int:
    found = [problem for path in sorted(WORKFLOWS.glob("*.y*ml"))
             for problem in problems(path.name, path.read_text(encoding="utf-8"), resolve)]
    for problem in found:
        print(problem, file=sys.stderr)
    print("OK" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
