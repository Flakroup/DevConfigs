#!/usr/bin/env python3
"""Validate the NuGet audit policy this repository ships to its consumers.

Every consumer of this submodule inherits these properties, so the audit is either on for all of
them or silently off for whoever happened to unset it. The pin closes one specific hole: an MSBuild
property assigned in a project file beats an *environment* variable of the same name, so
``NuGetAudit=false`` in the environment - which leaves no file for any scan to read - stops working
once the property is assigned here.

A plain assignment does not beat a command line: ``-p:NuGetAudit=false`` is a global property and
wins over one. ``TreatAsLocalProperty`` on the ``Project`` element is what closes that route, and
the props file carries it for both properties, so a global value of either is demoted to a local one
the assignment below then overwrites. The cost is deliberate: no consumer of this submodule can
override these two from a command line any more.

What this validator covers, and why each part is here rather than assumed:

* ``Directory.Build.props`` must carry the pin unconditionally. Absence is a failure - a missing
  property, or a missing file altogether - so deleting what the guard protects cannot turn it green.
* ``Directory.Build.props`` must also declare both properties in ``TreatAsLocalProperty``. Only the
  props file is required to: that is where the pin lives, and that is where the attribute was
  measured to demote a global value. The targets file needs none of its own.
* ``Directory.Build.targets`` is checked too. This repository ships it, MSBuild evaluates it AFTER
  the project body, and a property set there beats the props file. A guard that reads only the props
  file names itself after a policy it cannot see half of. Its absence is not a failure: deleting that
  file opens no hole.
* Property NAMES are matched case-insensitively, because MSBuild matches them that way.
  ``<nugetaudit>false</nugetaudit>`` disables the audit exactly as the capitalised spelling does.
* An ``Import`` in either file is reported. Its contents are evaluated but not parsed here, so the
  guard cannot honestly speak for a file it has not read.
* ``NoWarn`` carrying NU1901-NU1904 is reported. That silences the audit's entire output while both
  properties still read as policy-compliant.
* Values are compared verbatim rather than trimmed. MSBuild stores the whitespace around a property
  value, so a padded value is a real divergence between what this check reports and what the build
  holds.

Both files are parsed as XML, never scanned as text - ``<!-- <NuGetAudit>true</NuGetAudit> -->`` does
not satisfy the check, a commented-out ``Project`` element does not supply the attribute, and a
comment merely mentioning a property is not mistaken for it.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import NamedTuple

PROPS_FILE_NAME = "Directory.Build.props"
TARGETS_FILE_NAME = "Directory.Build.targets"

# The pair is the policy: auditing enabled, and applied to the whole transitive graph rather than
# only the direct references. Keyed by the casefolded name, because MSBuild resolves names that way.
POLICY = {
    "nugetaudit": ("NuGetAudit", "true"),
    "nugetauditmode": ("NuGetAuditMode", "all"),
}

# The warnings the audit raises. Suppressing them leaves the audit running and its findings unread.
AUDIT_WARNING_CODES = ("NU1901", "NU1902", "NU1903", "NU1904")

# The attribute that demotes a global property to a local one, so the assignment above wins over
# `-p:NuGetAudit=false`. MSBuild splits its value on semicolons and nothing else: a comma or a bare
# newline between two names does not separate them, it lands inside one name and fails evaluation
# with MSB5016 ("contains invalid character"). Whitespace around an entry is trimmed, so a value
# wrapped across lines after a semicolon is accepted, and empty entries are ignored. Names resolve
# case-insensitively, like every other MSBuild property name.
LOCAL_PROPERTY_SEPARATOR = ";"


class Assignment(NamedTuple):
    """One assignment of a guarded property, in document order."""

    key: str
    value: str
    holds_always: bool


def _local_name(tag: str) -> str:
    """Return an element's tag without its XML namespace, if it carries one."""
    return tag.rpartition("}")[2]


def _collect(root: ElementTree.Element) -> list[Assignment]:
    """Return every assignment of a guarded property, in document order.

    An assignment holds unconditionally only when it sits in a top-level PropertyGroup and neither
    that group nor the property element itself carries a Condition. Anything else - a conditional
    group, a Choose/When branch, a Target - is a pin some configuration can dodge.
    """
    top_level = {id(child) for child in root}
    assignments: list[Assignment] = []
    for group in root.iter():
        if _local_name(group.tag) != "PropertyGroup":
            continue
        group_holds_always = id(group) in top_level and "Condition" not in group.attrib
        for element in group:
            key = _local_name(element.tag).casefold()
            if key not in POLICY:
                continue
            holds_always = group_holds_always and "Condition" not in element.attrib
            assignments.append(Assignment(key, element.text or "", holds_always))
    return assignments


