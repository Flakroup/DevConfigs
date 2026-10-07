#!/usr/bin/env python3
"""Tests for validate_workflows. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import validate_workflows as module  # noqa: E402
from validate_workflows import main, problems  # noqa: E402

SHA = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
PYTHON_SHA = "a26af69be951a213d495a4c3e4e4022e16d87065"
OLD_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"
TAGS = {("actions/checkout", "v5.1.0"): SHA, ("actions/checkout", "v4.2.2"): OLD_SHA,
        ("actions/setup-python", "v5.6.0"): PYTHON_SHA, ("pypa/gh-action-pypi-publish", "release/v1"): SHA,
        ("actions/checkout-fork", "v5.1.0"): SHA}


def resolve(repository: str, tag: str) -> str | None:
    return TAGS.get((repository, tag))


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

      - uses: actions/setup-python@{PYTHON_SHA} # v5.6.0
        with:
          python-version: '3.13'
"""


def changed(old: str, new: str) -> str:
    assert HARDENED.count(old) == 1, old
    return HARDENED.replace(old, new)


def check_of(text: str) -> list[str]:
    return problems("w.yml", text, resolve)


def main_on(text: str, name: str = "w.yml") -> int:
    """main() over a workflows directory holding one file with this text."""
    saved = module.WORKFLOWS
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / name).write_text(text, encoding="utf-8")
        module.WORKFLOWS = Path(directory)
        try:
            return main(resolve)
        finally:
            module.WORKFLOWS = saved


def tag_from(listing: str, tag: str = "v1") -> str | None:
    """tag_commit() over a canned git ls-remote listing, without the network."""
    saved = module.subprocess.run
    module.subprocess.run = lambda *_, **__: SimpleNamespace(stdout=listing)
    try:
        return module.tag_commit("owner/repo", tag)
    finally:
        module.subprocess.run = saved


ANNOTATED = f"{OLD_SHA}\trefs/tags/v1\n{SHA}\trefs/tags/v1^{{}}\n"
NO_PERMISSIONS = changed("permissions:\n  contents: read\n", "")
NO_PERSIST = changed("        with:\n          persist-credentials: false\n", "")
TOKEN_KEPT = ["w.yml:13: actions/checkout keeps the token (no persist-credentials: false)"]
LAST_CHECKOUT = f"""permissions:
  contents: read
jobs:
  a:
    steps:
      - uses: actions/checkout@{SHA} # v5.1.0
  b:
    uses: ./.github/workflows/other.yml
    with:
      persist-credentials: false
"""

