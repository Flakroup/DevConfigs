#!/usr/bin/env python3
"""Tests for validate_build_props. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from validate_build_props import (  # noqa: E402
    PROPS_FILE_NAME,
    TARGETS_FILE_NAME,
    validate,
    validate_targets,
)

PINS = "        <NuGetAudit>true</NuGetAudit>\n        <NuGetAuditMode>all</NuGetAuditMode>"

# The attribute the props file has to carry, as the fixtures spell it by default. A case that is
# about the attribute passes its own value, or None to build a Project element without one.
LOCAL = 'TreatAsLocalProperty="NuGetAudit;NuGetAuditMode"'


def opening(local: str | None) -> str:
    """Build the opening Project tag, with the given attribute text or without any."""
    return f"<Project {local}>" if local else "<Project>"


def wrap(properties: str, local: str | None = LOCAL) -> str:
    """Wrap property assignments in the surrounding Project/PropertyGroup skeleton."""
    return (
        f"{opening(local)}\n\n    <PropertyGroup>\n{properties}\n    </PropertyGroup>\n\n</Project>"
    )


def group(properties: str, condition: str = "") -> str:
    """Build one PropertyGroup, optionally conditional, for cases that need several of them."""
    attribute = f' Condition=" {condition} "' if condition else ""
    return f"    <PropertyGroup{attribute}>\n{properties}\n    </PropertyGroup>"


def project(*groups: str, local: str | None = LOCAL) -> str:
    """Build a Project holding the given groups verbatim, in order."""
    body = "\n".join(groups)
    return f"{opening(local)}\n{body}\n</Project>"


def check(content: str | None, expected: str | None, label: str, *, targets: bool = False) -> bool:
    """Run the validator over a throwaway file and assert on the problems it reports.

    ``content`` of None writes no file at all - the case that must fail rather than pass silently.
    ``targets`` picks the Directory.Build.targets entry point, which requires no pin of its own.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / (TARGETS_FILE_NAME if targets else PROPS_FILE_NAME)
        if content is not None:
            path.write_text(content, encoding="utf-8")
        problems = validate_targets(path) if targets else validate(path)

    if expected is None:
        passed = not problems
        detail = "clean" if passed else f"unexpected: {problems}"
    else:
        passed = any(expected in problem for problem in problems)
        detail = "reported" if passed else f"expected {expected!r}, got {problems}"

    print(f"{'PASS' if passed else 'FAIL'}  {label}: {detail}")
    return passed


