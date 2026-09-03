# DevConfigs

Shared build and editor configuration for Flakroup .NET repositories. Consumed as a git submodule
(`DevConfigs/`) by FEx, FExApps and friends, so a single change propagates to every repo that bumps it.

## Contents

| File | Purpose |
|---|---|
| `Directory.Build.props` | Common MSBuild properties: target-framework ids (`TargetFrameworkId`, `StandardTargetFrameworkIds`, `WindowsTargetFrameworkIds`, ...), `Nullable`, `LangVersion`, warning levels and warnings-as-errors, authors/company/copyright, centrally pinned package versions, and the `*.Tests` block (MTP runner, xUnit v3, NSubstitute, Shouldly, coverage). |
| `Directory.Build.targets` | Packaging defaults for packable projects in Release (SourceLink, symbols, XML docs, deterministic build) and the shared analyzer set (IDisposableAnalyzers, VS Threading, ReflectionAnalyzers, PolySharp). |
| `.editorconfig` | Formatting and code-style rules, linked into every project. |
| `FEx.sln.DotSettings` | ReSharper settings, including the inspection severities promoted to ERROR that gate commits. |
| `Settings.XamlStyler` | XAML Styler configuration. |
| `tools/validate_dotsettings.py` | Guard for the shared ReSharper layers - see [Editing the ReSharper layer](#editing-the-resharper-layer). |
| `tools/validate_build_props.py` | Guard for the NuGet audit policy in `Directory.Build.props`/`.targets` - see [The NuGet audit pin](#the-nuget-audit-pin). |

## Usage

Add it as a submodule at the repo root and import it from the repo's own `Directory.Build.props`
and `Directory.Build.targets`:

```bash
git submodule add https://github.com/Flakroup/DevConfigs.git DevConfigs
git submodule update --init
```

```xml
<!-- Directory.Build.props -->
<Import Project="$(MSBuildThisFileDirectory)DevConfigs\Directory.Build.props" />
```

```xml
<!-- Directory.Build.targets -->
<Import Project="$(MSBuildThisFileDirectory)DevConfigs\Directory.Build.targets" />
```

The consuming repo keeps its own repo-specific settings (package metadata, URLs, icon) in its root
`Directory.Build.props`/`.targets` alongside the import.

## Changing it

A change here affects every consumer, so keep edits conservative and verify a build in at least one
consuming repo before bumping its submodule pointer.

## Editing the ReSharper layer

Nothing builds `FEx.sln.DotSettings`, so a broken entry propagates silently to every consumer. CI
validates it on each push; run the same check locally before committing:

```bash
python tools/validate_dotsettings.py
```

It rejects three things: a duplicate `x:Key`, which makes the dictionary formally invalid and can
make ReSharper drop the whole layer; an absolute filesystem path, which forces one machine's profile
or cache directory on everyone; and personal ReSharper state - one-shot markers, panel geometry,
telemetry consent, `IsMigratorApplied` entries.

That state lands here when the save layer in ReSharper's options dialog is set to **Solution
team-shared**. Save personal preferences to a personal layer instead, and keep this file to
configuration the team actually shares.

## The NuGet audit pin

`Directory.Build.props` assigns `NuGetAudit=true` and `NuGetAuditMode=all`, so every consumer
audits its whole transitive package graph. `NuGetAudit` already defaults to `true` in the .NET
SDK, so the assignment is a pin rather than a behaviour change - what it buys is precedence:
a property assigned in a project file beats an environment variable of the same name, which
closes the `NuGetAudit=false` route that leaves no file behind for any scan to read.

It does **not** beat a command line. `-p:NuGetAudit=false` is a global property and still wins
over a plain assignment, so a consuming repository that invokes MSBuild from a script has to
guard that script itself. A `TreatAsLocalProperty` attribute on the `Project` element would
close that route as well; this repository has not taken that decision, because it would also
take the override away from every consumer that has a legitimate use for it.

Audit findings are warnings (NU1901-NU1904), not errors. Nothing here promotes them, so a
consumer that wants a vulnerable package to fail its build opts into that itself.

CI validates the policy on each push; run the same check locally before committing:

```bash
python tools/validate_build_props.py
```

It reads both `Directory.Build.props` and `Directory.Build.targets` - the targets file is
evaluated after the project body, so a property set there would beat the pin. It parses them as
XML rather than scanning text, matches property names case-insensitively as MSBuild does, and
rejects a commented-out or conditional assignment, a `NoWarn` covering the audit's own warning
codes, and a missing `Directory.Build.props`.

What it cannot see, so do not read a green run as more than it is: an `Import` in either file
(reported rather than followed), anything a consuming repository assigns after its own import
of these files, and the workflow being edited away in the same commit.
