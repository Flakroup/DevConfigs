#!/usr/bin/env python3
"""Behavioural test of the opt-in .editorconfig mirror: build tiny consumers against the real props/targets.

An .editorconfig governs only the files beneath it, so the one inside this submodule reaches no consumer source. A
repository that sets ``DevConfigsMirrorEditorConfig=true`` before importing the shared props gets a copy of it at its root
on every restore and build. Three consumers, each a throwaway repository root with a copy of the shared files in its own
``DevConfigs`` directory:

* ``opted-in`` sets the switch and has a ``.gitmodules``: the root ends up with a byte-identical copy, and a restore alone
  is enough to create it.
* ``no-switch`` has a ``.gitmodules`` but does not opt in: no copy, so no repository is changed by merely bumping this one.
* ``owned`` sets the switch but already has a root ``.editorconfig`` of its own: it is kept and a ``DEVCFG001`` warning is raised.
* ``standalone`` sets the switch but has no ``.gitmodules`` (this repository's own build): the file is not copied onto itself.
* ``parallel`` restores a solution of many projects at once, each running the mirror: no project may fail on a sibling's
  write. A plain Copy failed this on Linux in half of the rounds.

Needs the .NET 10 SDK. Plain asserts, no test framework.
"""

from __future__ import annotations

import re
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
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
    return process.returncode, process.stdout + process.stderr


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="devconfigs-mirror-"))
    expected = (REPOSITORY / ".editorconfig").read_bytes()

    # The mirror makes every line count in a consumer's build, so the shared file may only raise severities: a
    # demotion would silently switch off a rule the consumer's build enforced before it opted in.
    demotions = [line for line in expected.decode("utf-8").splitlines()
                 if re.search(r"severity\s*=\s*(none|silent|suggestion)\b|:\s*(none|silent|suggestion)\s*$|generated_code",
                           line, re.IGNORECASE)]
    check(not demotions, "shared: no rule is demoted or declared generated", "\n".join(demotions))

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

    # The copy is made by a restore alone, which is what `dotnet format --no-restore` and an IDE load rely on.
    root = consumer(work / "restore-only", True, True)
    process = subprocess.run(["dotnet", "restore", str(root / "Lib" / "Lib.csproj"), "-nologo", "-v", "q"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
    check(process.returncode == 0 and (root / ".editorconfig").is_file(), "restore-only: a restore alone creates the copy",
          process.stdout + process.stderr)

    OWN = "root = true\n# consumer rules\n"
    # A root file the consumer owns survives, and the build says so.
    root = consumer(work / "owned", True, True)
    (root / ".editorconfig").write_text(OWN, encoding="utf-8")
    code, output = restore_and_build(root)
    check("DEVCFG001" in output, "owned: the skipped copy is reported", output)
    check((root / ".editorconfig").read_text(encoding="utf-8") == OWN, "owned: the consumer's file is untouched")

    # An edit to the shared file must reach an already-mirrored consumer on the next build.
    root = work / "opted-in"
    (root / "DevConfigs" / ".editorconfig").write_text(
        (root / "DevConfigs" / ".editorconfig").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    code, output = restore_and_build(root)
    check(code == 0 and b"# edited" in (root / ".editorconfig").read_bytes(), "opted-in: an edit is mirrored on the next build", output)

    # Every project of a parallel restore mirrors the same file at once: none may fail reading or replacing it.
    root = consumer(work / "parallel", True, True)
    names = [f"P{i}" for i in range(24)]
    for name in names:
        (root / name).mkdir()
        (root / name / f"{name}.csproj").write_text(PROJECT, encoding="utf-8")
    (root / "All.slnx").write_text(
        "<Solution>" + "".join(f'<Project Path="{n}/{n}.csproj" />' for n in names) + "</Solution>\n", encoding="utf-8")
    failed = []
    for round_number in range(8):
        (root / ".editorconfig").unlink(missing_ok=True)
        process = subprocess.run(["dotnet", "restore", "All.slnx", "-nologo", "-v", "q"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
        copy = root / ".editorconfig"
        if process.returncode != 0 or not copy.is_file() or copy.read_bytes() != expected:
            failed.append(f"round {round_number}: " + process.stdout + process.stderr)
    check(not failed, "parallel: every round of a parallel restore mirrors without a failure", "\n".join(failed))
    check(not list(root.glob(".editorconfig.*.tmp")), "parallel: no temporary copy is left behind")

    shutil.rmtree(work, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
