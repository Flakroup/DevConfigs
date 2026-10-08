#!/usr/bin/env python3
"""Tests for validate_analyzers_corpus. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import validate_analyzers_corpus as module  # noqa: E402
from validate_analyzers_corpus import (  # noqa: E402
    corpus_markers,
    decode_key,
    digest,
    error_inspections,
    main,
    marker_at,
    markers_in,
    record,
    run_resharper,
    validate,
    verdicts,
)

LAYER = """<wpf:ResourceDictionary>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=CheckNamespace/@EntryIndexedValue">ERROR</s:String>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=CSharpWarnings_003A_003ACS0108_002CCS0114/@EntryIndexedValue">ERROR</s:String>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=UnusedMember_002ELocal/@EntryIndexedValue">WARNING</s:String>
\t<s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/=CheckNamespace/@EntryIndexedValue">ERROR</s:String>
</wpf:ResourceDictionary>"""

EDITORCONFIG = "root = true\n[*.cs]\ncsharp_prefer_braces = true\n"
BANNER = "JetBrains Inspect Code\nVersion: 2026.2.3.1\n"

SOURCE = "using System;\n//# _header\n\n//# Alpha\nclass A { }\n//# Beta #1\nclass B { }\n"
MARKERS = {"A.cs": markers_in(SOURCE)}


def result(rule: str, uri: str, line: int) -> dict:
    return {"ruleId": rule, "locations": [{"physicalLocation": {
        "artifactLocation": {"uri": uri}, "region": {"startLine": line}}}]}


def sarif(*results: dict) -> dict:
    return {"runs": [{"results": list(results)}]}


def consistent(**changes: object) -> dict:
    """A record that agrees with MARKERS and the digest "D", with any key replaced."""
    return {"digest": "D", "inspections": {"Alpha": ["A.cs: Alpha"], "Beta": ["A.cs: Beta #1"]}} | changes


def inputs(directory: Path, source: bytes = SOURCE.encode(), extra: dict[str, bytes] | None = None
           ) -> tuple[Path, Path, Path]:
    """A one-file corpus with build output and a record inside it, plus a layer and an editorconfig."""
    corpus = directory / "corpus"
    (corpus / "obj").mkdir(parents=True)
    (corpus / "A.cs").write_bytes(source)
    (corpus / "expected.json").write_text("{}", encoding="utf-8")
    (corpus / "obj" / "Generated.cs").write_text("//# Alpha", encoding="utf-8")
    for name, content in (extra or {}).items():
        (corpus / name).parent.mkdir(parents=True, exist_ok=True)
        (corpus / name).write_bytes(content)
    layer = directory / "FEx.sln.DotSettings"
    layer.write_text(LAYER, encoding="utf-8")
    editorconfig = directory / ".editorconfig"
    editorconfig.write_text(EDITORCONFIG, encoding="utf-8")
    return corpus, layer, editorconfig


def recorded(version: str) -> tuple[dict, dict]:
    """Record a one-file corpus through a stand-in for ReSharper; return the record and what it saw."""
    seen: dict = {}

    def inspect(copy: Path) -> tuple[dict, str]:
        seen["layer"] = (copy / "Corpus.sln.DotSettings").read_text(encoding="utf-8")
        seen["editorconfig"] = (copy / ".editorconfig").read_text(encoding="utf-8")
        seen["files"] = sorted(path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file())
        return sarif(result("CheckNamespace", "A.cs", 5)), version

    with tempfile.TemporaryDirectory() as directory:
        corpus, layer, editorconfig = inputs(Path(directory))
        content = record(corpus, layer, editorconfig, {"unsampled": {"Gone": "why"}, "silent": {"Quiet": "why"}},
                         inspect)
        seen["digest"] = digest(corpus, layer, editorconfig)
        return content, seen


def hashed(source: bytes = SOURCE.encode(), layer: str = LAYER, editorconfig: str = EDITORCONFIG,
           extra: dict[str, bytes] | None = None) -> str:
    """The digest of a one-file corpus, with any of its inputs swapped."""
    with tempfile.TemporaryDirectory() as directory:
        corpus, layer_path, editorconfig_path = inputs(Path(directory), source, extra)
        layer_path.write_text(layer, encoding="utf-8")
        editorconfig_path.write_text(editorconfig, encoding="utf-8")
        return digest(corpus, layer_path, editorconfig_path)


def bom_markers() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "A.cs").write_bytes(b"\xef\xbb\xbf//# Alpha\nclass A { }\n")
        return corpus_markers(Path(directory))


def resharper_calls() -> tuple[list[list[str]], tuple[dict, str]]:
    """Run run_resharper against a stand-in for subprocess.run; return the commands and its result."""
    calls: list[list[str]] = []

    class Completed:
        stdout = BANNER

    def run(command: list[str], **_: object) -> Completed:
        calls.append(command)
        for argument in command:
            if argument.startswith("--output="):
                Path(argument.removeprefix("--output=")).write_text(json.dumps(sarif()), encoding="utf-8-sig")
        return Completed()

    with tempfile.TemporaryDirectory() as directory:
        solution_directory = Path(directory) / "Corpus"
        solution_directory.mkdir()
        return calls, run_resharper(solution_directory, run)


@contextlib.contextmanager
def patched(**values: object):
    saved = {name: getattr(module, name) for name in values}
    for name, value in values.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def main_record(existing: str | None) -> tuple[int, bytes, dict]:
    """Run main(['--record']) with a stand-in recorder; return the exit code, the bytes written and its input."""
    given: dict = {}

    def fake_record(corpus: Path, dotsettings: Path, editorconfig: Path, reasons: dict) -> dict:
        given.update(corpus=corpus, dotsettings=dotsettings, editorconfig=editorconfig, reasons=reasons)
        return {"resharper": "1.0", **reasons, "inspections": {"Alpha": ["A.cs: Alpha"]}}

    with tempfile.TemporaryDirectory() as directory:
        expected = Path(directory) / "expected.json"
        if existing is not None:
            expected.write_text(existing, encoding="utf-8")
        with patched(ROOT=Path(directory), EXPECTED=expected, record=fake_record):
            code = main(["--record"])
        return code, expected.read_bytes(), given


CASES = [
    ("a DotSettings key is unescaped",
     lambda: decode_key("CSharpWarnings_003A_003ACS0108_002CCS0114") == "CSharpWarnings::CS0108,CS0114"),
    ("only ERROR inspections are taken, decoded, deduplicated and sorted",
     lambda: error_inspections(LAYER) == ["CSharpWarnings::CS0108,CS0114", "CheckNamespace"]),
    ("markers carry their 1-based line and keep a variant suffix",
     lambda: MARKERS["A.cs"] == [(2, "_header"), (4, "Alpha"), (6, "Beta #1")]),
    ("a file opening with a byte order mark keeps its first marker",
     lambda: bom_markers() == {"A.cs": [(1, "Alpha")]}),
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
     lambda: validate(consistent(), ["Alpha", "Beta"], MARKERS, "D") == []),
    ("a record made on other inputs is reported",
     lambda: validate(consistent(), ["Alpha", "Beta"], MARKERS, "E")
     == ["digest: the corpus, FEx.sln.DotSettings or .editorconfig changed since the last recording"
         " - run --record"]),
    ("an ERROR inspection with no verdict is reported",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs: Alpha"]}), ["Alpha", "Beta"], MARKERS, "D")
     == ["Beta: an ERROR inspection with no recorded verdict"]),
    ("a verdict for an inspection that is not ERROR is reported",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs: Alpha"], "Gone": []}), ["Alpha"], MARKERS, "D")
     == ["Gone: a recorded verdict for an inspection that is not ERROR"]),
    ("an ERROR inspection with no sample and no reason is reported",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs: Alpha"], "Gamma": []}), ["Alpha", "Gamma"], MARKERS, "D")
     == ["Gamma: an ERROR inspection with no sample and no reason under unsampled"]),
    ("an empty reason does not excuse a missing sample",
     lambda: validate(consistent(inspections={"Gamma": []}, unsampled={"Gamma": ""}), ["Gamma"], MARKERS, "D")
     == ["Gamma: an ERROR inspection with no sample and no reason under unsampled"]),
    ("an ERROR inspection with no sample and a reason passes",
     lambda: validate(consistent(inspections={"Gamma": []}, unsampled={"Gamma": "XAML only"}), ["Gamma"],
                      MARKERS, "D") == []),
    ("a variant marker alone samples its inspection",
     lambda: validate(consistent(inspections={"Beta": ["A.cs: Beta #1"]}), ["Beta"], MARKERS, "D") == []),
    ("an unsampled entry for an inspection with a sample is reported",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs: Alpha"]}, unsampled={"Alpha": "why"}), ["Alpha"],
                      MARKERS, "D") == ["Alpha: listed under unsampled, but the corpus has a sample"]),
    ("a blank or non-text unsampled reason does not excuse a missing sample",
     lambda: all(validate(consistent(inspections={"Gamma": []}, unsampled={"Gamma": reason}), ["Gamma"], MARKERS,
                          "D") == ["Gamma: an ERROR inspection with no sample and no reason under unsampled"]
                 for reason in (" ", True))),
    ("an unsampled entry for an inspection that is not ERROR is reported",
     lambda: validate(consistent(inspections={}, unsampled={"Gone": "why"}), [], MARKERS, "D")
     == ["Gone: listed under unsampled, but it is not an ERROR inspection"]),
    ("a sampled inspection ReSharper reports nowhere needs a reason under silent",
     lambda: validate(consistent(inspections={"Beta": []}), ["Beta"], MARKERS, "D")
     == ["Beta: ReSharper reports none of its samples and silent gives no reason"]),
    ("an empty silent reason does not excuse it",
     lambda: validate(consistent(inspections={"Beta": []}, silent={"Beta": ""}), ["Beta"], MARKERS, "D")
     == ["Beta: ReSharper reports none of its samples and silent gives no reason"]),
    ("a blank or non-text silent reason does not excuse it",
     lambda: all(validate(consistent(inspections={"Beta": []}, silent={"Beta": reason}), ["Beta"], MARKERS, "D")
                 == ["Beta: ReSharper reports none of its samples and silent gives no reason"]
                 for reason in (" ", True))),
    ("an inspection reported only in another rule's sample still needs a reason under silent",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs: Alpha"], "Beta": ["A.cs: Alpha"]}), ["Alpha", "Beta"],
                      MARKERS, "D") == ["Beta: ReSharper reports none of its samples and silent gives no reason"]),
    ("a silent inspection may be reported in another rule's sample",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs: Alpha"], "Beta": ["A.cs: Alpha"]},
                                 silent={"Beta": "quiet"}), ["Alpha", "Beta"], MARKERS, "D") == []),
    ("silent problems come out sorted by inspection",
     lambda: validate(consistent(inspections={"Beta": [], "Alpha": []}, silent={"Zeta": "q", "Gone": "q"}),
                      ["Alpha", "Beta"], MARKERS, "D")
     == ["Alpha: ReSharper reports none of its samples and silent gives no reason",
         "Beta: ReSharper reports none of its samples and silent gives no reason",
         "Gone: listed under silent, but it is not an ERROR inspection",
         "Zeta: listed under silent, but it is not an ERROR inspection"]),
    ("a sampled inspection ReSharper reports nowhere passes with a reason under silent",
     lambda: validate(consistent(inspections={"Beta": []}, silent={"Beta": "superseded"}), ["Beta"], MARKERS, "D")
     == []),
    ("a silent entry for an inspection ReSharper reports is reported",
     lambda: validate(consistent(inspections={"Beta": ["A.cs: Beta #1"]}, silent={"Beta": "quiet"}), ["Beta"],
                      MARKERS, "D") == ["Beta: listed under silent, but ReSharper reports one of its samples"]),
    ("a silent entry for an inspection that is not ERROR is reported",
     lambda: validate(consistent(inspections={}, silent={"Gone": "quiet"}), [], MARKERS, "D")
     == ["Gone: listed under silent, but it is not an ERROR inspection"]),
    ("a silent entry for an inspection with no sample is reported",
     lambda: validate(consistent(inspections={"Gamma": []}, unsampled={"Gamma": "XAML only"},
                                 silent={"Gamma": "quiet"}), ["Gamma"], MARKERS, "D")
     == ["Gamma: listed under silent, but the corpus has no sample"]),
    ("a marker repeated within one file is reported",
     lambda: validate(consistent(inspections={}), [], {"A.cs": [(1, "Alpha"), (9, "Alpha")]}, "D")
     == ["A.cs:9: marker 'Alpha' repeats line 1"]),
    ("the same marker in two files is allowed",
     lambda: validate(consistent(inspections={}), [], {"A.cs": [(1, "Alpha")], "B.cs": [(1, "Alpha")]}, "D")
     == []),
    ("a cited sample missing from the corpus is reported",
     lambda: validate(consistent(inspections={"Alpha": ["B.cs: Alpha"]}), ["Alpha"], MARKERS, "D")
     == ["Alpha: cites 'B.cs: Alpha', which is no sample or file in the corpus"]),
    ("a cited file without markers passes",
     lambda: validate(consistent(inspections={"Alpha": ["A.cs", "A.cs: Alpha"]}), ["Alpha"], MARKERS, "D") == []),
    ("the digest ignores line endings",
     lambda: hashed(SOURCE.replace("\n", "\r\n").encode()) == hashed()),
    ("the digest changes with a sample",
     lambda: hashed(SOURCE.replace("class A", "class Z").encode()) != hashed()),
    ("the digest changes with a file that is not C#",
     lambda: hashed(extra={"Mismatch.asmdef": b"{}"}) != hashed()),
    ("the digest changes with the shared layer",
     lambda: hashed(layer=LAYER.replace("WARNING", "ERROR")) != hashed()),
    ("the digest changes with the editorconfig",
     lambda: hashed(editorconfig=EDITORCONFIG.replace("true\n", "false\n")) != hashed()),
    ("the digest ignores build output and the record itself",
     lambda: hashed(extra={"bin/Corpus.dll": b"x", "obj/Other.cs": b"x", "expected.json": b"[]"}) == hashed()),
    ("a recording inspects a copy with the shared layer and the editorconfig, without the record or build output",
     lambda: {key: value for key, value in recorded("2026.2")[1].items() if key != "digest"}
     == {"layer": LAYER, "editorconfig": EDITORCONFIG,
         "files": [".editorconfig", "A.cs", "Corpus.sln.DotSettings"]}),
    ("a recording files ReSharper's reports under the ERROR inspections",
     lambda: recorded("2026.2")[0]["inspections"]
     == {"CSharpWarnings::CS0108,CS0114": [], "CheckNamespace": ["A.cs: Alpha"]}),
    ("a recording stores the digest of its inputs and keeps the unsampled and silent reasons",
     lambda: (lambda pair: pair[0]["digest"] == pair[1]["digest"] and pair[0]["unsampled"] == {"Gone": "why"}
              and pair[0]["silent"] == {"Quiet": "why"})(recorded("2026.2"))),
    ("a recording keeps only the version number from jb's banner",
     lambda: recorded(BANNER)[0]["resharper"] == "2026.2.3.1"),
    ("a banner without a version number records it as unknown",
     lambda: recorded("no version here")[0]["resharper"] == "unknown"),
    ("ReSharper is asked for solution-wide ERROR results as SARIF, without building the violating samples",
     lambda: (lambda command: command[:2] == ["jb", "inspectcode"]
              and {"--swea", "--severity=ERROR", "--format=Sarif", "--no-build"} <= set(command))(
         next(c for c in resharper_calls()[0] if c[:2] == ["jb", "inspectcode"]))),
    ("the copy is restored before ReSharper inspects it: --no-build resolves no packages of its own",
     lambda: (lambda heads: heads.index(["dotnet", "restore"]) < heads.index(["jb", "inspectcode"]))(
         [c[:2] for c in resharper_calls()[0]])),
    ("ReSharper's report and version are returned",
     lambda: resharper_calls()[1] == (sarif(), BANNER)),
    ("--record writes LF-only JSON with a two-space indent and carries the unsampled and silent reasons",
     lambda: main_record('{"unsampled": {"X": "why"}, "silent": {"Y": "quiet"}}')[:2] == (0, json.dumps(
         {"resharper": "1.0", "unsampled": {"X": "why"}, "silent": {"Y": "quiet"},
          "inspections": {"Alpha": ["A.cs: Alpha"]}}, indent=2).encode() + b"\n")),
    ("--record without a previous record starts with no reasons",
     lambda: main_record(None)[2]["reasons"] == {"unsampled": {}, "silent": {}}),
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
