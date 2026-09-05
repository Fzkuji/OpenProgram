$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Native {
    param([string]$FilePath)

    $Arguments = $args
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Invoke-NativeOutput {
    param([string]$FilePath)

    $Arguments = $args
    $Output = (& $FilePath @Arguments | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
    return $Output
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Algorithm = [Security.Cryptography.SHA256]::Create()
    $Stream = [IO.File]::OpenRead($Path)
    try {
        return ([BitConverter]::ToString($Algorithm.ComputeHash($Stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $Stream.Dispose()
        $Algorithm.Dispose()
    }
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeRoot = if ($env:OPENPROGRAM_RUNTIME_ROOT) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_RUNTIME_ROOT)
} else {
    Join-Path $RepoRoot "apps\desktop\build\runtime"
}
$OutputDir = if ($env:OPENPROGRAM_RUNTIME_OUTPUT_DIR) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_RUNTIME_OUTPUT_DIR)
} else {
    Join-Path $RepoRoot "dist"
}
$Platform = $env:OPENPROGRAM_RUNTIME_PLATFORM
$Arch = $env:OPENPROGRAM_RUNTIME_ARCH

if ((Split-Path -Leaf $RuntimeRoot) -ne "runtime") {
    throw "OPENPROGRAM_RUNTIME_ROOT must end in \runtime: $RuntimeRoot"
}
if (-not $Platform -or -not $Arch) {
    throw "OPENPROGRAM_RUNTIME_PLATFORM and OPENPROGRAM_RUNTIME_ARCH are required"
}
$ManifestPath = Join-Path $RuntimeRoot "runtime-manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "runtime manifest not found: $ManifestPath"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$PythonBin = Join-Path $RuntimeRoot ([string]$Manifest.python)
if (-not (Test-Path -LiteralPath $PythonBin -PathType Leaf)) {
    throw "managed Python is missing: $PythonBin"
}
Invoke-Native $PythonBin -I (Join-Path $RuntimeRoot "bin\verify-product-runtime.py") $RuntimeRoot
$BuildMetadata = Invoke-NativeOutput $PythonBin -I `
    (Join-Path $PSScriptRoot "runtime-build-metadata.py") $PythonBin $RuntimeRoot |
    ConvertFrom-Json
$Version = [string]$BuildMetadata.openprogram_version

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$Archive = Join-Path $OutputDir "OpenProgram-$Version-runtime-$Platform-$Arch.zip"
Invoke-Native $PythonBin -I (Join-Path $PSScriptRoot "archive-product-runtime.py") `
    $RuntimeRoot $Archive
$Size = (Get-Item -LiteralPath $Archive).Length
if ($Size -ge 2147483648) {
    throw "runtime archive exceeds GitHub Release 2GiB limit: $Archive ($Size bytes)"
}
$Hash = Get-Sha256 $Archive
$Checksum = "$Hash  $([IO.Path]::GetFileName($Archive))`n"
[IO.File]::WriteAllText("$Archive.sha256", $Checksum, [Text.UTF8Encoding]::new($false))
Write-Output $Archive
