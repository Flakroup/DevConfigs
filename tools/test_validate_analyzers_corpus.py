#!/usr/bin/env python3
"""Tests for validate_analyzers_corpus. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from validate_analyzers_corpus import (  # noqa: E402
    decode_key,
    error_inspections,
    main,
    marker_at,
    markers_in,
    record,
    validate,
    verdicts,
)

LAYER = """<wpf:ResourceDictionary>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=CheckNamespace/@EntryIndexedValue">ERROR</s:String>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=CSharpWarnings_003A_003ACS0108_002CCS0114/@EntryIndexedValue">ERROR</s:String>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=UnusedMember_002ELocal/@EntryIndexedValue">WARNING</s:String>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=CheckNamespace/@EntryIndexedValue">ERROR</s:String>
</wpf:ResourceDictionary>"""

SOURCE = "using System;\n//# _header\n\n//# Alpha\nclass A { }\n//# Beta #1\nclass B { }\n"
MARKERS = {"A.cs": markers_in(SOURCE)}


def result(rule: str, uri: str, line: int) -> dict:
    return {"ruleId": rule, "locations": [{"physicalLocation": {
        "artifactLocation": {"uri": uri}, "region": {"startLine": line}}}]}


def sarif(*results: dict) -> dict:
    return {"runs": [{"results": list(results)}]}


def recorded(version: str) -> tuple[dict, dict]:
    """Record a one-file corpus through a stand-in for ReSharper; return the record and what it saw."""
    seen: dict = {}

    def inspect(copy: Path) -> tuple[dict, str]:
        seen["layer"] = (copy / "Corpus.sln.DotSettings").read_text(encoding="utf-8")
        seen["files"] = sorted(path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file())
        return sarif(result("CheckNamespace", "A.cs", 5)), version

    with tempfile.TemporaryDirectory() as directory:
        corpus = Path(directory) / "corpus"
        (corpus / "obj").mkdir(parents=True)
        (corpus / "A.cs").write_text(SOURCE, encoding="utf-8")
        (corpus / "expected.json").write_text("{}", encoding="utf-8")
        (corpus / "obj" / "Generated.cs").write_text("//# Alpha", encoding="utf-8")
        layer = Path(directory) / "FEx.sln.DotSettings"
        layer.write_text(LAYER, encoding="utf-8")
        return record(corpus, layer, inspect), seen


CASES = [
    ("a DotSettings key is unescaped",
     lambda: decode_key("CSharpWarnings_003A_003ACS0108_002CCS0114") == "CSharpWarnings::CS0108,CS0114"),
    ("only ERROR inspections are taken, decoded, deduplicated and sorted",
     lambda: error_inspections(LAYER) == ["CSharpWarnings::CS0108,CS0114", "CheckNamespace"]),
    ("markers carry their 1-based line and keep a variant suffix",
     lambda: MARKERS["A.cs"] == [(2, "_header"), (4, "Alpha"), (6, "Beta #1")]),
    ("a line above the first marker belongs to no sample",
     lambda: marker_at(MARKERS["A.cs"], 1) is None),
    ("a marker line belongs to its own sample",
     lambda: marker_at(MARKERS["A.cs"], 4) == "Alpha"),
    ("a line belongs to the nearest marker above it",
     lambda: marker_at(MARKERS["A.cs"], 5) == "Alpha"),
    ("a line after the last marker belongs to the last sample",
     lambda: marker_at(MARKERS["A.cs"], 99) == "Beta #1"),
    ("a report is filed under its file and sample; other ids are ignored",
     lambda: verdicts(sarif(result("Alpha", "A.cs", 5), result("Noise", "A.cs", 5)), MARKERS, ["Alpha", "Beta"])
     == {"Alpha": ["A.cs: Alpha"], "Beta": []}),
    ("a backslash path is matched against the forward-slash key",
     lambda: verdicts(sarif(result("Alpha", "Sub\\C.cs", 2)), {"Sub/C.cs": [(1, "Alpha")]}, ["Alpha"])
     == {"Alpha": ["Sub/C.cs: Alpha"]}),
    ("a report outside any sample is filed under its file, not dropped",
     lambda: verdicts(sarif(result("Alpha", "Mismatch.asmdef", 1)), MARKERS, ["Alpha"])
     == {"Alpha": ["Mismatch.asmdef"]}),
    ("repeated reports of one sample are recorded once",
     lambda: verdicts(sarif(result("Alpha", "A.cs", 4), result("Alpha", "A.cs", 5)), MARKERS, ["Alpha"])
     == {"Alpha": ["A.cs: Alpha"]}),
    ("a consistent record passes",
     lambda: validate({"inspections": {"Alpha": ["A.cs: Alpha"], "Beta": []}}, ["Alpha", "Beta"], MARKERS) == []),
    ("an ERROR inspection with no verdict is reported",
     lambda: validate({"inspections": {"Alpha": []}}, ["Alpha", "Beta"], MARKERS)
     == ["Beta: an ERROR inspection with no recorded verdict"]),
    ("a verdict for an inspection that is not ERROR is reported",
     lambda: validate({"inspections": {"Alpha": [], "Gone": []}}, ["Alpha"], MARKERS)
     == ["Gone: a recorded verdict for an inspection that is not ERROR"]),
    ("a marker repeated within one file is reported",
     lambda: validate({"inspections": {}}, [], {"A.cs": [(1, "Alpha"), (9, "Alpha")]})
     == ["A.cs:9: marker 'Alpha' repeats line 1"]),
    ("the same marker in two files is allowed",
     lambda: validate({"inspections": {}}, [], {"A.cs": [(1, "Alpha")], "B.cs": [(1, "Alpha")]}) == []),
    ("a cited sample missing from the corpus is reported",
     lambda: validate({"inspections": {"Alpha": ["B.cs: Alpha"]}}, ["Alpha"], MARKERS)
     == ["Alpha: cites 'B.cs: Alpha', which is no sample or file in the corpus"]),
    ("a cited file without markers passes",
     lambda: validate({"inspections": {"Alpha": ["A.cs"]}}, ["Alpha"], MARKERS) == []),
    ("a recording inspects a copy carrying the shared layer, without expected.json or build output",
     lambda: recorded("2026.2")[1] == {"layer": LAYER, "files": ["A.cs", "Corpus.sln.DotSettings"]}),
    ("a recording files ReSharper's reports under the ERROR inspections",
     lambda: recorded("2026.2")[0]["inspections"]
     == {"CSharpWarnings::CS0108,CS0114": [], "CheckNamespace": ["A.cs: Alpha"]}),
    ("a recording keeps only the version number from jb's banner",
     lambda: recorded("JetBrains Inspect Code\nVersion: 2026.2.3.1\n")[0]["resharper"] == "2026.2.3.1"),
    ("a banner without a version number records it as unknown",
     lambda: recorded("no version here")[0]["resharper"] == "unknown"),
    ("the committed corpus and expected.json agree with FEx.sln.DotSettings",
     lambda: main([]) == 0),
    ("an unknown argument is a usage error",
     lambda: main(["--recrod"]) == 2),
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
