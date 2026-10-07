#!/usr/bin/env python3
"""Guard the analyzers corpus and record ReSharper's verdicts on it.

``analyzers-corpus/`` holds one labelled violation per inspection that ``FEx.sln.DotSettings``
promotes to ERROR. Each sample starts with a ``//# <inspection id>`` marker line (``//# <id> #2`` for a
second variant) and runs until the next marker; a marker is unique within its file, and two files may
carry the same rule in different layouts. ``analyzers-corpus/expected.json`` records, for every ERROR
inspection, the samples ReSharper reported it in, as ``<file>: <marker>`` - an empty list means ReSharper
reported it nowhere in the corpus. Once ReSharper is gone that file is the specification the Roslyn rules are
measured against, so it must stay complete and consistent with the corpus.

Default mode (CI, no ReSharper needed): check that ``expected.json`` names exactly the ERROR inspections
of ``FEx.sln.DotSettings``, that no file repeats a marker, and that every sample it cites exists.

``--record`` (needs ``jb`` from ``JetBrains.ReSharper.GlobalTools`` and the .NET SDK): inspect a copy of
the corpus with the shared layer as its solution settings, and rewrite ``expected.json``. The copy keeps
ReSharper from rewriting the shared layer and from reading this repository's ``.editorconfig``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "analyzers-corpus"
EXPECTED = CORPUS / "expected.json"
DOTSETTINGS = ROOT / "FEx.sln.DotSettings"

ERROR_SEVERITY = re.compile(r'InspectionSeverities/=([^/"]+)/@EntryIndexedValue">ERROR<')
ESCAPE = re.compile(r"_([0-9A-F]{4})")
MARKER = re.compile(r"^//# (.+?)\s*$")


def decode_key(key: str) -> str:
    """Undo the DotSettings key escaping: ``_003A`` is ``:``, ``_002E`` is ``.``."""
    return ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), key)


def error_inspections(dotsettings: str) -> list[str]:
    """The inspection ids a DotSettings layer sets to ERROR, decoded and sorted."""
    return sorted({decode_key(key) for key in ERROR_SEVERITY.findall(dotsettings)})


def markers_in(source: str) -> list[tuple[int, str]]:
    """``(line, marker)`` for every marker line of one file, 1-based like SARIF."""
    return [(number, match.group(1))
            for number, line in enumerate(source.splitlines(), start=1)
            if (match := MARKER.match(line))]


def marker_at(markers: list[tuple[int, str]], line: int) -> str | None:
    """The sample a line belongs to: the nearest marker at or above it, or None above the first."""
    owner = None
    for number, marker in markers:
        if number > line:
            break
        owner = marker
    return owner


def corpus_markers(corpus: Path) -> dict[str, list[tuple[int, str]]]:
    """Markers of every C# file in the corpus, keyed by the path relative to it (forward slashes)."""
    return {path.relative_to(corpus).as_posix(): markers_in(path.read_text(encoding="utf-8"))
            for path in sorted(corpus.rglob("*.cs"))}


def verdicts(sarif: dict, markers: dict[str, list[tuple[int, str]]], inspections: list[str]) -> dict[str, list[str]]:
    """For each inspection, the sorted samples (``<file>: <marker>``) ReSharper reported it in. Other ids are ignored."""
    found: dict[str, set[str]] = {inspection: set() for inspection in inspections}
    for run in sarif["runs"]:
        for result in run["results"]:
            if result["ruleId"] not in found:
                continue
            for location in result["locations"]:
                physical = location["physicalLocation"]
                path = physical["artifactLocation"]["uri"].replace("\\", "/")
                owner = marker_at(markers.get(path, []), physical["region"]["startLine"])
                # A report outside any marker (a project-level one such as the asmdef check) is filed
                # under the file itself, so it is still recorded rather than silently dropped.
                found[result["ruleId"]].add(f"{path}: {owner}" if owner is not None else path)
    return {inspection: sorted(owners) for inspection, owners in found.items()}


def validate(expected: dict, inspections: list[str], markers: dict[str, list[tuple[int, str]]]) -> list[str]:
    """Problems with ``expected.json`` against the shared layer and the corpus; empty when consistent."""
    problems = []
    recorded = set(expected.get("inspections", {}))
    for missing in sorted(set(inspections) - recorded):
        problems.append(f"{missing}: an ERROR inspection with no recorded verdict")
    for extra in sorted(recorded - set(inspections)):
        problems.append(f"{extra}: a recorded verdict for an inspection that is not ERROR")

    seen: dict[str, int] = {}
    for path, file_markers in markers.items():
        for line, marker in file_markers:
            sample = f"{path}: {marker}"
            if sample in seen:
                problems.append(f"{path}:{line}: marker '{marker}' repeats line {seen[sample]}")
            else:
                seen[sample] = line

    for inspection, owners in sorted(expected.get("inspections", {}).items()):
        for owner in owners:
            if owner not in seen and owner not in markers:
                problems.append(f"{inspection}: cites '{owner}', which is no sample or file in the corpus")
    return problems


def run_resharper(solution_directory: Path) -> tuple[dict, str]:
    """Inspect the project in a directory as ``Corpus.sln``; return the SARIF report and ``jb``'s version."""
    solution = solution_directory / "Corpus.sln"
    subprocess.run(["dotnet", "new", "sln", "--format", "sln", "--name", "Corpus",
                    "--output", str(solution_directory)], check=True, capture_output=True)
    subprocess.run(["dotnet", "sln", str(solution), "add", str(solution_directory / "Corpus.csproj")],
                   check=True, capture_output=True)
    report = solution_directory.parent / "report.sarif"
    subprocess.run(["jb", "inspectcode", str(solution), "--swea", "--severity=ERROR", "--format=Sarif",
                    f"--output={report}"], check=True)
    version = subprocess.run(["jb", "inspectcode", "--version"], capture_output=True, text=True).stdout
    return json.loads(report.read_text(encoding="utf-8-sig")), version


def record(corpus: Path, dotsettings: Path, inspect=run_resharper) -> dict:
    """Inspect a copy of the corpus with the shared layer as its settings; return ``expected.json``'s content."""
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / "Corpus"
        shutil.copytree(corpus, copy, ignore=shutil.ignore_patterns("bin", "obj", "expected.json"))
        shutil.copyfile(dotsettings, copy / "Corpus.sln.DotSettings")
        sarif, version = inspect(copy)
        markers = corpus_markers(copy)

    version_number = re.search(r"\d+(?:\.\d+)+", version)
    inspections = error_inspections(dotsettings.read_text(encoding="utf-8-sig"))
    return {"resharper": version_number.group(0) if version_number else "unknown",
            "inspections": verdicts(sarif, markers, inspections)}


def main(arguments: list[str]) -> int:
    if arguments == ["--record"]:
        EXPECTED.write_text(json.dumps(record(CORPUS, DOTSETTINGS), indent=2) + "\n", encoding="utf-8")
        print(f"Recorded {EXPECTED.relative_to(ROOT)}")
        return 0
    if arguments:
        print("usage: validate_analyzers_corpus.py [--record]", file=sys.stderr)
        return 2

    problems = validate(json.loads(EXPECTED.read_text(encoding="utf-8")),
                        error_inspections(DOTSETTINGS.read_text(encoding="utf-8-sig")),
                        corpus_markers(CORPUS))
    for problem in problems:
        print(problem, file=sys.stderr)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
