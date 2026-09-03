#!/usr/bin/env python3
"""Validate the NuGet audit policy pinned in this repository's ``Directory.Build.props``.

Every consumer of this submodule inherits these properties, so the audit is either on for all of
them or silently off for whoever happened to unset it. The pin closes one specific hole: an MSBuild
property assigned in a project file beats an *environment* variable of the same name, so
``NuGetAudit=false`` in the environment - which leaves no file for any scan to read - stops working
once the property is assigned here.

It does NOT beat a command line: ``-p:NuGetAudit=false`` is a global property and still wins. That
route leaves a file behind, so it is a consuming repository's own job to guard its build scripts.

Two design constraints, because a naive version of this check is worse than none:

* absence is a failure - a missing property, or a missing ``Directory.Build.props`` altogether, is
  reported rather than passed over, so deleting the file cannot turn the guard green;
* the file is parsed as XML, never scanned as text - ``<!-- <NuGetAudit>true</NuGetAudit> -->`` does
  not satisfy the check, and a comment merely mentioning the property is not mistaken for it.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

DEFAULT_FILE_NAME = "Directory.Build.props"

# The pair is the policy: auditing enabled, and applied to the whole transitive graph rather than
# only the direct references. Values are compared case-insensitively - MSBuild reads them that way,
# whatever this repository's lowercase house style prefers.
REQUIRED_PROPERTIES = {
    "NuGetAudit": "true",
    "NuGetAuditMode": "all",
}


def _local_name(tag: str) -> str:
    """Return an element's tag without its XML namespace, if it carries one."""
    return tag.rpartition("}")[2]


def validate(path: Path) -> list[str]:
    """Return one message per problem found in ``path``; an empty list means the pins are in place."""
    if not path.is_file():
        return [f"missing: {path} does not exist, so the audit pin cannot be guaranteed"]

    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as error:
        return [f"not well-formed XML: {error}"]

    problems: list[str] = []
    if _local_name(root.tag) != "Project":
        problems.append(f"root element is {root.tag}, expected an MSBuild Project")

    # An assignment only holds unconditionally when it sits in a top-level PropertyGroup and neither
    # that group nor the property element itself carries a Condition. Anything else - a conditional
    # group, a Choose/When branch, a Target - is a pin some configuration can dodge.
    unconditional: dict[str, str] = {}
    every_assignment: dict[str, list[str]] = {}
    top_level = {id(child) for child in root}
    for group in root.iter():
        if _local_name(group.tag) != "PropertyGroup":
            continue
        holds_always = id(group) in top_level and "Condition" not in group.attrib
        for element in group:
            name = _local_name(element.tag)
            if name not in REQUIRED_PROPERTIES:
                continue
            value = (element.text or "").strip()
            every_assignment.setdefault(name, []).append(value)
            if holds_always and "Condition" not in element.attrib:
                unconditional[name] = value

    for name, expected in sorted(REQUIRED_PROPERTIES.items()):
        assignments = every_assignment.get(name, [])
        if not assignments:
            problems.append(f"{name} is not assigned - expected <{name}>{expected}</{name}>")
            continue
        if name not in unconditional:
            problems.append(f"{name} is only assigned conditionally, so the pin can be dodged")
            continue
        actual = unconditional[name]
        if actual.casefold() != expected.casefold():
            problems.append(f"{name} is '{actual}', expected '{expected}'")
            continue
        for other in assignments:
            if other.casefold() != expected.casefold():
                problems.append(f"{name} is reassigned to '{other}' elsewhere in the file")

    return problems


def main(argv: list[str]) -> int:
    paths = [Path(argument) for argument in argv] or [Path.cwd() / DEFAULT_FILE_NAME]

    failed = False
    for path in paths:
        problems = validate(path)
        if problems:
            failed = True
            print(f"{path}: {len(problems)} problem(s)")
            for problem in problems:
                print(f"  {problem}")
        else:
            print(f"{path}: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
