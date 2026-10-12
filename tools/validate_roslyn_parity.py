#!/usr/bin/env python3
"""Hold the Roslyn analyzers to the ReSharper verdicts recorded on the analyzers corpus.

``analyzers-corpus/expected.json`` says, for every ERROR inspection of ``FEx.sln.DotSettings``, in which corpus
samples ReSharper reported it. Once ReSharper is gone that file is the specification, and this gate measures the
Roslyn side against it: the corpus is built in a throwaway consumer of this repository's shared files (so the
compiler, the SDK analyzers, FEx.Analyzers and the shared ``.editorconfig`` apply exactly as they do in a
consumer), and what Roslyn reports at error is compared with what ReSharper reported.

``analyzers-corpus/roslyn-map.json`` is the bridge, because no mechanical map between the two id spaces exists:

* ``inspections`` - ReSharper id -> ``{"roslyn": [ids]}``, plus ``"scope": "own"`` (with a ``reason``) for an
  inspection whose ReSharper verdict includes reports in samples of OTHER rules, which Roslyn has no reason to
  repeat: only the inspection's own samples are compared then;
* ``gaps`` - ReSharper id -> reason: Roslyn does not match yet. The gate tolerates it;
* ``waivers`` - ReSharper id -> ``{"extra": [...], "missing": [...], "reason": ...}``: a deliberate, exactly
  named difference. ``extra`` are samples Roslyn reports and ReSharper does not, ``missing`` the other way.

The denominator is the inspections that have a ReSharper verdict on a sample of their own. The ``silent`` and
``unsampled`` ones have none, so there is nothing to match: they are excluded and counted with the reason.

The ratchet - a gap that now matches, or a waiver that no longer describes the difference, fails the gate, so
the lists can only shrink. These also fail it: a new mismatch, an inspection with no entry in the map, a FEX
rule reported below error (the shared config is not reaching the compiler), AD0001 (an analyzer crashed), and a
canary (an engine that silently did not run would otherwise look like a wall of gaps).

Two engines: ``dotnet build`` writes a SARIF log, and ``dotnet format style --report`` supplies IDE0001, IDE0002,
IDE0003 and IDE0049, which Roslyn marks ``EnforceOnBuild.Never`` and so never reports in a build.

Modes: default runs the gate; ``--summary`` also prints one line per inspection; ``--propose`` prints a draft
``roslyn-map.json`` from the overlap between the two verdicts, for a person to check entry by entry.
``--json PATH`` writes the full result; with ``GITHUB_STEP_SUMMARY`` set the headline goes there too.
``--work DIR`` keeps the throwaway consumer in DIR and ``--reuse`` reads the engines' output already there.

Needs the .NET 10 SDK and network access for restore.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_analyzers_corpus import (  # noqa: E402
    CORPUS,
    EXPECTED,
    NOT_INPUTS,
    ROOT,
    corpus_markers,
    explained,
    marker_at,
    own_samples,
)

MAP = CORPUS / "roslyn-map.json"
SHARED = ["Directory.Build.props", "Directory.Build.targets", ".editorconfig", "build.globalconfig",
          "BannedSymbols.txt", "FEx.Analyzers.globalconfig"]
# The corpus builds in isolation with the two empty Directory.Build files; the copy takes the consumer's instead.
ISOLATING = ("Directory.Build.props", "Directory.Build.targets")
FORMAT_RULES = ("IDE0001", "IDE0002", "IDE0003", "IDE0049")
PROPS = ('<Project>\n  <PropertyGroup>\n    <DevConfigsMirrorEditorConfig>true</DevConfigsMirrorEditorConfig>\n'
         '  </PropertyGroup>\n  <Import Project="$(MSBuildThisFileDirectory)DevConfigs/Directory.Build.props"/>\n'
         '</Project>\n')
TARGETS = ('<Project>\n  <Import Project="$(MSBuildThisFileDirectory)DevConfigs/Directory.Build.targets"/>\n'
           '</Project>\n')
GITMODULES = '[submodule "DevConfigs"]\n\tpath = DevConfigs\n'
PLACEHOLDER = "TODO"


def owner_of(path: str, line: int, markers: dict[str, list[tuple[int, str]]]) -> str:
    """The sample ``<file>: <marker>`` a report at a line belongs to, or the bare file above its first marker.

    Spelled as ``expected.json`` spells it, so both engines' reports compare against the recording."""
    owner = marker_at(markers.get(path, []), line)
    return f"{path}: {owner}" if owner is not None else path


