#!/usr/bin/env python3
"""Guard the analyzers corpus and record ReSharper's verdicts on it.

``analyzers-corpus/`` holds labelled violations of the inspections that ``FEx.sln.DotSettings`` promotes to
ERROR. Each sample starts with a ``//# <inspection id>`` marker line (``//# <id> #2`` for a second variant)
and runs until the next marker; a marker is unique within its file, and two files may carry the same rule in
different layouts. ``analyzers-corpus/expected.json`` records, for every ERROR inspection, the samples
ReSharper reported it in, as ``<file>: <marker>`` - an empty list means ReSharper reported it nowhere in the
corpus. An ERROR inspection with no sample is named under ``unsampled`` with the reason, and one ReSharper
reports in none of its own samples is named under ``silent`` with the reason it stays quiet. Once ReSharper
is gone that file is the specification the Roslyn rules are measured against, so it must stay complete and
consistent with the corpus.

Default mode (CI, no ReSharper needed): check that ``expected.json`` names exactly the ERROR inspections
of ``FEx.sln.DotSettings``, that each has a sample or a reason, that each rule ReSharper reports in none
of its own samples has a reason, that no file repeats a marker, that every sample it cites exists, and that
its ``digest`` still matches the inputs it was recorded on.

``--record`` (needs ``jb`` from ``JetBrains.ReSharper.GlobalTools`` and the .NET SDK): inspect a copy of
the corpus with the shared layer as its solution settings and this repository's ``.editorconfig`` beside it,
as a consumer sees both, and rewrite ``expected.json``. The copy keeps ReSharper from rewriting the shared
layer.
"""

from __future__ import annotations

import hashlib
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
EDITORCONFIG = ROOT / ".editorconfig"
NOT_INPUTS = ("bin", "obj", "expected.json")

ERROR_SEVERITY = re.compile(r'InspectionSeverities/=([^/"]+)/@EntryIndexedValue">ERROR<')
ESCAPE = re.compile(r"_([0-9A-F]{4})")
MARKER = re.compile(r"^//# (.+?)\s*$")
VARIANT = re.compile(r" #\d+$")


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
    return {path.relative_to(corpus).as_posix(): markers_in(path.read_text(encoding="utf-8-sig"))
            for path in sorted(corpus.rglob("*.cs"))}


