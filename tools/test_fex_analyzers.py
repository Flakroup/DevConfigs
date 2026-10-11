#!/usr/bin/env python3
"""Behavioural test of the FEx.Analyzers wiring: build tiny consumers against the real props/targets.

``Directory.Build.targets`` adds a ``PackageReference`` to FEx.Analyzers (version ``$(FExAnalyzersVersion)``, a pin of its own)
and ``Directory.Build.props`` hands ``FEx.Analyzers.globalconfig`` to the compiler, which holds every rule at error. The
consumers, each a throwaway repository root with a copy of the shared files in its own ``DevConfigs`` directory:

* ``fex-clean`` has no violation: it builds, so a failure elsewhere is the analyzer's and not the fixture's.
* ``fex-error`` has a stray empty statement (FEX0011) and no ``TreatWarningsAsErrors``: the build still fails, because the
  rule is held at error by the global config and not by the consumer's warning policy.
* ``fex-off`` is that project built with ``-p:DevConfigsFExAnalyzers=false``: it builds green.
* ``fex-self`` is a project named ``FEx.Analyzers``: it does not reference its own package.
* ``fex-pin`` reads the ``PackageReference`` version: the default is the props file's pin, and
  ``-p:FExAnalyzersVersion`` overrides it.

Needs the .NET 10 SDK and network access for restore. Plain asserts, no test framework.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
SHARED = ["Directory.Build.props", "Directory.Build.targets", ".editorconfig", "build.globalconfig", "BannedSymbols.txt",
          "FEx.Analyzers.globalconfig"]
failures: list[str] = []

PROPS = """<Project>
    <Import Project="$(MSBuildThisFileDirectory)DevConfigs/Directory.Build.props"/>
</Project>
"""
TARGETS = """<Project>
    <Import Project="$(MSBuildThisFileDirectory)DevConfigs/Directory.Build.targets"/>
</Project>
"""
PROJECT = """<Project Sdk="Microsoft.NET.Sdk">
    <PropertyGroup>
        <TargetFramework>net10.0</TargetFramework>
    </PropertyGroup>
</Project>
"""
CLEAN = "namespace Lib;\n\npublic static class Marker\n{\n    public static void Run()\n    {\n    }\n}\n"
EMPTY_STATEMENT = "namespace Lib;\n\npublic static class Marker\n{\n    public static void Run()\n    {\n        ;\n    }\n}\n"


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print("      " + detail.strip().replace("\n", "\n      ")[:1500])


def consumer(root: Path, name: str, source: str) -> Path:
    """A repository root with the shared files and one project, ``name``, holding ``source``."""
    shutil.rmtree(root, ignore_errors=True)
    (root / "DevConfigs").mkdir(parents=True)
    for shared in SHARED:
        shutil.copy2(REPOSITORY / shared, root / "DevConfigs" / shared)
    (root / "Directory.Build.props").write_text(PROPS, encoding="utf-8")
    (root / "Directory.Build.targets").write_text(TARGETS, encoding="utf-8")
    (root / name).mkdir()
    (root / name / f"{name}.csproj").write_text(PROJECT, encoding="utf-8")
    (root / name / "Marker.cs").write_text(source, encoding="utf-8")
    return root / name / f"{name}.csproj"


def dotnet(project: Path, *args: str) -> tuple[int, str]:
    process = subprocess.run(["dotnet", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             cwd=project.parent)
    return process.returncode, process.stdout + process.stderr


def build(project: Path, *extra: str) -> tuple[int, str]:
    return dotnet(project, "build", str(project), "-nologo", "-v", "q", *extra)


def package_references(project: Path, *extra: str) -> dict[str, str]:
    """Identity -> Version of every PackageReference the project evaluates to."""
    code, output = dotnet(project, "msbuild", str(project), "-getItem:PackageReference", *extra)
    if code != 0 or "{" not in output:
        failures.append(f"msbuild -getItem failed for {project.name}")
        print(output[-1500:])
        return {}
    items = json.loads(output[output.index("{"):])["Items"].get("PackageReference", [])
    return {item["Identity"]: item.get("Version", "") for item in items}


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="devconfigs-fex-"))

    # The global config is per ID, not per category: FEX0001..FEX00NN with nothing missing, nothing else, all at error.
    entries = [line.strip() for line in (REPOSITORY / "FEx.Analyzers.globalconfig").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    rules = [re.fullmatch(r"dotnet_diagnostic\.FEX(\d{4})\.severity = error", line) for line in entries[1:]]
    check(entries[:1] == ["is_global = true"] and all(rules)
          and [int(rule.group(1)) for rule in rules if rule] == list(range(1, len(rules) + 1)),
          "shared: FEx.Analyzers.globalconfig holds FEX0001..FEX00NN, each at error, in order", "\n".join(entries))

    pin = re.search(r'<FExAnalyzersVersion Condition=[^>]*>([^<]+)</FExAnalyzersVersion>',
                    (REPOSITORY / "Directory.Build.props").read_text(encoding="utf-8"))
    check(pin is not None, "shared: Directory.Build.props pins FExAnalyzersVersion")
    default_version = pin.group(1) if pin else ""

    project = consumer(work / "fex-clean", "Lib", CLEAN)
    code, output = build(project)
    check(code == 0, "fex-clean: a project with no violation builds", output)

    project = consumer(work / "fex-error", "Lib", EMPTY_STATEMENT)
    code, output = build(project)
    check(code != 0 and "error FEX0011" in output,
          "fex-error: a stray empty statement fails the build with error FEX0011, without TreatWarningsAsErrors", output)

    project = consumer(work / "fex-off", "Lib", EMPTY_STATEMENT)
    code, output = build(project, "-p:DevConfigsFExAnalyzers=false")
    check(code == 0 and "FEX0011" not in output, "fex-off: -p:DevConfigsFExAnalyzers=false builds the same project green", output)
    check("FEx.Analyzers" not in package_references(project, "-p:DevConfigsFExAnalyzers=false"),
          "fex-off: the opt-out leaves no FEx.Analyzers reference")

    project = consumer(work / "fex-self", "FEx.Analyzers", CLEAN)
    check("FEx.Analyzers" not in package_references(project) and "FEx.Analyzers" in package_references(
        consumer(work / "fex-self-control", "Lib", CLEAN)),
          "fex-self: a project named FEx.Analyzers does not reference the package, an ordinary project does")

    project = consumer(work / "fex-pin", "Lib", CLEAN)
    check(default_version != "" and package_references(project).get("FEx.Analyzers") == default_version,
          f"fex-pin: the default version is the props file's pin ({default_version})", str(package_references(project)))
    other = "0.4.0-alpha.374"
    check(package_references(project, f"-p:FExAnalyzersVersion={other}").get("FEx.Analyzers") == other,
          f"fex-pin: -p:FExAnalyzersVersion={other} overrides it")

    shutil.rmtree(work, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