PROPS_CASES = [
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
        project(group(PINS, "'$(Configuration)' == 'Release'")),
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
        project(
            group(PINS),
            group("        <NuGetAudit>false</NuGetAudit>", "'$(Fast)' == 'true'"),
        ),
        "reassigned to 'false' after the pin",
        "a later conditional group turning the audit off is rejected",
    ),
    (
        project(
            group("        <NuGetAudit>false</NuGetAudit>\n        <NuGetAuditMode>all</NuGetAuditMode>"),
            group("        <NuGetAudit>true</NuGetAudit>"),
        ),
        None,
        "a later group fixing an earlier wrong value passes - MSBuild is last-wins",
    ),
    (
        wrap("        <nugetaudit>false</nugetaudit>\n        <nugetauditmode>direct</nugetauditmode>"),
        "NuGetAudit is 'false', expected 'true'",
        "a lowercase property name disabling the audit is rejected - MSBuild names are case-insensitive",
    ),
    (
        wrap("        <NUGETAUDIT>TRUE</NUGETAUDIT>\n        <NuGetAuditMode>ALL</NuGetAuditMode>"),
        None,
        "uppercase spelling of the name and the value still satisfies the pin",
    ),
    (
        project(
            group(PINS),
            group("        <NuGetAudit>false</NuGetAudit>"),
        ),
        "reassigned to 'false' after the pin",
        "a second unconditional group turning the audit off is rejected",
    ),
    (
        wrap("        <NuGetAudit>\n            true\n        </NuGetAudit>\n" + PINS.splitlines()[1]),
        "expected 'true'",
        "a value padded with whitespace is rejected - MSBuild keeps the padding",
    ),
    (
        project(group(PINS), '    <Import Project="audit-off.props" />'),
        "this guard does not read imported files",
        "an Import the guard cannot follow is reported",
    ),
    (
        project(group(PINS), '    <ImportGroup><import Project="audit-off.props" /></ImportGroup>'),
        "this guard does not read imported files",
        "an Import is caught whatever its spelling and wherever it is nested",
    ),
    (
        wrap(f"{PINS}\n        <NoWarn>$(NoWarn);CA2254;NU1901;NU1903</NoWarn>"),
        "NoWarn suppresses NU1901, NU1903",
        "suppressing the audit's own warnings is rejected",
    ),
    (
        wrap(f"{PINS}\n        <NoWarn>$(NoWarn);CA2254;CS1591</NoWarn>"),
        None,
        "an unrelated NoWarn entry is left alone",
    ),
    (
        f"<Project xmlns=\"http://schemas.microsoft.com/developer/msbuild/2003\" {LOCAL}>\n"
        f"    <PropertyGroup>\n{PINS}\n    </PropertyGroup>\n</Project>",
        None,
        "the legacy MSBuild namespace is understood",
    ),
    (
        f"<PropertyGroup>\n{PINS}\n</PropertyGroup>",
        "expected an MSBuild Project",
        "a file whose root is not Project is rejected",
    ),
    (
        wrap(PINS, local=None),
        "TreatAsLocalProperty is not declared",
        "pins without the attribute are rejected - a command line would still beat them",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty="NuGetAudit"'),
        "TreatAsLocalProperty does not cover NuGetAuditMode",
        "the attribute naming only the audit is rejected",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty="NuGetAuditMode"'),
        "TreatAsLocalProperty does not cover NuGetAudit",
        "the attribute naming only the mode is rejected",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty=""'),
        "TreatAsLocalProperty is not declared",
        "an empty attribute value counts as no declaration",
    ),
    (
        wrap(
            f'        <!-- <Project {LOCAL}> -->\n{PINS}',
            local=None,
        ),
        "TreatAsLocalProperty is not declared",
        "the attribute only inside an XML comment is rejected",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty="NuGetAudit,NuGetAuditMode"'),
        "TreatAsLocalProperty does not cover NuGetAudit, NuGetAuditMode",
        "a comma-separated value is rejected - MSBuild splits on semicolons only",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty="NuGetAudit;&#10;    NuGetAuditMode"'),
        None,
        "a value wrapped across lines after the semicolon passes - MSBuild trims each entry",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty=" nugetaudit ; NUGETAUDITMODE "'),
        None,
        "the attribute is matched case-insensitively and padding is ignored, as MSBuild does",
    ),
    (
        wrap(PINS, local='TreatAsLocalProperty="Version;NuGetAudit;;NuGetAuditMode"'),
        None,
        "unrelated names and empty entries alongside the two guarded ones are fine",
    ),
]

TARGETS_CASES = [
    (
        None,
        None,
        "an absent Directory.Build.targets is not a problem - deleting it opens no hole",
    ),
    (
        project(group("        <IsPackable>true</IsPackable>")),
        None,
        "a targets file that says nothing about the audit passes",
    ),
    (
        project(group("        <NuGetAudit>false</NuGetAudit>")),
        "NuGetAudit is 'false', expected 'true'",
        "the targets file turning the audit off is rejected - it evaluates after the props file",
    ),
    (
        project(group("        <NuGetAuditMode>direct</NuGetAuditMode>", "'$(Fast)' == 'true'")),
        "NuGetAuditMode is conditionally set to 'direct'",
        "the targets file weakening the mode under a condition is rejected",
    ),
    (
        project(group(PINS)),
        None,
        "the targets file restating the policy verbatim is harmless",
    ),
    (
        project(group("        <IsPackable>true</IsPackable>"), local=None),
        None,
        "the targets file needs no TreatAsLocalProperty of its own - the props file carries it",
    ),
    (
        project(group("        <NoWarn>$(NoWarn);NU1902</NoWarn>")),
        "NoWarn suppresses NU1902",
        "the targets file suppressing an audit warning is rejected",
    ),
]


def main() -> int:
    if not PROPS_CASES or not TARGETS_CASES:
        print("FAIL  no cases declared - an empty suite passes vacuously")
        return 1

    results = [check(content, expected, label) for content, expected, label in PROPS_CASES]
    results += [
        check(content, expected, label, targets=True) for content, expected, label in TARGETS_CASES
    ]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
