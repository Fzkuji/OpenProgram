$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeRoot = Join-Path $RepoRoot "apps\desktop\build\runtime"
$Archive = $env:OPENPROGRAM_RUNTIME_ARCHIVE

if (-not $Archive) {
    if (Test-Path -LiteralPath $RuntimeRoot) {
        Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force
    }
    $PreviousRuntimeRoot = $env:OPENPROGRAM_RUNTIME_ROOT
    $env:OPENPROGRAM_RUNTIME_ROOT = $RuntimeRoot
    try {
        & (Join-Path $PSScriptRoot "build-product-runtime.ps1")
    } finally {
        if ($null -eq $PreviousRuntimeRoot) {
            Remove-Item Env:OPENPROGRAM_RUNTIME_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:OPENPROGRAM_RUNTIME_ROOT = $PreviousRuntimeRoot
        }
    }
    return
}

$Archive = [IO.Path]::GetFullPath($Archive)
if (-not [IO.Path]::IsPathRooted($env:OPENPROGRAM_RUNTIME_ARCHIVE) -or
    -not $Archive.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
    throw "OPENPROGRAM_RUNTIME_ARCHIVE must be an absolute existing .zip path"
}

$Package = Get-Content -LiteralPath (Join-Path $RepoRoot "apps\desktop\package.json") `
    -Raw -Encoding UTF8 | ConvertFrom-Json
$Version = [string]$Package.version
$Staging = Join-Path ([IO.Path]::GetTempPath()) (
    "openprogram-desktop-runtime-" + [guid]::NewGuid().ToString("N")
)
$StateRoot = Join-Path $Staging "state"
$BinDir = Join-Path $Staging "bin"
New-Item -ItemType Directory -Path $Staging | Out-Null

$PreviousVersion = $env:OPENPROGRAM_VERSION
$PreviousState = $env:OPENPROGRAM_STATE_DIR
$PreviousBin = $env:OPENPROGRAM_BIN_DIR
try {
    $env:OPENPROGRAM_VERSION = $Version
    $env:OPENPROGRAM_RUNTIME_ARCHIVE = $Archive
    $env:OPENPROGRAM_STATE_DIR = $StateRoot
    $env:OPENPROGRAM_BIN_DIR = $BinDir
    & (Join-Path $PSScriptRoot "install-release.ps1")

    $Prepared = Join-Path $StateRoot "runtime\cli\releases\$Version"
    if (-not (Test-Path -LiteralPath (Join-Path $Prepared "runtime-manifest.json") -PathType Leaf)) {
        throw "prepared Windows Desktop runtime is incomplete: $Prepared"
    }
    if (Test-Path -LiteralPath $RuntimeRoot) {
        Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $RuntimeRoot) -Force | Out-Null
    Move-Item -LiteralPath $Prepared -Destination $RuntimeRoot
    Write-Output "prepared Windows Desktop runtime from $Archive"
} finally {
    if ($null -eq $PreviousVersion) { Remove-Item Env:OPENPROGRAM_VERSION -ErrorAction SilentlyContinue } else { $env:OPENPROGRAM_VERSION = $PreviousVersion }
    if ($null -eq $PreviousState) { Remove-Item Env:OPENPROGRAM_STATE_DIR -ErrorAction SilentlyContinue } else { $env:OPENPROGRAM_STATE_DIR = $PreviousState }
    if ($null -eq $PreviousBin) { Remove-Item Env:OPENPROGRAM_BIN_DIR -ErrorAction SilentlyContinue } else { $env:OPENPROGRAM_BIN_DIR = $PreviousBin }
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
}
