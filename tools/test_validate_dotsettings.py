#!/usr/bin/env python3
"""Tests for validate_dotsettings. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from validate_dotsettings import validate  # noqa: E402

HEADER = (
    '<wpf:ResourceDictionary xml:space="preserve" '
    'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" '
    'xmlns:s="clr-namespace:System;assembly=mscorlib" '
    'xmlns:wpf="http://schemas.microsoft.com/winfx/2006/xaml/presentation">'
)


def check(entries: str, expected: str | None, label: str) -> bool:
    """Run the validator over a throwaway file and assert on the first problem it reports."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "Sample.sln.DotSettings"
        path.write_text(f"{HEADER}\n{entries}\n</wpf:ResourceDictionary>", encoding="utf-8")
        problems = validate(path)

    if expected is None:
        passed = not problems
        detail = "clean" if passed else f"unexpected: {problems}"
    else:
        passed = any(expected in problem for problem in problems)
        detail = "reported" if passed else f"expected {expected!r}, got {problems}"

    print(f"{'PASS' if passed else 'FAIL'}  {label}: {detail}")
    return passed


ABBREVIATION = '/Default/CodeStyle/Naming/CSharpNaming/Abbreviations'

CASES = [
    (
        f'\t<s:String x:Key="{ABBREVIATION}/=IE/@EntryIndexedValue">IE</s:String>',
        None,
        "a well-formed entry passes",
    ),
    (
        f'\t<s:String x:Key="{ABBREVIATION}/=IE/@EntryIndexedValue">DI</s:String>\n'
        f'\t<s:String x:Key="{ABBREVIATION}/=IE/@EntryIndexedValue">IE</s:String>',
        "duplicate x:Key",
        "two entries sharing a key are rejected",
    ),
    (
        '\t<s:String x:Key="/Default/SnapshotsStore/CurrentStore/@EntryValue">'
        "C:\\Users\\Someone\\AppData\\Local\\JetBrains</s:String>",
        "absolute path",
        "a Windows profile path is rejected",
    ),
    (
        '\t<s:String x:Key="/Default/Environment/Hierarchy/PsiConfigurationSettingsKey'
        '/CustomLocation/@EntryValue">C:\\_R#Cache</s:String>',
        "absolute path",
        "a forced cache location is rejected",
    ),
    (
        '\t<s:String x:Key="/Default/Environment/ExternalSources/Home/@EntryValue">'
        "/home/someone/sources</s:String>",
        "absolute path",
        "a POSIX home path is rejected",
    ),
    (
        '\t<s:Boolean x:Key="/Default/Environment/SettingsMigration/IsMigratorApplied'
        '/=SomeMigration/@EntryIndexedValue">True</s:Boolean>',
        None,
        "a settings-migration marker is tolerated - ReSharper re-adds it on every load",
    ),
    (
        '\t<s:String x:Key="/Default/Housekeeping/OptionsDialog/SelectedPageId/@EntryValue">'
        "EnvironmentGeneral</s:String>",
        "personal ReSharper state",
        "last-opened options page is rejected",
    ),
    (
        '\t<s:Boolean x:Key="/Default/Housekeeping/VsHighlighting/ImportVsSquiggles'
        '/@EntryValue">False</s:Boolean>',
        None,
        "deliberate VsHighlighting configuration is kept",
    ),
    (
        '\t<s:String x:Key="/Default/Environment/General/Timeout/@EntryValue">4000</s:String>',
        None,
        "deliberate Environment/General configuration is kept",
    ),
    (
        f'\t<s:String x:Key="{ABBREVIATION}/=IE/@EntryIndexedValue">IE',
        "not well-formed XML",
        "an unclosed element is rejected",
    ),
    (
        "\t<s:String>IE</s:String>",
        "has no x:Key",
        "an entry without x:Key is rejected",
    ),
]


def main() -> int:
    results = [check(entries, expected, label) for entries, expected, label in CASES]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