def relative_path(location: str, corpus: Path) -> str | None:
    """A SARIF uri or a file path as a forward-slash path under the corpus, or None for a file outside it."""
    if location.startswith("file:"):
        path = unquote(urlparse(location).path)
        if len(path) > 2 and path[0] == "/" and path[2] == ":":  # file:///C:/x on Windows
            path = path[1:]
    else:
        path = location
    try:
        return Path(path).resolve().relative_to(corpus.resolve()).as_posix()
    except ValueError:
        return None


def sarif_findings(sarif: dict, corpus: Path, markers: dict[str, list[tuple[int, str]]]
                   ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(errors, below)``: rule id -> samples it was reported in at error, and at any lower level.

    A report outside the corpus (a generated file) belongs to no sample and is dropped; one with no location
    (a project-level diagnostic) cannot be a sample verdict either."""
    errors: dict[str, set[str]] = {}
    below: dict[str, set[str]] = {}
    for run in sarif["runs"]:
        for result in run["results"]:
            target = errors if result.get("level") == "error" else below
            for location in result.get("locations", []):
                physical = location["physicalLocation"]
                path = relative_path(physical["artifactLocation"]["uri"], corpus)
                if path is not None:
                    target.setdefault(result["ruleId"], set()).add(
                        owner_of(path, physical["region"]["startLine"], markers))
    return errors, below


def format_findings(report: list[dict], corpus: Path, markers: dict[str, list[tuple[int, str]]]
                    ) -> dict[str, set[str]]:
    """Rule id -> samples ``dotnet format style --report`` listed it in, for the rules only that engine supplies."""
    found: dict[str, set[str]] = {rule: set() for rule in FORMAT_RULES}
    for entry in report:
        path = relative_path(entry["FilePath"], corpus)
        for change in entry["FileChanges"]:
            if path is not None and change["DiagnosticId"] in found:
                found[change["DiagnosticId"]].add(owner_of(path, change["LineNumber"], markers))
    return {rule: owners for rule, owners in found.items() if owners}


def rule_levels(sarif: dict) -> dict[str, set[str]]:
    """Rule id -> the levels it was reported at, over every result (a result with no location included)."""
    levels: dict[str, set[str]] = {}
    for run in sarif["runs"]:
        for result in run["results"]:
            levels.setdefault(result["ruleId"], set()).add(result.get("level", "warning"))
    return levels


def engine_problems(levels: dict[str, set[str]], format_report: dict[str, set[str]], build_code: int) -> list[str]:
    """Problems with the engines themselves: a crashed analyzer, a FEX rule below error, a canary that is silent."""
    problems = []
    if "AD0001" in levels:
        problems.append("AD0001: an analyzer crashed on the corpus (see roslyn.sarif)")
    for rule in sorted(levels):
        if rule.startswith("FEX") and levels[rule] != {"error"}:
            problems.append(f"{rule}: reported at {'/'.join(sorted(levels[rule] - {'error'}))}, not error - "
                            "FEx.Analyzers.globalconfig is not reaching the compiler")
    # A canary per engine: a rule that is certain to fire, so an engine that did not run is told apart from a
    # wall of honest gaps. The build must also FAIL: the corpus violates error-level rules on purpose.
    if build_code == 0:
        problems.append("canary: the corpus built green, but it violates error-level rules on purpose")
    for label, prefix in (("the compiler", "CS"), ("the SDK analyzers", "IDE"), ("FEx.Analyzers", "FEX")):
        if not any(rule.startswith(prefix) and "error" in found for rule, found in levels.items()):
            problems.append(f"canary: {label} reported nothing at error ({prefix}*), so the engine did not run")
    for rule in FORMAT_RULES:
        if rule not in format_report:
            problems.append(f"canary: dotnet format style reported no {rule}, so the second engine did not run")
    return problems


def sides(expected_owners: list[str], found: set[str], inspection: str, scope: str | None
          ) -> tuple[set[str], set[str]]:
    """``(extra, missing)``: samples Roslyn reported and ReSharper did not, and the other way round."""
    wanted = set(expected_owners)
    if scope == "own":
        wanted, found = in_scope(inspection, wanted, wanted), in_scope(inspection, found, wanted)
    return found - wanted, wanted - found


def in_scope(inspection: str, owners: set[str], wanted: set[str]) -> set[str]:
    """The owners ``scope: own`` compares: the inspection's own samples, and the bare files ReSharper names.

    A bare file is a report above the first marker of a file (a using directive, a stub): no sample claims it, so
    it is compared where ReSharper's verdict names it and ignored where it does not."""
    return set(own_samples(inspection, sorted(owners))) | {owner for owner in owners & wanted if ": " not in owner}


def map_problems(mapping: dict, compared: list[str]) -> list[str]:
    """Problems with the shape of ``roslyn-map.json``, before any verdict is looked at."""
    problems = []
    inspections, gaps, waivers = (mapping.get(key, {}) for key in ("inspections", "gaps", "waivers"))
    for inspection, entry in sorted(inspections.items()):
        ids = entry.get("roslyn")
        if not isinstance(ids, list) or not all(isinstance(rule, str) and rule for rule in ids):
            problems.append(f"{inspection}: 'roslyn' must be a list of rule ids")
        if entry.get("scope") not in (None, "own"):
            problems.append(f"{inspection}: scope must be 'own' or absent")
        if entry.get("scope") == "own" and not reasoned(entry.get("reason")):
            problems.append(f"{inspection}: scope 'own' needs a reason")
        if inspection not in compared:
            problems.append(f"{inspection}: mapped, but it is not an inspection with a ReSharper verdict on its own sample")
    for inspection, reason in sorted(gaps.items()):
        if not reasoned(reason):
            problems.append(f"{inspection}: a gap needs a real reason")
        if inspection not in inspections:
            problems.append(f"{inspection}: a gap without an entry under inspections (use an empty 'roslyn' list)")
    for inspection, waiver in sorted(waivers.items()):
        if not reasoned(waiver.get("reason")):
            problems.append(f"{inspection}: a waiver needs a real reason")
        if not waiver.get("extra") and not waiver.get("missing"):
            problems.append(f"{inspection}: a waiver must name at least one extra or missing sample")
        if inspection not in inspections:
            problems.append(f"{inspection}: a waiver without an entry under inspections")
        if inspection in gaps:
            problems.append(f"{inspection}: both a gap and a waiver - pick one")
    return problems


def reasoned(reason: object) -> bool:
    """Whether a reason is real: text, and not the placeholder ``--propose`` writes."""
    return explained({"r": reason}, "r") and not str(reason).strip().startswith(PLACEHOLDER)


def evaluate(expected: dict, mapping: dict, errors: dict[str, set[str]]) -> dict:
    """The verdict of every compared inspection and the problems, as one JSON-ready result."""
    recorded = expected["inspections"]
    silent, unsampled = expected.get("silent", {}), expected.get("unsampled", {})
    compared = sorted(inspection for inspection, owners in recorded.items() if own_samples(inspection, owners))
    excluded = {inspection: ("silent" if inspection in silent else "unsampled")
                for inspection in sorted(recorded) if inspection not in compared}
    problems = map_problems(mapping, compared)
    for inspection, kind in excluded.items():
        if not explained(silent if kind == "silent" else unsampled, inspection):
            problems.append(f"{inspection}: excluded but neither silent nor unsampled gives a reason")

    entries, gaps, waivers = mapping.get("inspections", {}), mapping.get("gaps", {}), mapping.get("waivers", {})
    results: dict[str, dict] = {}
    for inspection in compared:
        entry = entries.get(inspection)
        if entry is None:
            problems.append(f"{inspection}: unmapped - add it to roslyn-map.json under inspections")
            results[inspection] = {"status": "unmapped"}
            continue
        found = set().union(*(errors.get(rule, set()) for rule in entry.get("roslyn", [])))
        extra, missing = sides(recorded[inspection], found, inspection, entry.get("scope"))
        result = {"status": "exact", "roslyn": entry.get("roslyn", []), "scope": entry.get("scope") or "all",
                  "extra": sorted(extra), "missing": sorted(missing)}
        results[inspection] = result
        waiver = waivers.get(inspection)
        if waiver is not None:
            waived_extra, waived_missing = set(waiver.get("extra", [])), set(waiver.get("missing", []))
            if not extra and not missing:
                result["status"] = "fail"
                problems.append(f"{inspection}: stale waiver - Roslyn now matches ReSharper exactly, remove it")
            elif (extra, missing) == (waived_extra, waived_missing):
                result["status"] = "waived"
            else:
                result["status"] = "fail"
                problems.append(f"{inspection}: the difference is not the waived one - "
                                f"new extra {sorted(extra - waived_extra)}, new missing {sorted(missing - waived_missing)}, "
                                f"waived but gone: extra {sorted(waived_extra - extra)}, "
                                f"missing {sorted(waived_missing - missing)}")
        elif inspection in gaps:
            if extra or missing:
                result["status"] = "gap"
            else:
                result["status"] = "fail"
                problems.append(f"{inspection}: listed as a gap, but Roslyn now matches ReSharper exactly - "
                                "remove it from gaps")
        elif extra or missing:
            result["status"] = "fail"
            problems.append(f"{inspection}: mismatch - Roslyn ({', '.join(entry.get('roslyn', [])) or 'no rule'}) "
                            f"extra {sorted(extra)}, missing {sorted(missing)}")
    for kind, names in (("gaps", gaps), ("waivers", waivers)):
        for inspection in sorted(names):
            if inspection not in recorded:
                problems.append(f"{inspection}: under {kind}, but expected.json has no such inspection")

    counts = {status: sum(1 for result in results.values() if result["status"] == status)
              for status in ("exact", "waived", "gap", "fail", "unmapped")}
    reasons = {kind: sum(1 for value in excluded.values() if value == kind) for kind in ("silent", "unsampled")}
    return {"compared": len(compared), "exact": counts["exact"], "waived": counts["waived"], "gaps": counts["gap"],
            "failing": counts["fail"] + counts["unmapped"], "excluded": len(excluded), "excludedBy": reasons,
            "accounted": len(compared) + len(excluded), "total": len(recorded),
            "headline": headline(counts["exact"], len(compared), counts["waived"], counts["gap"], len(excluded),
                                 len(compared) + len(excluded)),
            "inspections": results, "excludedInspections": excluded, "problems": problems}


def headline(exact: int, compared: int, waived: int, gaps: int, excluded: int, accounted: int) -> str:
    return (f"Roslyn parity: {exact}/{compared} exact (+{waived} waived, {gaps} gaps); "
            f"{excluded} excluded; {accounted} accounted")


def propose(expected: dict, errors: dict[str, set[str]]) -> dict:
    """A draft ``roslyn-map.json``: per inspection the Roslyn rules whose reports overlap ReSharper's best.

    Greedy over the rules that fire in at least one own sample of the inspection, by Jaccard overlap with the
    ReSharper verdict; a rule joins the set while it raises the overlap. What is not exact becomes a waiver
    (Roslyn reports something comparable) or a gap (no rule fires there), with a placeholder reason: the draft
    is a start, and every entry needs a person to read the difference."""
    recorded = expected["inspections"]
    draft: dict = {"inspections": {}, "gaps": {}, "waivers": {}}

    def overlap(wanted: set[str], found: set[str]) -> float:
        return len(wanted & found) / len(wanted | found) if wanted | found else 0.0

    for inspection in sorted(recorded):
        if not own_samples(inspection, recorded[inspection]):
            continue
        wanted, own = set(recorded[inspection]), set(own_samples(inspection, recorded[inspection]))
        candidates = sorted((rule for rule, owners in errors.items() if owners & own),
                            key=lambda rule: (-overlap(wanted, errors[rule]), rule))
        chosen: list[str] = []
        found: set[str] = set()
        for rule in candidates:
            if not chosen or overlap(wanted, found | errors[rule]) > overlap(wanted, found):
                chosen.append(rule)
                found |= errors[rule]
        scope = None
        if found != wanted and set(own_samples(inspection, sorted(found))) == own:
            scope = "own"
        extra, missing = sides(recorded[inspection], found, inspection, scope)
        draft["inspections"][inspection] = {"roslyn": sorted(chosen)}
        if scope:
            draft["inspections"][inspection] |= {"scope": scope, "reason": f"{PLACEHOLDER}: collateral reports"}
        if not chosen:
            draft["gaps"][inspection] = f"{PLACEHOLDER}: no Roslyn rule reports a sample of this inspection"
        elif extra or missing:
            draft["waivers"][inspection] = {"extra": sorted(extra), "missing": sorted(missing),
                                            "reason": f"{PLACEHOLDER}: explain the difference"}
    return draft


def consumer(work: Path, corpus: Path = CORPUS, repository: Path = ROOT) -> Path:
    """A throwaway consumer: the shared files in ``DevConfigs``, the mirror switch on, the corpus beside them.

    Returns the copied project. The corpus drops its isolating Directory.Build files and ``expected.json``."""
    shutil.rmtree(work, ignore_errors=True)
    (work / "DevConfigs").mkdir(parents=True)
    for shared in SHARED:
        shutil.copy2(repository / shared, work / "DevConfigs" / shared)
    (work / "Directory.Build.props").write_text(PROPS, encoding="utf-8")
    (work / "Directory.Build.targets").write_text(TARGETS, encoding="utf-8")
    (work / ".gitmodules").write_text(GITMODULES, encoding="utf-8")
    shutil.copytree(corpus, work / "Corpus", ignore=shutil.ignore_patterns(*NOT_INPUTS, *ISOLATING))
    return work / "Corpus" / "Corpus.csproj"


def run_engines(work: Path, run=subprocess.run) -> int:
    """Build the consumer's corpus for its SARIF log, then ask ``dotnet format style`` for the rules a build skips.

    Returns the build's exit code, which is non-zero by design. The build restores; format reuses that."""
    project = consumer(work)
    environment = os.environ | {"DOTNET_CLI_UI_LANGUAGE": "en", "DOTNET_NOLOGO": "1"}
    sarif = work / "roslyn.sarif"
    build = run(["dotnet", "build", str(project), "-nologo", "-v", "q", f"-p:ErrorLog={sarif}%2Cversion=2.1"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment)
    if not sarif.exists():
        raise SystemExit(f"dotnet build wrote no SARIF log (exit {build.returncode}):\n"
                         + (build.stdout + build.stderr)[-3000:])
    # --verify-no-changes: report, never rewrite. Its exit code is non-zero whenever there is something to report.
    formatted = run(["dotnet", "format", "style", str(project), "--diagnostics", *FORMAT_RULES, "--severity", "info",
                     "--verify-no-changes", "--no-restore", "--report", str(work / "format")],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment)
    if not (work / "format" / "format-report.json").exists():
        raise SystemExit(f"dotnet format style wrote no report (exit {formatted.returncode}):\n"
                         + (formatted.stdout + formatted.stderr)[-3000:])
    return build.returncode


def measure(work: Path, reuse: bool) -> tuple[dict[str, set[str]], list[str]]:
    """Run (or reuse) both engines; return the rule -> samples map at error and the engine problems."""
    if reuse and (work / "roslyn.sarif").exists():
        build_code = 1
    else:
        build_code = run_engines(work)
    markers = corpus_markers(CORPUS)
    sarif = json.loads((work / "roslyn.sarif").read_text(encoding="utf-8-sig"))
    report = json.loads((work / "format" / "format-report.json").read_text(encoding="utf-8-sig"))
    errors, _ = sarif_findings(sarif, work / "Corpus", markers)
    from_format = format_findings(report, work / "Corpus", markers)
    for rule, owners in from_format.items():
        errors.setdefault(rule, set()).update(owners)
    return errors, engine_problems(rule_levels(sarif), from_format, build_code)


def summary_lines(result: dict) -> list[str]:
    lines = []
    for inspection, found in result["inspections"].items():
        lines.append(f"{found['status']:<8} {inspection}  <- {', '.join(found.get('roslyn', [])) or '-'}"
                     + (f"  [{found['scope']}]" if found.get("scope") == "own" else ""))
    return lines


def markdown(result: dict, mapping: dict, seconds: float) -> str:
    out = [f"### {result['headline']}", "",
           f"Excluded: {result['excludedBy']['silent']} silent, {result['excludedBy']['unsampled']} unsampled "
           f"(ReSharper has no verdict on a sample of their own). Measured in {seconds:.0f} s.", ""]
    if result["problems"]:
        out += [f"**{len(result['problems'])} problem(s):**", ""] + [f"- {problem}" for problem in result["problems"]] + [""]
    if mapping.get("gaps"):
        out += ["<details><summary>Gaps</summary>", ""] + [f"- `{i}`: {r}" for i, r in sorted(mapping["gaps"].items())]
        out += ["", "</details>", ""]
    if mapping.get("waivers"):
        out += ["<details><summary>Waivers</summary>", ""]
        out += [f"- `{i}`: {w['reason']}" for i, w in sorted(mapping["waivers"].items())] + ["", "</details>", ""]
    return "\n".join(out) + "\n"


def main(arguments: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # absent when a caller swaps stdout for a buffer
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--propose", action="store_true", help="print a draft roslyn-map.json")
    parser.add_argument("--summary", action="store_true", help="also print one line per inspection")
    parser.add_argument("--json", metavar="PATH", help="write the full result as JSON")
    parser.add_argument("--work", metavar="DIR", help="keep the throwaway consumer in DIR")
    parser.add_argument("--reuse", action="store_true", help="read the engines' output already in --work")
    options = parser.parse_args(arguments)

    started = time.monotonic()
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    own_work = options.work is None
    work = Path(tempfile.mkdtemp(prefix="devconfigs-roslyn-")) if own_work else Path(options.work)
    try:
        errors, engine = measure(work, options.reuse)
    finally:
        if own_work:
            shutil.rmtree(work, ignore_errors=True)
    if options.propose:
        print(json.dumps(propose(expected, errors), indent=2, ensure_ascii=False))
        return 0

    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    result = evaluate(expected, mapping, errors)
    result["problems"] += engine
    seconds = time.monotonic() - started
    result["seconds"] = round(seconds, 1)
    if options.summary:
        print("\n".join(summary_lines(result)))
    for problem in result["problems"]:
        print(problem, file=sys.stderr)
    print(result["headline"])
    if options.json:
        Path(options.json).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                      newline="\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8", newline="\n") as step:
            step.write(markdown(result, mapping, seconds))
    print("OK" if not result["problems"] else f"{len(result['problems'])} problem(s)")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
