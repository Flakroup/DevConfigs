#!/usr/bin/env python3
"""Behavioural test of the opt-in .editorconfig mirror: build tiny consumers against the real props/targets.

An .editorconfig governs only the files beneath it, so the one inside this submodule reaches no consumer source. A
repository that sets ``DevConfigsMirrorEditorConfig=true`` before importing the shared props gets a copy of it at its root
on every restore and build. The consumers, each a throwaway repository root with a copy of the shared files in its own
``DevConfigs`` directory:

* ``opted-in`` sets the switch and has a ``.gitmodules``: the root ends up with a byte-identical copy, and a restore alone
  is enough to create it.
* ``no-switch`` has a ``.gitmodules`` but does not opt in: no copy, so no repository is changed by merely bumping this one.
* ``owned`` sets the switch but already has a root ``.editorconfig`` of its own: it is kept and a ``DEVCFG001`` warning is raised.
* ``standalone`` sets the switch but has no ``.gitmodules`` (this repository's own build): the file is not copied onto itself.
* ``parallel`` restores a solution of many projects at once, each running the mirror: no project may fail on a sibling's
  write, whether the root copy is missing or stale. A plain Copy failed this on Linux in half of the rounds.
* ``read-only`` holds a stale copy that cannot be replaced: the build fails with ``DEVCFG002`` instead of passing
  under the old rules.
* ``enforced`` breaks a naming rule: the build fails, because the IDE rules run in it.
* ``banned`` uses each JetBrains nullness attribute: RS0030 reports every one.

``build.globalconfig`` is checked to hold its one severity and nothing else, and no mirrored consumer's build may
report the ``EnableGenerateDocumentationFile`` diagnostic it silences.

Needs the .NET 10 SDK. Plain asserts, no test framework.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
SHARED = ["Directory.Build.props", "Directory.Build.targets", ".editorconfig", "build.globalconfig", "BannedSymbols.txt"]
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
    (root / "Lib" / "Marker.cs").write_text("namespace Lib;\n\npublic static class Marker\n{\n}\n", encoding="utf-8")
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

    # build.globalconfig is the one place a severity goes down, so it may hold that one line and nothing else - not a
    # second severity, and not a section declaring files generated, which silences every analyzer at once.
    global_entries = [line.strip() for line in (REPOSITORY / "build.globalconfig").read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.lstrip().startswith("#")]
    check(global_entries == ["is_global = true", "dotnet_diagnostic.EnableGenerateDocumentationFile.severity = none"],
          "shared: build.globalconfig silences EnableGenerateDocumentationFile and nothing else", "\n".join(global_entries))

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
            # IDE0005 at error with XML docs off makes the compiler report EnableGenerateDocumentationFile instead;
            # build.globalconfig, wired by Directory.Build.props, keeps that out of the build. Only a consumer the
            # rules reach can see it, so the other shapes would pass this with the wiring gone.
            check("EnableGenerateDocumentationFile" not in output, f"{name}: the IDE0005 documentation nag is silenced",
                  output)
            check(copy.is_file() and copy.read_bytes() == expected, f"{name}: the root holds a byte-identical copy")
        else:
            check(not copy.exists(), f"{name}: nothing is copied to the root")

    # The copy is made by a restore alone, which is what `dotnet format --no-restore` and an IDE load rely on.
    root = consumer(work / "restore-only", True, True)
    process = subprocess.run(["dotnet", "restore", str(root / "Lib" / "Lib.csproj"), "-nologo", "-v", "q"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
    check(process.returncode == 0 and (root / ".editorconfig").is_file(), "restore-only: a restore alone creates the copy",
          process.stdout + process.stderr)

    # The IDE rules fail the build where the mirrored copy reaches the code - EnforceCodeStyleInBuild, not the editor.
    root = consumer(work / "enforced", True, True)
    (root / "Lib" / "Marker.cs").write_text(
        "namespace Lib;\n\npublic static class Marker\n{\n    public static int Count() => count;\n\n"
        "    private static readonly int count = 1;\n}\n", encoding="utf-8")
    code, output = restore_and_build(root)
    check(code != 0 and "IDE1006" in output, "enforced: an IDE rule at error fails the build (IDE1006)", output)

    # RS0030 matches a banned symbol by its documentation ID, so attributes declared under JetBrains' names stand in
    # for the package without a download. The names are spelled here, not read from BannedSymbols.txt, so a line
    # dropped from that file is a failure rather than a smaller test.
    banned = ["NotNull", "CanBeNull", "ItemNotNull", "ItemCanBeNull"]
    root = consumer(work / "banned", True, True)
    (root / "Lib" / "Annotations.cs").write_text(
        "#pragma warning disable IDE0130 // the documentation ID needs JetBrains' namespace\n"
        "using System;\n\nnamespace JetBrains.Annotations;\n"
        + "".join(f"\n[AttributeUsage(AttributeTargets.All)]\npublic sealed class {name}Attribute : Attribute\n{{\n}}\n"
                  for name in banned), encoding="utf-8")
    (root / "Lib" / "Guarded.cs").write_text(
        "using JetBrains.Annotations;\n\nnamespace Lib;\n\npublic static class Guarded\n{\n"
        + "".join(f"    public static string Echo{name}([{name}] string value) => value;\n\n" for name in banned).rstrip("\n")
        + "\n}\n", encoding="utf-8")
    code, output = restore_and_build(root)
    # By the line of each use, not by name: the message is localized, and NotNull is a substring of ItemNotNull.
    for index, name in enumerate(banned):
        location = f"Guarded.cs({7 + 2 * index},"
        check(any(location in line and "RS0030" in line for line in output.splitlines()),
              f"banned: JetBrains' {name} is reported (RS0030)", output)

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

    # A current copy is left alone: the compiler takes it as an input, so a rewrite would recompile every project.
    written = (root / ".editorconfig").stat().st_mtime_ns
    code, output = restore_and_build(root)
    check(code == 0 and (root / ".editorconfig").stat().st_mtime_ns == written,
          "opted-in: a copy already current is not rewritten", output)

    # Every project of a parallel restore mirrors the same file at once: none may fail reading or replacing it.
    root = consumer(work / "parallel", True, True)
    names = [f"P{i}" for i in range(24)]
    for name in names:
        (root / name).mkdir()
        (root / name / f"{name}.csproj").write_text(PROJECT, encoding="utf-8")
    (root / "All.slnx").write_text(
        "<Solution>" + "".join(f'<Project Path="{n}/{n}.csproj" />' for n in names) + "</Solution>\n", encoding="utf-8")
    failed = []
    stale = expected + b"\n# stale\n"
    for round_number in range(8):
        # Even rounds create the copy (a rename onto nothing), odd ones replace a stale one (a rename over it).
        if round_number % 2:
            (root / ".editorconfig").write_bytes(stale)
        else:
            (root / ".editorconfig").unlink(missing_ok=True)
        # -m:24 runs every project at once whatever the core count: on CI's four cores the old Copy passed every round.
        process = subprocess.run(["dotnet", "restore", "All.slnx", "-nologo", "-v", "q", "-m:24"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
        copy = root / ".editorconfig"
        if process.returncode != 0 or not copy.is_file() or copy.read_bytes() != expected:
            failed.append(f"round {round_number}: " + (process.stdout + process.stderr)[-300:])
    check(not failed, "parallel: every round of a parallel restore mirrors without a failure", "\n".join(failed))
    check(not list(root.glob(".editorconfig.*.tmp")), "parallel: no temporary copy is left behind")

    # A copy that can never be replaced fails the build: a pass would compile under the stale rules.
    root = consumer(work / "read-only", True, True)
    (root / ".editorconfig").write_bytes(stale)
    if os.name == "nt":
        os.chmod(root / ".editorconfig", stat.S_IREAD)
    else:
        os.chmod(root, stat.S_IREAD | stat.S_IEXEC)
    if os.name != "nt" and os.geteuid() == 0:
        print("SKIP  read-only: root ignores permissions")
    else:
        code, output = restore_and_build(root)
        check(code != 0 and "DEVCFG002" in output, "read-only: a copy that cannot be replaced fails the build", output)
    os.chmod(root, stat.S_IRWXU)
    os.chmod(root / ".editorconfig", stat.S_IREAD | stat.S_IWRITE)

    shutil.rmtree(work, ignore_errors=True)
    print("OK" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