CASES = [
    ("a hardened workflow passes",
     lambda: check_of(HARDENED) == []),
    ("a workflow without a top-level permissions block is reported",
     lambda: check_of(NO_PERMISSIONS) == ["w.yml: no top-level permissions block"]),
    ("a permissions block inside a job does not count as top-level",
     lambda: check_of(NO_PERMISSIONS.replace("    runs-on:", "    permissions:\n      contents: read\n    runs-on:"))
     == ["w.yml: no top-level permissions block"]),
    ("a top-level write-all is reported",
     lambda: check_of(changed("permissions:\n  contents: read\n", "permissions: write-all\n"))
     == ["w.yml:6: grants write access (permissions: write-all)"]),
    ("a job-level write scope is reported",
     lambda: check_of(changed("    runs-on:", "    permissions:\n      id-token: write\n    runs-on:"))
     == ["w.yml:12: grants write access (id-token: write)"]),
    ("an action referenced by a tag is reported",
     lambda: check_of(changed(f"setup-python@{PYTHON_SHA} # v5.6.0", "setup-python@v5"))
     == ["w.yml:17: actions/setup-python@v5 is not pinned to a commit SHA"]),
    ("an action pinned by a short SHA is reported",
     lambda: check_of(changed(f"setup-python@{PYTHON_SHA}", f"setup-python@{PYTHON_SHA[:7]}"))
     == [f"w.yml:17: actions/setup-python@{PYTHON_SHA[:7]} is not pinned to a commit SHA"]),
    ("a SHA with extra characters is reported",
     lambda: check_of(changed(f"setup-python@{PYTHON_SHA}", f"setup-python@{PYTHON_SHA}0"))
     == [f"w.yml:17: actions/setup-python@{PYTHON_SHA}0 is not pinned to a commit SHA"]),
    ("a pinned action without a trailing comment is reported",
     lambda: check_of(changed(f"setup-python@{PYTHON_SHA} # v5.6.0", f"setup-python@{PYTHON_SHA}"))
     == ["w.yml:17: actions/setup-python is pinned without its tag in a trailing comment"]),
    ("a comment naming no tag of the action is reported",
     lambda: check_of(changed("# v5.6.0", "# pinned"))
     == [f"w.yml:17: actions/setup-python@{PYTHON_SHA} is not the commit of pinned (None)"]),
    ("a comment naming another release than the pinned commit is reported",
     lambda: check_of(changed(f"checkout@{SHA} # v5.1.0", f"checkout@{OLD_SHA} # v5.1.0"))
     == [f"w.yml:13: actions/checkout@{OLD_SHA} is not the commit of v5.1.0 ({SHA})"]),
    ("a tag outside the v-number scheme is checked like any other",
     lambda: check_of(changed(f"actions/setup-python@{PYTHON_SHA} # v5.6.0",
                              f"pypa/gh-action-pypi-publish@{SHA} # release/v1")) == []),
    ("a quoted pinned reference passes",
     lambda: check_of(changed(f"actions/setup-python@{PYTHON_SHA}", f"'actions/setup-python@{PYTHON_SHA}'")) == []),
    ("a docker image by tag is reported",
     lambda: check_of(changed(f"actions/setup-python@{PYTHON_SHA} # v5.6.0", "docker://alpine:latest"))
     == ["w.yml:17: docker://alpine:latest is not pinned to a sha256 digest"]),
    ("a docker image by digest passes",
     lambda: check_of(changed(f"actions/setup-python@{PYTHON_SHA} # v5.6.0", "docker://alpine@sha256:" + "a" * 64))
     == []),
    ("a local action is not a pin",
     lambda: check_of(changed(f"actions/setup-python@{PYTHON_SHA} # v5.6.0", "./.github/actions/setup")) == []),
    ("a checkout without persist-credentials: false is reported",
     lambda: check_of(NO_PERSIST) == TOKEN_KEPT),
    ("a checkout spelled in another case is still checked",
     lambda: check_of(NO_PERSIST.replace("actions/checkout@", "Actions/Checkout@"))
     == ["w.yml:13: Actions/Checkout keeps the token (no persist-credentials: false)"]),
    ("an action whose name only starts with actions/checkout is not a checkout",
     lambda: check_of(NO_PERSIST.replace("actions/checkout@", "actions/checkout-fork@")) == []),
    ("persist-credentials in a later step does not cover the checkout",
     lambda: check_of(NO_PERSIST.replace("python-version: '3.13'", "persist-credentials: false")) == TOKEN_KEPT),
    ("persist-credentials in a later job does not cover the checkout",
     lambda: problems("w.yml", LAST_CHECKOUT, resolve)
     == ["w.yml:6: actions/checkout keeps the token (no persist-credentials: false)"]),
    ("persist-credentials: true is reported",
     lambda: check_of(changed("persist-credentials: false", "persist-credentials: true")) == TOKEN_KEPT),
    ("a quoted false with a trailing comment passes",
     lambda: check_of(changed("persist-credentials: false", "persist-credentials: 'false' # no token")) == []),
    ("an annotated tag resolves to its commit, not to the tag object",
     lambda: tag_from(ANNOTATED) == SHA),
    ("a lightweight tag resolves to the commit it names",
     lambda: tag_from(f"{SHA}\trefs/tags/v1\n") == SHA),
    ("a missing tag resolves to nothing",
     lambda: tag_from("") is None),
    ("a directory holding an unhardened workflow fails",
     lambda: main_on(NO_PERMISSIONS) == 1),
    ("a .yaml workflow is checked too",
     lambda: main_on(NO_PERMISSIONS, "w.yaml") == 1),
    ("a directory holding only hardened workflows passes",
     lambda: main_on(HARDENED) == 0),
    ("the committed workflows are hardened and their tags still point at the pins (needs github.com)",
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
