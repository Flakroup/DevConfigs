#!/usr/bin/env python3
"""Tests for validate_roslyn_parity. Plain asserts, no test framework - CI needs nothing installed.

The engines (dotnet build, dotnet format) are never run here: the verdict logic is pure and takes their output as
data, and the two functions that shell out take an injected ``run``."""

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
    engine_problems,
    evaluate,
    format_findings,
    headline,
    in_scope,
    main,
    map_problems,
    markdown,
    owner_of,
    propose,
    reasoned,
    relative_path,
    rule_levels,
    run_engines,
    sarif_findings,
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
EXACT = {"Alpha": {"roslyn": ["R1"]}, "Beta": {"roslyn": ["R2"], "scope": "own", "reason": "R2 repeats Alpha"}}
FOUND = {"R1": {"A.cs: Alpha"}, "R2": {"A.cs: Beta #1", "A.cs: Alpha", "A.cs"}}
LEVELS = {"CS0001": {"error"}, "IDE0007": {"error", "suggestion"}, "FEX0001": {"error"}}
FORMAT = {rule: {"A.cs: Alpha"} for rule in module.FORMAT_RULES}


def mapping(**changes) -> dict:
    return {"inspections": json.loads(json.dumps(EXACT)), "gaps": {}, "waivers": {}} | changes


def verdict(map_=None, errors=None, expected=None) -> dict:
    return evaluate(expected or EXPECTED, map_ or mapping(), FOUND if errors is None else errors)


def status(result: dict, inspection: str) -> str:
    return result["inspections"][inspection]["status"]


def has(result: dict, text: str) -> bool:
    return any(text in problem for problem in result["problems"])


def sarif(*results: tuple[str, str, str, int]) -> dict:
    """A SARIF log of (rule, level, uri, line) results; a uri of None is a result with no location."""
    return {"runs": [{"results": [
        {"ruleId": rule, "level": level, **({"locations": [{"physicalLocation": {
            "artifactLocation": {"uri": uri}, "region": {"startLine": line}}}]} if uri else {})}
        for rule, level, uri, line in results]}]}


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


def run_main(arguments, expected=EXPECTED, map_=None, errors=None, engine=None, summary=None):
    """Run main() on in-memory data; returns (exit code, stdout, stderr, files written)."""
    def go(folder: Path):
        expected_path, map_path = folder / "expected.json", folder / "roslyn-map.json"
        expected_path.write_text(json.dumps(expected), encoding="utf-8")
        map_path.write_text(json.dumps(map_ or mapping()), encoding="utf-8")
        environment = {"GITHUB_STEP_SUMMARY": str(folder / "step.md")} if summary else {}
        saved = os.environ.pop("GITHUB_STEP_SUMMARY", None)
        os.environ.update(environment)
        out, err = io.StringIO(), io.StringIO()
        try:
            with patched(EXPECTED=expected_path, MAP=map_path,
                         measure=lambda work, reuse: (FOUND if errors is None else errors, engine or [])):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(arguments(folder) if callable(arguments) else arguments)
        finally:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            if saved is not None:
                os.environ["GITHUB_STEP_SUMMARY"] = saved
        files = {path.name: path.read_text(encoding="utf-8") for path in folder.iterdir()}
        return code, out.getvalue(), err.getvalue(), files
    return in_corpus(go)


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


def engines_in_scratch(run):
    def go(folder: Path):
        try:
            return run_engines(folder / "work", run=run), None
        except SystemExit as exit_:
            return None, str(exit_)
    return in_corpus(go)


