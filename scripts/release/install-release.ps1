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

function Assert-AllowedUrl {
    param([Parameter(Mandatory = $true)][string]$Url)

    $Parsed = [Uri]$Url
    $AllowedHosts = @(
        "github.com",
        "release-assets.githubusercontent.com"
    )
    if ($Parsed.Scheme -ne "https" -or $Parsed.UserInfo -or $Parsed.Host -notin $AllowedHosts) {
        throw "release URL is not allowed: $Url"
    }
}

function Download-ReleaseFile {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
    if (-not $Curl) {
        throw "curl.exe is required to download OpenProgram"
    }
    $CurrentUrl = $Url
    for ($Redirect = 0; $Redirect -le 5; $Redirect++) {
        Assert-AllowedUrl $CurrentUrl
        $Headers = Join-Path ([IO.Path]::GetDirectoryName($Destination)) (".headers-" + [guid]::NewGuid().ToString("N"))
        try {
            $Status = Invoke-NativeOutput $Curl --disable --proto "=https" --tlsv1.2 `
                --silent --show-error --connect-timeout 15 --speed-limit 1024 `
                --speed-time 120 --dump-header $Headers --output $Destination `
                --write-out "%{http_code}" $CurrentUrl
            if ($Status -match "^20[0-6]$") {
                return
            }
            if ($Status -notmatch "^(301|302|303|307|308)$") {
                throw "release download failed with HTTP $Status"
            }
            $Location = [IO.File]::ReadAllLines(
                $Headers,
                [Text.Encoding]::GetEncoding(28591)
            ) |
                Where-Object { $_ -match "^Location:\s*(.+?)\s*$" } |
                Select-Object -Last 1
            if (-not $Location) {
                throw "release redirect has no location"
            }
            $CurrentUrl = ([regex]::Match($Location, "^Location:\s*(.+?)\s*$", "IgnoreCase")).Groups[1].Value
        } finally {
            Remove-Item -LiteralPath $Headers -Force -ErrorAction SilentlyContinue
        }
    }
    throw "release redirect limit exceeded"
}

function Assert-SafeArchive {
    param([Parameter(Mandatory = $true)][string]$Archive)

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    $Names = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    [long]$ExpandedBytes = 0
    try {
        foreach ($Entry in $Zip.Entries) {
            $Name = $Entry.FullName
            if (
                -not $Name -or
                $Name.Contains("\") -or
                $Name.StartsWith("/") -or
                $Name.Contains(":") -or
                ($Name -ne "runtime" -and -not $Name.StartsWith("runtime/"))
            ) {
                throw "invalid archive path: $Name"
            }
            $Parts = $Name.Split("/")
            for ($Index = 0; $Index -lt $Parts.Count; $Index++) {
                $Part = $Parts[$Index]
                if ($Part -eq ".." -or $Part -eq ".") {
                    throw "invalid archive path: $Name"
                }
                if (
                    -not $Part -and
                    $Index -ne ($Parts.Count - 1)
                ) {
                    throw "invalid archive path: $Name"
                }
                if ($Part -and $Part.TrimEnd(@(" ", ".")) -ne $Part) {
                    throw "archive path is not portable to Windows: $Name"
                }
            }
            if (-not $Names.Add($Name.TrimEnd("/"))) {
                throw "archive contains a duplicate path: $Name"
            }
            $ExpandedBytes += $Entry.Length
            if ($ExpandedBytes -gt 8589934592) {
                throw "runtime archive expands beyond the 8 GiB safety limit"
            }
            $UnixType = (($Entry.ExternalAttributes -shr 16) -band 0xF000)
            if ($UnixType -eq 0xA000) {
                throw "archive contains a symbolic link: $Name"
            }
        }
    } finally {
        $Zip.Dispose()
    }
}

function Expand-SafeArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    Assert-SafeArchive $Archive
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
    $ReparsePoint = Get-ChildItem -LiteralPath $Destination -Force -Recurse |
        Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($ReparsePoint) {
        throw "extracted runtime contains a reparse point: $($ReparsePoint.FullName)"
    }
}

function Test-WorkerHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][int]$Port
    )

    Invoke-Native $Python -I -B -c @'
import json
import sys
import time
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/healthz"
for attempt in range(120):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.load(response)
        if payload.get("status") == "ok":
            raise SystemExit(0)
    except Exception:
        if attempt == 119:
            raise
        time.sleep(0.25)
raise SystemExit("worker health probe did not become ready")
'@ ([string]$Port)
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function ConvertTo-CmdBatchLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "path cannot be represented safely in a Windows batch launcher"
    }
    # Percent signs are environment-variable syntax even inside quotes. A
    # doubled percent survives batch parsing as the literal path character.
    return $Value.Replace("%", "%%")
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

function Move-Atomic {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string]$Backup
    )

    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        if ($Backup) {
            Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
            [IO.File]::Replace($Source, $Destination, $Backup, $true)
        } else {
            [IO.File]::Replace($Source, $Destination, $null, $true)
        }
    } else {
        [IO.File]::Move($Source, $Destination)
    }
}

$Version = if ($env:OPENPROGRAM_VERSION) { $env:OPENPROGRAM_VERSION } else { "0.8.1" }
$Repository = if ($env:OPENPROGRAM_REPOSITORY) { $env:OPENPROGRAM_REPOSITORY } else { "Fzkuji/OpenProgram" }
if ($Version -notmatch "^\d+\.\d+\.\d+$") {
    throw "invalid OpenProgram version: $Version"
}
if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "invalid OpenProgram repository: $Repository"
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "OpenProgram release installer requires 64-bit Windows"
}
$Architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($Architecture -notin @("x64", "arm64")) {
    throw "unsupported CPU architecture: $Architecture"
}
$Arch = if ($Architecture -eq "x64") { "x86_64" } else { "arm64" }

$StateRoot = if ($env:OPENPROGRAM_STATE_DIR) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_STATE_DIR)
} else {
    Join-Path $env:USERPROFILE ".openprogram"
}
$RuntimeRoot = Join-Path $StateRoot "runtime\cli"
$ReleasesRoot = Join-Path $RuntimeRoot "releases"
$ReleaseDir = Join-Path $ReleasesRoot $Version
$BinDir = if ($env:OPENPROGRAM_BIN_DIR) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_BIN_DIR)
} elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "OpenProgram\bin"
} else {
    Join-Path $StateRoot "bin"
}
New-Item -ItemType Directory -Path $ReleasesRoot, $BinDir -Force | Out-Null

$InstallLock = [IO.File]::Open((Join-Path $RuntimeRoot '.install.lock'),
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$Staging = Join-Path $RuntimeRoot (".staging-$Version-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path $Staging | Out-Null
    $CandidateRoot = $ReleaseDir
    if (-not (Test-Path -LiteralPath $ReleaseDir -PathType Container)) {
        $ArchiveName = "OpenProgram-$Version-runtime-windows-$Arch.zip"
        if ($env:OPENPROGRAM_RUNTIME_ARCHIVE) {
            $Archive = [IO.Path]::GetFullPath($env:OPENPROGRAM_RUNTIME_ARCHIVE)
            if (-not [IO.Path]::IsPathRooted($env:OPENPROGRAM_RUNTIME_ARCHIVE) -or
                -not $Archive.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
                throw "OPENPROGRAM_RUNTIME_ARCHIVE must be an absolute existing .zip path"
            }
        } else {
            $Archive = Join-Path $Staging $ArchiveName
            $ReleaseUrl = "https://github.com/$Repository/releases/download/v$Version"
            Download-ReleaseFile "$ReleaseUrl/$ArchiveName" $Archive
            Download-ReleaseFile "$ReleaseUrl/$ArchiveName.sha256" "$Archive.sha256"
        }

        $Expected = $env:OPENPROGRAM_RUNTIME_SHA256
        if (-not $Expected -and (Test-Path -LiteralPath "$Archive.sha256" -PathType Leaf)) {
            $Expected = ((Get-Content -LiteralPath "$Archive.sha256" -TotalCount 1) -split "\s+")[0]
        }
        if (-not $Expected -or $Expected -notmatch "^[a-fA-F0-9]{64}$") {
            throw "runtime archive checksum is required"
        }
        $Actual = Get-Sha256 $Archive
        if (-not $Actual.Equals($Expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw "runtime archive checksum mismatch"
        }

        Expand-SafeArchive $Archive $Staging
        $ExtractedRuntime = Join-Path $Staging "runtime"
        if (-not (Test-Path -LiteralPath (Join-Path $ExtractedRuntime "runtime-manifest.json") -PathType Leaf)) {
            throw "runtime archive has no manifest"
        }
        $CandidateRoot = $ExtractedRuntime
    }

$ManifestPath = Join-Path $CandidateRoot "runtime-manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "candidate runtime has no manifest: $CandidateRoot"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$PythonRelative = [string]$Manifest.python
if (-not $PythonRelative -or [IO.Path]::IsPathRooted($PythonRelative)) {
    throw "runtime manifest Python path is invalid"
}
$PythonBin = [IO.Path]::GetFullPath((Join-Path $CandidateRoot $PythonRelative))
$ReleasePrefix = [IO.Path]::GetFullPath($CandidateRoot).TrimEnd("\") + "\"
if (-not $PythonBin.StartsWith($ReleasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "runtime manifest Python path escapes the release directory"
}
if (-not (Test-Path -LiteralPath $PythonBin -PathType Leaf)) {
    throw "managed Python is missing: $PythonBin"
}
Invoke-Native $PythonBin -I (Join-Path $CandidateRoot "bin\verify-product-runtime.py") $CandidateRoot
Invoke-Native $PythonBin -I -m openprogram --version

$ProbeListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$ProbeListener.Start()
$ProbePort = ([Net.IPEndPoint]$ProbeListener.LocalEndpoint).Port
$ProbeListener.Stop()
$ProbeState = Join-Path ([IO.Path]::GetTempPath()) ("openprogram-release-probe-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $ProbeState | Out-Null
$PreviousEnvironment = @{
    OPENPROGRAM_STATE_DIR = $env:OPENPROGRAM_STATE_DIR
    OPENPROGRAM_WEB_PORT = $env:OPENPROGRAM_WEB_PORT
    PLAYWRIGHT_BROWSERS_PATH = $env:PLAYWRIGHT_BROWSERS_PATH
    GPA_MODEL_PATH = $env:GPA_MODEL_PATH
    HOME = $env:HOME
    USERPROFILE = $env:USERPROFILE
}
try {
    # Product state follows Path.home(); isolate both Windows and POSIX-style
    # home resolution so this probe never sees or stops the user's worker.
    $env:HOME = $ProbeState
    $env:USERPROFILE = $ProbeState
    Remove-Item Env:OPENPROGRAM_STATE_DIR -ErrorAction SilentlyContinue
    $env:OPENPROGRAM_WEB_PORT = [string]$ProbePort
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $CandidateRoot "assets\playwright"
    $env:GPA_MODEL_PATH = Join-Path $CandidateRoot "assets\gpa\model.pt"
    Invoke-Native $PythonBin -I -B -m openprogram worker start
    Test-WorkerHealth $PythonBin $ProbePort
    Invoke-Native $PythonBin -I -B -m openprogram worker stop
} finally {
    try {
        & $PythonBin -I -B -m openprogram worker stop *> $null
    } catch {
    }
    foreach ($Name in $PreviousEnvironment.Keys) {
        $Value = $PreviousEnvironment[$Name]
        if ($null -eq $Value) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        } else {
            Set-Item "Env:$Name" $Value
        }
    }
    Remove-Item -LiteralPath $ProbeState -Recurse -Force -ErrorAction SilentlyContinue
}

# Only a completely verified candidate becomes a reusable immutable release.
# The per-runtime lock also serializes launcher activation and rollback.
if ($CandidateRoot -ne $ReleaseDir) {
    [IO.Directory]::Move($CandidateRoot, $ReleaseDir)
    $PythonBin = [IO.Path]::GetFullPath((Join-Path $ReleaseDir $PythonRelative))
}

$LauncherPs1 = Join-Path $BinDir "openprogram.ps1"
$LauncherCmd = Join-Path $BinDir "openprogram.cmd"
$LauncherTemporary = Join-Path $BinDir (".openprogram-" + [guid]::NewGuid().ToString("N") + ".ps1")
$LauncherContent = @"
`$ErrorActionPreference = "Stop"
`$env:PLAYWRIGHT_BROWSERS_PATH = '$((Join-Path $ReleaseDir "assets\playwright").Replace("'", "''"))'
`$env:GPA_MODEL_PATH = '$((Join-Path $ReleaseDir "assets\gpa\model.pt").Replace("'", "''"))'
`$env:OPENPROGRAM_IMMUTABLE_RUNTIME = "1"
& '$($PythonBin.Replace("'", "''"))' -I -m openprogram @args
exit `$LASTEXITCODE
"@
# Windows PowerShell 5 otherwise decodes UTF-8 paths as the system ANSI page.
[IO.File]::WriteAllText($LauncherTemporary, $LauncherContent, [Text.UTF8Encoding]::new($true))
try {
    Move-Atomic $LauncherTemporary $LauncherPs1 (Join-Path $BinDir "openprogram.previous.ps1")
} finally {
    Remove-Item -LiteralPath $LauncherTemporary -Force -ErrorAction SilentlyContinue
}
$CmdTemporary = Join-Path $BinDir (".openprogram-" + [guid]::NewGuid().ToString("N") + ".cmd")
$CmdPython = ConvertTo-CmdBatchLiteral $PythonBin
$CmdPlaywright = ConvertTo-CmdBatchLiteral (Join-Path $ReleaseDir "assets\playwright")
$CmdGpa = ConvertTo-CmdBatchLiteral (Join-Path $ReleaseDir "assets\gpa\model.pt")
$CmdContent = @"
@echo off
setlocal DisableDelayedExpansion
for /f "tokens=2 delims=:" %%P in ('chcp') do set "_OPENPROGRAM_CODEPAGE=%%P"
chcp 65001 >nul
set "PLAYWRIGHT_BROWSERS_PATH=$CmdPlaywright"
set "GPA_MODEL_PATH=$CmdGpa"
set "OPENPROGRAM_IMMUTABLE_RUNTIME=1"
"$CmdPython" -I -B -m openprogram %*
set "_OPENPROGRAM_EXIT=%ERRORLEVEL%"
chcp %_OPENPROGRAM_CODEPAGE% >nul
exit /b %_OPENPROGRAM_EXIT%
"@
# CMD needs BOM-free UTF-8 and an ASCII preamble selecting that code page
# before it parses embedded paths. Restore the caller's console on return;
# delayed expansion must not consume literal exclamation marks in paths.
Write-Utf8NoBom $CmdTemporary $CmdContent
try {
    # A managed install must replace launchers left by an older release or a
    # source checkout. Otherwise PATH can silently continue to run a stale
    # virtualenv even though the new runtime passed every activation probe.
    Move-Atomic $CmdTemporary $LauncherCmd (Join-Path $BinDir "openprogram.previous.cmd")
} finally {
    Remove-Item -LiteralPath $CmdTemporary -Force -ErrorAction SilentlyContinue
}

if (-not $env:OPENPROGRAM_BIN_DIR) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Parts = @($UserPath -split ";" | Where-Object { $_ })
    if (-not ($Parts | Where-Object { $_.TrimEnd("\") -ieq $BinDir.TrimEnd("\") })) {
        $NextPath = (@($Parts) + $BinDir) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $NextPath, "User")
    }
}

Write-Host "OpenProgram $Version installed."
Write-Host "Executable: $LauncherCmd"
Write-Host "Runtime: $ReleaseDir"
} finally {
    try {
        if (Test-Path -LiteralPath $Staging) {
            $StagingFull = [IO.Path]::GetFullPath($Staging)
            $RuntimePrefix = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') + '\'
            if (-not $StagingFull.StartsWith($RuntimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'refusing cleanup outside the CLI runtime staging directory'
            }
            try { Remove-Item -LiteralPath $StagingFull -Recurse -Force -ErrorAction Stop }
            catch { Write-Warning "runtime staging retained at ${StagingFull}: $($_.Exception.Message)" }
        }
    } finally {
        $InstallLock.Dispose()
    }
}
