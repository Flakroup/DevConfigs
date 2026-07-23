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
