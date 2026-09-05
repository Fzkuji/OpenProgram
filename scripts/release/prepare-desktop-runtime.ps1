$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot "apps\desktop\build"))
$RuntimeRoot = Join-Path $BuildRoot "runtime"
$Archive = $env:OPENPROGRAM_RUNTIME_ARCHIVE
if ($Archive) {
    if (-not [IO.Path]::IsPathRooted($Archive) -or
        -not $Archive.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        throw "OPENPROGRAM_RUNTIME_ARCHIVE must be an absolute existing .zip path"
    }
    $Archive = [IO.Path]::GetFullPath($Archive)
}
$Package = Get-Content -LiteralPath (Join-Path $RepoRoot "apps\desktop\package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$Version = [string]$Package.version

# All replacement paths stay under this checkout's build directory, never the
# installed App. Reject redirected ancestors before any move or recursive cleanup.
$Current = $BuildRoot
while ($Current -and $Current.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    if ((Test-Path -LiteralPath $Current) -and
        ((Get-Item -LiteralPath $Current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "runtime build path is redirected: $Current"
    }
    if ($Current -eq $RepoRoot) { break }
    $Current = Split-Path -Parent $Current
}
if ((Test-Path -LiteralPath $RuntimeRoot) -and
    ((Get-Item -LiteralPath $RuntimeRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "runtime build target is redirected: $RuntimeRoot"
}
if ((Test-Path -LiteralPath $RuntimeRoot) -and -not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
    throw "runtime build target is not a directory: $RuntimeRoot"
}
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
$Lock = [IO.File]::Open((Join-Path $BuildRoot ".runtime-prepare.lock"),
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$Staging = Join-Path $BuildRoot (".runtime-stage-" + [guid]::NewGuid().ToString("N"))
$Backup = Join-Path $Staging "previous-runtime"
$KeepStaging = $false
$SavedEnvironment = @{}
foreach ($Name in @("OPENPROGRAM_VERSION", "OPENPROGRAM_STATE_DIR", "OPENPROGRAM_BIN_DIR",
    "OPENPROGRAM_RUNTIME_ROOT", "OPENPROGRAM_RUNTIME_ARCHIVE", "OPENPROGRAM_UV_BIN", "OPENPROGRAM_BUILD_PYTHON")) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}
try {
    New-Item -ItemType Directory -Path $Staging | Out-Null
    if ($Archive) {
        $env:OPENPROGRAM_VERSION = $Version
        $env:OPENPROGRAM_RUNTIME_ARCHIVE = $Archive
        $env:OPENPROGRAM_STATE_DIR = Join-Path $Staging "state"
        $env:OPENPROGRAM_BIN_DIR = Join-Path $Staging "bin"
        & (Join-Path $PSScriptRoot "install-release.ps1")
        $Prepared = Join-Path $env:OPENPROGRAM_STATE_DIR "runtime\cli\releases\$Version"
    } else {
        $Prepared = Join-Path $Staging "runtime"
        $env:OPENPROGRAM_RUNTIME_ROOT = $Prepared
        & (Join-Path $PSScriptRoot "build-product-runtime.ps1")
    }
    $Prepared = [IO.Path]::GetFullPath($Prepared)
    if (-not $Prepared.StartsWith($Staging + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "prepared runtime escaped staging"
    }
    $ManifestPath = Join-Path $Prepared "runtime-manifest.json"
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "prepared Windows Desktop runtime is incomplete: $Prepared"
    }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$Manifest.openprogram -ne $Version) {
        throw "prepared Windows Desktop runtime version does not match $Version"
    }
    # Keep the old payload until the fully prepared replacement can be renamed
    # into place. The lock prevents concurrent packaging from claiming it.
    if (Test-Path -LiteralPath $RuntimeRoot) {
        Move-Item -LiteralPath $RuntimeRoot -Destination $Backup
    }
    try {
        Move-Item -LiteralPath $Prepared -Destination $RuntimeRoot
    } catch {
        $PublishError = $_
        if (Test-Path -LiteralPath $Backup) {
            try {
                Move-Item -LiteralPath $Backup -Destination $RuntimeRoot
            } catch {
                $KeepStaging = $true
                throw "runtime publication and rollback failed; original retained at $Backup"
            }
        }
        throw $PublishError
    }
    Write-Output "prepared Windows Desktop runtime $Version at $RuntimeRoot"
} finally {
    foreach ($Name in $SavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], "Process")
    }
    # Only our unique, absolute staging child is eligible for cleanup.
    if (-not $KeepStaging -and (Test-Path -LiteralPath $Staging)) {
        if (-not ([IO.Path]::GetFullPath($Staging)).StartsWith($BuildRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "refusing cleanup outside the runtime build directory"
        }
        try { Remove-Item -LiteralPath $Staging -Recurse -Force }
        catch { Write-Warning "temporary runtime files retained at $Staging" }
    }
    $Lock.Dispose()
}
