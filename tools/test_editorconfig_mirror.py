#!/usr/bin/env python3
"""Behavioural test of the opt-in .editorconfig mirror: build tiny consumers against the real props/targets.

An .editorconfig governs only the files beneath it, so the one inside this submodule reaches no consumer source. A
repository that sets ``DevConfigsMirrorEditorConfig=true`` before importing the shared props gets a copy of it at its root
on every restore and build. Three consumers, each a throwaway repository root with a copy of the shared files in its own
``DevConfigs`` directory:

* ``opted-in`` sets the switch and has a ``.gitmodules``: the root ends up with a byte-identical copy.
* ``no-switch`` has a ``.gitmodules`` but does not opt in: no copy, so no repository is changed by merely bumping this one.
* ``standalone`` sets the switch but has no ``.gitmodules`` (this repository's own build): the file is not copied onto itself.

Needs the .NET 10 SDK. Plain asserts, no test framework.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
SHARED = ["Directory.Build.props", "Directory.Build.targets", ".editorconfig"]
failures: list[str] = []

PROPS = """<Project>
    <PropertyGroup>
        {switch}
    </PropertyGroup>
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


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print("      " + detail.strip().replace("\n", "\n      ")[:1500])


def consumer(root: Path, switch: bool, gitmodules: bool) -> Path:
    shutil.rmtree(root, ignore_errors=True)
    (root / "DevConfigs").mkdir(parents=True)
    for name in SHARED:
        shutil.copy2(REPOSITORY / name, root / "DevConfigs" / name)
    value = "<DevConfigsMirrorEditorConfig>true</DevConfigsMirrorEditorConfig>" if switch else ""
    (root / "Directory.Build.props").write_text(PROPS.format(switch=value), encoding="utf-8")
    (root / "Directory.Build.targets").write_text(TARGETS, encoding="utf-8")
    (root / "Lib").mkdir()
    (root / "Lib" / "Lib.csproj").write_text(PROJECT, encoding="utf-8")
    (root / "Lib" / "Marker.cs").write_text("namespace Fixture;\npublic static class Marker { }\n", encoding="utf-8")
    if gitmodules:
        (root / ".gitmodules").write_text('[submodule "DevConfigs"]\n\tpath = DevConfigs\n', encoding="utf-8")
    return root


def restore_and_build(root: Path) -> tuple[int, str]:
    process = subprocess.run(
        ["dotnet", "build", str(root / "Lib" / "Lib.csproj"), "-nologo", "-v", "q"],
        capture_output=True, text=True, cwd=root)
    return process.returncode, process.stdout + process.stderr


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="devconfigs-mirror-"))
    expected = (REPOSITORY / ".editorconfig").read_bytes()

    for name, switch, gitmodules, should_copy in [
        ("opted-in", True, True, True),
        ("no-switch", False, True, False),
        ("standalone", True, False, False),
    ]:
        root = consumer(work / name, switch, gitmodules)
        code, output = restore_and_build(root)
        check(code == 0, f"{name}: the consumer builds", output)
        copy = root / ".editorconfig"
        if should_copy:
            check(copy.is_file() and copy.read_bytes() == expected, f"{name}: the root holds a byte-identical copy")
        else:
            check(not copy.exists(), f"{name}: nothing is copied to the root")

    # An edit to the shared file must reach an already-mirrored consumer on the next build.
    root = work / "opted-in"
    (root / "DevConfigs" / ".editorconfig").write_text(
        (root / "DevConfigs" / ".editorconfig").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    code, output = restore_and_build(root)
    check(code == 0 and b"# edited" in (root / ".editorconfig").read_bytes(), "opted-in: an edit is mirrored on the next build", output)

    shutil.rmtree(work, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
