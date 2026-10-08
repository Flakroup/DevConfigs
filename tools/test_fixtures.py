#!/usr/bin/env python3
"""Behavioural test of the shared packaging defaults, plus the TRX report name every ``*.Tests`` project passes to
``dotnet test``: build tiny projects against the real props/targets.

Reading the XML cannot tell whether a pack actually contains the docs, symbols and SourceLink, so this builds
``tools/fixtures`` in Release the way a consumer with ``TreatWarningsAsErrors=true`` would, and asserts what comes out.

Four repository roots mirror the kinds of consumer (``enabled``, ``disabled``, ``explicit-docs``, ``late-switch``):

* ``enabled`` sets ``DevConfigsPackageDefaults=true`` (a library repository). ``Lib`` gets an ``.xml`` per TFM, a ``.nupkg``
  and a ``.snupkg`` whose pdbs carry SourceLink and the embedded sources, a ``<repository>`` in the nuspec, and CS1591 stays a
  warning. ``NonPackableLib`` (IsPackable=false), ``Tests`` (*.Tests) and ``App`` (Exe) get nothing, and their pdb holds no
  source. ``OptOuts``, ``ExplicitFalse``, ``LicenseFile`` and ``OwnDocFile`` keep what the project sets itself. ``Embedded``
  (DebugType=embedded) packs without a symbol package, ``NoPdb`` (DebugType=none) builds without /embed.
* ``disabled`` does not opt in: an application and its internal library get nothing, and the library's pdb holds no source.

Needs the .NET 10 SDK and network access for restore. SourceLink needs a git repository with a remote, as in CI.
Plain asserts, no test framework.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
failures: list[str] = []

PDB_SCANNER = r'''using System.IO.Compression;
using System.Reflection.Metadata;
using System.Text;

var embeddedSource = new Guid("0E8A571B-6926-466E-B4AD-8AB04611F5FE");
var sourceLink = new Guid("CC110556-A091-4D38-9FEC-25AB9A351A6A");
foreach (var path in args)
{
    using var stream = File.OpenRead(path);
    using var provider = MetadataReaderProvider.FromPortablePdbStream(stream);
    var reader = provider.GetMetadataReader();
    var hasSourceLink = false;
    var embedded = 0;
    var marker = false;
    foreach (var handle in reader.CustomDebugInformation)
    {
        var entry = reader.GetCustomDebugInformation(handle);
        var kind = reader.GetGuid(entry.Kind);
        if (kind == sourceLink)
        {
            hasSourceLink = true;
        }
        else if (kind == embeddedSource)
        {
            embedded++;
            var blob = reader.GetBlobBytes(entry.Value);
            var length = BitConverter.ToInt32(blob, 0);
            byte[] data;
            if (length == 0)
            {
                data = blob[4..];
            }
            else
            {
                using var deflate = new DeflateStream(new MemoryStream(blob, 4, blob.Length - 4), CompressionMode.Decompress);
                using var buffer = new MemoryStream();
                deflate.CopyTo(buffer);
                data = buffer.ToArray();
            }
            if (Encoding.UTF8.GetString(data).Contains("PROPRIETARY_MARKER", StringComparison.Ordinal)) marker = true;
        }
    }
    Console.WriteLine($"{hasSourceLink}|{embedded}|{marker}");
}
'''


def run(*args: str, cwd: Path = FIXTURES) -> tuple[int, str]:
    process = subprocess.run(["dotnet", *args], capture_output=True, text=True, cwd=cwd)
    return process.returncode, process.stdout + process.stderr


def csproj(root: str, name: str) -> str:
    return str(FIXTURES / root / name / f"Fixture.{name}.csproj")


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print("      " + detail.strip().replace("\n", "\n      ")[:1500])


def properties(root: str, name: str, *names: str) -> dict[str, str]:
    arguments = ["msbuild", csproj(root, name), "-p:Configuration=Release", "-p:TargetFramework=net10.0"]
    arguments += [f"-getProperty:{property_name}" for property_name in names]
    code, output = run(*arguments)
    if code != 0:
        failures.append(f"msbuild -getProperty failed for {root}/{name}")
        print(output)
        return {}
    if "{" not in output:  # msbuild prints a bare value when a single property is asked for
        return {names[0]: output.strip()}
    return json.loads(output[output.index("{"):])["Properties"]


def build(root: str, name: str) -> tuple[int, str]:
    return run("build", csproj(root, name), "-c", "Release")


def pack(root: str, name: str, destination: Path) -> tuple[int, str]:
    return run("pack", csproj(root, name), "-c", "Release", "-o", str(destination))


def entries(package: Path | None) -> list[str]:
    if package is None:
        return []
    with zipfile.ZipFile(package) as archive:
        return archive.namelist()


def read(package: Path | None, entry: str) -> str:
    if package is None:
        return ""
    with zipfile.ZipFile(package) as archive:
        return archive.read(entry).decode("utf-8", "replace")


def produced(root: str, name: str, pattern: str) -> list[Path]:
    return sorted((FIXTURES / root / name).glob(f"**/Release/**/{pattern}"))


def scan(scanner: Path, pdbs: list[Path]) -> list[tuple[bool, int, bool]]:
    """Return (has SourceLink, embedded source count, marker found in an embedded source) per pdb."""
    if not pdbs:
        return []
    code, output = run("run", str(scanner), "--", *map(str, pdbs), cwd=scanner.parent)
    rows = [line.split("|") for line in output.splitlines() if line.count("|") == 2]
    if code != 0 or len(rows) != len(pdbs):
        failures.append("pdb scanner failed")
        print(output[-1500:])
        return []
    return [(row[0] == "True", int(row[1]), row[2] == "True") for row in rows]


def unpacked_pdbs(snupkg: Path | None, into: Path) -> list[Path]:
    if snupkg is None:
        return []
    with zipfile.ZipFile(snupkg) as archive:
        archive.extractall(into)
    return sorted(into.glob("**/*.pdb"))


def main() -> int:
    for directory in FIXTURES.glob("*/*/"):
        for leftover in ("bin", "obj", "own-docs"):
            shutil.rmtree(directory / leftover, ignore_errors=True)
    out = Path(tempfile.mkdtemp(prefix="devconfigs-fixtures-"))
    (out / "scanner").mkdir()
    scanner = out / "scanner" / "pdbscan.cs"
    scanner.write_text(PDB_SCANNER, encoding="utf-8")
    nothing = {"GenerateDocumentationFile": "false", "DocumentationFile": "", "EmbedAllSources": "",
               "IncludeSymbols": "", "PackageLicenseExpression": ""}
    names = tuple(nothing)

    # --- enabled/Lib: the case the shared defaults exist for ------------------------------------
    code, log = build("enabled", "Lib")
    check(code == 0, "Lib: Release build succeeds with TreatWarningsAsErrors=true", log[-1500:])
    check(re.search(r"warning CS1591", log) is not None and not re.search(r"error CS1591", log),
          "Lib: CS1591 is reported as a warning, not an error")
    check(not produced("enabled", "Lib", "*.nupkg"), "Lib: a plain build produces no package (GeneratePackageOnBuild is not forced)")
    code, log = pack("enabled", "Lib", out / "Lib")
    check(code == 0, "Lib: pack succeeds", log[-1500:])
    nupkg = next((out / "Lib").glob("*.nupkg"), None)
    snupkg = next((out / "Lib").glob("*.snupkg"), None)
    check(nupkg is not None and snupkg is not None, "Lib: pack produces a .nupkg and a .snupkg")
    for framework in ("net10.0", "netstandard2.0"):
        check(f"lib/{framework}/Fixture.Lib.xml" in entries(nupkg), f"Lib: the .nupkg holds the XML docs for {framework}")
        check(f"lib/{framework}/Fixture.Lib.pdb" in entries(snupkg), f"Lib: the .snupkg holds the pdb for {framework}")
    nuspec = next((entry for entry in entries(nupkg) if entry.endswith(".nuspec")), "")
    check("<repository type=\"git\"" in read(nupkg, nuspec) and "<license type=\"expression\">MIT</license>" in read(nupkg, nuspec),
          "Lib: the nuspec carries the repository (SourceLink) and the MIT license", read(nupkg, nuspec))
    rows = scan(scanner, unpacked_pdbs(snupkg, out / "Lib-pdbs"))
    check(len(rows) == 2 and all(row == (True, row[1], True) and row[1] > 0 for row in rows),
          "Lib: every pdb carries SourceLink and the embedded sources (marker found in them)", str(rows))
    values = properties("enabled", "Lib", "EmbedAllSources", "IncludeSymbols", "PublishRepositoryUrl", "SymbolPackageFormat",
                        "GeneratePackageOnBuild", "PackageLicenseExpression", "DeterministicSourcePaths", "EnableSourceLink",
                        "PackageRequireLicenseAcceptance")
    check(values == {"EmbedAllSources": "true", "IncludeSymbols": "true", "PublishRepositoryUrl": "true",
                     "SymbolPackageFormat": "snupkg", "GeneratePackageOnBuild": "false", "PackageLicenseExpression": "MIT",
                     "DeterministicSourcePaths": "true", "EnableSourceLink": "true", "PackageRequireLicenseAcceptance": "false"},
          "Lib: packaging properties", str(values))
    code, items = run("msbuild", csproj("enabled", "Lib"), "-p:Configuration=Debug", "-p:TargetFramework=net10.0", "-getItem:PackageReference")
    check(code == 0 and "Microsoft.SourceLink.GitHub" in items, "Lib: the pinned SourceLink reference stays in a Debug build too", items[-500:])

    # --- A test project names its TRX report after itself --------------------------------------
    # `dotnet test` in MTP mode reads RunArguments from each project and puts it first on that assembly's command line
    # (dotnet/sdk v10.0.401, TestApplication.GetArguments); without a name of its own, two assemblies that
    # start in the same microsecond share xUnit's timestamped default and one report replaces the other.
    values = properties("enabled", "Tests", "RunArguments")
    check(values == {"RunArguments": "--report-xunit-trx --report-xunit-trx-filename Fixture.Tests.trx"},
          "*.Tests project: RunArguments name its TRX report after the project", str(values))
    values = properties("enabled", "App", "RunArguments")
    check("trx" not in values.get("RunArguments", "trx"), "Exe application: no TRX arguments", str(values))

    # --- Things that must get nothing -----------------------------------------------------------
    for name, label in (("NonPackableLib", "IsPackable=false library"), ("Tests", "*.Tests project"),
                        ("LibTest", "library-type test project not named *.Tests"), ("App", "Exe application")):
        code, log = build("enabled", name)
        check(code == 0, f"{label}: Release build succeeds", log[-1500:])
        check(not produced("enabled", name, "*.xml") and not produced("enabled", name, "*.nupkg"), f"{label}: no XML docs and no package")
        values = properties("enabled", name, *names)
        check(values == nothing, f"{label}: no docs, no embedded sources, no symbols, no license default", str(values))
        pdbs = produced("enabled", name, f"Fixture.{name}.pdb")
        rows = scan(scanner, pdbs)
        check(bool(rows) and all(not row[2] for row in rows), f"{label}: its pdb embeds none of the project's own source (generated files aside)", str(rows))

    # --- A project keeps what it sets itself ---------------------------------------------------
    code, log = pack("enabled", "OptOuts", out / "OptOuts")
    check(code == 0, "OptOuts: pack succeeds", log[-1500:])
    nupkg = next((out / "OptOuts").glob("*.nupkg"), None)
    check("lib/net10.0/Fixture.OptOuts.xml" in entries(nupkg), "OptOuts: the docs default still applies")
    check(next((out / "OptOuts").glob("*.snupkg"), None) is None, "OptOuts: IncludeSymbols=false yields no .snupkg")
    values = properties("enabled", "OptOuts", "EmbedAllSources", "IncludeSymbols", "PackageLicenseExpression", "GeneratePackageOnBuild")
    check(values == {"EmbedAllSources": "false", "IncludeSymbols": "false", "PackageLicenseExpression": "Apache-2.0", "GeneratePackageOnBuild": "false"},
          "OptOuts: EmbedAllSources, IncludeSymbols and the license are the project's own", str(values))

    code, log = pack("enabled", "ExplicitFalse", out / "ExplicitFalse")
    check(code == 0 and "CS1591" not in log, "ExplicitFalse: pack succeeds and no docs were compiled", log[-1500:])
    nupkg = next((out / "ExplicitFalse").glob("*.nupkg"), None)
    check(nupkg is not None and not any(entry.endswith(".xml") and entry.startswith("lib/") for entry in entries(nupkg)),
          "ExplicitFalse: an explicit GenerateDocumentationFile=false keeps the docs out of the package")
    values = properties("enabled", "ExplicitFalse", "GenerateDocumentationFile", "DocumentationFile")
    check(values == {"GenerateDocumentationFile": "false", "DocumentationFile": ""}, "ExplicitFalse: properties", str(values))

    code, log = pack("enabled", "LicenseFile", out / "LicenseFile")
    nupkg = next((out / "LicenseFile").glob("*.nupkg"), None)
    nuspec = next((entry for entry in entries(nupkg) if entry.endswith(".nuspec")), "")
    check(code == 0 and "<license type=\"file\">LICENSE.txt</license>" in read(nupkg, nuspec) and "expression" not in read(nupkg, nuspec),
          "LicenseFile: a project's own PackageLicenseFile gets no MIT expression on top", read(nupkg, nuspec))

    code, log = build("enabled", "OwnDocFile")
    own = FIXTURES / "enabled" / "OwnDocFile" / "own-docs" / "Fixture.OwnDocFile.xml"
    values = properties("enabled", "OwnDocFile", "DocumentationFile")
    check(code == 0 and own.is_file() and values.get("DocumentationFile", "").endswith("own-docs/Fixture.OwnDocFile.xml"),
          "OwnDocFile: a project's own DocumentationFile path is kept", str(values) + log[-500:])

    # --- Debug types without a standalone pdb --------------------------------------------------
    code, log = pack("enabled", "Embedded", out / "Embedded")
    check(code == 0 and "NU5017" not in log, "Embedded: pack succeeds, no NU5017", log[-1500:])
    nupkg = next((out / "Embedded").glob("*.nupkg"), None)
    check("lib/net10.0/Fixture.Embedded.xml" in entries(nupkg), "Embedded: the .nupkg still holds the docs")
    check(next((out / "Embedded").glob("*.snupkg"), None) is None, "Embedded: no .snupkg is requested")

    code, log = pack("enabled", "NoPdb", out / "NoPdb")
    check(code == 0 and "CS2045" not in log, "NoPdb: DebugType=none builds and packs without /embed (no CS2045)", log[-1500:])
    check(next((out / "NoPdb").glob("*.snupkg"), None) is None, "NoPdb: no .snupkg is requested")

    # --- disabled: a consumer that did not opt in ------------------------------------------------
    code, log = run("publish", csproj("disabled", "App"), "-c", "Release", "-o", str(out / "publish"))
    check(code == 0, "disabled: an application and its internal library publish", log[-1500:])
    check(not list((out / "publish").glob("*.xml")), "disabled: no XML docs in the publish folder")
    rows = scan(scanner, sorted((out / "publish").glob("*.pdb")))
    check(len(rows) == 2 and all(not row[2] for row in rows),
          "disabled: no pdb in the publish folder (the internal library's included) embeds the project's own source", str(rows))
    values = properties("disabled", "InternalLib", *names)
    check(values == nothing, "disabled: the internal library gets no package defaults", str(values))

    # --- disabled: base behaviour for a project that sets IsPackable=true itself, switch or not ----
    values = properties("disabled", "Tool", "OutputType", "PackageLicenseExpression", "IncludeSymbols", "SymbolPackageFormat",
                        "EmbedAllSources", "GeneratePackageOnBuild", "PublishRepositoryUrl", "GenerateDocumentationFile")
    check(values == {"OutputType": "Exe", "PackageLicenseExpression": "MIT", "IncludeSymbols": "true", "SymbolPackageFormat": "snupkg",
                     "EmbedAllSources": "true", "GeneratePackageOnBuild": "true", "PublishRepositoryUrl": "true",
                     "GenerateDocumentationFile": "false"},
          "disabled: an Exe with an explicit IsPackable=true (a dotnet tool) keeps the base packaging properties, without docs", str(values))
    values = properties("enabled", "NonPackableLib", "GeneratePackageOnBuild")
    check(values == {"GeneratePackageOnBuild": "false"}, "enabled: IsPackable=false stays without GeneratePackageOnBuild", str(values))

    # --- disabled: no switch, docs exactly as before ---------------------------------------------
    code, log = build("disabled", "ExplicitDocs")
    check(code == 0 and produced("disabled", "ExplicitDocs", "Fixture.ExplicitDocs.xml"),
          "disabled: a project's own GenerateDocumentationFile=true still produces docs", log[-1500:])
    values = properties("disabled", "DocPath", "GenerateDocumentationFile", "DocumentationFile")
    check(values == {"GenerateDocumentationFile": "false", "DocumentationFile": ""},
          "disabled: a project that sets only DocumentationFile still compiles no docs", str(values))

    # --- explicit-docs: an opted-in repo that sets GenerateDocumentationFile itself ---------------
    for name in ("Lib", "App"):
        code, log = build("explicit-docs", name)
        check(code == 0 and produced("explicit-docs", name, f"Fixture.{name}.xml"),
              f"explicit-docs: {name} keeps the docs the repository asked for", log[-1500:])

    # --- late-switch: the switch set after the props import is reported ---------------------------
    code, log = build("late-switch", "Lib")
    check(code != 0 and "Set DevConfigsPackageDefaults before importing DevConfigs/Directory.Build.props" in log,
          "late-switch: a switch set after the props import fails the build with the fix named", log[-800:])

    shutil.rmtree(out, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