def digest(corpus: Path, dotsettings: Path, editorconfig: Path) -> str:
    """SHA-256 over every input of a recording, line endings normalised so a CRLF checkout hashes like LF."""
    inputs = sorted((path.relative_to(corpus).as_posix(), path) for path in corpus.rglob("*")
                    if path.is_file() and not set(path.relative_to(corpus).parts) & set(NOT_INPUTS))
    inputs += [("FEx.sln.DotSettings", dotsettings), (".editorconfig", editorconfig)]
    hasher = hashlib.sha256()
    for name, path in inputs:
        hasher.update(name.encode("utf-8") + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return hasher.hexdigest()


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
                # A report outside any marker (a project-level one, or one in a file that cannot carry a
                # marker) is filed under the file itself, so it is still recorded rather than silently dropped.
                found[result["ruleId"]].add(f"{path}: {owner}" if owner is not None else path)
    return {inspection: sorted(owners) for inspection, owners in found.items()}


def explained(reasons: dict, inspection: str) -> bool:
    """Whether ``reasons`` gives an inspection a reason - a string with something in it."""
    reason = reasons.get(inspection)
    return isinstance(reason, str) and bool(reason.strip())


def own_samples(inspection: str, owners: list[str]) -> list[str]:
    """The owners that are samples of the inspection itself, not reports it makes in another rule's sample."""
    return [owner for owner in owners if VARIANT.sub("", owner.partition(": ")[2]) == inspection]


def validate(expected: dict, inspections: list[str], markers: dict[str, list[tuple[int, str]]],
             inputs: str) -> list[str]:
    """Problems with ``expected.json`` against the shared layer and the corpus; empty when consistent."""
    problems = []
    if expected.get("digest") != inputs:
        problems.append("digest: the corpus, FEx.sln.DotSettings or .editorconfig changed since the last "
                        "recording - run --record")
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

    sampled = {VARIANT.sub("", marker) for file_markers in markers.values() for _, marker in file_markers}
    unsampled = expected.get("unsampled", {})
    for inspection in inspections:
        if inspection not in sampled and not explained(unsampled, inspection):
            problems.append(f"{inspection}: an ERROR inspection with no sample and no reason under unsampled")
    for inspection in sorted(unsampled):
        if inspection in sampled:
            problems.append(f"{inspection}: listed under unsampled, but the corpus has a sample")
        elif inspection not in inspections:
            problems.append(f"{inspection}: listed under unsampled, but it is not an ERROR inspection")

    silent = expected.get("silent", {})
    for inspection, owners in sorted(expected.get("inspections", {}).items()):
        if inspection in sampled and not own_samples(inspection, owners) and not explained(silent, inspection):
            problems.append(f"{inspection}: ReSharper reports none of its samples and silent gives no reason")
    for inspection in sorted(silent):
        if own_samples(inspection, expected.get("inspections", {}).get(inspection, [])):
            problems.append(f"{inspection}: listed under silent, but ReSharper reports one of its samples")
        elif inspection not in inspections:
            problems.append(f"{inspection}: listed under silent, but it is not an ERROR inspection")
        elif inspection not in sampled:
            problems.append(f"{inspection}: listed under silent, but the corpus has no sample")

    for inspection, owners in sorted(expected.get("inspections", {}).items()):
        for owner in owners:
            if owner not in seen and owner not in markers:
                problems.append(f"{inspection}: cites '{owner}', which is no sample or file in the corpus")
    return problems


def run_resharper(solution_directory: Path, run=subprocess.run) -> tuple[dict, str]:
    """Inspect the project in a directory as ``Corpus.sln``; return the SARIF report and ``jb``'s version."""
    solution = solution_directory / "Corpus.sln"
    run(["dotnet", "new", "sln", "--format", "sln", "--name", "Corpus", "--output", str(solution_directory)],
        check=True, capture_output=True)
    run(["dotnet", "sln", str(solution), "add", str(solution_directory / "Corpus.csproj")],
        check=True, capture_output=True)
    report = solution_directory.parent / "report.sarif"
    run(["jb", "inspectcode", str(solution), "--swea", "--severity=ERROR", "--format=Sarif", f"--output={report}"],
        check=True)
    version = run(["jb", "inspectcode", "--version"], capture_output=True, text=True).stdout
    return json.loads(report.read_text(encoding="utf-8-sig")), version


def record(corpus: Path, dotsettings: Path, editorconfig: Path, reasons: dict, inspect=run_resharper) -> dict:
    """Inspect a copy of the corpus with the shared layer and editorconfig; return ``expected.json``'s content.

    ``reasons`` carries the hand-written ``unsampled`` and ``silent`` maps over unchanged."""
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / "Corpus"
        shutil.copytree(corpus, copy, ignore=shutil.ignore_patterns(*NOT_INPUTS))
        shutil.copyfile(dotsettings, copy / "Corpus.sln.DotSettings")
        shutil.copyfile(editorconfig, copy / ".editorconfig")
        sarif, version = inspect(copy)
        markers = corpus_markers(copy)

    version_number = re.search(r"\d+(?:\.\d+)+", version)
    inspections = error_inspections(dotsettings.read_text(encoding="utf-8-sig"))
    return {"resharper": version_number.group(0) if version_number else "unknown",
            "digest": digest(corpus, dotsettings, editorconfig),
            "unsampled": reasons.get("unsampled", {}),
            "silent": reasons.get("silent", {}),
            "inspections": verdicts(sarif, markers, inspections)}


def main(arguments: list[str]) -> int:
    if arguments == ["--record"]:
        previous = json.loads(EXPECTED.read_text(encoding="utf-8")) if EXPECTED.exists() else {}
        content = record(CORPUS, DOTSETTINGS, EDITORCONFIG,
                         {key: previous.get(key, {}) for key in ("unsampled", "silent")})
        # LF on every platform, so a recording on Windows diffs only where a verdict changed.
        EXPECTED.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Recorded {EXPECTED.relative_to(ROOT)}")
        return 0
    if arguments:
        print("usage: validate_analyzers_corpus.py [--record]", file=sys.stderr)
        return 2

    problems = validate(json.loads(EXPECTED.read_text(encoding="utf-8")),
                        error_inspections(DOTSETTINGS.read_text(encoding="utf-8-sig")),
                        corpus_markers(CORPUS), digest(CORPUS, DOTSETTINGS, EDITORCONFIG))
    for problem in problems:
        print(problem, file=sys.stderr)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
