# DevConfigs

Shared build and editor configuration for Flakroup .NET repositories. Consumed as a git submodule
(`DevConfigs/`) by FEx, FExApps and friends, so a single change propagates to every repo that bumps it.

## Contents

| File | Purpose |
|---|---|
| `Directory.Build.props` | Common MSBuild properties: target-framework ids (`TargetFrameworkId`, `StandardTargetFrameworkIds`, `WindowsTargetFrameworkIds`, ...), `Nullable`, `LangVersion`, warning levels and warnings-as-errors (`CS1591` is kept out of it via `WarningsNotAsErrors`), authors/company/copyright, centrally pinned package versions, and the `*.Tests` block (MTP runner, xUnit v3, NSubstitute, Shouldly, coverage, hang dump). |
| `Directory.Build.targets` | Opt-in packaging defaults for library repositories (XML docs, `snupkg` symbols, embedded sources, SourceLink, deterministic paths, MIT license) - see [Packaging defaults](#packaging-defaults) - the pinned SourceLink package reference, and the shared analyzer set (IDisposableAnalyzers, VS Threading, ReflectionAnalyzers, PolySharp). |
| `.editorconfig` | Formatting and code-style rules, linked into every project. |
| `FEx.sln.DotSettings` | ReSharper settings, including the inspection severities promoted to ERROR that gate commits. |
| `Settings.XamlStyler` | XAML Styler configuration. |
| `tools/validate_dotsettings.py` | Guard for the shared ReSharper layers - see [Editing the ReSharper layer](#editing-the-resharper-layer). |
| `tools/test_fixtures.py` | Behavioural guard for the packaging defaults: builds and packs the projects in `tools/fixtures` in Release (needs the .NET 10 SDK) and asserts what comes out - see [Packaging defaults](#packaging-defaults). |
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

## Packaging defaults

**Opt-in per repository.** A library repository sets `DevConfigsPackageDefaults` in its root `Directory.Build.props`,
**before** importing `DevConfigs/Directory.Build.props` (the props file needs it to default the XML docs):

```xml
<PropertyGroup>
    <DevConfigsPackageDefaults>true</DevConfigsPackageDefaults>
</PropertyGroup>
<Import Project="$(MSBuildThisFileDirectory)DevConfigs\Directory.Build.props" />
```

Without the switch nothing below applies and a consumer's build is unchanged. It is a switch rather than something
inferred from `OutputType`, because "a library" is also every internal library of an application, and embedding its
sources in a pdb that ships with the app would hand them to whoever receives the app.

With the switch, a **Release build of a library** (`OutputType` Library or unset, `IsPackable` not `false`) gets:

- an XML doc file per target framework, packed;
- `snupkg` symbols with embedded sources (`EmbedAllSources`), SourceLink data, deterministic paths, `PublishRepositoryUrl`;
- `PackageLicenseExpression=MIT`, unless the project sets `PackageLicenseFile`, `PackageLicense` or its own expression.

Executables, test projects (`*.Tests`, `IsPackable=false` in the props file) and `IsPackable=false` projects get none of it.

**Where it is decided.** `IsPackable` is still empty while `Directory.Build.targets` is evaluated for a project that relies on
the SDK default (the pack targets set it later), so "packable" is tested as "not explicitly `false`"; written as
`IsPackable == 'true'` a condition is never true and everything under it is silently dead - that is how packages used to
ship without docs, symbols and SourceLink. The docs default lives in the props file instead: the SDK derives
`DocumentationFile` from `GenerateDocumentationFile` before the targets file is imported. The props file defaults it to
`true` in Release (with the switch, and only while empty), so a project's own value - `false` included - wins, and the
targets file takes the default back from everything that is not a packable library.

**A project keeps what it sets itself.** Every default is applied only while the property is still empty. Two limits:
`SymbolPackageFormat` already holds the SDK default `symbols.nupkg` by then, so an explicit `symbols.nupkg` cannot be told
from "unset" and becomes `snupkg`; and an explicit `GenerateDocumentationFile=true` on an executable or test project cannot be
told from the default and is taken back. Embedded sources and symbols are skipped for `DebugType=none` (the compiler
rejects `/embed` without a pdb), symbols also for `DebugType=embedded` (`NU5017` on the empty symbol package).

**Not set.** `GeneratePackageOnBuild`: it was dead until now, and switching it on would pack every library on each Release
build and again in a consumer's own pack step. A consumer that wants it sets it in its csproj.

**SourceLink pin.** The `Microsoft.SourceLink.GitHub` reference (it pulls a `Microsoft.Build.Tasks.Git` that clears
GHSA-23fw-v26w-5fgq) stays on every packable build, whatever the Configuration or the switch.

**Doc diagnostics.** `CS1591` (missing XML comment) stays a warning, also under `TreatWarningsAsErrors`. Malformed doc
comments (`CS1572`, `CS1573`, `CS1574`, `CS1584`, `CS1658`, ...) stay errors there on purpose: they are real defects that were
never compiled before, so a consumer that turns the switch on fixes them in the same PR. Expect many `CS1591` warnings until
the documentation backlog is done.

**The guard.** `tools/test_fixtures.py` (the `packaging` job in `validate.yml`) builds and packs the projects in
`tools/fixtures` - `enabled` is a repository with the switch, `disabled` one without - with `TreatWarningsAsErrors=true`, and
checks the packages and the pdbs (SourceLink data, and whether a project's own source is embedded). The fixtures stop at their own
`.editorconfig` so the repository's `CS1591 = none` does not hide the warning they assert on.

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

A plain assignment does **not** beat a command line: `-p:NuGetAudit=false` is a global property
and wins over one. So the `Project` element also carries
`TreatAsLocalProperty="NuGetAudit;NuGetAuditMode"`, which demotes a global value of either to a
local one the assignment then overwrites - closing the command-line route too. The trade is
deliberate and it is not free: **no consuming repository can override these two properties from a
command line any more**, whatever its reason. A repository that needs the audit off assigns it
after its own import of this file, which still wins - it just cannot be done per invocation.

That is a statement about those two properties, not about the audit as a whole:
`-p:NuGetAuditLevel=critical` is neither pinned nor local here, and still hides everything below
critical severity.

MSBuild splits that attribute on semicolons and nothing else - a comma or a bare line break
between two names lands inside one name and fails evaluation with `MSB5016`. Whitespace around
each name is trimmed, so wrapping the value across lines after a semicolon is fine.

Audit findings are warnings (NU1901-NU1904), not errors. Nothing here promotes them, so a
consumer that wants a vulnerable package to fail its build opts into that itself.

CI validates the policy on each push; run the same check locally before committing:

```bash
python tools/validate_build_props.py
```

It reads both `Directory.Build.props` and `Directory.Build.targets` - the targets file is
evaluated after the project body, so a property set there would beat the pin. It parses them as
XML rather than scanning text, matches property names case-insensitively as MSBuild does, and
rejects a commented-out or conditional assignment, a `NoWarn` or `MSBuildWarningsAsMessages`
covering the audit's own warning codes, and a missing `Directory.Build.props`. In
`Directory.Build.props` only, it also rejects a `TreatAsLocalProperty` that is absent, does not name
both properties, or names anything MSBuild would refuse with `MSB5016` - the targets file needs no
attribute of its own.

What it cannot see, so do not read a green run as more than it is:

- An `Import` in either file - reported rather than followed.
- Anything a consuming repository assigns after its own import of these files, in its own
  `Directory.Build.props`/`.targets` or a project file. That route is open by design.
- `NuGetAuditLevel`, which hides everything below its value, and `NuGetAuditSuppress` items, which
  drop a named advisory. Neither is pinned or checked.
- A consumer's `nuget.config`: an `auditSources` block pointed somewhere without vulnerability data
  turns findings into a single NU1905.
- A consumer that never bumps its submodule pointer. It keeps whatever this file said when it was
  pinned, and nothing here can tell.
- `dotnet restore` on its own: with `RestoreUseStaticGraphEvaluation` (set to `true` above) the
  audit warnings do not reach the console summary. A subsequent build replays them from the assets
  file, so a pipeline whose only audit signal is a standalone restore step reads zero.
- The workflow being edited away in the same commit.