def _policy_problems(assignments: list[Assignment], require_pin: bool) -> list[str]:
    """Report every way this file's assignments fail to leave the policy in force."""
    problems: list[str] = []
    for key, (name, expected) in POLICY.items():
        entries = [entry for entry in assignments if entry.key == key]
        if not entries:
            if require_pin:
                problems.append(f"{name} is not assigned - expected <{name}>{expected}</{name}>")
            continue

        # MSBuild is last-wins, so the pin is in force from the last unconditional assignment that
        # matches the policy; an earlier wrong value is overridden and is not a problem.
        pinned_at = None
        for index, entry in enumerate(entries):
            if entry.holds_always and entry.value.casefold() == expected.casefold():
                pinned_at = index

        if pinned_at is None:
            unconditional = [entry for entry in entries if entry.holds_always]
            if unconditional:
                problems.append(f"{name} is {unconditional[-1].value!r}, expected '{expected}'")
            elif require_pin:
                problems.append(f"{name} is only assigned conditionally, so the pin can be dodged")
            else:
                problems.append(f"{name} is conditionally set to {entries[-1].value!r} here")
            continue

        for entry in entries[pinned_at + 1:]:
            if entry.value.casefold() != expected.casefold():
                problems.append(f"{name} is reassigned to {entry.value!r} after the pin")

    return problems


def _local_property_problems(root: ElementTree.Element) -> list[str]:
    """Report whether the Project element demotes both guarded properties to local ones.

    Split the way MSBuild splits, so a value this accepts is a value MSBuild also reads as two
    names. Anything it does not - a comma-separated list, names run together across a line break -
    leaves at least one name unmatched and is reported here rather than failing the next build.
    """
    declared = {
        name.strip().casefold()
        for name in (root.get("TreatAsLocalProperty") or "").split(LOCAL_PROPERTY_SEPARATOR)
        if name.strip()
    }
    if not declared:
        return [
            "TreatAsLocalProperty is not declared on the Project element - a command line passing "
            "-p:NuGetAudit=false would still beat the pin"
        ]

    missing = [name for key, (name, _) in POLICY.items() if key not in declared]
    if missing:
        return [
            f"TreatAsLocalProperty does not cover {', '.join(missing)} - a command line passing "
            f"-p:{missing[0]}=... would still beat the pin"
        ]
    return []


def _blind_spots(root: ElementTree.Element) -> list[str]:
    """Report the constructs that would let a guarded file pass while the policy does not hold."""
    problems: list[str] = []
    for element in root.iter():
        name = _local_name(element.tag).casefold()
        if name == "import":
            problems.append(
                f"Import of {element.get('Project', '?')!r} - this guard does not read imported "
                "files, so it cannot vouch for what they assign"
            )
        elif name == "nowarn":
            value = (element.text or "").upper()
            suppressed = [code for code in AUDIT_WARNING_CODES if code in value]
            if suppressed:
                problems.append(
                    f"NoWarn suppresses {', '.join(suppressed)} - the audit would run and report "
                    "nothing"
                )
    return problems


def _validate(path: Path, require_pin: bool) -> list[str]:
    """Check one MSBuild file; ``require_pin`` says whether it must carry the policy itself."""
    if not path.is_file():
        if require_pin:
            return [f"missing: {path} does not exist, so the audit pin cannot be guaranteed"]
        return []

    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as error:
        return [f"not well-formed XML: {error}"]

    if _local_name(root.tag) != "Project":
        return [f"root element is {root.tag}, expected an MSBuild Project"]

    problems = _policy_problems(_collect(root), require_pin)
    if require_pin:
        problems += _local_property_problems(root)
    return problems + _blind_spots(root)


def validate(path: Path) -> list[str]:
    """Return one message per problem in ``Directory.Build.props``.

    Empty means the pin is in place and no command line can override it.
    """
    return _validate(path, require_pin=True)


def validate_targets(path: Path) -> list[str]:
    """Return one message per problem in ``Directory.Build.targets``; a missing file is not one."""
    return _validate(path, require_pin=False)


def main(argv: list[str]) -> int:
    if argv:
        # A path named on the command line is judged by its file name: only the props file has to
        # carry the pin itself.
        targets: list[tuple[Path, bool]] = [
            (path, path.name.casefold() != TARGETS_FILE_NAME.casefold())
            for path in (Path(argument) for argument in argv)
        ]
    else:
        targets = [
            (Path.cwd() / PROPS_FILE_NAME, True),
            (Path.cwd() / TARGETS_FILE_NAME, False),
        ]

    failed = False
    for path, require_pin in targets:
        problems = _validate(path, require_pin)
        if problems:
            failed = True
            print(f"{path}: {len(problems)} problem(s)")
            for problem in problems:
                print(f"  {problem}")
        elif path.is_file():
            print(f"{path}: OK")
        else:
            print(f"{path}: absent, nothing to check")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
