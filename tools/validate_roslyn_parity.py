#!/usr/bin/env python3
"""Hold the Roslyn analyzers to the ReSharper verdicts recorded on the analyzers corpus.

``analyzers-corpus/expected.json`` says, for every ERROR inspection of ``FEx.sln.DotSettings``, in which corpus
samples ReSharper reported it. Once ReSharper is gone that file is the specification, and this gate measures the
Roslyn side against it: the corpus is built in a throwaway consumer of this repository's shared files (so the
compiler, the SDK analyzers, FEx.Analyzers and the shared ``.editorconfig`` apply exactly as they do in a
consumer), and what Roslyn reports at error is compared with what ReSharper reported.

``analyzers-corpus/roslyn-map.json`` is the bridge, because no mechanical map between the two id spaces exists:

* ``inspections`` - ReSharper id -> ``{"roslyn": [ids]}``, plus ``"scope": "own"`` for an inspection whose ReSharper
  verdict includes reports in samples of OTHER rules. Only the inspection's own samples are held to the verdict then,
  and the difference outside them, in both directions (``extra``: Roslyn reports it, ReSharper does not;
  ``missing``: the other way), is pinned exactly under ``"outside": {"extra": [...], "missing": [...]}``, with a
  ``reason``. Any change of that set fails, and so does a scope that hides nothing;
* ``gaps`` - ReSharper id -> ``{"reason": ..., "extra": [...], "missing": [...]}``: Roslyn does not match yet, and
  the measured difference is pinned. The gate tolerates exactly that difference: it fails when the difference grew
  or shrank, and when it is gone (the gap is then closed - remove it);
* ``waivers`` - the same shape: a deliberate, exactly named difference. It fails when it no longer describes the
  difference, and when there is none left.

The denominator is the inspections that have a ReSharper verdict on a sample of their own. The ``silent`` and
``unsampled`` ones have none, so there is nothing to match: they are excluded and counted with the reason. Together
the two sets must be exactly the keys of ``expected.json``, disjoint, or it is a problem.

The ratchet - a gap or waiver that stops describing the difference exactly, in either direction, fails the gate, so
the lists can only shrink. These also fail it: a new mismatch, an inspection with no entry in the map, a FEX
rule or one of the ``dotnet format`` rules reported below error (the shared config is not reaching the compiler),
AD0001 (an analyzer crashed; the exception text is in the problem), and a canary (an engine that silently did not
run would otherwise look like a wall of gaps). A suppressed result counts as not reported.

Two engines: ``dotnet build`` writes a SARIF log, and ``dotnet format style --report`` supplies IDE0001, IDE0002,
IDE0003 and IDE0049, which Roslyn marks ``EnforceOnBuild.Never`` and so never reports in a build.

Modes: default runs the gate; ``--summary`` also prints one line per inspection; ``--propose`` prints a draft
``roslyn-map.json`` from the overlap between the two verdicts, for a person to check entry by entry.
``--json PATH`` writes the full result; with ``GITHUB_STEP_SUMMARY`` set the headline goes there too, red or green.
``--work DIR`` keeps the throwaway consumer in DIR (an empty directory, or one this tool made before: it refuses
any other and deletes only its own files) and ``--reuse`` reads the engines' output already there.

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
WORK_MARKER = ".roslyn-parity-work"
# What the tool writes into a work directory, and so the only things it ever deletes there.
WORK_FILES = ("DevConfigs", "Corpus", "format", "roslyn.sarif", ".editorconfig", ".gitmodules",
              "Directory.Build.props", "Directory.Build.targets", WORK_MARKER)


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


def reported(sarif: dict):
    """The SARIF results that count: a suppressed one (``#pragma``, ``SuppressMessage``) is not reported."""
    for run in sarif["runs"]:
        for result in run["results"]:
            if not result.get("suppressions"):
                yield result


def sarif_findings(sarif: dict, corpus: Path, markers: dict[str, list[tuple[int, str]]]) -> dict[str, set[str]]:
    """Rule id -> the samples it was reported in at error.

    A report outside the corpus (a generated file) belongs to no sample and is dropped; one with no location
    (a project-level diagnostic) cannot be a sample verdict either."""
    errors: dict[str, set[str]] = {}
    for result in reported(sarif):
        if result.get("level") != "error":
            continue
        for location in result.get("locations", []):
            physical = location["physicalLocation"]
            path = relative_path(physical["artifactLocation"]["uri"], corpus)
            if path is not None:
                errors.setdefault(result["ruleId"], set()).add(
                    owner_of(path, physical["region"]["startLine"], markers))
    return errors


def format_findings(report: list[dict], corpus: Path, markers: dict[str, list[tuple[int, str]]]
                    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(errors, levels)`` from ``dotnet format style --report``, for the rules only that engine supplies.

    ``errors``: rule id -> samples it listed at error. ``levels``: rule id -> every level it was listed at, read
    from the first word of ``FormatDescription`` (``error IDE0001: ...``); ``--severity info`` makes the report
    carry the lower ones too, which are not verdicts."""
    errors: dict[str, set[str]] = {}
    levels: dict[str, set[str]] = {}
    for entry in report:
        path = relative_path(entry["FilePath"], corpus)
        for change in entry["FileChanges"]:
            rule = change["DiagnosticId"]
            if path is None or rule not in FORMAT_RULES:
                continue
            level = (change.get("FormatDescription") or "").split(None, 1)[0:1]
            level = level[0].lower() if level else "unknown"
            levels.setdefault(rule, set()).add(level)
            if level == "error":
                errors.setdefault(rule, set()).add(owner_of(path, change["LineNumber"], markers))
    return errors, levels


def rule_levels(sarif: dict) -> dict[str, set[str]]:
    """Rule id -> the levels it was reported at, over every result (a result with no location included)."""
    levels: dict[str, set[str]] = {}
    for result in reported(sarif):
        levels.setdefault(result["ruleId"], set()).add(result.get("level", "warning"))
    return levels


def analyzer_crashes(sarif: dict) -> list[str]:
    """The message of every AD0001 result: the exception an analyzer died with."""
    texts: list[str] = []
    for result in reported(sarif):
        if result["ruleId"] == "AD0001":
            text = str(result.get("message", {}).get("text", "")).strip()[:2000]
            if text and text not in texts:
                texts.append(text)
    return texts


def engine_problems(levels: dict[str, set[str]], format_errors: dict[str, set[str]],
                    format_levels: dict[str, set[str]], build_code: int, crashes: list[str]) -> list[str]:
    """Problems with the engines themselves: a crashed analyzer, a rule below error, a canary that is silent."""
    problems = []
    if "AD0001" in levels:
        for text in crashes or ["(the log carries no message)"]:
            problems.append(f"AD0001: an analyzer crashed on the corpus: {text}")
    for rule in sorted(levels):
        if rule.startswith("FEX") and levels[rule] != {"error"}:
            problems.append(f"{rule}: reported at {'/'.join(sorted(levels[rule] - {'error'}))}, not error - "
                            "FEx.Analyzers.globalconfig is not reaching the compiler")
    for rule in sorted(format_levels):
        if format_levels[rule] != {"error"}:
            problems.append(f"{rule}: dotnet format reported it at {'/'.join(sorted(format_levels[rule] - {'error'}))}, "
                            "not error - the shared .editorconfig is not reaching it")
    # A canary per engine: a rule that is certain to fire, so an engine that did not run is told apart from a
    # wall of honest gaps. The build must also FAIL: the corpus violates error-level rules on purpose.
    if build_code == 0:
        problems.append("canary: the corpus built green, but it violates error-level rules on purpose")
    for label, prefix in (("the compiler", "CS"), ("the SDK analyzers", "IDE"), ("FEx.Analyzers", "FEX")):
        if not any(rule.startswith(prefix) and "error" in found for rule, found in levels.items()):
            problems.append(f"canary: {label} reported nothing at error ({prefix}*), so the engine did not run")
    for rule in FORMAT_RULES:
        if rule not in format_errors:
            problems.append(f"canary: dotnet format style reported no {rule} at error, so the second engine did not run")
    return problems


def sides(expected_owners: list[str], found: set[str], inspection: str, scope: str | None
          ) -> tuple[set[str], set[str]]:
    """``(extra, missing)``: samples Roslyn reported and ReSharper did not, and the other way round."""
    wanted = set(expected_owners)
    if scope == "own":
        wanted, found = in_scope(inspection, wanted, wanted), in_scope(inspection, found, wanted)
    return found - wanted, wanted - found


def outside(expected_owners: list[str], found: set[str], inspection: str) -> tuple[set[str], set[str]]:
    """``(extra, missing)`` that ``scope: own`` leaves out of the comparison: all of it, minus the own part."""
    everything, own = sides(expected_owners, found, inspection, None), sides(expected_owners, found, inspection, "own")
    return everything[0] - own[0], everything[1] - own[1]


def in_scope(inspection: str, owners: set[str], wanted: set[str]) -> set[str]:
    """The owners ``scope: own`` compares: the inspection's own samples, and the bare files ReSharper names.

    A bare file is a report above the first marker of a file (a using directive, a stub): no sample claims it, so
    it is compared where ReSharper's verdict names it and ignored where it does not."""
    return set(own_samples(inspection, sorted(owners))) | {owner for owner in owners & wanted if ": " not in owner}


def pinned(entry: dict) -> tuple[set[str], set[str]]:
    """The ``(extra, missing)`` a gap, a waiver or an ``outside`` records."""
    return set(entry.get("extra", [])), set(entry.get("missing", []))


def list_problems(inspection: str, kind: str, entry: object) -> list[str]:
    """Problems with the shape of a gap, a waiver or an ``outside``: an object of lists of sample names."""
    if not isinstance(entry, dict):
        return [f"{inspection}: a {kind} is an object with a reason, extra and missing"]
    return [f"{inspection}: {kind} '{key}' must be a list of sample names"
            for key in ("extra", "missing")
            if not isinstance(entry.get(key, []), list)
            or not all(isinstance(owner, str) and owner for owner in entry.get(key, []))]


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
        if entry.get("scope") == "own":
            if not reasoned(entry.get("reason")):
                problems.append(f"{inspection}: scope 'own' needs a reason")
            problems += list_problems(inspection, "scope 'own' outside", entry.get("outside"))
        elif "outside" in entry:
            problems.append(f"{inspection}: 'outside' belongs to scope 'own' only")
        if inspection not in compared:
            problems.append(f"{inspection}: mapped, but it is not an inspection with a ReSharper verdict on its own sample")
    for kind, entries in (("gap", gaps), ("waiver", waivers)):
        for inspection, entry in sorted(entries.items()):
            problems += list_problems(inspection, kind, entry)
            if not isinstance(entry, dict):
                continue
            if not reasoned(entry.get("reason")):
                problems.append(f"{inspection}: a {kind} needs a real reason")
            if not entry.get("extra") and not entry.get("missing"):
                problems.append(f"{inspection}: a {kind} must name at least one extra or missing sample")
            if inspection not in inspections:
                problems.append(f"{inspection}: a {kind} without an entry under inspections"
                                + (" (use an empty 'roslyn' list)" if kind == "gap" else ""))
    for inspection in sorted(set(gaps) & set(waivers)):
        problems.append(f"{inspection}: both a gap and a waiver - pick one")
    return problems


def reasoned(reason: object) -> bool:
    """Whether a reason is real: text, and not the placeholder ``--propose`` writes."""
    return explained({"r": reason}, "r") and not str(reason).strip().startswith(PLACEHOLDER)


def drift(label: str, found: tuple[set[str], set[str]], recorded: tuple[set[str], set[str]]) -> str:
    """Why a difference is no longer the recorded one, in both directions."""
    return (f"{label}: the difference is not the recorded one - new extra {sorted(found[0] - recorded[0])}, "
            f"new missing {sorted(found[1] - recorded[1])}, recorded but gone: extra {sorted(recorded[0] - found[0])}, "
            f"missing {sorted(recorded[1] - found[1])}")


def evaluate(expected: dict, mapping: dict, errors: dict[str, set[str]]) -> dict:
    """The verdict of every compared inspection and the problems, as one JSON-ready result."""
    recorded = expected["inspections"]
    silent, unsampled = expected.get("silent", {}), expected.get("unsampled", {})
    compared = sorted(inspection for inspection, owners in recorded.items() if own_samples(inspection, owners))
    excluded = {inspection: kind for kind, names in (("unsampled", unsampled), ("silent", silent)) for inspection in names}
    problems = map_problems(mapping, compared)
    # Accounting: compared and excluded are two disjoint sets, and together exactly the recorded inspections.
    for inspection in sorted(set(silent) & set(unsampled)):
        problems.append(f"{inspection}: listed under both silent and unsampled")
    for inspection in sorted(set(compared) & set(excluded)):
        problems.append(f"{inspection}: has a ReSharper verdict on a sample of its own, yet is excluded as "
                        f"{excluded[inspection]}")
    for inspection in sorted(set(recorded) - set(compared) - set(excluded)):
        problems.append(f"{inspection}: neither compared nor excluded - it has no verdict on its own sample "
                        "and is under neither silent nor unsampled")
    for inspection in sorted(set(excluded) - set(recorded)):
        problems.append(f"{inspection}: excluded as {excluded[inspection]}, but expected.json has no such inspection")
    for inspection, kind in sorted(excluded.items()):
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
        scope_problem = None
        if entry.get("scope") == "own":
            outer = outside(recorded[inspection], found, inspection)
            result["outside"] = {"extra": sorted(outer[0]), "missing": sorted(outer[1])}
            if not outer[0] and not outer[1]:
                scope_problem = f"{inspection}: scope 'own' hides nothing - remove it"
            elif outer != pinned(entry.get("outside") or {}):
                scope_problem = drift(f"{inspection} (outside its own samples)", outer,
                                      pinned(entry.get("outside") or {}))
        waiver, gap = waivers.get(inspection), gaps.get(inspection)
        if waiver is not None:
            if not extra and not missing:
                result["status"] = "fail"
                problems.append(f"{inspection}: stale waiver - Roslyn now matches ReSharper exactly, remove it")
            elif (extra, missing) == pinned(waiver):
                result["status"] = "waived"
            else:
                result["status"] = "fail"
                problems.append(drift(f"{inspection} (waiver)", (extra, missing), pinned(waiver)))
        elif gap is not None:
            if not extra and not missing:
                result["status"] = "fail"
                problems.append(f"{inspection}: listed as a gap, but Roslyn now matches ReSharper exactly - "
                                "remove it from gaps")
            elif (extra, missing) == pinned(gap):
                result["status"] = "gap"
            else:
                result["status"] = "fail"
                problems.append(drift(f"{inspection} (gap)", (extra, missing), pinned(gap)))
        elif extra or missing:
            result["status"] = "fail"
            problems.append(f"{inspection}: mismatch - Roslyn ({', '.join(entry.get('roslyn', [])) or 'no rule'}) "
                            f"extra {sorted(extra)}, missing {sorted(missing)}")
        if scope_problem:
            result["status"] = "fail"
            problems.append(scope_problem)
    for kind, names in (("gaps", gaps), ("waivers", waivers)):
        for inspection in sorted(names):
            if inspection not in recorded:
                problems.append(f"{inspection}: under {kind}, but expected.json has no such inspection")

    counts = {status: sum(1 for result in results.values() if result["status"] == status)
              for status in ("exact", "waived", "gap", "fail", "unmapped")}
    reasons = {kind: sum(1 for value in excluded.values() if value == kind) for kind in ("silent", "unsampled")}
    accounted = len(set(compared) | set(excluded))
    return {"compared": len(compared), "exact": counts["exact"], "waived": counts["waived"], "gaps": counts["gap"],
            "failing": counts["fail"] + counts["unmapped"], "excluded": len(excluded), "excludedBy": reasons,
            "accounted": accounted, "total": len(recorded),
            "headline": headline(counts["exact"], len(compared), counts["waived"], counts["gap"], len(excluded),
                                 accounted),
            "inspections": results, "excludedInspections": dict(sorted(excluded.items())), "problems": problems}


def headline(exact: int, compared: int, waived: int, gaps: int, excluded: int, accounted: int) -> str:
    return (f"Roslyn parity: {exact}/{compared} exact (+{waived} waived, {gaps} gaps); "
            f"{excluded} excluded; {accounted} accounted")


def propose(expected: dict, errors: dict[str, set[str]]) -> dict:
    """A draft ``roslyn-map.json``: per inspection the Roslyn rules whose reports overlap ReSharper's best.

    Greedy over the rules that fire in at least one own sample of the inspection, by Jaccard overlap with the
    ReSharper verdict; a rule joins the set while it raises the overlap. What is not exact becomes a waiver
    (Roslyn reports something comparable) or a gap (no rule fires there), with a placeholder reason: the draft
    is a start, and every entry needs a person to read the difference. The differences are pinned as measured."""
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
            outer = outside(recorded[inspection], found, inspection)
            draft["inspections"][inspection] |= {
                "scope": scope, "reason": f"{PLACEHOLDER}: collateral reports",
                "outside": {"extra": sorted(outer[0]), "missing": sorted(outer[1])}}
        if extra or missing:
            kind = "waivers" if chosen else "gaps"
            note = "explain the difference" if chosen else "no Roslyn rule reports a sample of this inspection"
            draft[kind][inspection] = {"reason": f"{PLACEHOLDER}: {note}", "extra": sorted(extra),
                                       "missing": sorted(missing)}
    return draft


def claim_work(work: Path) -> None:
    """Make ``work`` ready for a fresh consumer without ever deleting what is not the tool's own.

    A directory that is not empty and carries no marker was not made by this tool, so it is refused: ``--work``
    pointed at a scratch folder must not wipe it. What the tool wrote before (``WORK_FILES``) is cleared."""
    if work.exists() and any(work.iterdir()) and not (work / WORK_MARKER).exists():
        raise SystemExit(f"{work} is not empty and is not a work directory of this tool - refusing to clear it. "
                         "Name an empty or new directory.")
    work.mkdir(parents=True, exist_ok=True)
    for name in WORK_FILES:
        target = work / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    (work / WORK_MARKER).write_text("made by tools/validate_roslyn_parity.py; --work may clear what it wrote\n",
                                    encoding="utf-8")
    (work / "DevConfigs").mkdir()


def consumer(work: Path, corpus: Path = CORPUS, repository: Path = ROOT) -> Path:
    """A throwaway consumer: the shared files in ``DevConfigs``, the mirror switch on, the corpus beside them.

    Returns the copied project. The corpus drops its isolating Directory.Build files and ``expected.json``."""
    claim_work(work)
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


def measure(work: Path, reuse: bool, corpus: Path = CORPUS) -> tuple[dict[str, set[str]], list[str]]:
    """Run (or reuse) both engines; return the rule -> samples map at error and the engine problems."""
    if reuse and (work / "roslyn.sarif").exists():
        build_code = 1  # the build's exit code is not kept; the corpus fails it by design
    else:
        build_code = run_engines(work)
    markers = corpus_markers(corpus)
    sarif = json.loads((work / "roslyn.sarif").read_text(encoding="utf-8-sig"))
    report = json.loads((work / "format" / "format-report.json").read_text(encoding="utf-8-sig"))
    errors = sarif_findings(sarif, work / "Corpus", markers)
    format_errors, format_levels = format_findings(report, work / "Corpus", markers)
    for rule, owners in format_errors.items():
        errors.setdefault(rule, set()).update(owners)
    return errors, engine_problems(rule_levels(sarif), format_errors, format_levels, build_code,
                                   analyzer_crashes(sarif))


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
        out += ["<details><summary>Gaps</summary>", ""]
        out += [f"- `{i}`: {gap['reason']}" for i, gap in sorted(mapping["gaps"].items())]
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
    parser.add_argument("--work", metavar="DIR", help="keep the throwaway consumer in DIR (empty, or made by this tool)")
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
