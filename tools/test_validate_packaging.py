#!/usr/bin/env python3
"""Tests for validate_packaging. Plain asserts, no test framework - CI needs nothing installed."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from validate_packaging import validate_props, validate_targets  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NOT_FALSE = "'$([System.String]::Copy($(IsPackable)).ToLower())' != 'false'"
IS_TRUE = "'$([System.String]::Copy($(IsPackable)).ToLower())' == 'true'"

PACKABLE = (
    "<GenerateDocumentationFile>true</GenerateDocumentationFile><IncludeSymbols>true</IncludeSymbols>"
    "<SymbolPackageFormat>snupkg</SymbolPackageFormat><EmbedAllSources>true</EmbedAllSources>"
    "<GeneratePackageOnBuild>true</GeneratePackageOnBuild><PublishRepositoryUrl>true</PublishRepositoryUrl>"
    "<DocumentationFile>x.xml</DocumentationFile><_DocumentationFileProduced>true</_DocumentationFileProduced>"
)
failures = 0


def run(validator, content: str) -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "file"
        path.write_text(content, encoding="utf-8")
        return validator(path)


def expect(label: str, problems: list[str], fragment: str | None) -> None:
    global failures
    ok = (not problems) if fragment is None else any(fragment in problem for problem in problems)
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures += 1
        print(f"      got: {problems}")


def targets(condition: str, body: str = PACKABLE) -> str:
    return (
        f"<Project><PropertyGroup Condition=\"'$(Configuration)' == 'Release' And {condition}\">"
        f"{body}</PropertyGroup></Project>"
    )


expect("packable group testing != false is accepted", run(validate_targets, targets(NOT_FALSE)), None)
expect("packable group testing == true is rejected", run(validate_targets, targets(IS_TRUE)), "never true")
expect(
    "an ItemGroup conditioned on IsPackable == true is rejected",
    run(validate_targets, targets(NOT_FALSE) .replace("</Project>", f"<ItemGroup Condition=\"{IS_TRUE}\"/></Project>")),
    "never true",
)
for name in ("IncludeSymbols", "GenerateDocumentationFile", "DocumentationFile", "_DocumentationFileProduced"):
    stripped = PACKABLE.replace(f"<{name}>", "<X>").replace(f"</{name}>", "</X>")
    expect(f"dropping {name} is rejected", run(validate_targets, targets(NOT_FALSE, stripped)), name)
expect("a missing file is rejected", validate_targets(Path("/nonexistent/x")), "missing")

PROPS = "<Project><PropertyGroup><WarningsNotAsErrors>$(WarningsNotAsErrors);CS1591</WarningsNotAsErrors></PropertyGroup></Project>"
expect("props keeping CS1591 as warning is accepted", run(validate_props, PROPS), None)
expect("props without CS1591 is rejected", run(validate_props, "<Project><PropertyGroup/></Project>"), "CS1591")
expect(
    "a conditional CS1591 is rejected",
    run(validate_props, PROPS.replace("<WarningsNotAsErrors>", "<WarningsNotAsErrors Condition=\"'$(X)'==''\">")),
    "CS1591",
)
expect(
    "unconditional GenerateDocumentationFile=true in props is rejected",
    run(validate_props, PROPS.replace("</Project>", "<PropertyGroup><GenerateDocumentationFile>true</GenerateDocumentationFile></PropertyGroup></Project>")),
    "targets file",
)
expect("the real targets file passes", validate_targets(REPO / "Directory.Build.targets"), None)
expect("the real props file passes", validate_props(REPO / "Directory.Build.props"), None)

print("OK" if not failures else f"{failures} failed")
raise SystemExit(1 if failures else 0)
