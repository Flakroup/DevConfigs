# DevConfigs

Shared build and editor configuration for Flakroup .NET repositories. Consumed as a git submodule
(`DevConfigs/`) by FEx, FExApps and friends, so a single change propagates to every repo that bumps it.

## Contents

| File | Purpose |
|---|---|
| `Directory.Build.props` | Common MSBuild properties: target-framework ids (`TargetFrameworkId`, `StandardTargetFrameworkIds`, `WindowsTargetFrameworkIds`, ...), `Nullable`, `LangVersion`, warning levels and warnings-as-errors (`CS1591` is kept out of it via `WarningsNotAsErrors`), authors/company/copyright, centrally pinned package versions, and the `*.Tests` block (MTP runner, xUnit v3, NSubstitute, Shouldly, coverage, hang dump). |
| `Directory.Build.targets` | Packaging defaults, applied only to **libraries** (`OutputType` Library or unset) that are not `IsPackable=false` and not test projects, in Release: XML docs, `snupkg` symbols, embedded sources, SourceLink, deterministic paths, MIT license. Applications get none of it. See [Packaging defaults](#packaging-defaults). Also holds the shared analyzer set (IDisposableAnalyzers, VS Threading, ReflectionAnalyzers, PolySharp). |
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

`Directory.Build.targets` decides these, not the props file: `IsPackable` is still empty while the targets file is
evaluated for a project that relies on the SDK default (the pack targets set it later), so "packable" is tested as
"not explicitly `false`". A condition written as `IsPackable == 'true'` is never true and leaves the whole block dead -
that is how packages used to ship without docs, symbols and SourceLink.

- **Scope.** Release builds of libraries only: `OutputType` is `Library` or unset, `IsPackable` is not `false`, and the
  project is not a test project. Executables and `*.Tests` get no docs, no embedded sources and no package.
- **Applied:** an XML doc file per target framework, `snupkg` symbols (skipped when `DebugType=embedded`, which has no
  standalone pdb to put in one), `EmbedAllSources`, SourceLink, deterministic paths, `PublishRepositoryUrl`, and
  `PackageLicenseExpression=MIT`.
- **A project keeps what it sets itself.** Every default above is applied only while the property is still empty
  (the license is skipped when `PackageLicenseFile` or `PackageLicense` is set). The one exception is documentation:
  the SDK has already turned `GenerateDocumentationFile` into `false` before the targets file is imported, so an explicit
  `false` cannot be told from the default. Opt out with `<DevConfigsSkipDocumentationFile>true</DevConfigsSkipDocumentationFile>`.
- **`GeneratePackageOnBuild` is not set.** It was dead until now; switching it on would pack every library on every
  Release build and again in a consumer's own pack step. A consumer that wants it sets it in its csproj.
- **Doc diagnostics.** `CS1591` (missing XML comment) stays a warning, also under `TreatWarningsAsErrors`. Malformed
  doc comments (`CS1572`, `CS1573`, `CS1574`, `CS1584`, `CS1658`, ...) stay errors there on purpose: they are real
  defects that were never compiled before, and each consumer fixes them in the PR that bumps the submodule pin. Expect
  a lot of `CS1591` warnings until the documentation backlog is done.
- **The guard.** `tools/test_fixtures.py` (run by the `packaging` job in `validate.yml`) builds and packs the projects
  in `tools/fixtures` with `TreatWarningsAsErrors=true` and checks the produced packages. It also pins the reliance on
  the SDK's private `_DocumentationFileProduced`, which decides whether the `.xml` reaches the folder pack reads. The
  fixtures stop at their own `.editorconfig` so the repository's `CS1591 = none` does not hide the warning they assert on.

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
