#!/usr/bin/env python3
"""Validate the packaging policy this repository ships to its consumers.

``IsPackable`` has no value while ``Directory.Build.targets`` is evaluated for a project that relies
on the SDK default; the pack targets set it to true later. A condition written as
``IsPackable == 'true'`` is therefore never true and everything under it is silently dead - that
is how packages shipped without XML docs, symbols and embedded sources. Nothing else in CI evaluates
MSBuild, so this reads the XML:

* No ``Condition`` in ``Directory.Build.targets`` may require ``IsPackable`` to equal ``true``; it
  has to test for "not false" instead.
* The Release packable group must still assign the properties that make a package complete.
* ``DocumentationFile`` and the SDK's private ``_DocumentationFileProduced`` must be set next to
  ``GenerateDocumentationFile``: the SDK derives both before the targets file is imported.
* ``Directory.Build.props`` must keep ``CS1591`` in ``WarningsNotAsErrors`` unconditionally, so a
  consumer's ``TreatWarningsAsErrors`` does not turn missing docs into errors.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

REQUIRED_PACKABLE_PROPERTIES = (
    "GenerateDocumentationFile",
    "IncludeSymbols",
    "SymbolPackageFormat",
    "EmbedAllSources",
    "GeneratePackageOnBuild",
    "PublishRepositoryUrl",
)
REQUIRED_DOCUMENT_PROPERTIES = ("DocumentationFile", "_DocumentationFileProduced")

# IsPackable compared for equality with true, however it is spelled or wrapped in ToLower().
POSITIVE_ISPACKABLE = re.compile(r"ispackable[^=!]*==\s*'true'", re.IGNORECASE)


def _local_name(tag: str) -> str:
    return tag.rpartition("}")[2]


def _root(path: Path) -> tuple[ElementTree.Element | None, list[str]]:
    if not path.is_file():
        return None, [f"missing: {path}"]
    try:
        return ElementTree.parse(path).getroot(), []
    except ElementTree.ParseError as error:
        return None, [f"{path.name}: not well-formed XML: {error}"]


def _assigned(group: ElementTree.Element) -> set[str]:
    return {_local_name(child.tag).casefold() for child in group}


def validate_targets(path: Path) -> list[str]:
    root, problems = _root(path)
    if root is None:
        return problems

    for element in root.iter():
        condition = element.get("Condition", "")
        if POSITIVE_ISPACKABLE.search(condition):
            problems.append(
                f"{_local_name(element.tag)} is conditioned on IsPackable == 'true', which is never "
                "true here (IsPackable is still empty); use != 'false'"
            )

    packable = [
        group
        for group in root
        if _local_name(group.tag) == "PropertyGroup"
        and "ispackable" in group.get("Condition", "").casefold()
        and "release" in group.get("Condition", "").casefold()
    ]
    assigned = set().union(*(_assigned(group) for group in packable)) if packable else set()
    for name in REQUIRED_PACKABLE_PROPERTIES + REQUIRED_DOCUMENT_PROPERTIES:
        if name.casefold() not in assigned:
            problems.append(f"no Release/IsPackable PropertyGroup assigns {name}")
    return problems


def validate_props(path: Path) -> list[str]:
    root, problems = _root(path)
    if root is None:
        return problems

    unconditional = [
        child
        for group in root
        if _local_name(group.tag) == "PropertyGroup" and "Condition" not in group.attrib
        for child in group
        if "Condition" not in child.attrib
    ]
    if not any(
        _local_name(child.tag).casefold() == "warningsnotaserrors" and "CS1591" in (child.text or "")
        for child in unconditional
    ):
        problems.append("WarningsNotAsErrors must unconditionally carry CS1591")
    for child in unconditional:
        if (
            _local_name(child.tag).casefold() == "generatedocumentationfile"
            and (child.text or "").strip().casefold() == "true"
        ):
            problems.append(
                "GenerateDocumentationFile is true for every project here, including the ones "
                "that set IsPackable=false later; decide it in the targets file"
            )
    return problems


def main(argv: list[str]) -> int:
    directory = Path(argv[0]) if argv else Path.cwd()
    failed = False
    for path, validator in (
        (directory / "Directory.Build.targets", validate_targets),
        (directory / "Directory.Build.props", validate_props),
    ):
        problems = validator(path)
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