def consumer_tree(inspect):
    """Build a consumer from a fake repository and corpus, and return ``inspect(work, project)``."""
    def go(folder: Path):
        corpus, repository = folder / "corpus", folder / "repo"
        corpus.mkdir()
        repository.mkdir()
        for name in module.SHARED:
            (repository / name).write_text(f"shared {name}", encoding="utf-8")
        for name, text in (("Corpus.csproj", "<Project/>"), ("A.cs", "class A { }"), ("expected.json", "{}"),
                           ("roslyn-map.json", "{}"), ("Directory.Build.props", "<isolating/>"),
                           ("Directory.Build.targets", "<isolating/>")):
            (corpus / name).write_text(text, encoding="utf-8")
        (corpus / "obj").mkdir()
        (corpus / "obj" / "x.cs").write_text("junk", encoding="utf-8")
        project = module.consumer(folder / "work", corpus, repository)
        return inspect(folder / "work", project)
    return in_corpus(go)


def committed_map_is_complete() -> bool:
    expected = json.loads(module.EXPECTED.read_text(encoding="utf-8"))
    committed = json.loads(module.MAP.read_text(encoding="utf-8"))
    result = evaluate(expected, committed, {})
    return not [p for p in result["problems"] if "mismatch" not in p and "listed as a gap" not in p
                and "stale waiver" not in p and "not the waived one" not in p]


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

    # --- reading the engines' output
    ("sarif_findings splits error from lower levels",
     lambda: in_corpus(lambda c: sarif_findings(
         sarif(("R1", "error", (c / "A.cs").as_uri(), 5), ("R2", "warning", (c / "A.cs").as_uri(), 9)), c, MARKERS)
         == ({"R1": {"A.cs: Alpha"}}, {"R2": {"A.cs: Beta #1"}}))),
    ("sarif_findings treats a result with no level as below error",
     lambda: in_corpus(lambda c: sarif_findings(
         {"runs": [{"results": [{"ruleId": "R1", "locations": [{"physicalLocation": {
             "artifactLocation": {"uri": (c / "A.cs").as_uri()}, "region": {"startLine": 5}}}]}]}]},
         c, MARKERS) == ({}, {"R1": {"A.cs: Alpha"}}))),
    ("sarif_findings drops a result with no location",
     lambda: in_corpus(lambda c: sarif_findings(sarif(("R1", "error", None, 0)), c, MARKERS) == ({}, {}))),
    ("sarif_findings drops a result outside the corpus",
     lambda: in_corpus(lambda c: sarif_findings(
         sarif(("R1", "error", (c.parent / "G.cs").as_uri(), 5)), c, MARKERS) == ({}, {}))),
    ("sarif_findings collects every run",
     lambda: in_corpus(lambda c: sarif_findings(
         {"runs": sarif(("R1", "error", (c / "A.cs").as_uri(), 5))["runs"]
          + sarif(("R1", "error", (c / "A.cs").as_uri(), 9))["runs"]}, c, MARKERS)[0]
         == {"R1": {"A.cs: Alpha", "A.cs: Beta #1"}})),
    ("format_findings keeps only the rules a build skips",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c / "A.cs"), "FileChanges": [
             {"DiagnosticId": "IDE0001", "LineNumber": 5}, {"DiagnosticId": "IDE0007", "LineNumber": 5},
             {"DiagnosticId": "IDE0049", "LineNumber": 9}]}], c, MARKERS)
         == {"IDE0001": {"A.cs: Alpha"}, "IDE0049": {"A.cs: Beta #1"}})),
    ("format_findings drops a file outside the corpus",
     lambda: in_corpus(lambda c: format_findings(
         [{"FilePath": str(c.parent / "G.cs"), "FileChanges": [{"DiagnosticId": "IDE0001", "LineNumber": 1}]}],
         c, MARKERS) == {})),
    ("rule_levels collects levels over results with and without a location",
     lambda: rule_levels(sarif(("R1", "error", None, 0), ("R1", "warning", None, 0)))
     == {"R1": {"error", "warning"}}),

    # --- the engines themselves
    ("engine_problems is empty when both engines ran", lambda: engine_problems(LEVELS, FORMAT, 1) == []),
    ("engine_problems reports AD0001", lambda: any(
        p.startswith("AD0001") for p in engine_problems(LEVELS | {"AD0001": {"error"}}, FORMAT, 1))),
    ("engine_problems reports a FEX rule at warning only", lambda: any(
        p.startswith("FEX0002") for p in engine_problems(LEVELS | {"FEX0002": {"warning"}}, FORMAT, 1))),
    ("engine_problems reports a FEX rule at both error and warning", lambda: any(
        p.startswith("FEX0001") for p in engine_problems(LEVELS | {"FEX0001": {"error", "warning"}}, FORMAT, 1))),
    ("engine_problems accepts a non-FEX rule below error", lambda: engine_problems(
        LEVELS | {"CA9999": {"warning"}}, FORMAT, 1) == []),
    ("engine_problems fails a build that exited green", lambda: any(
        "built green" in p for p in engine_problems(LEVELS, FORMAT, 0))),
    ("engine_problems fails a silent compiler", lambda: any(
        "compiler" in p for p in engine_problems({"IDE0007": {"error"}, "FEX0001": {"error"}}, FORMAT, 1))),
    ("engine_problems fails silent SDK analyzers", lambda: any(
        "SDK analyzers" in p for p in engine_problems({"CS0001": {"error"}, "FEX0001": {"error"}}, FORMAT, 1))),
    ("engine_problems fails a silent FEx.Analyzers", lambda: any(
        "FEx.Analyzers" in p for p in engine_problems({"CS0001": {"error"}, "IDE0007": {"error"}}, FORMAT, 1))),
    ("engine_problems counts only error as proof an engine ran", lambda: any(
        "compiler" in p for p in engine_problems(
            {"CS0001": {"warning"}, "IDE0007": {"error"}, "FEX0001": {"error"}}, FORMAT, 1))),
    ("engine_problems fails each missing dotnet format rule", lambda: [
        rule for rule in module.FORMAT_RULES
        if any(rule in p for p in engine_problems(LEVELS, {r: o for r, o in FORMAT.items() if r != rule}, 1))]
     == list(module.FORMAT_RULES)),

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
    ("in_scope keeps a bare file only where ReSharper names it",
     lambda: in_scope("Alpha", {"A.cs", "B.cs", "A.cs: Alpha"}, {"A.cs", "A.cs: Alpha"}) == {"A.cs", "A.cs: Alpha"}),
    ("in_scope drops a bare file ReSharper does not name",
     lambda: in_scope("Alpha", {"B.cs", "A.cs: Alpha"}, {"A.cs: Alpha"}) == {"A.cs: Alpha"}),

    # --- the shape of the map
    ("map_problems is empty for a sound map", lambda: map_problems(mapping(), ["Alpha", "Beta"]) == []),
    ("map_problems rejects roslyn that is not a list of ids", lambda: any(
        "'roslyn'" in p for p in map_problems(mapping(inspections={"Alpha": {"roslyn": "R1"}}), ["Alpha"]))),
    ("map_problems rejects an empty rule id", lambda: any(
        "'roslyn'" in p for p in map_problems(mapping(inspections={"Alpha": {"roslyn": [""]}}), ["Alpha"]))),
    ("map_problems rejects a scope other than own", lambda: any(
        "scope must be" in p for p in map_problems(
            mapping(inspections={"Alpha": {"roslyn": [], "scope": "all"}}), ["Alpha"]))),
    ("map_problems wants a reason for scope own", lambda: any(
        "needs a reason" in p for p in map_problems(
            mapping(inspections={"Alpha": {"roslyn": [], "scope": "own"}}), ["Alpha"]))),
    ("map_problems rejects a TODO reason for scope own", lambda: any(
        "needs a reason" in p for p in map_problems(
            mapping(inspections={"Alpha": {"roslyn": [], "scope": "own", "reason": "TODO: later"}}), ["Alpha"]))),
    ("map_problems rejects an inspection that is not compared", lambda: any(
        "not an inspection with a ReSharper verdict" in p
        for p in map_problems(mapping(inspections={"Quiet": {"roslyn": []}}), ["Alpha"]))),
    ("map_problems wants a real reason on a gap", lambda: any(
        "real reason" in p for p in map_problems(mapping(
            inspections={"Alpha": {"roslyn": []}}, gaps={"Alpha": " "}), ["Alpha"]))),
    ("map_problems rejects a TODO gap", lambda: any(
        "real reason" in p for p in map_problems(mapping(
            inspections={"Alpha": {"roslyn": []}}, gaps={"Alpha": "TODO: no Roslyn rule"}), ["Alpha"]))),
    ("map_problems wants a gap to have an inspections entry", lambda: any(
        "without an entry" in p for p in map_problems(mapping(inspections={}, gaps={"Alpha": "why"}), ["Alpha"]))),
    ("map_problems wants a real reason on a waiver", lambda: any(
        "real reason" in p for p in map_problems(mapping(
            waivers={"Alpha": {"extra": ["A.cs"], "reason": ""}}), ["Alpha", "Beta"]))),
    ("map_problems wants a waiver to name a sample", lambda: any(
        "at least one" in p for p in map_problems(mapping(
            waivers={"Alpha": {"reason": "why"}}), ["Alpha", "Beta"]))),
    ("map_problems wants a waiver to have an inspections entry", lambda: any(
        "without an entry" in p for p in map_problems(mapping(
            inspections={}, waivers={"Alpha": {"extra": ["A.cs"], "reason": "why"}}), ["Alpha"]))),
    ("map_problems rejects a gap that is also a waiver", lambda: any(
        "both a gap and a waiver" in p for p in map_problems(mapping(
            gaps={"Alpha": "why"}, waivers={"Alpha": {"extra": ["A.cs"], "reason": "why"}}), ["Alpha", "Beta"]))),
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
    ("evaluate: scope own ignores collateral on the mapped rule", lambda: (
        status(verdict(errors=FOUND | {"R2": {"A.cs: Beta #1", "A.cs: Gamma"}}), "Beta") == "exact")),
    ("evaluate: a gap that still differs is tolerated", lambda: (
        lambda r: status(r, "Alpha") == "gap" and r["gaps"] == 1 and r["problems"] == [] and r["failing"] == 0)(
        verdict(map_=mapping(gaps={"Alpha": "no rule"}), errors=FOUND | {"R1": set()}))),
    ("evaluate: a gap that now passes fails the ratchet", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "now matches ReSharper exactly - remove it from gaps"))(
        verdict(map_=mapping(gaps={"Alpha": "no rule"})))),
    ("evaluate: a gap does not hide a different mismatch of another inspection", lambda: (
        lambda r: status(r, "Beta") == "fail" and status(r, "Alpha") == "gap")(
        verdict(map_=mapping(gaps={"Alpha": "no rule"}), errors={"R1": set(), "R2": set()}))),
    ("evaluate: the exact waived difference is tolerated", lambda: (
        lambda r: status(r, "Alpha") == "waived" and r["waived"] == 1 and r["problems"] == [])(
        verdict(map_=mapping(waivers={"Alpha": {"extra": ["A.cs: Alpha #2"], "reason": "why"}}),
                errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #2"}}))),
    ("evaluate: a waiver on a missing sample is tolerated", lambda: status(verdict(
        map_=mapping(waivers={"Alpha": {"missing": ["A.cs: Alpha"], "reason": "why"}}),
        errors=FOUND | {"R1": set()}), "Alpha") == "waived"),
    ("evaluate: a stale waiver fails the ratchet", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "stale waiver"))(
        verdict(map_=mapping(waivers={"Alpha": {"extra": ["A.cs: Alpha #2"], "reason": "why"}})))),
    ("evaluate: a waiver that no longer describes the difference fails", lambda: (
        lambda r: status(r, "Alpha") == "fail" and has(r, "not the waived one"))(
        verdict(map_=mapping(waivers={"Alpha": {"extra": ["A.cs: Alpha #2"], "reason": "why"}}),
                errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #3"}}))),
    ("evaluate: a waiver does not cover an additional difference", lambda: status(verdict(
        map_=mapping(waivers={"Alpha": {"extra": ["A.cs: Alpha #2"], "reason": "why"}}),
        errors=FOUND | {"R1": {"A.cs: Alpha", "A.cs: Alpha #2", "A.cs: Alpha #3"}}), "Alpha") == "fail"),
    ("evaluate: a gap on an inspection that does not exist fails", lambda: has(
        verdict(map_=mapping(gaps={"Ghost": "why"}, inspections=EXACT | {"Ghost": {"roslyn": []}})),
        "expected.json has no such inspection")),
    ("evaluate: an exclusion without a reason fails", lambda: has(verdict(
        expected=EXPECTED | {"silent": {"Quiet": " "}}), "Quiet: excluded but neither")),
    ("evaluate: a problem in the map surfaces", lambda: has(verdict(
        map_=mapping(gaps={"Alpha": "TODO"})), "Alpha: a gap needs a real reason")),
    ("evaluate: the failing count is the failures plus the unmapped", lambda: verdict(
        map_=mapping(inspections={}), errors={})["failing"] == 2),

    # --- drafting a map
    ("propose maps an exact rule", lambda: propose(EXPECTED, FOUND)["inspections"]["Alpha"] == {"roslyn": ["R1"]}),
    ("propose adds scope own when collateral is the only difference", lambda: (
        lambda d: d["inspections"]["Beta"]["scope"] == "own" and d["inspections"]["Beta"]["reason"].startswith("TODO")
        and "Beta" not in d["waivers"])(propose(EXPECTED, FOUND))),
    ("propose drafts a gap when no rule fires in an own sample", lambda: (
        lambda d: d["inspections"]["Alpha"] == {"roslyn": []} and d["gaps"]["Alpha"].startswith("TODO"))(
        propose(EXPECTED, {}))),
    ("propose drafts a waiver for an unexplained difference", lambda: (
        lambda d: d["waivers"]["Alpha"]["extra"] == ["A.cs: Alpha #2"])(
        propose(EXPECTED, {"R1": {"A.cs: Alpha", "A.cs: Alpha #2"}}))),
    ("propose skips the inspections it cannot compare", lambda: (
        lambda d: "Quiet" not in d["inspections"] and "Missing" not in d["inspections"])(propose(EXPECTED, FOUND))),
    ("propose never writes a draft that passes map_problems", lambda: map_problems(
        propose(EXPECTED, {}), ["Alpha", "Beta"]) != []),
    ("propose joins a second rule while it raises the overlap", lambda: propose(
        {"inspections": {"Alpha": ["A.cs: Alpha", "B.cs: Alpha"]}},
        {"R1": {"A.cs: Alpha"}, "R2": {"B.cs: Alpha"}})["inspections"]["Alpha"]["roslyn"] == ["R1", "R2"]),

    # --- the consumer tree and the engines
    ("consumer copies the shared files under DevConfigs", lambda: consumer_tree(
        lambda work, _: all((work / "DevConfigs" / name).read_text() == f"shared {name}" for name in module.SHARED))),
    ("consumer writes the mirror switch, the imports and .gitmodules", lambda: consumer_tree(
        lambda work, _: (work / "Directory.Build.props").read_text() == module.PROPS
        and (work / "Directory.Build.targets").read_text() == module.TARGETS
        and (work / ".gitmodules").read_text() == module.GITMODULES)),
    ("consumer's props switch the mirror on before importing DevConfigs", lambda: (
        module.PROPS.index("DevConfigsMirrorEditorConfig>true") < module.PROPS.index("DevConfigs/Directory.Build.props"))),
    ("consumer copies the corpus without its recording or the isolating files", lambda: consumer_tree(
        lambda work, _: sorted(p.name for p in (work / "Corpus").iterdir()) == ["A.cs", "Corpus.csproj"])),
    ("consumer returns the copied project", lambda: consumer_tree(
        lambda work, project: project == work / "Corpus" / "Corpus.csproj")),
    ("run_engines builds, then formats, and returns the build's exit code", lambda: (
        lambda run: engines_in_scratch(run)[0] == 1 and [c[1] for c in run.calls] == ["build", "format"])(
        fake_run())),
    ("run_engines asks format for exactly the four skipped rules", lambda: (
        lambda run: (engines_in_scratch(run), run.calls[1])[1][
            run.calls[1].index("--diagnostics") + 1:run.calls[1].index("--diagnostics") + 5]
        == list(module.FORMAT_RULES))(fake_run())),
    ("run_engines formats read-only", lambda: (
        lambda run: (engines_in_scratch(run), "--verify-no-changes" in run.calls[1])[1])(fake_run())),
    ("run_engines fails loudly when the build wrote no SARIF log", lambda: (
        lambda out: out[0] is None and "wrote no SARIF log" in out[1])(engines_in_scratch(fake_run(sarif_text=None)))),
    ("run_engines fails loudly when format wrote no report", lambda: (
        lambda out: out[0] is None and "wrote no report" in out[1])(engines_in_scratch(fake_run(report=False)))),

    # --- reporting
    ("headline spells the contract", lambda: headline(102, 124, 1, 21, 36, 160)
     == "Roslyn parity: 102/124 exact (+1 waived, 21 gaps); 36 excluded; 160 accounted"),
    ("summary_lines marks the scope and the rules", lambda: summary_lines(verdict()) == [
        "exact    Alpha  <- R1", "exact    Beta  <- R2  [own]"]),
    ("summary_lines shows a gap with no rule as a dash", lambda: summary_lines(verdict(
        map_=mapping(inspections={"Alpha": {"roslyn": []}, "Beta": EXACT["Beta"]}, gaps={"Alpha": "none"}),
        errors=FOUND))[0] == "gap      Alpha  <- -"),
    ("markdown carries the headline, the exclusions, the gaps and the waivers", lambda: (
        lambda text: all(part in text for part in (
            "### Roslyn parity: 2/2 exact", "1 silent, 1 unsampled", "`Alpha`: no rule", "`Beta`: why")))(
        markdown(verdict(), mapping(gaps={"Alpha": "no rule"}, waivers={"Beta": {"extra": ["x"], "reason": "why"}}),
                 3.2))),
    ("markdown lists the problems", lambda: "- Alpha: unmapped" in markdown(
        verdict(map_=mapping(inspections={"Beta": EXACT["Beta"]})), mapping(), 1.0)),

    # --- main
    ("main exits 0 and prints the headline when the gate is green", lambda: (
        lambda out: out[0] == 0 and out[1].splitlines() == [
            "Roslyn parity: 2/2 exact (+0 waived, 0 gaps); 2 excluded; 4 accounted", "OK"])(
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
    ("main --propose prints a draft and exits 0 without judging", lambda: (
        lambda out: out[0] == 0 and json.loads(out[1])["inspections"]["Alpha"] == {"roslyn": ["R1"]})(
        run_main(["--propose"], map_={"inspections": {}}))),
    ("main rejects an unknown argument with a usage error", lambda: exit_code(lambda: run_main(["--nope"])) == 2),

    # --- the committed files
    ("the committed map covers every inspection ReSharper has a verdict on, with real reasons",
     committed_map_is_complete),
    ("the committed map matches expected.json's denominator", lambda: (
        lambda r: (r["compared"], r["excluded"], r["accounted"]) == (124, 36, 160))(
        evaluate(json.loads(module.EXPECTED.read_text(encoding="utf-8")),
                 json.loads(module.MAP.read_text(encoding="utf-8")), {}))),
    ("every committed scope own, gap and waiver carries a real reason", lambda: (
        lambda m: all(reasoned(e.get("reason")) for e in m["inspections"].values() if e.get("scope"))
        and all(reasoned(r) for r in m["gaps"].values())
        and all(reasoned(w["reason"]) for w in m["waivers"].values()))(
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
