#!/usr/bin/env python3
"""Behavioural test of the NuGet audit severity: a vulnerable package must fail the build in a consumer.

``Directory.Build.props`` lists NU1902, NU1903 and NU1904 (moderate, high, critical) in ``WarningsAsErrors``. The consumers,
each a throwaway repository root with a copy of the shared files in its own ``DevConfigs`` directory, and none of them sets
``TreatWarningsAsErrors``:

* The shared ``WarningsAsErrors`` is read statically first: it keeps the leading ``$(WarningsAsErrors)`` (a consumer's own value
  survives) and names NU1902, NU1903 and NU1904. The builds below then show each code failing a consumer, which also catches what a
  static read cannot (a later override, a ``WarningsNotAsErrors`` entry).
* ``audit-clean`` references a patched Newtonsoft.Json: it builds, so a failure elsewhere is the package's and not the fixture's.
* ``audit-moderate`` references Microsoft.IdentityModel.JsonWebTokens 6.5.0, whose only finding is moderate: error NU1902.
* ``audit-vulnerable`` references Newtonsoft.Json 12.0.3 (GHSA-5crp-9r3c-p9vr, high): the build fails rather than passing with
  a warning. The props file sets ``RestoreUseStaticGraphEvaluation``, and under it the restore fails without naming the
  finding (NuGet reports "failed with exit code '1' but did not log an error"), so the code itself is asserted on the same
  project restored without it: error NU1903.
* ``audit-critical`` references System.Text.Encodings.Web 4.5.0 (GHSA-ghhp-997w-qr28, critical): error NU1904.

The gate fails open on purpose: when the vulnerability data cannot be fetched NuGet logs NU1900 (and NU1905 for an audit source
that carries none) as warnings, which stay warnings, so an offline restore of a vulnerable package still builds.

Needs the .NET 10 SDK and network access for restore and for the vulnerability data. Plain asserts, no test framework.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
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
    <ItemGroup>
        <PackageReference Include="{package}" Version="{version}"/>
    </ItemGroup>
</Project>
"""
MARKER = "namespace Lib;\n\npublic static class Marker\n{\n    public static void Run()\n    {\n    }\n}\n"


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print("      " + detail.strip().replace("\n", "\n      ")[:1500])


def consumer(root: Path, version: str, package: str = "Newtonsoft.Json") -> Path:
    """A repository root with the shared files and one project referencing ``package`` at ``version``."""
    shutil.rmtree(root, ignore_errors=True)
    (root / "DevConfigs").mkdir(parents=True)
    for shared in SHARED:
        shutil.copy2(REPOSITORY / shared, root / "DevConfigs" / shared)
    (root / "Directory.Build.props").write_text(PROPS, encoding="utf-8")
    (root / "Directory.Build.targets").write_text(TARGETS, encoding="utf-8")
    (root / "Lib").mkdir()
    (root / "Lib" / "Lib.csproj").write_text(PROJECT.format(package=package, version=version), encoding="utf-8")
    (root / "Lib" / "Marker.cs").write_text(MARKER, encoding="utf-8")
    return root / "Lib" / "Lib.csproj"


def build(project: Path, *extra: str) -> tuple[int, str]:
    # English messages, so the assertions do not depend on the machine's UI language.
    process = subprocess.run(["dotnet", "build", str(project), "-nologo", "-v", "q", *extra], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=project.parent,
                             env={**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en", "VSLANG": "1033"})
    return process.returncode, process.stdout + process.stderr


def shared_warnings_as_errors() -> str:
    """The unconditional top-level ``WarningsAsErrors`` of the shared props file, or an empty string."""
    root = ElementTree.parse(REPOSITORY / "Directory.Build.props").getroot()
    for group in root.findall("PropertyGroup"):
        if "Condition" not in group.attrib:
            for element in group.findall("WarningsAsErrors"):
                if "Condition" not in element.attrib:
                    return element.text or ""
    return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = Path(tempfile.mkdtemp(prefix="devconfigs-audit-"))

    entries = [entry.strip() for entry in shared_warnings_as_errors().split(";")]
    check(entries[:1] == ["$(WarningsAsErrors)"] and {"NU1902", "NU1903", "NU1904"} <= set(entries),
          "shared: WarningsAsErrors keeps $(WarningsAsErrors) first and lists NU1902, NU1903 and NU1904", str(entries))

    code, output = build(consumer(work / "audit-clean", "13.0.3"))
    check(code == 0 and "NU190" not in output, "audit-clean: a patched package builds with no audit finding", output)

    project = consumer(work / "audit-vulnerable", "12.0.3")
    code, output = build(project)
    check(code != 0, "audit-vulnerable: a high-severity advisory fails the build, without TreatWarningsAsErrors", output)
    code, output = build(project, "-p:RestoreUseStaticGraphEvaluation=false")
    check(code != 0 and "error NU1903" in output,
          "audit-vulnerable: the failure is error NU1903 (restored without static graph evaluation, which names it)", output)

    project = consumer(work / "audit-moderate", "6.5.0", "Microsoft.IdentityModel.JsonWebTokens")
    code, output = build(project, "-p:RestoreUseStaticGraphEvaluation=false")
    check(code != 0 and "error NU1902" in output and "NU1903" not in output and "NU1904" not in output,
          "audit-moderate: a moderate advisory alone fails the build with error NU1902", output)

    project = consumer(work / "audit-critical", "4.5.0", "System.Text.Encodings.Web")
    code, output = build(project, "-p:RestoreUseStaticGraphEvaluation=false")
    check(code != 0 and "error NU1904" in output, "audit-critical: a critical advisory fails the build with error NU1904", output)

    shutil.rmtree(work, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
