#!/usr/bin/env python3
"""Tests for validate_roslyn_parity. Plain asserts, no test framework - CI needs nothing installed.

The engines (dotnet build, dotnet format) are never run here: the verdict logic is pure and takes their output as
data, and the functions that shell out take an injected ``run`` or are patched."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import validate_roslyn_parity as module  # noqa: E402
from validate_analyzers_corpus import corpus_markers  # noqa: E402
from validate_roslyn_parity import (  # noqa: E402
    SDK_VERSION,
    analyzer_crashes,
    claim_work,
    engine_problems,
    evaluate,
    format_findings,
    headline,
    in_scope,
    main,
    map_problems,
    markdown,
    measure,
    outside,
    owner_of,
    propose,
    reasoned,
    relative_path,
    reused_sdk,
    rule_levels,
    run_engines,
    sarif_findings,
    sdk_status,
    sdk_version,
    sides,
    summary_lines,
)

MARKERS = {"A.cs": [(3, "Alpha"), (8, "Beta #1")]}

EXPECTED = {
    "inspections": {
        "Alpha": ["A.cs: Alpha"],
        "Beta": ["A.cs: Beta #1", "A.cs: Alpha"],
        "Quiet": [],
        "Missing": [],
    },
    "silent": {"Quiet": "ReSharper did not report it"},
    "unsampled": {"Missing": "no sample in the corpus"},
}
# Beta's R2 also reports the bare file A.cs, which ReSharper does not: that is the pinned outside difference.
EXACT = {"Alpha": {"roslyn": ["R1"]},
         "Beta": {"roslyn": ["R2"], "scope": "own", "reason": "R2 repeats Alpha",
                  "outside": {"extra": ["A.cs"], "missing": []}}}
FOUND = {"R1": {"A.cs: Alpha"}, "R2": {"A.cs: Beta #1", "A.cs: Alpha", "A.cs"}}
LEVELS = {"CS0001": {"error"}, "IDE0007": {"error", "suggestion"}, "FEX0001": {"error"}}
FORMAT = {rule: {"A.cs: Alpha"} for rule in ("IDE0001", "IDE0002", "IDE0003", "IDE0049")}
FORMAT_LEVELS = {rule: {"error"} for rule in FORMAT}


SDK = "10.0.401"


def mapping(**changes) -> dict:
    return {"sdk": SDK, "inspections": json.loads(json.dumps(EXACT)), "gaps": {}, "waivers": {}} | changes


def without_sdk() -> dict:
    return {key: value for key, value in mapping().items() if key != "sdk"}


def pin(*, extra=(), missing=(), reason="why") -> dict:
    """A gap or a waiver."""
    return {"reason": reason, "extra": list(extra), "missing": list(missing)}


def verdict(map_=None, errors=None, expected=None) -> dict:
    return evaluate(expected or EXPECTED, map_ or mapping(), FOUND if errors is None else errors)


def status(result: dict, inspection: str) -> str:
    return result["inspections"][inspection]["status"]


def has(result: dict, text: str) -> bool:
    return any(text in problem for problem in result["problems"])


def problems_of(map_: dict, compared=("Alpha", "Beta")) -> list[str]:
    return map_problems(map_, list(compared))


def sarif(*results: tuple[str, str, str, int], suppressed=()) -> dict:
    """A SARIF log of (rule, level, uri, line) results; a uri of None is a result with no location.

    ``suppressed`` holds the indexes of the results that carry a suppression."""
    return {"runs": [{"results": [
        {"ruleId": rule, "level": level, "message": {"text": f"{rule} message"},
         **({"locations": [{"physicalLocation": {
             "artifactLocation": {"uri": uri}, "region": {"startLine": line}}}]} if uri else {}),
         **({"suppressions": [{"kind": "inSource"}]} if index in suppressed else {})}
        for index, (rule, level, uri, line) in enumerate(results)]}]}


def in_corpus(callback):
    with tempfile.TemporaryDirectory() as folder:
        return callback(Path(folder).resolve())


@contextlib.contextmanager
def patched(**attributes):
    saved = {name: getattr(module, name) for name in attributes}
    for name, value in attributes.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def run_main(arguments, expected=EXPECTED, map_=None, errors=None, engine=None, summary=None, measuring=None,
             summary_before="", sdk=SDK, actions=False, stops=False):
    """Run main() on in-memory data; returns (exit code, stdout, stderr, files, folders) left in the scratch dir."""
    def go(folder: Path):
        expected_path, map_path = folder / "expected.json", folder / "roslyn-map.json"
        expected_path.write_text(json.dumps(expected), encoding="utf-8")
        map_path.write_text(json.dumps(map_ or mapping()), encoding="utf-8")
        saved = os.environ.pop("GITHUB_STEP_SUMMARY", None)
        saved_actions = os.environ.pop("GITHUB_ACTIONS", None)
        if actions:
            os.environ["GITHUB_ACTIONS"] = "true"
        if summary:
            os.environ["GITHUB_STEP_SUMMARY"] = str(folder / "step.md")
            (folder / "step.md").write_text(summary_before, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        fixed = lambda work, reuse: (FOUND if errors is None else errors, engine or [])  # noqa: E731
        try:
            with patched(EXPECTED=expected_path, MAP=map_path, measure=measuring or fixed,
                         sdk_version=lambda: sdk):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = main(arguments(folder) if callable(arguments) else arguments)
                    except SystemExit as stop:
                        if not stops:
                            raise
                        code = stop.code
        finally:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            os.environ.pop("GITHUB_ACTIONS", None)
            if saved_actions is not None:
                os.environ["GITHUB_ACTIONS"] = saved_actions
            if saved is not None:
                os.environ["GITHUB_STEP_SUMMARY"] = saved
        files = {path.name: path.read_text(encoding="utf-8") for path in folder.iterdir() if path.is_file()}
        folders = sorted(path.name for path in folder.iterdir() if path.is_dir())
        return code, out.getvalue(), err.getvalue(), files, folders
    return in_corpus(go)


def spied(arguments, make=False):
    """run_main() with a measure() spy: (run_main result, reuse flags, work directory names, work paths)."""
    reuse_flags, work_paths = [], []

    def measuring(work, reuse):
        reuse_flags.append(reuse)
        work_paths.append(work)
        if make:
            work.mkdir(parents=True, exist_ok=True)
            (work / "x.txt").write_text("x", encoding="utf-8")
        return FOUND, []
    result = run_main(arguments, measuring=measuring)
    return result, reuse_flags, [path.name for path in work_paths], work_paths


def exit_code(call):
    try:
        call()
    except SystemExit as exit_:
        return exit_.code
    return None


def fake_run(sarif_text="{}", report=True, build_code=1):
    """A stand-in for subprocess.run that writes what dotnet build and dotnet format would."""
    calls = []

    def run(command, **_):
        calls.append(command)
        if command[1] == "build":
            target = next(part for part in command if part.startswith("-p:ErrorLog=")).split("=", 1)[1].split("%2C")[0]
            if sarif_text is not None:
                Path(target).write_text(sarif_text, encoding="utf-8")
            return SimpleNamespace(returncode=build_code, stdout="", stderr="")
        if report:
            folder = Path(command[command.index("--report") + 1])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "format-report.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=2, stdout="", stderr="")
    run.calls = calls
    return run


def sdk_run(stdout="10.0.401\n", returncode=0, stderr=""):
    """A stand-in for subprocess.run that answers ``dotnet --version``; it keeps the call it saw."""
    def run(command, **options):
        run.seen = (command, options)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


def stopping(message="engines stopped"):
    """A measure() that dies the way run_engines does when dotnet writes no log."""
    def measure_stops(work, reuse):
        raise SystemExit(message)
    return measure_stops


def in_work(files: dict[str, str], extra=(), **options):
    """run_main() with --work pointing at the scratch folder itself, holding ``files`` already; sdk.txt shows up in
    the files it returns."""
    def arguments(folder: Path):
        for name, text in files.items():
            (folder / name).write_text(text, encoding="utf-8")
        return ["--work", str(folder), *extra]
    return run_main(arguments, **options)


def engines_in_scratch(run):
    def go(folder: Path):
        try:
            return run_engines(folder / "work", run=run), None
        except SystemExit as exit_:
            return None, str(exit_)
    return in_corpus(go)


SHARED_NAMES = ["Directory.Build.props", "Directory.Build.targets", ".editorconfig", "build.globalconfig",
                "BannedSymbols.txt", "FEx.Analyzers.globalconfig"]


def consumer_tree(inspect, prepare=None):
    """Build a consumer from a fake repository and corpus, and return ``inspect(work, project)``."""
    def go(folder: Path):
        corpus, repository = folder / "corpus", folder / "repo"
        corpus.mkdir()
        repository.mkdir()
        for name in SHARED_NAMES:
            (repository / name).write_text(f"shared {name}", encoding="utf-8")
        for name, text in (("Corpus.csproj", "<Project/>"), ("A.cs", "class A { }"), ("expected.json", "{}"),
                           ("roslyn-map.json", "{}"), ("Directory.Build.props", "<isolating/>"),
                           ("Directory.Build.targets", "<isolating/>")):
            (corpus / name).write_text(text, encoding="utf-8")
        (corpus / "obj").mkdir()
        (corpus / "obj" / "x.cs").write_text("junk", encoding="utf-8")
        if prepare:
            prepare(folder / "work")
        project = module.consumer(folder / "work", corpus, repository)
        return inspect(folder / "work", project)
    return in_corpus(go)


def populated(files: dict[str, str]):
    """A ``prepare`` for consumer_tree that fills the work directory first."""
    def prepare(work: Path):
        for name, text in files.items():
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return prepare


def claimed(files: dict[str, str]):
    """claim_work() on a directory holding ``files``: (exit message or None, directory listing after)."""
    def go(folder: Path):
        work = folder / "work"
        work.mkdir()
        populated(files)(work)
        try:
            claim_work(work)
            refusal = None
        except SystemExit as exit_:
            refusal = str(exit_)
        return refusal, sorted(path.name for path in work.iterdir())
    return in_corpus(go)


def measured(results, entries=(), reuse=True, run=None, work_files=True, suppressed=()):
    """measure() over a work directory holding a SARIF log and a format report; a tiny corpus supplies the markers."""
    def go(folder: Path):
        corpus, work = folder / "corpus", folder / "work"
        corpus.mkdir()
        (corpus / "A.cs").write_text("//# Alpha\nclass A { }\n//# Beta #1\nclass B { }\n", encoding="utf-8")
        uri = (work / "Corpus" / "A.cs").as_uri()
        (work / "format").mkdir(parents=True)
        if work_files:
            (work / "roslyn.sarif").write_text(json.dumps(sarif(*((r, level, uri if located else None, line)
                                                                  for r, level, located, line in results),
                                                                suppressed=suppressed)),
                                               encoding="utf-8")
        (work / "format" / "format-report.json").write_text(json.dumps([
            {"FilePath": str(work / "Corpus" / "A.cs"), "FileChanges": [
                {"DiagnosticId": rule, "LineNumber": line, "FormatDescription": f"{level} {rule}: text"}
                for rule, level, line in entries]}]), encoding="utf-8")
        with patched(run_engines=run or (lambda directory: 1)):
            return measure(work, reuse, corpus)
    return in_corpus(go)


CANARIES = [("CS0001", "error", True, 2), ("IDE0007", "error", True, 2), ("FEX0001", "error", True, 2)]
FORMAT_ENTRIES = [(rule, "error", 2) for rule in ("IDE0001", "IDE0002", "IDE0003", "IDE0049")]


def committed_map_is_structurally_sound() -> bool:
    expected = json.loads(module.EXPECTED.read_text(encoding="utf-8"))
    committed = json.loads(module.MAP.read_text(encoding="utf-8"))
    # No engine output here, so every verdict differs; what must hold is everything but the verdicts.
    verdicts = ("mismatch", "not the recorded one", "remove it", "hides nothing")
    return not [p for p in evaluate(expected, committed, {})["problems"] if not any(v in p for v in verdicts)]


CASES = [
    # --- locating a report in a sample
    ("owner_of names the nearest marker above a line", lambda: owner_of("A.cs", 5, MARKERS) == "A.cs: Alpha"),
    ("owner_of keeps the variant suffix", lambda: owner_of("A.cs", 9, MARKERS) == "A.cs: Beta #1"),
    ("owner_of gives the bare file above the first marker", lambda: owner_of("A.cs", 1, MARKERS) == "A.cs"),
    ("owner_of gives the bare file when the file has no markers", lambda: owner_of("B.cs", 4, MARKERS) == "B.cs"),
    ("relative_path reads a plain path under the corpus",
     lambda: in_corpus(lambda c: relative_path(str(c / "sub" / "A.cs"), c) == "sub/A.cs")),
    ("relative_path reads a file uri",
     lambda: in_corpus(lambda c: relative_path((c / "A.cs").as_uri(), c) == "A.cs")),
    ("relative_path drops a file outside the corpus",
     lambda: in_corpus(lambda c: relative_path(str(c.parent / "Other.cs"), c) is None)),
    ("relative_path drops a generated file under another root",
     lambda: in_corpus(lambda c: relative_path((c.parent / "obj" / "G.cs").as_uri(), c) is None)),

    # --- reading the SARIF log
    ("sarif_findings keeps what was reported at error and nothing lower",
     lambda: in_corpus(lambda c: sarif_findings(
         sarif(("R1", "error", (c / "A.cs").as_uri(), 5), ("R2", "warning", (c / "A.cs").as_uri(), 9)), c, MARKERS)
         == {"R1": {"A.cs: Alpha"}})),
    ("sarif_findings treats a result with no level as below error",
     lambda: in_corpus(lambda c: sarif_findings(
         {"runs": [{"results": [{"ruleId": "R1", "locations": [{"physicalLocation": {
             "artifactLocation": {"uri": (c / "A.cs").as_uri()}, "region": {"startLine": 5}}}]}]}]},
         c, MARKERS) == {})),
    ("sarif_findings drops a result with no location",
     lambda: in_corpus(lambda c: sarif_findings(sarif(("R1", "error", None, 0)), c, MARKERS) == {})),
    ("sarif_findings drops a result outside the corpus",
     lambda: in_corpus(lambda c: sarif_findings(
         sarif(("R1", "error", (c.parent / "G.cs").as_uri(), 5)), c, MARKERS) == {})),
    ("sarif_findings collects every run",
     lambda: in_corpus(lambda c: sarif_findings(
         {"runs": sarif(("R1", "error", (c / "A.cs").as_uri(), 5))["runs"]
          + sarif(("R1", "error", (c / "A.cs").as_uri(), 9))["runs"]}, c, MARKERS)
         == {"R1": {"A.cs: Alpha", "A.cs: Beta #1"}})),
    ("sarif_findings counts a suppressed result as not reported",
     lambda: in_corpus(lambda c: sarif_findings(
         sarif(("R1", "error", (c / "A.cs").as_uri(), 5), ("R2", "error", (c / "A.cs").as_uri(), 9), suppressed=(0,)),
         c, MARKERS) == {"R2": {"A.cs: Beta #1"}})),
    ("sarif_findings counts a result with an empty suppressions list as reported",
     lambda: in_corpus(lambda c: sarif_findings(
         {"runs": [{"results": [{"ruleId": "R1", "level": "error", "suppressions": [], "locations": [
             {"physicalLocation": {"artifactLocation": {"uri": (c / "A.cs").as_uri()},
                                   "region": {"startLine": 5}}}]}]}]}, c, MARKERS) == {"R1": {"A.cs: Alpha"}})),
    ("rule_levels collects levels over results with and without a location",
     lambda: rule_levels(sarif(("R1", "error", None, 0), ("R1", "warning", None, 0))) == {"R1": {"error", "warning"}}),
    ("rule_levels reads a missing level as warning", lambda: rule_levels(
        {"runs": [{"results": [{"ruleId": "R1"}]}]}) == {"R1": {"warning"}}),
    ("rule_levels ignores a suppressed result", lambda: rule_levels(
        sarif(("R1", "warning", None, 0), ("R1", "error", None, 0), suppressed=(0,))) == {"R1": {"error"}}),
    ("analyzer_crashes returns the message of every AD0001, once", lambda: analyzer_crashes(
        sarif(("AD0001", "warning", None, 0), ("AD0001", "warning", None, 0), ("R1", "error", None, 0)))
        == ["AD0001 message"]),
    ("analyzer_crashes keeps distinct messages and trims them", lambda: analyzer_crashes(
        {"runs": [{"results": [{"ruleId": "AD0001", "message": {"text": " first "}},
                               {"ruleId": "AD0001", "message": {"text": "second"}}]}]}) == ["first", "second"]),
    ("analyzer_crashes cuts a long exception", lambda: len(analyzer_crashes(
        {"runs": [{"results": [{"ruleId": "AD0001", "message": {"text": "x" * 5000}}]}]})[0]) == 2000),
    ("analyzer_crashes survives a result with no message", lambda: analyzer_crashes(
        {"runs": [{"results": [{"ruleId": "AD0001"}]}]}) == []),

    # --- reading the dotnet format report
    ("format_findings keeps only the rules a build skips, at error",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c / "A.cs"), "FileChanges": [
             {"DiagnosticId": "IDE0001", "LineNumber": 5, "FormatDescription": "error IDE0001: x"},
             {"DiagnosticId": "IDE0007", "LineNumber": 5, "FormatDescription": "error IDE0007: x"},
             {"DiagnosticId": "IDE0049", "LineNumber": 9, "FormatDescription": "error IDE0049: x"}]}], c, MARKERS)
         == ({"IDE0001": {"A.cs: Alpha"}, "IDE0049": {"A.cs: Beta #1"}},
             {"IDE0001": {"error"}, "IDE0049": {"error"}}))),
    ("format_findings does not count a report below error as a verdict, and records its level",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c / "A.cs"), "FileChanges": [
             {"DiagnosticId": "IDE0049", "LineNumber": 5, "FormatDescription": "warning IDE0049: x"},
             {"DiagnosticId": "IDE0049", "LineNumber": 9, "FormatDescription": "error IDE0049: x"},
             {"DiagnosticId": "IDE0003", "LineNumber": 5, "FormatDescription": "info IDE0003: x"}]}], c, MARKERS)
         == ({"IDE0049": {"A.cs: Beta #1"}}, {"IDE0049": {"warning", "error"}, "IDE0003": {"info"}}))),
    ("format_findings reads the level from the first word, whatever its case",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c / "A.cs"), "FileChanges": [
             {"DiagnosticId": "IDE0002", "LineNumber": 5, "FormatDescription": "Error IDE0002: x"}]}], c, MARKERS)[0]
         == {"IDE0002": {"A.cs: Alpha"}})),
    ("format_findings treats a missing description as an unknown level, not an error",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c / "A.cs"), "FileChanges": [{"DiagnosticId": "IDE0002", "LineNumber": 5}]}], c, MARKERS)
         == ({}, {"IDE0002": {"unknown"}}))),
    ("format_findings drops a file outside the corpus",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c.parent / "G.cs"), "FileChanges": [
             {"DiagnosticId": "IDE0001", "LineNumber": 1, "FormatDescription": "error IDE0001: x"}]}],
         c, MARKERS) == ({}, {}))),
    ("the dotnet format rules are exactly the four a build skips",
     lambda: module.FORMAT_RULES == ("IDE0001", "IDE0002", "IDE0003", "IDE0049")),

    # --- the engines themselves
    ("engine_problems is empty when both engines ran",
     lambda: engine_problems(LEVELS, FORMAT, FORMAT_LEVELS, 1, []) == []),
    ("engine_problems reports AD0001 with the exception text", lambda: engine_problems(
        LEVELS | {"AD0001": {"warning"}}, FORMAT, FORMAT_LEVELS, 1, ["System.NullReferenceException at X"])
        == ["AD0001: an analyzer crashed on the corpus: System.NullReferenceException at X"]),
    ("engine_problems reports one problem per distinct crash", lambda: len(engine_problems(
        LEVELS | {"AD0001": {"warning"}}, FORMAT, FORMAT_LEVELS, 1, ["a", "b"])) == 2),
    ("engine_problems reports AD0001 even when its message is missing", lambda: any(
        p.startswith("AD0001") for p in engine_problems(
            LEVELS | {"AD0001": {"warning"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems does not point at a log file nobody uploads", lambda: not any(
        "sarif" in p for p in engine_problems(LEVELS | {"AD0001": {"warning"}}, FORMAT, FORMAT_LEVELS, 1, ["x"]))),
    ("engine_problems reports a FEX rule at warning only", lambda: any(p.startswith("FEX0002") for p in engine_problems(
        LEVELS | {"FEX0002": {"warning"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems reports a FEX rule at both error and warning", lambda: any(
        p.startswith("FEX0001") for p in engine_problems(
            LEVELS | {"FEX0001": {"error", "warning"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems accepts a non-FEX rule below error", lambda: engine_problems(
        LEVELS | {"CA9999": {"warning"}}, FORMAT, FORMAT_LEVELS, 1, []) == []),
    ("engine_problems reports a dotnet format rule listed below error", lambda: [
        p for p in engine_problems(LEVELS, FORMAT, FORMAT_LEVELS | {"IDE0049": {"error", "warning"}}, 1, [])]
        == ["IDE0049: dotnet format reported it at warning, not error - the shared .editorconfig is not reaching it"]),
    ("engine_problems reports a dotnet format rule listed only below error, and its canary", lambda: (
        lambda found: len(found) == 2 and found[0].startswith("IDE0049: dotnet format reported it at info")
        and "no IDE0049 at error" in found[1])(engine_problems(
            LEVELS, {r: o for r, o in FORMAT.items() if r != "IDE0049"}, FORMAT_LEVELS | {"IDE0049": {"info"}}, 1, []))),
    ("engine_problems fails a build that exited green", lambda: any(
        "built green" in p for p in engine_problems(LEVELS, FORMAT, FORMAT_LEVELS, 0, []))),
    ("engine_problems fails a silent compiler", lambda: any("compiler" in p for p in engine_problems(
        {"IDE0007": {"error"}, "FEX0001": {"error"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems fails silent SDK analyzers", lambda: any("SDK analyzers" in p for p in engine_problems(
        {"CS0001": {"error"}, "FEX0001": {"error"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems fails a silent FEx.Analyzers", lambda: any("FEx.Analyzers" in p for p in engine_problems(
        {"CS0001": {"error"}, "IDE0007": {"error"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems counts only error as proof an engine ran", lambda: any("compiler" in p for p in engine_problems(
        {"CS0001": {"warning"}, "IDE0007": {"error"}, "FEX0001": {"error"}}, FORMAT, FORMAT_LEVELS, 1, []))),
    ("engine_problems fails each missing dotnet format rule", lambda: [
        rule for rule in ("IDE0001", "IDE0002", "IDE0003", "IDE0049")
        if any(rule in p for p in engine_problems(
            LEVELS, {r: o for r, o in FORMAT.items() if r != rule}, FORMAT_LEVELS, 1, []))]
     == ["IDE0001", "IDE0002", "IDE0003", "IDE0049"]),

    # --- comparing samples
    ("sides is empty on an exact match", lambda: sides(["A.cs: Alpha"], {"A.cs: Alpha"}, "Alpha", None) == (set(), set())),
    ("sides names what Roslyn added", lambda: sides(["A.cs: Alpha"], {"A.cs: Alpha", "A.cs: Beta #1"}, "Alpha", None)
     == ({"A.cs: Beta #1"}, set())),
    ("sides names what Roslyn missed", lambda: sides(["A.cs: Alpha", "A.cs: Beta #1"], {"A.cs: Alpha"}, "Alpha", None)
     == (set(), {"A.cs: Beta #1"})),
    ("sides with scope own ignores other inspections' samples", lambda: sides(
        ["A.cs: Beta #1", "A.cs: Alpha"], {"A.cs: Beta #1", "A.cs: Gamma"}, "Beta", "own") == (set(), set())),
    ("sides with scope own still sees an own sample Roslyn missed", lambda: sides(
        ["A.cs: Beta #1"], set(), "Beta", "own") == (set(), {"A.cs: Beta #1"})),
    ("sides with scope own still sees an own sample Roslyn added", lambda: sides(
        ["A.cs: Beta #1"], {"A.cs: Beta #1", "A.cs: Beta #2"}, "Beta", "own") == ({"A.cs: Beta #2"}, set())),
    ("outside is what scope own leaves out, in both directions", lambda: outside(
        ["A.cs: Beta #1", "A.cs: Alpha"], {"A.cs: Beta #1", "A.cs: Gamma"}, "Beta")
        == ({"A.cs: Gamma"}, {"A.cs: Alpha"})),
    ("outside is empty when the scope hides nothing", lambda: outside(
        ["A.cs: Beta #1"], {"A.cs: Beta #1"}, "Beta") == (set(), set())),
    ("outside leaves the own difference out", lambda: outside(
        ["A.cs: Beta #1"], {"A.cs: Beta #2"}, "Beta") == (set(), set())),
    ("in_scope keeps a bare file only where ReSharper names it",
     lambda: in_scope("Alpha", {"A.cs", "B.cs", "A.cs: Alpha"}, {"A.cs", "A.cs: Alpha"}) == {"A.cs", "A.cs: Alpha"}),
    ("in_scope drops a bare file ReSharper does not name",
     lambda: in_scope("Alpha", {"B.cs", "A.cs: Alpha"}, {"A.cs: Alpha"}) == {"A.cs: Alpha"}),

    # --- the shape of the map
    ("map_problems is empty for a sound map", lambda: problems_of(mapping()) == []),
    ("map_problems is empty for a sound gap and waiver", lambda: problems_of(mapping(
        gaps={"Alpha": pin(missing=["A.cs: Alpha"])}, waivers={"Beta": pin(extra=["x"])})) == []),
    ("map_problems rejects roslyn that is not a list of ids", lambda: any(
        "'roslyn'" in p for p in problems_of(mapping(inspections={"Alpha": {"roslyn": "R1"}}))) ),
    ("map_problems rejects an empty rule id", lambda: any(
        "'roslyn'" in p for p in problems_of(mapping(inspections={"Alpha": {"roslyn": [""]}})))),
    ("map_problems rejects a scope other than own", lambda: any(
        "scope must be" in p for p in problems_of(mapping(inspections={"Alpha": {"roslyn": [], "scope": "all"}})))),
    ("map_problems wants a reason for scope own", lambda: any("needs a reason" in p for p in problems_of(mapping(
        inspections={"Alpha": {"roslyn": [], "scope": "own", "outside": {"extra": []}}})))),
    ("map_problems rejects a TODO reason for scope own", lambda: any("needs a reason" in p for p in problems_of(mapping(
        inspections={"Alpha": {"roslyn": [], "scope": "own", "reason": "TODO: later", "outside": {"extra": []}}})))),
    ("map_problems wants scope own to pin its outside", lambda: any("outside" in p for p in problems_of(mapping(
        inspections={"Alpha": {"roslyn": [], "scope": "own", "reason": "why"}})))),
    ("map_problems wants outside to hold lists of samples", lambda: any("'extra' must be a list" in p for p in problems_of(
        mapping(inspections={"Alpha": {"roslyn": [], "scope": "own", "reason": "why", "outside": {"extra": "x"}}})))),
    ("map_problems rejects outside without scope own", lambda: any("belongs to scope 'own' only" in p for p in problems_of(
        mapping(inspections={"Alpha": {"roslyn": [], "outside": {"extra": []}}})))),
    ("map_problems rejects an inspection that is not compared", lambda: any(
        "not an inspection with a ReSharper verdict" in p for p in problems_of(
            mapping(inspections={"Quiet": {"roslyn": []}}), ["Alpha"]))),
    ("map_problems wants a gap to be an object", lambda: any("a gap is an object" in p for p in problems_of(
        mapping(gaps={"Alpha": "a string reason"})))),
    ("map_problems wants a real reason on a gap", lambda: any("a gap needs a real reason" in p for p in problems_of(
        mapping(gaps={"Alpha": pin(missing=["x"], reason=" ")})))),
    ("map_problems rejects a TODO gap", lambda: any("a gap needs a real reason" in p for p in problems_of(
        mapping(gaps={"Alpha": pin(missing=["x"], reason="TODO: no Roslyn rule")})))),
    ("map_problems wants a gap to pin its difference", lambda: any(
        "a gap must name at least one extra or missing sample" in p for p in problems_of(
            mapping(gaps={"Alpha": pin()})))),
    ("map_problems wants a gap to have an inspections entry", lambda: any(
        "a gap without an entry" in p for p in problems_of(mapping(inspections={}, gaps={"Alpha": pin(missing=["x"])})))),
    ("map_problems wants a gap's lists to be lists", lambda: any("'missing' must be a list" in p for p in problems_of(
        mapping(gaps={"Alpha": {"reason": "why", "missing": "x"}})))),
    ("map_problems wants a real reason on a waiver", lambda: any("a waiver needs a real reason" in p for p in problems_of(
        mapping(waivers={"Alpha": pin(extra=["A.cs"], reason="")})))),
    ("map_problems wants a waiver to name a sample", lambda: any(
        "a waiver must name at least one" in p for p in problems_of(mapping(waivers={"Alpha": pin()})))),
    ("map_problems wants a waiver to have an inspections entry", lambda: any(
        "a waiver without an entry" in p for p in problems_of(
            mapping(inspections={}, waivers={"Alpha": pin(extra=["A.cs"])}), ["Alpha"]))),
    ("map_problems rejects a gap that is also a waiver", lambda: any(
        "both a gap and a waiver" in p for p in problems_of(mapping(
            gaps={"Alpha": pin(missing=["x"])}, waivers={"Alpha": pin(extra=["A.cs"])})))),
    ("reasoned accepts text", lambda: reasoned("Roslyn has no such rule")),
    ("reasoned rejects blank, non-text and the placeholder",
     lambda: not any(reasoned(r) for r in (None, "", "  ", 3, "TODO", "  TODO: explain"))),

    # --- the verdict
    ("evaluate: a clean map is green", lambda: verdict()["problems"] == [] and verdict()["exact"] == 2),
    ("evaluate: counts the denominator and the exclusions", lambda: (
        lambda r: (r["compared"], r["excluded"], r["accounted"], r["total"], r["excludedBy"])
        == (2, 2, 4, 4, {"silent": 1, "unsampled": 1}))(verdict())),
    ("evaluate: the headline is computed from the data", lambda: verdict()["headline"]
     == "Roslyn parity: 2/2 exact (+0 waived, 0 gaps); 2 excluded; 4 accounted"),
    ("evaluate: Roslyn reporting extra is a failing mismatch", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "Alpha: mismatch") and r["failing"] == 1)(
        verdict(errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Beta #1"}}))),
    ("evaluate: Roslyn missing a sample is a failing mismatch", lambda: (
        lambda r: status(r, "Alpha") == "fail" and r["inspections"]["Alpha"]["missing"] == ["A.cs: Alpha"])(
        verdict(errors=FOUND | {"R1": set()}))),
    ("evaluate: a rule that never fires fails", lambda: status(verdict(errors={}), "Alpha") == "fail"),
    ("evaluate: an inspection with no map entry is unmapped and fails", lambda: (
        lambda r: status(r, "Beta") == "unmapped" and has(r, "Beta: unmapped") and r["failing"] == 1)(
        verdict(map_=mapping(inspections={"Alpha": {"roslyn": ["R1"]}})))),

    # scope own pins the difference outside the inspection's own samples
    ("evaluate: scope own tolerates exactly its pinned outside difference", lambda: (
        lambda r: status(r, "Beta") == "exact" and r["inspections"]["Beta"]["outside"]
        == {"extra": ["A.cs"], "missing": []})(verdict())),
    ("evaluate: scope own fails when Roslyn adds a sample outside", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "Beta (outside its own samples)")
        and has(r, "new extra ['A.cs: Gamma']"))(
        verdict(errors=FOUND | {"R2": FOUND["R2"] | {"A.cs: Gamma"}}))),
    ("evaluate: scope own fails when Roslyn stops reporting every pinned outside sample", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "scope 'own' hides nothing"))(
        verdict(errors=FOUND | {"R2": {"A.cs: Beta #1", "A.cs: Alpha"}}))),
    ("evaluate: scope own fails when Roslyn stops reporting one of two pinned outside samples", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "recorded but gone: extra ['A.cs: Gamma']"))(
        verdict(map_=mapping(inspections=EXACT | {"Beta": EXACT["Beta"] | {
            "outside": {"extra": ["A.cs", "A.cs: Gamma"], "missing": []}}})))),
    ("evaluate: scope own fails when Roslyn stops reporting a sample ReSharper has outside", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "new missing ['A.cs: Gamma']"))(
        verdict(expected=EXPECTED | {"inspections": EXPECTED["inspections"] | {"Beta": [
            "A.cs: Beta #1", "A.cs: Alpha", "A.cs: Gamma"]}}))),
    ("evaluate: scope own without a pinned outside fails where there is one", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "Beta (outside its own samples)"))(
        verdict(map_=mapping(inspections=EXACT | {"Beta": {k: v for k, v in EXACT["Beta"].items()
                                                              if k != "outside"}})))),
    ("evaluate: scope own that hides nothing fails", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "Beta: scope 'own' hides nothing - remove it"))(
        verdict(errors=FOUND | {"R2": {"A.cs: Beta #1", "A.cs: Alpha"}},
                map_=mapping(inspections=EXACT | {"Beta": EXACT["Beta"] | {"outside": {"extra": []}}})))),
    ("evaluate: scope own still compares the own samples", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "Beta: mismatch"))(
        verdict(errors=FOUND | {"R2": FOUND["R2"] - {"A.cs: Beta #1"}}))),

    # gaps pin their measured difference
    ("evaluate: a gap whose difference is the pinned one is tolerated", lambda: (
        lambda r: status(r, "Alpha") == "gap" and r["gaps"] == 1 and r["problems"] == [] and r["failing"] == 0)(
        verdict(map_=mapping(gaps={"Alpha": pin(missing=["A.cs: Alpha"])}), errors=FOUND | {"R1": set()}))),
    ("evaluate: a gap that now passes fails the ratchet", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "now matches ReSharper exactly - remove it from gaps"))(
        verdict(map_=mapping(gaps={"Alpha": pin(missing=["A.cs: Alpha"])})))),
    ("evaluate: a gap whose difference grew fails", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "Alpha (gap): the difference is not the recorded one")
        and has(r, "new extra ['A.cs: Beta #1']"))(
        verdict(map_=mapping(gaps={"Alpha": pin(missing=["A.cs: Alpha"])}), errors=FOUND | {"R1": {"A.cs: Beta #1"}}))),
    ("evaluate: a gap that lost a different sample fails", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "new missing ['A.cs: Beta #1']"))(
        verdict(map_=mapping(gaps={"Beta": pin(missing=["A.cs: Alpha"])}),
                errors=FOUND | {"R2": {"A.cs: Alpha", "A.cs"}}))),
    ("evaluate: a gap whose difference shrank fails", lambda: (
        lambda r: status(r, "Beta") == "fail" and has(r, "Beta (gap): the difference is not the recorded one")
        and has(r, "recorded but gone: extra [], missing ['A.cs: Alpha']"))(
        verdict(map_=mapping(gaps={"Beta": pin(missing=["A.cs: Beta #1", "A.cs: Alpha"])}),
                errors=FOUND | {"R2": {"A.cs: Alpha", "A.cs"}}))),
    ("evaluate: a gap does not hide a different mismatch of another inspection", lambda: (
        lambda r: status(r, "Beta") == "fail" and status(r, "Alpha") == "gap")(
        verdict(map_=mapping(gaps={"Alpha": pin(missing=["A.cs: Alpha"])}), errors={"R1": set(), "R2": set()}))),

    # waivers pin their difference
    ("evaluate: the exact waived difference is tolerated", lambda: (
        lambda r: status(r, "Alpha") == "waived" and r["waived"] == 1 and r["problems"] == [])(
        verdict(map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Alpha #2"])}),
                errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #2"}}))),
    ("evaluate: a waiver on a missing sample is tolerated", lambda: status(verdict(
        map_=mapping(waivers={"Alpha": pin(missing=["A.cs: Alpha"])}), errors=FOUND | {"R1": set()}), "Alpha")
     == "waived"),
    ("evaluate: a stale waiver fails the ratchet", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "stale waiver"))(
        verdict(map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Alpha #2"])})))),
    ("evaluate: a waiver that no longer describes the difference fails", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "Alpha (waiver): the difference is not the recorded one"))(
        verdict(map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Alpha #2"])}),
                errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #3"}}))),
    ("evaluate: a waiver does not cover an additional difference", lambda: status(verdict(
        map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Alpha #2"])}),
        errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #2", "A.cs: Alpha #3"}}), "Alpha") == "fail"),
    ("evaluate: a waiver whose missing side changed fails", lambda: status(verdict(
        map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Alpha #2"], missing=["A.cs: Alpha"])}),
        errors=FOUND | {"R1": {"A.cs: Alpha #2", "A.cs: Beta #1"}}), "Alpha") == "fail"),
    ("evaluate: a waiver with both sides is tolerated when both match", lambda: status(verdict(
        map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Beta #1"], missing=["A.cs: Alpha"])}),
        errors=FOUND | {"R1": {"A.cs: Beta #1"}}), "Alpha") == "waived"),
    ("evaluate: a gap with both sides is tolerated when both match", lambda: status(verdict(
        map_=mapping(gaps={"Alpha": pin(extra=["A.cs: Beta #1"], missing=["A.cs: Alpha"])}),
        errors=FOUND | {"R1": {"A.cs: Beta #1"}}), "Alpha") == "gap"),
    ("evaluate: a gap whose missing side changed fails", lambda: status(verdict(
        map_=mapping(gaps={"Alpha": pin(extra=["A.cs: Beta #1"], missing=["A.cs: Alpha"])}),
        errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Beta #1"}}), "Alpha") == "fail"),
    ("evaluate: a gap whose extra side changed fails", lambda: status(verdict(
        map_=mapping(gaps={"Alpha": pin(extra=["A.cs: Beta #1"], missing=["A.cs: Alpha"])}),
        errors=FOUND | {"R1": {"A.cs"}}), "Alpha") == "fail"),
    ("evaluate: a waiver that shrank only partly fails", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "recorded but gone: extra ['A.cs: Alpha #3']"))(
        verdict(map_=mapping(waivers={"Alpha": pin(extra=["A.cs: Alpha #2", "A.cs: Alpha #3"])}),
                errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #2"}}))),
    ("evaluate: a gap on an inspection that does not exist fails", lambda: has(
        verdict(map_=mapping(gaps={"Ghost": pin(missing=["x"])}, inspections=EXACT | {"Ghost": {"roslyn": []}})),
        "expected.json has no such inspection")),
    ("evaluate: a problem in the map surfaces", lambda: has(
        verdict(map_=mapping(gaps={"Alpha": pin(missing=["x"], reason="TODO")})), "Alpha: a gap needs a real reason")),
    ("evaluate: the failing count is the failures plus the unmapped", lambda: verdict(
        map_=mapping(inspections={}), errors={})["failing"] == 2),

    # accounting: compared and excluded are disjoint, and together exactly what expected.json records
    ("evaluate: an exclusion without a reason fails", lambda: has(verdict(
        expected=EXPECTED | {"silent": {"Quiet": " "}}), "Quiet: excluded but neither")),
    ("evaluate: an inspection that is neither compared nor excluded fails", lambda: (
        lambda r: has(r, "Stray: neither compared nor excluded") and r["accounted"] == 4 and r["total"] == 5)(
        verdict(expected=EXPECTED | {"inspections": EXPECTED["inspections"] | {"Stray": []}}))),
    ("evaluate: an inspection with an own sample that is also excluded fails", lambda: has(verdict(
        expected=EXPECTED | {"silent": {"Quiet": "why", "Alpha": "why"}}),
        "Alpha: has a ReSharper verdict on a sample of its own, yet is excluded as silent")
        and verdict(expected=EXPECTED | {"silent": {"Quiet": "why", "Alpha": "why"}})["accounted"] == 4),
    ("evaluate: an inspection under both silent and unsampled fails", lambda: has(verdict(
        expected=EXPECTED | {"unsampled": {"Missing": "why", "Quiet": "why"}}),
        "Quiet: listed under both silent and unsampled")),
    ("evaluate: an exclusion expected.json does not record fails", lambda: (
        lambda r: has(r, "Ghost: excluded as silent, but expected.json has no such inspection") and r["accounted"] == 5)(
        verdict(expected=EXPECTED | {"silent": {"Quiet": "why", "Ghost": "why"}}))),

    # --- drafting a map
    ("propose maps an exact rule", lambda: propose(EXPECTED, FOUND)["inspections"]["Alpha"] == {"roslyn": ["R1"]}),
    ("propose adds scope own with its pinned outside when collateral is the only difference", lambda: (
        lambda d: d["inspections"]["Beta"] == {
            "roslyn": ["R2"], "scope": "own", "reason": "TODO: collateral reports",
            "outside": {"extra": ["A.cs"], "missing": []}} and "Beta" not in d["waivers"])(propose(EXPECTED, FOUND))),
    ("propose drafts a gap that pins what is missing when no rule fires in an own sample", lambda: (
        lambda d: d["inspections"]["Alpha"] == {"roslyn": []} and d["gaps"]["Alpha"] == {
            "reason": "TODO: no Roslyn rule reports a sample of this inspection", "extra": [],
            "missing": ["A.cs: Alpha"]})(propose(EXPECTED, {}))),
    ("propose drafts a waiver that pins the difference", lambda: (
        lambda d: d["waivers"]["Alpha"] == {"reason": "TODO: explain the difference",
                                            "extra": ["A.cs: Alpha #2"], "missing": []})(
        propose(EXPECTED, {"R1": {"A.cs: Alpha", "A.cs: Alpha #2"}}))),
    ("propose does not join a second rule that adds nothing", lambda: propose(
        EXPECTED, {"R1": {"A.cs: Alpha"}, "R2": {"A.cs: Alpha"}})["inspections"]["Alpha"]["roslyn"] == ["R1"]),
    ("propose considers only rules that fire in an own sample", lambda: (
        lambda d: d["inspections"]["Alpha"] == {"roslyn": []} and "Alpha" in d["gaps"])(
        propose(EXPECTED, {"R3": {"A.cs: Beta #1"}}))),
    ("propose skips the inspections it cannot compare", lambda: (
        lambda d: "Quiet" not in d["inspections"] and "Missing" not in d["inspections"])(propose(EXPECTED, FOUND))),
    ("propose never writes a draft that passes map_problems", lambda: map_problems(
        propose(EXPECTED, {}), ["Alpha", "Beta"]) != []),
    ("propose joins a second rule while it raises the overlap", lambda: propose(
        {"inspections": {"Alpha": ["A.cs: Alpha", "B.cs: Alpha"]}},
        {"R1": {"A.cs: Alpha"}, "R2": {"B.cs: Alpha"}})["inspections"]["Alpha"]["roslyn"] == ["R1", "R2"]),

    # --- the consumer tree and the engines
    ("consumer copies each shared file under DevConfigs", lambda: consumer_tree(
        lambda work, _: all((work / "DevConfigs" / name).read_text() == f"shared {name}" for name in SHARED_NAMES))),
    ("consumer's props switch the mirror on before importing DevConfigs", lambda: consumer_tree(
        lambda work, _: (lambda text: "<DevConfigsMirrorEditorConfig>true</DevConfigsMirrorEditorConfig>" in text
                         and text.index("<DevConfigsMirrorEditorConfig>true")
                         < text.index('Import Project="$(MSBuildThisFileDirectory)DevConfigs/Directory.Build.props"'))(
            (work / "Directory.Build.props").read_text()))),
    ("consumer's targets import the DevConfigs targets", lambda: consumer_tree(
        lambda work, _: 'Import Project="$(MSBuildThisFileDirectory)DevConfigs/Directory.Build.targets"'
        in (work / "Directory.Build.targets").read_text())),
    ("consumer writes a .gitmodules that names DevConfigs", lambda: consumer_tree(
        lambda work, _: "path = DevConfigs" in (work / ".gitmodules").read_text())),
    ("consumer copies the corpus without its recording or the isolating files", lambda: consumer_tree(
        lambda work, _: sorted(p.name for p in (work / "Corpus").iterdir()) == ["A.cs", "Corpus.csproj"])),
    ("consumer returns the copied project", lambda: consumer_tree(
        lambda work, project: project == work / "Corpus" / "Corpus.csproj")),
    ("consumer clears what the tool wrote before", lambda: consumer_tree(
        lambda work, _: not (work / "Corpus" / "old.cs").exists() and not (work / "roslyn.sarif").exists()
        and not (work / "format").exists(),
        prepare=populated({module.WORK_MARKER: "", "Corpus/old.cs": "x", "roslyn.sarif": "{}",
                           "format/format-report.json": "[]"}))),
    ("consumer leaves what the tool did not write", lambda: consumer_tree(
        lambda work, _: (work / "notes.txt").read_text() == "mine" and (work / "keep" / "b.txt").exists(),
        prepare=populated({module.WORK_MARKER: "", "notes.txt": "mine", "keep/b.txt": "x"}))),
    ("claim_work refuses a directory that is not empty and has no marker", lambda: (
        lambda out: out[0] is not None and "refusing to clear it" in out[0] and out[1] == ["a.txt", "b"])(
        claimed({"a.txt": "mine", "b/c.txt": "mine"}))),
    ("claim_work deletes nothing when it refuses", lambda: claimed({"a.txt": "mine"})[1] == ["a.txt"]),
    ("claim_work accepts an empty directory and leaves its marker and the DevConfigs folder", lambda: (
        lambda out: out[0] is None and out[1] == [".roslyn-parity-work", "DevConfigs"])(claimed({}))),
    ("claim_work accepts a directory this tool made before", lambda: (
        lambda out: out[0] is None and "notes.txt" in out[1] and "old.sarif" not in out[1])(
        claimed({".roslyn-parity-work": "", "notes.txt": "mine", "roslyn.sarif": "{}"}))),
    ("claim_work accepts a directory that only holds a marker", lambda: claimed({".roslyn-parity-work": ""})[0] is None),
    ("claim_work creates a directory that does not exist", lambda: in_corpus(
        lambda folder: (claim_work(folder / "new" / "work"), (folder / "new" / "work" / ".roslyn-parity-work").exists())[1])),
    ("run_engines builds, then formats, and returns the build's exit code", lambda: (
        lambda run: engines_in_scratch(run)[0] == 1 and [c[1] for c in run.calls] == ["build", "format"])(
        fake_run())),
    ("run_engines asks format for exactly the four skipped rules", lambda: (
        lambda run: (engines_in_scratch(run), run.calls[1])[1][
            run.calls[1].index("--diagnostics") + 1:run.calls[1].index("--diagnostics") + 5]
        == ["IDE0001", "IDE0002", "IDE0003", "IDE0049"])(fake_run())),
    ("run_engines formats read-only", lambda: (
        lambda run: (engines_in_scratch(run), "--verify-no-changes" in run.calls[1])[1])(fake_run())),
    ("run_engines pins the build language so only structured fields are parsed", lambda: (
        lambda seen: seen == {"DOTNET_CLI_UI_LANGUAGE": "en", "DOTNET_NOLOGO": "1"})(
        (lambda captured: (engines_in_scratch(lambda command, **kw: captured.update(
            {k: kw["env"][k] for k in ("DOTNET_CLI_UI_LANGUAGE", "DOTNET_NOLOGO")}) or SimpleNamespace(
            returncode=1, stdout="", stderr="")), captured)[1])({}))),
    ("run_engines fails loudly when the build wrote no SARIF log", lambda: (
        lambda out: out[0] is None and "wrote no SARIF log" in out[1])(engines_in_scratch(fake_run(sarif_text=None)))),
    ("run_engines fails loudly when format wrote no report", lambda: (
        lambda out: out[0] is None and "wrote no report" in out[1])(engines_in_scratch(fake_run(report=False)))),

    # --- measure: both engines' output to one verdict map
    ("measure reads the SARIF log and the format report into one map", lambda: (
        lambda out: out[0] == {"CS0001": {"A.cs: Alpha"}, "IDE0007": {"A.cs: Alpha"}, "FEX0001": {"A.cs: Alpha"},
                               "IDE0001": {"A.cs: Alpha"}, "IDE0002": {"A.cs: Alpha"}, "IDE0003": {"A.cs: Alpha"},
                               "IDE0049": {"A.cs: Alpha"}} and out[1] == [])(
        measured(CANARIES, FORMAT_ENTRIES))),
    ("measure locates a report in the sample of the corpus it was given", lambda: measured(
        CANARIES + [("IDE0007", "error", True, 4)], FORMAT_ENTRIES)[0]["IDE0007"] == {"A.cs: Alpha", "A.cs: Beta #1"}),
    ("measure merges a rule both engines report", lambda: measured(
        CANARIES + [("IDE0001", "error", True, 4)], FORMAT_ENTRIES)[0]["IDE0001"] == {"A.cs: Alpha", "A.cs: Beta #1"}),
    ("measure leaves a format report below error out of the verdicts and reports it", lambda: (
        lambda out: out[0]["IDE0049"] == {"A.cs: Alpha"} and len(out[1]) == 1
        and out[1][0].startswith("IDE0049: dotnet format reported it at warning"))(
        measured(CANARIES, FORMAT_ENTRIES + [("IDE0049", "warning", 4)]))),
    ("measure treats a reused log as a failed build", lambda: not any(
        "built green" in p for p in measured(CANARIES, FORMAT_ENTRIES, reuse=True)[1])),
    ("measure reports a crashed analyzer with its message", lambda: (
        lambda out: out[1] == ["AD0001: an analyzer crashed on the corpus: AD0001 message"])(
        measured(CANARIES + [("AD0001", "warning", False, 0)], FORMAT_ENTRIES))),
    ("measure ignores a suppressed result", lambda: (
        lambda out: "IDE0099" not in out[0] and "IDE0007" in out[0])(measured(
            CANARIES + [("IDE0099", "error", True, 2)], FORMAT_ENTRIES, suppressed=(3,)))),
    ("measure reports a canary that only a suppressed result satisfied", lambda: any(
        "FEx.Analyzers" in p for p in measured(CANARIES, FORMAT_ENTRIES, suppressed=(2,))[1])),
    ("measure does not run the engines when it can reuse their output", lambda: (
        lambda log: (measured(CANARIES, FORMAT_ENTRIES, reuse=True, run=lambda work: log.append(True) or 1), log)[1]
        == [])([])),
    ("measure runs the engines when it is not reusing", lambda: (
        lambda called: called == [True])(
        (lambda log: (measured(CANARIES, FORMAT_ENTRIES, reuse=False, run=lambda work: log.append(True) or 1), log)[1])([]))),
    ("measure counts a green build of the corpus as a canary failure", lambda: any(
        "built green" in p for p in measured(CANARIES, FORMAT_ENTRIES, reuse=False, run=lambda work: 0)[1])),
    ("measure runs the engines when there is nothing to reuse", lambda: (
        lambda called: called == [True])(
        (lambda log: (measured(CANARIES, FORMAT_ENTRIES, reuse=True, work_files=False,
                               run=lambda work: log.append(True) or (work / "roslyn.sarif").write_text(
                                   json.dumps(sarif(*CANARIES[:0])), encoding="utf-8") or 1), log)[1])([]))),

    # --- reporting
    ("headline spells the contract", lambda: headline(102, 124, 1, 21, 36, 160)
     == "Roslyn parity: 102/124 exact (+1 waived, 21 gaps); 36 excluded; 160 accounted"),
    ("summary_lines marks the scope and the rules", lambda: summary_lines(verdict()) == [
        "exact    Alpha  <- R1", "exact    Beta  <- R2  [own]"]),
    ("summary_lines shows a gap with no rule as a dash", lambda: summary_lines(verdict(
        map_=mapping(inspections={"Alpha": {"roslyn": []}, "Beta": EXACT["Beta"]},
                     gaps={"Alpha": pin(missing=["A.cs: Alpha"])}), errors=FOUND))[0] == "gap      Alpha  <- -"),
    ("markdown carries the headline, the exclusions, the gaps and the waivers", lambda: (
        lambda text: all(part in text for part in (
            "### Roslyn parity: 2/2 exact", "1 silent, 1 unsampled", "`Alpha`: no rule", "`Beta`: why")))(
        markdown(verdict(), mapping(gaps={"Alpha": pin(missing=["x"], reason="no rule")},
                                    waivers={"Beta": pin(extra=["x"])}), 3.2))),
    ("markdown lists the problems", lambda: "- Alpha: unmapped" in markdown(
        verdict(map_=mapping(inspections={"Beta": EXACT["Beta"]})), mapping(), 1.0)),

    # --- main
    ("main exits 0 and prints the headline when the gate is green", lambda: (
        lambda out: out[0] == 0 and out[1].splitlines() == [
            "Roslyn parity: 2/2 exact (+0 waived, 0 gaps); 2 excluded; 4 accounted", f"SDK: {SDK}", "OK"])(
        run_main([]))),
    ("main exits 1 and names the problem on a mismatch", lambda: (
        lambda out: out[0] == 1 and "Alpha: mismatch" in out[2] and "1 problem(s)" in out[1])(
        run_main([], errors={"R1": set(), "R2": FOUND["R2"]}))),
    ("main exits 1 on an engine problem", lambda: (
        lambda out: out[0] == 1 and "canary: boom" in out[2])(run_main([], engine=["canary: boom"]))),
    ("main --summary prints a line per inspection", lambda: "exact    Alpha  <- R1" in run_main(["--summary"])[1]),
    ("main --json writes the full result", lambda: (
        lambda out: json.loads(out[3]["result.json"])["headline"].startswith("Roslyn parity: 2/2")
        and "seconds" in json.loads(out[3]["result.json"]))(
        run_main(lambda folder: ["--json", str(folder / "result.json")]))),
    ("main writes the headline to GITHUB_STEP_SUMMARY", lambda: (
        lambda out: out[3]["step.md"].startswith("### Roslyn parity: 2/2 exact"))(run_main([], summary=True))),
    ("main appends to the step summary other steps wrote", lambda: run_main(
        [], summary=True, summary_before="earlier step\n")[3]["step.md"].startswith("earlier step\n### Roslyn parity")),
    ("main writes the step summary on a red run too, with the problems", lambda: (
        lambda out: out[0] == 1 and out[3]["step.md"].startswith("### Roslyn parity: 1/2 exact")
        and "Alpha: mismatch" in out[3]["step.md"])(
        run_main([], summary=True, errors={"R1": set(), "R2": FOUND["R2"]}))),
    ("main --propose prints a draft and exits 0 without judging", lambda: (
        lambda out: out[0] == 0 and json.loads(out[1])["inspections"]["Alpha"] == {"roslyn": ["R1"]})(
        run_main(["--propose"], map_={"inspections": {}}))),
    ("main rejects an unknown argument with a usage error", lambda: exit_code(lambda: run_main(["--nope"])) == 2),
    ("main hands --reuse to measure", lambda: spied(["--reuse"])[1] == [True]),
    ("main does not reuse unless asked", lambda: spied([])[1] == [False]),
    ("main keeps the --work directory it was given and tells measure to use it", lambda: (
        lambda out: out[0][4] == ["kept"] and out[2] == ["kept"])(
        spied(lambda folder: ["--work", str(folder / "kept")], make=True))),
    ("main removes the temporary work directory it made", lambda: (
        lambda seen: seen[0].exists() is False)(spied([], make=True)[3])),

    # --- the SDK the map was measured with
    ("sdk_version asks plain dotnet --version and passes no cwd, so it resolves where the engines run", lambda: (
        lambda run: sdk_version(run) == "10.0.401" and run.seen[0] == ["dotnet", "--version"]
        and "cwd" not in run.seen[1])(sdk_run())),
    ("sdk_version takes the last line when dotnet prints a banner first", lambda: sdk_version(
        sdk_run(stdout="Welcome\n10.0.402\n")) == "10.0.402"),
    ("sdk_version takes the last line that is a version, not a trailing warning", lambda: sdk_version(
        sdk_run(stdout="10.0.401\nwarning: something trailing\n")) == "10.0.401"),
    ("sdk_version accepts a preview", lambda: sdk_version(sdk_run(stdout="10.0.100-rc.1.25451.107\n")) ==
     "10.0.100-rc.1.25451.107"),
    ("sdk_version stops the gate when dotnet fails", lambda: "dotnet --version failed" in str(
        exit_code(lambda: sdk_version(sdk_run(stdout="", returncode=1, stderr="no sdk")))) and "(exit 1)" in str(
        exit_code(lambda: sdk_version(sdk_run(stdout="", returncode=1, stderr="no sdk"))))),
    ("sdk_version stops the gate when dotnet prints nothing", lambda: exit_code(
        lambda: sdk_version(sdk_run(stdout=""))) is not None),
    ("sdk_version stops the gate when dotnet prints a global.json mismatch instead of a version (exit 155)", lambda: (
        lambda stop: stop is not None and "(exit 155)" in str(stop))(
        exit_code(lambda: sdk_version(sdk_run(stdout="10.0.401 [C:\\x]\n", returncode=155))))),
    ("sdk_version does not record a version that came with a non-zero exit", lambda: (
        lambda stop: stop is not None and "(exit 155)" in str(stop))(
        exit_code(lambda: sdk_version(sdk_run(stdout="10.0.401\n", returncode=155))))),
    ("sdk_version stops the gate when no line is a version", lambda: exit_code(
        lambda: sdk_version(sdk_run(stdout="10.0.401 [C:\\x]\nwarning\n"))) is not None),
    ("SDK_VERSION takes major.minor.patch with an optional preview suffix and nothing else", lambda: (
        all(SDK_VERSION.fullmatch(v) for v in ("10.0.401", "10.0.100-rc.1.25451.107", "9.0.0-preview.1"))
        and not any(SDK_VERSION.fullmatch(v) for v in (
            "", " 10.0.401", "10.0.401 ", "10.0.401\n", "10.0", "10.0.x", "v10.0.401", "10.0.401 [C:\\x]")))),
    ("reused_sdk reads the SDK kept beside the log", lambda: in_corpus(lambda folder: (
        (folder / "sdk.txt").write_text("10.0.300\n", encoding="utf-8"),
        reused_sdk(folder, "10.0.502"))[1] == ("10.0.300", None))),
    ("reused_sdk falls back to the installed SDK with a stale warning when sdk.txt is missing", lambda: in_corpus(
        lambda folder: (lambda sdk, warning: sdk == "10.0.502" and "stale" in warning and "sdk.txt" in warning)(
            *reused_sdk(folder, "10.0.502")))),
    ("reused_sdk treats an unreadable sdk.txt as stale too", lambda: in_corpus(lambda folder: (
        (folder / "sdk.txt").write_text("banana\n", encoding="utf-8"),
        reused_sdk(folder, "10.0.502"))[1][0] == "10.0.502")),
    ("sdk_status red and the SDK differs: the note says the SDK changed and to re-measure", lambda: (
        lambda s: s["note"] == "SDK changed: map measured with 10.0.401, this run used 10.0.502 - re-measure the map "
        "before reading the differences below as a regression." and (s["run"], s["recorded"]) == ("10.0.502", SDK))(
        sdk_status(mapping(), "10.0.502", red=True))),
    ("sdk_status red and the SDK is the same: no note", lambda: sdk_status(mapping(), SDK, red=True)["note"] is None),
    ("sdk_status green and the SDK differs: a notice that suggests the new version, not a failure", lambda: (
        lambda s: s["note"].startswith("SDK notice: map measured with 10.0.401, this run used 10.0.502")
        and 'set "sdk" in roslyn-map.json to 10.0.502' in s["note"] and "changed" not in s["note"])(
        sdk_status(mapping(), "10.0.502", red=False))),
    ("sdk_status green and the SDK is the same: no note", lambda: sdk_status(mapping(), SDK, red=False)["note"] is None),
    ("sdk_status with no recorded SDK has nothing to compare", lambda: sdk_status(
        {"inspections": {}}, "10.0.502", red=True) == {"run": "10.0.502", "recorded": None, "note": None}),
    ("map_problems: a map with no sdk is a map problem", lambda: any(
        "sdk: the map must name" in p for p in problems_of(without_sdk()))),
    ("map_problems: an empty or non-text sdk is a map problem too", lambda: all(
        any("sdk: the map must name" in p for p in problems_of(mapping(sdk=value)))
        for value in ("", "  ", 10, None, " 10.0.401 ", "10.0.401 ", "10.0.401\n", "10.0", "v10.0.401"))),
    ("map_problems: a plain version or a preview is a sound sdk", lambda: not any(
        "sdk:" in p for value in ("10.0.401", "10.0.100-rc.1.25451.107") for p in problems_of(mapping(sdk=value)))),
    ("sdk_status takes a padded recorded sdk as no sdk, never as a match", lambda: sdk_status(
        mapping(sdk=" 10.0.401 "), "10.0.401", red=True) == {"run": "10.0.401", "recorded": None, "note": None}),
    ("evaluate: a map with no sdk fails the gate", lambda: has(verdict(map_=without_sdk()), "sdk: the map must name")),
    ("main red and the SDK differs: stderr leads with the SDK line, before every problem", lambda: (
        lambda out: out[0] == 1 and out[2].splitlines()[0] == "SDK changed: map measured with 10.0.401, this run "
        "used 10.0.502 - re-measure the map before reading the differences below as a regression."
        and "Alpha: mismatch" in out[2].splitlines()[1] and "SDK: 10.0.502" in out[1])(
        run_main([], errors={"R1": set(), "R2": FOUND["R2"]}, sdk="10.0.502"))),
    ("main red and the SDK is the same: no SDK line, the problem leads", lambda: (
        lambda out: out[0] == 1 and out[2].splitlines()[0].startswith("Alpha: mismatch") and "SDK changed" not in out[2])(
        run_main([], errors={"R1": set(), "R2": FOUND["R2"]}))),
    ("main green and the SDK differs: exit 0 with a notice on stdout, nothing on stderr", lambda: (
        lambda out: out[0] == 0 and out[2] == "" and "SDK notice: map measured with 10.0.401, this run used 10.0.502"
        in out[1] and out[1].splitlines()[-1] == "OK")(run_main([], sdk="10.0.502"))),
    ("main green and the SDK is the same: no notice", lambda: (
        lambda out: out[0] == 0 and "notice" not in out[1] and "changed" not in out[1])(run_main([]))),
    ("main with no sdk in the map fails and names it", lambda: (
        lambda out: out[0] == 1 and "sdk: the map must name" in out[2] and "SDK changed" not in out[2])(
        run_main([], map_=without_sdk()))),
    ("main puts the SDK line and note in the step summary", lambda: (
        lambda red, same: "SDK: 10.0.502" in red[3]["step.md"] and "**SDK changed: map measured with 10.0.401"
        in red[3]["step.md"] and "SDK: 10.0.401" in same[3]["step.md"] and "SDK changed" not in same[3]["step.md"])(
        run_main([], summary=True, errors={"R1": set(), "R2": FOUND["R2"]}, sdk="10.0.502"),
        run_main([], summary=True))),
    ("main --json records the SDK the run used and the one the map names", lambda: (
        lambda sdk: (sdk["run"], sdk["recorded"]) == ("10.0.502", SDK) and bool(sdk["note"]))(
        json.loads(run_main(lambda folder: ["--json", str(folder / "result.json")], sdk="10.0.502")[3]["result.json"])[
            "sdk"])),
    ("main --propose writes the SDK it ran with into the draft, first", lambda: (
        lambda out: list(json.loads(out[1]))[0] == "sdk" and json.loads(out[1])["sdk"] == "10.0.502")(
        run_main(["--propose"], sdk="10.0.502"))),
    ("propose writes no sdk when none is given", lambda: "sdk" not in propose(EXPECTED, FOUND)),
    ("markdown shows the SDK and its note", lambda: (
        lambda text: "SDK: 10.0.502" in text and "**SDK changed: map" in text)(markdown(
            verdict() | {"sdk": sdk_status(mapping(), "10.0.502", red=True)}, mapping(), 1.0))),
    ("markdown without an SDK result is unchanged", lambda: "SDK" not in markdown(verdict(), mapping(), 1.0)),
    ("the committed map records an SDK version", lambda: (
        lambda sdk: isinstance(sdk, str) and bool(SDK_VERSION.fullmatch(sdk)))(
        json.loads(module.MAP.read_text(encoding="utf-8")).get("sdk"))),

    # --- a run that stops, the annotation, and --reuse
    ("main stops inside an engine: stderr leads with the SDK change, then names the SDK, then the engine's message",
     lambda: (lambda out: out[0] == "engines stopped" and out[1] == "" and out[2].splitlines() == [
         "SDK changed: map measured with 10.0.401, this run used 10.0.502 - re-measure the map before reading the "
         "differences below as a regression.", "SDK: 10.0.502"])(
         run_main([], measuring=stopping(), sdk="10.0.502", stops=True))),
    ("main stops inside an engine on the recorded SDK: it names the SDK and claims no change", lambda: (
        lambda out: out[0] == "engines stopped" and out[2].splitlines() == ["SDK: 10.0.401"])(
        run_main([], measuring=stopping(), stops=True))),
    ("main stops inside an engine with no readable map: it still names the SDK", lambda: (
        lambda out: out[2].splitlines() == ["SDK: 10.0.502"])(
        run_main([], measuring=stopping(), sdk="10.0.502", stops=True, map_={"sdk": 1}))),
    ("main emits a ::notice annotation for the green SDK notice under GitHub Actions", lambda: (
        lambda out: out[0] == 0 and "::notice title=Roslyn parity SDK::SDK notice: map measured with 10.0.401, this "
        "run used 10.0.502" in out[1])(run_main([], sdk="10.0.502", actions=True))),
    ("main emits no ::notice annotation outside GitHub Actions", lambda: (
        lambda out: out[0] == 0 and "SDK notice: map measured" in out[1] and "::notice" not in out[1])(
        run_main([], sdk="10.0.502"))),
    ("main emits no ::notice annotation when the SDK is the same", lambda: "::notice" not in run_main(
        [], actions=True)[1]),
    ("main emits no ::notice annotation on a red run, which leads with SDK changed instead", lambda: (
        lambda out: out[0] == 1 and "::notice" not in out[1] + out[2] and out[2].startswith("SDK changed"))(
        run_main([], sdk="10.0.502", actions=True, errors={"R1": set(), "R2": FOUND["R2"]}))),
    ("main --work keeps the SDK of a fresh run in sdk.txt", lambda: in_work({}, sdk="10.0.502")[3]["sdk.txt"] ==
     "10.0.502\n"),
    ("main --reuse reports the SDK kept beside the log, not the installed one", lambda: (
        lambda out: out[0] == 0 and "SDK: 10.0.300" in out[1] and "this run used 10.0.300" in out[1]
        and out[3]["sdk.txt"] == "10.0.300\n" and "stale" not in out[2])(
        in_work({"roslyn.sarif": "{}", "sdk.txt": "10.0.300\n"}, extra=["--reuse"], sdk="10.0.502"))),
    ("main --reuse of a directory with no sdk.txt says it is stale and falls back to the installed SDK", lambda: (
        lambda out: out[0] == 0 and "SDK: 10.0.502" in out[1] and "stale" in out[2] and "10.0.502" in out[2]
        and "sdk.txt" not in out[3])(
        in_work({"roslyn.sarif": "{}"}, extra=["--reuse"], sdk="10.0.502"))),
    ("main --reuse with no log to reuse measures again and keeps the installed SDK", lambda: (
        lambda out: out[0] == 0 and "SDK: 10.0.502" in out[1] and "stale" not in out[2]
        and out[3]["sdk.txt"] == "10.0.502\n")(
        in_work({"sdk.txt": "10.0.300\n"}, extra=["--reuse"], sdk="10.0.502"))),

    # --- the committed files
    ("the committed map covers every inspection ReSharper has a verdict on, with real reasons",
     committed_map_is_structurally_sound),
    ("the committed map matches expected.json's denominator", lambda: (
        lambda r: (r["compared"], r["excluded"], r["accounted"]) == (124, 36, 160))(
        evaluate(json.loads(module.EXPECTED.read_text(encoding="utf-8")),
                 json.loads(module.MAP.read_text(encoding="utf-8")), {}))),
    ("every committed scope own, gap and waiver carries a real reason and a pinned difference", lambda: (
        lambda m: all(reasoned(e.get("reason")) for e in m["inspections"].values() if e.get("scope"))
        and all(reasoned(g["reason"]) and (g["extra"] or g["missing"]) for g in m["gaps"].values())
        and all(reasoned(w["reason"]) and (w["extra"] or w["missing"]) for w in m["waivers"].values()))(
        json.loads(module.MAP.read_text(encoding="utf-8")))),
    ("corpus_markers reads the real corpus the gate classifies reports against", lambda: bool(
        corpus_markers(module.CORPUS))),
]


def check(label: str, case) -> bool:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
        try:
            passed = bool(case())
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
