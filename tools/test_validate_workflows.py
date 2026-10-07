#!/usr/bin/env python3
"""Tests for validate_workflows. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import validate_workflows as module  # noqa: E402
from validate_workflows import main, problems  # noqa: E402

SHA = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"

HARDENED = f"""name: validate

on:
  push:

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{SHA} # v5.1.0
        with:
          persist-credentials: false

      - uses: actions/setup-python@{SHA} # v5.6.0
        with:
          python-version: '3.13'
"""


def changed(old: str, new: str) -> str:
    assert HARDENED.count(old) == 1, old
    return HARDENED.replace(old, new)


def main_on(text: str) -> int:
    """main() over a workflows directory holding one file with this text."""
    saved = module.WORKFLOWS
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "w.yml").write_text(text, encoding="utf-8")
        module.WORKFLOWS = Path(directory)
        try:
            return main()
        finally:
            module.WORKFLOWS = saved


CASES = [
    ("a hardened workflow passes",
     lambda: problems("w.yml", HARDENED) == []),
    ("a workflow without a top-level permissions block is reported",
     lambda: problems("w.yml", changed("permissions:\n  contents: read\n", ""))
     == ["w.yml: no top-level permissions block"]),
    ("a permissions block inside a job does not count as top-level",
     lambda: problems("w.yml", changed("permissions:\n  contents: read\n", "").replace(
         "    runs-on:", "    permissions:\n      contents: read\n    runs-on:"))
     == ["w.yml: no top-level permissions block"]),
    ("an action referenced by a tag is reported",
     lambda: problems("w.yml", changed(f"setup-python@{SHA} # v5.6.0", "setup-python@v5"))
     == ["w.yml:17: actions/setup-python@v5 is not pinned to a commit SHA"]),
    ("an action pinned by a short SHA is reported",
     lambda: problems("w.yml", changed(f"setup-python@{SHA}", f"setup-python@{SHA[:7]}"))
     == [f"w.yml:17: actions/setup-python@{SHA[:7]} is not pinned to a commit SHA"]),
    ("a pinned action without its tag in a comment is reported",
     lambda: problems("w.yml", changed(f"setup-python@{SHA} # v5.6.0", f"setup-python@{SHA}"))
     == ["w.yml:17: actions/setup-python is pinned without its tag in a trailing comment"]),
    ("a checkout without persist-credentials: false is reported",
     lambda: problems("w.yml", changed("        with:\n          persist-credentials: false\n", ""))
     == ["w.yml:13: actions/checkout keeps the token (no persist-credentials: false)"]),
    ("persist-credentials in a later step does not cover the checkout",
     lambda: problems("w.yml", changed("        with:\n          persist-credentials: false\n", "").replace(
         "python-version: '3.13'", "persist-credentials: false"))
     == ["w.yml:13: actions/checkout keeps the token (no persist-credentials: false)"]),
    ("persist-credentials: true is reported",
     lambda: problems("w.yml", changed("persist-credentials: false", "persist-credentials: true"))
     == ["w.yml:13: actions/checkout keeps the token (no persist-credentials: false)"]),
    ("a directory holding an unhardened workflow fails",
     lambda: main_on(changed("permissions:\n  contents: read\n", "")) == 1),
    ("a directory holding only hardened workflows passes",
     lambda: main_on(HARDENED) == 0),
    ("the committed workflows are hardened",
     lambda: main() == 0),
]


def check(label: str, case) -> bool:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
        try:
            passed = case()
        except Exception as exception:  # one broken case must not hide the verdicts of the rest
            print(f"{type(exception).__name__}: {exception}", file=sys.stderr)
            passed = False
    print(f"{'PASS' if passed else 'FAIL'}  {label}")
    if not passed and errors.getvalue():
        print(errors.getvalue().rstrip())
    return passed


def run() -> int:
    results = [check(label, case) for label, case in CASES]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(run())
