#!/usr/bin/env python3
"""Behavioural test of the shared packaging defaults: build tiny projects against the real props/targets.

Reading the XML cannot tell whether a pack actually contains the docs - the SDK derives the doc-file
properties before ``Directory.Build.targets`` is imported, and one private SDK property decides whether
the ``.xml`` reaches the folder pack reads. So this builds ``tools/fixtures`` in Release, the way a
consumer with ``TreatWarningsAsErrors=true`` would, and asserts what comes out:

* Lib (netstandard2.0 + net10.0): an ``.xml`` and a ``.pdb`` per TFM, packed into a ``.nupkg`` and a ``.snupkg``;
  a plain ``dotnet build`` produces no package; CS1591 is reported and does not fail the build.
* NonPackableLib (IsPackable=false), Tests (*.Tests) and App (Exe): no docs, no embedded sources, no package.
* OptOuts: a project keeps what it sets itself (no docs, no embedded sources, no symbols, its own license).
* Embedded (DebugType=embedded): packs, without a symbol package (NU5017 otherwise).

Needs the .NET 10 SDK and network access for restore. Plain asserts, no test framework.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
failures: list[str] = []


def run(*args: str) -> tuple[int, str]:
    process = subprocess.run(["dotnet", *args], capture_output=True, text=True, cwd=FIXTURES)
    return process.returncode, process.stdout + process.stderr


def csproj(name: str) -> str:
    return str(FIXTURES / name / f"Fixture.{name}.csproj")


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print("      " + detail.strip().replace("\n", "\n      ")[:1500])


def properties(name: str, *names: str, framework: str = "net10.0") -> dict[str, str]:
    arguments = ["msbuild", csproj(name), "-p:Configuration=Release", f"-p:TargetFramework={framework}"]
    arguments += [f"-getProperty:{property_name}" for property_name in names]
    code, output = run(*arguments)
    if code != 0:
        failures.append(f"msbuild -getProperty failed for {name}")
        print(output)
        return {}
    return json.loads(output[output.index("{"):])["Properties"]


def build(name: str) -> tuple[int, str]:
    return run("build", csproj(name), "-c", "Release")


def pack(name: str, destination: Path) -> tuple[int, str]:
    return run("pack", csproj(name), "-c", "Release", "-o", str(destination))


def entries(package: Path) -> list[str]:
    with zipfile.ZipFile(package) as archive:
        return archive.namelist()


def files(name: str, pattern: str) -> list[Path]:
    return sorted((FIXTURES / name).glob(f"**/Release/**/{pattern}"))


def main() -> int:
    for directory in FIXTURES.glob("*/"):
        for leftover in ("bin", "obj"):
            shutil.rmtree(directory / leftover, ignore_errors=True)
    out = Path(tempfile.mkdtemp(prefix="devconfigs-fixtures-"))

    # --- Lib: the case the shared defaults exist for -------------------------------------------
    code, log = build("Lib")
    check(code == 0, "Lib: Release build succeeds with TreatWarningsAsErrors=true", log[-1500:])
    check(re.search(r"warning CS1591", log) is not None and not re.search(r"error CS1591", log),
          "Lib: CS1591 is reported as a warning, not an error")
    check(not files("Lib", "*.nupkg"), "Lib: a plain build produces no package (GeneratePackageOnBuild is not forced)")
    code, log = pack("Lib", out / "Lib")
    check(code == 0, "Lib: pack succeeds", log[-1500:])
    nupkg = next((out / "Lib").glob("*.nupkg"), None)
    snupkg = next((out / "Lib").glob("*.snupkg"), None)
    check(nupkg is not None and snupkg is not None, "Lib: pack produces a .nupkg and a .snupkg")
    for framework in ("net10.0", "netstandard2.0"):
        check(nupkg is not None and f"lib/{framework}/Fixture.Lib.xml" in entries(nupkg),
              f"Lib: the .nupkg holds the XML docs for {framework}")
        check(snupkg is not None and f"lib/{framework}/Fixture.Lib.pdb" in entries(snupkg),
              f"Lib: the .snupkg holds the pdb for {framework}")
    values = properties("Lib", "EmbedAllSources", "IncludeSymbols", "PublishRepositoryUrl", "SymbolPackageFormat",
                        "GeneratePackageOnBuild", "PackageLicenseExpression")
    check(values == {"EmbedAllSources": "true", "IncludeSymbols": "true", "PublishRepositoryUrl": "true",
                     "SymbolPackageFormat": "snupkg", "GeneratePackageOnBuild": "false",
                     "PackageLicenseExpression": "MIT"}, "Lib: packaging properties", str(values))

    # --- Things that must get nothing -----------------------------------------------------------
    for name, label in (("NonPackableLib", "IsPackable=false library"), ("Tests", "*.Tests project"),
                        ("App", "Exe application")):
        code, log = build(name)
        check(code == 0, f"{label}: Release build succeeds", log[-1500:])
        check(not files(name, "*.xml") and not files(name, "*.nupkg"),
              f"{label}: no XML docs and no package")
        values = properties(name, "GenerateDocumentationFile", "DocumentationFile", "EmbedAllSources",
                            "IncludeSymbols", "PackageLicenseExpression")
        check(values.get("GenerateDocumentationFile") == "false" and values.get("DocumentationFile") == ""
              and values.get("EmbedAllSources") == "" and values.get("IncludeSymbols") == ""
              and values.get("PackageLicenseExpression") == "",
              f"{label}: no docs, no embedded sources, no symbols, no license default", str(values))

    # --- A project keeps what it sets itself ---------------------------------------------------
    code, log = pack("OptOuts", out / "OptOuts")
    check(code == 0, "OptOuts: pack succeeds", log[-1500:])
    nupkg = next((out / "OptOuts").glob("*.nupkg"), None)
    check(nupkg is not None and not any(entry.endswith(".xml") and entry.startswith("lib/") for entry in entries(nupkg)),
          "OptOuts: DevConfigsSkipDocumentationFile=true keeps the docs out of the package")
    check(next((out / "OptOuts").glob("*.snupkg"), None) is None, "OptOuts: IncludeSymbols=false yields no .snupkg")
    values = properties("OptOuts", "EmbedAllSources", "IncludeSymbols", "PackageLicenseExpression")
    check(values == {"EmbedAllSources": "false", "IncludeSymbols": "false", "PackageLicenseExpression": "Apache-2.0"},
          "OptOuts: EmbedAllSources, IncludeSymbols and the license are the project's own", str(values))

    # --- DebugType=embedded --------------------------------------------------------------------
    code, log = pack("Embedded", out / "Embedded")
    check(code == 0 and "NU5017" not in log, "Embedded: pack succeeds, no NU5017", log[-1500:])
    nupkg = next((out / "Embedded").glob("*.nupkg"), None)
    check(nupkg is not None and "lib/net10.0/Fixture.Embedded.xml" in entries(nupkg), "Embedded: the .nupkg still holds the docs")
    check(next((out / "Embedded").glob("*.snupkg"), None) is None, "Embedded: no .snupkg is requested")

    shutil.rmtree(out, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
