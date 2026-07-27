#!/usr/bin/env python3
"""Validate the ReSharper settings layers this repository shares with its consumers.

A team-shared layer is a XAML ResourceDictionary. Consumers inject it, so anything wrong
here propagates to every repository that bumps the submodule - and stays invisible, because
nothing builds this file. The checks below cover the three ways it has actually broken:

* duplicate ``x:Key`` - the dictionary is then formally invalid and ReSharper may drop the layer,
* absolute filesystem paths - one machine's profile or cache directory forced on everyone,
* personal ReSharper state - what the IDE serializes here when the save layer is set to
  "Solution team-shared" instead of a personal one.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ElementTree
from collections import Counter
from pathlib import Path

XAML_NAMESPACE = "http://schemas.microsoft.com/winfx/2006/xaml"
KEY_ATTRIBUTE = f"{{{XAML_NAMESPACE}}}Key"
RESOURCE_DICTIONARY = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}ResourceDictionary"

ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]|(?:^|[\s\"'>])/(?:home|Users)/")

# Keys ReSharper writes to whichever layer is selected for saving. None of them is configuration:
# they are one-shot markers, window geometry, telemetry consent or per-install bookkeeping.
#
# Deliberately NOT listed: /Default/Environment/SettingsMigration/IsMigratorApplied/. ReSharper writes
# those markers back into any layer it loads that is missing them - `jb inspectcode --settings=<this file>`
# re-adds a dozen of them, along with a UTF-8 BOM. Flagging them would fail CI for something the tool does
# by design, so they are tolerated; the churn they cause in diffs is the price.
PERSONAL_KEY_PREFIXES = (
    "/Default/Connection/XmlConnectionList",
    "/Default/Environment/ExternalSources/FirstTimeFormShown",
    "/Default/Environment/Feedback/",
    "/Default/Environment/Hierarchy/",
    "/Default/Environment/MemoryUsageIndicator/",
    "/Default/Environment/UpdatesManger/",
    "/Default/Housekeeping/GlobalSettingsUpgraded/",
    "/Default/Housekeeping/IntellisenseHousekeeping/",
    "/Default/Housekeeping/LiveTemplatesHousekeeping/",
    "/Default/Housekeeping/OptionsDialog/",
    "/Default/Housekeeping/RefactoringsMru/",
    "/Default/Housekeeping/TreeModelBrowserPanelPersistence/",
    "/Default/Housekeeping/UpgradeFromExceptionReport/",
    "/Default/SnapshotsStore/",
    "/Default/SubsystemManager/",
)


def validate(path: Path) -> list[str]:
    """Return one message per problem found in ``path``; an empty list means the file is clean."""
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as error:
        return [f"not well-formed XML: {error}"]

    problems: list[str] = []
    if root.tag != RESOURCE_DICTIONARY:
        problems.append(f"root element is {root.tag}, expected a wpf:ResourceDictionary")

    keys = [element.get(KEY_ATTRIBUTE) for element in root]
    for index, key in enumerate(keys):
        if key is None:
            problems.append(f"entry #{index + 1} ({root[index].tag}) has no x:Key")

    for key, count in sorted(Counter(key for key in keys if key).items()):
        if count > 1:
            problems.append(f"duplicate x:Key ({count} entries): {key}")

    for element, key in zip(root, keys):
        value = element.text or ""
        if ABSOLUTE_PATH.search(value):
            problems.append(f"absolute path in {key}: {value.strip()}")
        if key and key.startswith(PERSONAL_KEY_PREFIXES):
            problems.append(f"personal ReSharper state, not team configuration: {key}")

    return problems


def main(argv: list[str]) -> int:
    paths = [Path(argument) for argument in argv] or sorted(Path.cwd().glob("*.DotSettings"))
    if not paths:
        print("no .DotSettings file to validate", file=sys.stderr)
        return 1

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
