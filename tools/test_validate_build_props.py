#!/usr/bin/env python3
"""Tests for validate_build_props. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from validate_build_props import DEFAULT_FILE_NAME, validate  # noqa: E402

PINS = "        <NuGetAudit>true</NuGetAudit>\n        <NuGetAuditMode>all</NuGetAuditMode>"


def wrap(properties: str) -> str:
    """Wrap property assignments in the surrounding Project/PropertyGroup skeleton."""
    return f"<Project>\n\n    <PropertyGroup>\n{properties}\n    </PropertyGroup>\n\n</Project>"


def check(content: str | None, expected: str | None, label: str) -> bool:
    """Run the validator over a throwaway file and assert on the problems it reports.

    ``content`` of None writes no file at all - the case that must fail rather than pass silently.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / DEFAULT_FILE_NAME
        if content is not None:
            path.write_text(content, encoding="utf-8")
        problems = validate(path)

    if expected is None:
        passed = not problems
        detail = "clean" if passed else f"unexpected: {problems}"
    else:
        passed = any(expected in problem for problem in problems)
        detail = "reported" if passed else f"expected {expected!r}, got {problems}"

    print(f"{'PASS' if passed else 'FAIL'}  {label}: {detail}")
    return passed


CASES = [
    (
        wrap(PINS),
        None,
        "both pins present and correct pass",
    ),
    (
        wrap("        <Nullable>enable</Nullable>"),
        "NuGetAudit is not assigned",
        "a file without the pins is rejected",
    ),
    (
        wrap("        <NuGetAudit>true</NuGetAudit>"),
        "NuGetAuditMode is not assigned",
        "pinning the audit without the mode is rejected",
    ),
    (
        wrap("        <NuGetAudit>false</NuGetAudit>\n        <NuGetAuditMode>all</NuGetAuditMode>"),
        "NuGetAudit is 'false', expected 'true'",
        "the audit disabled is rejected",
    ),
    (
        wrap("        <NuGetAudit>true</NuGetAudit>\n        <NuGetAuditMode>direct</NuGetAuditMode>"),
        "NuGetAuditMode is 'direct', expected 'all'",
        "auditing only direct references is rejected",
    ),
    (
        wrap(f"        <!--\n{PINS}\n        -->"),
        "NuGetAudit is not assigned",
        "the pins commented out are rejected",
    ),
    (
        wrap(f"{PINS}\n        <!-- NuGetAudit stays true; do not set NuGetAudit to false here -->"),
        None,
        "a comment mentioning the property is not mistaken for an assignment",
    ),
    (
        None,
        "does not exist",
        "a missing Directory.Build.props is rejected",
    ),
    (
        wrap(PINS).replace("</Project>", ""),
        "not well-formed XML",
        "an unclosed Project element is rejected",
    ),
    (
        f"<Project>\n    <PropertyGroup Condition=\" '$(Configuration)' == 'Release' \">\n{PINS}\n"
        "    </PropertyGroup>\n</Project>",
        "only assigned conditionally",
        "pins confined to a conditional PropertyGroup are rejected",
    ),
    (
        wrap(
            '        <NuGetAudit Condition=" \'$(CI)\' == \'true\' ">true</NuGetAudit>\n'
            "        <NuGetAuditMode>all</NuGetAuditMode>"
        ),
        "only assigned conditionally",
        "a Condition on the property element itself is rejected",
    ),
    (
        f"<Project>\n    <PropertyGroup>\n{PINS}\n    </PropertyGroup>\n"
        "    <PropertyGroup Condition=\" '$(Fast)' == 'true' \">\n"
        "        <NuGetAudit>false</NuGetAudit>\n    </PropertyGroup>\n</Project>",
        "reassigned to 'false'",
        "a later conditional group turning the audit off is rejected",
    ),
    (
        "<Project xmlns=\"http://schemas.microsoft.com/developer/msbuild/2003\">\n"
        f"    <PropertyGroup>\n{PINS}\n    </PropertyGroup>\n</Project>",
        None,
        "the legacy MSBuild namespace is understood",
    ),
    (
        f"<PropertyGroup>\n{PINS}\n</PropertyGroup>",
        "expected an MSBuild Project",
        "a file whose root is not Project is rejected",
    ),
]


def main() -> int:
    results = [check(content, expected, label) for content, expected, label in CASES]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
