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

function Assert-AllowedUrl {
    param([Parameter(Mandatory = $true)][string]$Url)

    $Parsed = [Uri]$Url
    $AllowedHosts = @(
        "github.com",
        "release-assets.githubusercontent.com",
        "fzkuji.github.io"
    )
    if ($Parsed.Scheme -ne "https" -or $Parsed.UserInfo -or $Parsed.Host -notin $AllowedHosts) {
        throw "release URL is not allowed: $Url"
    }
}

function Get-ReleaseCurl {
    $Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
    if (-not $Curl) {
        throw "curl.exe is required to download OpenProgram"
    }
    return $Curl
}

function Get-NetworkFailureHelp {
    return (
        "Could not download OpenProgram from GitHub. Check that this computer is online. " +
        "If it reaches the internet through a proxy, set HTTPS_PROXY (and NO_PROXY if some hosts skip the proxy), " +
        "for example: `$env:HTTPS_PROXY = 'http://proxy.example:8080'. " +
        "Terminal downloads do not use the proxy from Windows settings. " +
        "Run this command again afterwards; it continues where it stopped."
    )
}

function Format-ByteSize {
    param([Parameter(Mandatory = $true)][long]$Bytes)

    if ($Bytes -ge 1GB) {
        return ("{0:N1} GB" -f ($Bytes / 1GB))
    }
    return ("{0:N0} MB" -f ($Bytes / 1MB))
}

# The missing amount in the unit that shows it: a need and a free amount within
# the same tenth of a gigabyte print as the same figure.
function Format-Shortfall {
    param([Parameter(Mandatory = $true)][long]$Bytes)

    if ($Bytes -ge 1GB) {
        return ("{0:N1} GB" -f ($Bytes / 1GB))
    }
    if ($Bytes -ge 1MB) {
        return ("{0:N0} MB" -f ($Bytes / 1MB))
    }
    return "less than 1 MB"
}

function Get-FileLength {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return [long](Get-Item -LiteralPath $Path).Length
    }
    return [long]0
}

function Test-InteractiveTerminal {
    try {
        return (-not [Console]::IsErrorRedirected)
    } catch {
        return $false
    }
}

# Unpacking needs room for the download and for what it expands into.
# How many copies of the archive sit on disk during unpacking. A seed holds two:
# the download and the archives inside it.
$script:ArchiveCopiesOnDisk = 1

function Assert-FreeDiskSpace {
    param(
        [Parameter(Mandatory = $true)][long]$ArchiveBytes,
        [Parameter(Mandatory = $true)][string]$Directory,
        [long]$AlreadyDownloaded = 0
    )

    try {
        $Root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Directory))
        [long]$Available = ([IO.DriveInfo]::new($Root)).AvailableFreeSpace
    } catch {
        Write-Verbose "free disk space is unknown for ${Directory}: $($_.Exception.Message)"
        return
    }
    [long]$Needed = ($ArchiveBytes * ($script:ArchiveCopiesOnDisk + 4)) + 536870912 - $AlreadyDownloaded
    if ($Available -lt $Needed) {
        throw ("Not enough free disk space. OpenProgram needs $(Format-ByteSize $Needed) on $Root " +
            "(room for the $(Format-ByteSize $ArchiveBytes) archive, about four times that once unpacked, and 512 MB to spare), " +
            "and $(Format-ByteSize $Available) is free, $(Format-Shortfall ($Needed - $Available)) short. Free up some space, then run this command again.")
    }
}

function Get-ResponseHeader {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $Pattern = "^" + [regex]::Escape($Name) + ":\s*(.+?)\s*$"
    $Line = [IO.File]::ReadAllLines(
        $Path,
        [Text.Encoding]::GetEncoding(28591)
    ) |
        Where-Object { $_ -match $Pattern } |
        Select-Object -Last 1
    if (-not $Line) {
        return ""
    }
    return ([regex]::Match($Line, $Pattern, "IgnoreCase")).Groups[1].Value
}

# Follows the redirects one by one, so every host is checked against the list.
function Resolve-ReleaseUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$HeaderDirectory
    )

    $Curl = Get-ReleaseCurl
    $CurrentUrl = $Url
    for ($Redirect = 0; $Redirect -le 5; $Redirect++) {
        Assert-AllowedUrl $CurrentUrl
        $Headers = Join-Path $HeaderDirectory (".headers-" + [guid]::NewGuid().ToString("N"))
        try {
            $Status = (& $Curl --disable --proto "=https" --tlsv1.2 `
                --silent --show-error --head --connect-timeout 15 --max-time 60 `
                --retry 3 --retry-delay 2 --retry-connrefused `
                --dump-header $Headers --output NUL `
                --write-out "%{http_code}" $CurrentUrl | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                throw (Get-NetworkFailureHelp)
            }
            if ($Status -eq "200") {
                [long]$Size = -1
                $Length = Get-ResponseHeader $Headers "Content-Length"
                if ($Length -match "^\d+$") {
                    $Size = [long]$Length
                }
                return @{ Url = $CurrentUrl; Size = $Size }
            }
            if ($Status -notmatch "^(301|302|303|307|308)$") {
                throw "release download failed with HTTP $Status"
            }
            $CurrentUrl = Get-ResponseHeader $Headers "Location"
            if (-not $CurrentUrl) {
                throw "release redirect has no location"
            }
        } finally {
            Remove-Item -LiteralPath $Headers -Force -ErrorAction SilentlyContinue
        }
    }
    throw "release redirect limit exceeded"
}

# A label names a large download: it gets a size line, a disk check, a progress
# bar and a partial file that a later attempt or a later run continues. curl's
# own --retry is left out of this transfer because it discards what the failed
# attempt wrote; this loop keeps it and continues from there.
function Download-ReleaseFile {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string]$Label = ""
    )

    $Curl = Get-ReleaseCurl
    if (-not $Label) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
    }
    $ProgressOption = "--silent"
    if ($Label -and (Test-InteractiveTerminal)) {
        $ProgressOption = "--progress-bar"
    }
    $Directory = [IO.Path]::GetDirectoryName($Destination)
    $Announced = $false
    $StalledAttempts = 0
    while ($true) {
        $Resolved = Resolve-ReleaseUrl $Url $Directory
        [long]$Size = $Resolved.Size
        [long]$ExistingBytes = Get-FileLength $Destination
        if ($Size -ge 0 -and $ExistingBytes -gt $Size) {
            Remove-Item -LiteralPath $Destination -Force
            $ExistingBytes = 0
        }
        if ($Label -and -not $Announced) {
            $Announced = $true
            if ($Size -ge 0) {
                if ($ExistingBytes -gt 0) {
                    Write-Host "Continuing the download of ${Label}: $(Format-ByteSize $ExistingBytes) of $(Format-ByteSize $Size) is already here."
                } else {
                    Write-Host "Downloading $Label ($(Format-ByteSize $Size))."
                }
                Assert-FreeDiskSpace $Size $Directory $ExistingBytes
            } else {
                Write-Host "Downloading $Label."
            }
        }
        if ($Size -ge 0 -and $ExistingBytes -eq $Size) {
            return
        }
        $Status = (& $Curl --disable --proto "=https" --tlsv1.2 --fail `
            $ProgressOption --show-error --connect-timeout 15 `
            --speed-limit 1024 --speed-time 120 --continue-at - `
            --output $Destination --write-out "%{http_code}" $Resolved.Url | Out-String).Trim()
        $TransferExit = $LASTEXITCODE
        [long]$CurrentBytes = Get-FileLength $Destination
        if ($TransferExit -eq 0 -and $Status -match "^(20[0-6]|416)$") {
            if ($Size -lt 0 -or $CurrentBytes -eq $Size) {
                return
            }
        }
        if ($TransferExit -eq 22 -and $Status -match "^(404|410)$") {
            throw "release download failed with HTTP $Status"
        }
        if ($TransferExit -eq 23) {
            throw "Could not write $Destination. Check that the disk has free space, then run this command again."
        }
        if ($TransferExit -in @(33, 36)) {
            # The server cannot continue this file, so start it over.
            Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            $CurrentBytes = 0
        }
        if ($CurrentBytes -gt $ExistingBytes) {
            $StalledAttempts = 0
        } else {
            $StalledAttempts++
        }
        if ($StalledAttempts -ge 5) {
            throw (Get-NetworkFailureHelp)
        }
        if ($Label) {
            Write-Host "The download stopped at $(Format-ByteSize $CurrentBytes). Trying again from there."
        }
        Start-Sleep -Seconds 2
    }
}

function Assert-SafeArchive {
    param([Parameter(Mandatory = $true)][string]$Archive)

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [IO.Compression.ZipFile]::OpenRead((ConvertTo-ExtendedFileSystemPath $Archive))
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

function ConvertTo-ExtendedFileSystemPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Full = [IO.Path]::GetFullPath($Path)
    if ($Full.StartsWith('\\?\', [StringComparison]::Ordinal)) { return $Full }
    if ($Full.StartsWith('\\', [StringComparison]::Ordinal)) {
        return '\\?\UNC\' + $Full.Substring(2)
    }
    return '\\?\' + $Full
}

function Expand-SafeArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    Assert-SafeArchive $Archive
    # Windows PowerShell 5's default .NET paths and provider enumeration can
    # fail on ordinary dependency trees once the staging prefix exceeds 260
    # characters. Keep extraction and inspection on extended-length paths.
    $Extended = ConvertTo-ExtendedFileSystemPath $Destination
    if ([IO.Directory]::Exists($Extended) -and
        ([IO.File]::GetAttributes($Extended) -band [IO.FileAttributes]::ReparsePoint)) {
        throw "runtime extraction destination is redirected: $Destination"
    }
    $Zip = [IO.Compression.ZipFile]::OpenRead((ConvertTo-ExtendedFileSystemPath $Archive))
    try {
        [IO.Directory]::CreateDirectory($Extended) | Out-Null
        $Buffer = New-Object byte[] 65536
        [long]$Written = 0
        foreach ($Entry in $Zip.Entries) {
            $Name = $Entry.FullName.Replace('\', '/')
            $Target = $Extended.TrimEnd('\') + '\' + $Name.Replace('/', '\').TrimEnd('\')
            if ($Name.EndsWith('/')) {
                [IO.Directory]::CreateDirectory($Target) | Out-Null
                continue
            }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Target)) | Out-Null
            $InputStream = $Entry.Open()
            try {
                $OutputStream = [IO.File]::Open($Target, [IO.FileMode]::CreateNew,
                    [IO.FileAccess]::Write, [IO.FileShare]::None)
                try {
                    while (($Count = $InputStream.Read($Buffer, 0, $Buffer.Length)) -gt 0) {
                        $Written += $Count
                        if ($Written -gt 8589934592) { throw 'runtime archive expands beyond the 8 GiB limit' }
                        $OutputStream.Write($Buffer, 0, $Count)
                    }
                    if ($OutputStream.Length -ne $Entry.Length) { throw "archive entry length mismatch: $Name" }
                } finally { $OutputStream.Dispose() }
            } finally { $InputStream.Dispose() }
        }
    } finally { $Zip.Dispose() }
    $Pending = [Collections.Generic.Stack[string]]::new()
    $Pending.Push($Extended)
    while ($Pending.Count -gt 0) {
        foreach ($Entry in [IO.Directory]::EnumerateFileSystemEntries($Pending.Pop())) {
            $Attributes = [IO.File]::GetAttributes($Entry)
            if ($Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "extracted runtime contains a reparse point: $Entry"
            }
            if ($Attributes -band [IO.FileAttributes]::Directory) { $Pending.Push($Entry) }
        }
    }
}

function Test-WorkerHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][int]$Port
    )

    # Windows PowerShell 5.1 does not escape double quotes in a native argument,
    # so the Python here has to stay free of them.
    Invoke-Native $Python -I -B -X utf8 -c @'
import json
import sys
import time
import urllib.request

url = f'http://127.0.0.1:{sys.argv[1]}/healthz'
for attempt in range(120):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.load(response)
        if payload.get('status') == 'ok':
            raise SystemExit(0)
    except Exception:
        if attempt == 119:
            raise
        time.sleep(0.25)
raise SystemExit('worker health probe did not become ready')
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

# Every .cmd this installer writes goes through here, because every one names a
# path and cmd.exe parses a batch file in the console's code page: a profile
# folder with an accented or Chinese name, written as UTF-8 and read as code
# page 437, is a folder that does not exist. The file is BOM-free UTF-8 whose first lines are ASCII:
# they note the caller's code page and switch the console to UTF-8 before any
# path is read. With -Run the last lines give the caller its code page back
# with the exit status of those lines. With -HandOffTo the launcher starts
# another launcher that has the same preamble and never returns: it passes the
# caller's code page on in _OPENPROGRAM_CALLER_CODEPAGE, and the launcher it
# starts restores it. That launcher is started without `call`, which would
# double every caret inside a quoted argument and expand percent signs a second
# time. %* is expanded as text, so non-ASCII arguments arrive unchanged in any
# code page. scripts/release/bootstrap/bin-openprogram.cmd and scripts/install.ps1
# carry the same preamble; a test keeps the three equal.
function Write-CmdLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$Run = @(),
        [string]$HandOffTo = ""
    )

    $Lines = @(
        "@echo off",
        "setlocal DisableDelayedExpansion",
        'set "_OPENPROGRAM_CODEPAGE=%_OPENPROGRAM_CALLER_CODEPAGE%"',
        'set "_OPENPROGRAM_CALLER_CODEPAGE="',
        'if not defined _OPENPROGRAM_CODEPAGE for /f "tokens=2 delims=:." %%P in (''chcp'') do set "_OPENPROGRAM_CODEPAGE=%%P"',
        "chcp 65001 >nul"
    )
    if ($HandOffTo) {
        $Lines += @(
            'set "_OPENPROGRAM_CALLER_CODEPAGE=%_OPENPROGRAM_CODEPAGE%"',
            "`"$(ConvertTo-CmdBatchLiteral $HandOffTo)`" %*"
        )
    } else {
        $Lines += $Run
        $Lines += @(
            'set "_OPENPROGRAM_EXIT=%ERRORLEVEL%"',
            "chcp %_OPENPROGRAM_CODEPAGE% >nul",
            "exit /b %_OPENPROGRAM_EXIT%"
        )
    }
    Write-Utf8NoBom $Path (($Lines -join "`r`n") + "`r`n")
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
        [Parameter(Mandatory = $true)][string]$Backup
    )

    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
        [IO.File]::Replace($Source, $Destination, $Backup, $true)
    } else {
        [IO.File]::Move($Source, $Destination)
    }
}

# PowerShell prefers a .ps1 over a .cmd of the same name and then refuses it
# under the default Restricted policy, so an old one has to go.
function Remove-PowerShellLaunchers {
    param([Parameter(Mandatory = $true)][string]$Directory)

    foreach ($Name in @("openprogram.ps1", "openprogram.previous.ps1")) {
        $Path = Join-Path $Directory $Name
        if (-not (Test-Path -LiteralPath $Path)) {
            continue
        }
        try {
            Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
        } catch {
            Write-Warning "Could not delete the old launcher $Path. Delete it, or PowerShell keeps using it: $($_.Exception.Message)"
        }
    }
}

function Publish-CliLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    if ((Test-Path -LiteralPath $Target) -and -not (Test-Path -LiteralPath $Target -PathType Leaf)) {
        throw "CLI launcher target is not a file: $Target"
    }
    $Directory = Split-Path -Parent $Target
    Move-Atomic $Source $Target (Join-Path $Directory "openprogram.previous.cmd")
    Remove-PowerShellLaunchers $Directory
}

function Add-DirectoryToUserPath {
    param([Parameter(Mandatory = $true)][string]$Directory)

    # The registry value is read and written as stored: the Environment calls
    # would expand an entry like %JAVA_HOME%\bin and freeze it at today's value.
    $Key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
    try {
        $Stored = $null -ne $Key.GetValue("Path")
        $UserPath = [string]$Key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $Parts = @($UserPath -split ";" | Where-Object { $_ })
        if ($Parts | Where-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd("\") -ieq $Directory.TrimEnd("\") }) {
            return
        }
        $Kind = if ($Stored) { $Key.GetValueKind("Path") } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
        $Key.SetValue("Path", ((@($Parts) + $Directory) -join ";"), $Kind)
    } finally {
        $Key.Dispose()
    }
    # Deleting an absent variable writes nothing, and this call still tells
    # Explorer and new terminals that the environment changed.
    [Environment]::SetEnvironmentVariable("OPENPROGRAM_PATH_CHANGED", $null, "User")
}

function Write-InstallSummary {
    param(
        [Parameter(Mandatory = $true)][string]$Headline,
        [Parameter(Mandatory = $true)][string]$Launcher,
        [Parameter(Mandatory = $true)][string]$Runtime
    )

    $LauncherDirectory = Split-Path -Parent $Launcher
    Write-Host $Headline
    Write-Host "Executable: $Launcher"
    Write-Host "Runtime: $Runtime"
    Write-Host "Desktop control is optional. Before using it, open Settings > System on the execution computer. Installation does not elevate the application."
    if ($env:OPENPROGRAM_BIN_DIR) {
        Write-Host "PATH: $LauncherDirectory was not added to your PATH because OPENPROGRAM_BIN_DIR chose it."
        Write-Host "Next, run: $Launcher"
    } else {
        Write-Host "PATH: $LauncherDirectory is on your user PATH. Terminals that are already open do not see it yet."
        Write-Host "Next, open a new terminal and run: openprogram"
    }
}

# Starts the candidate worker in a throwaway home and waits until it answers.
function Invoke-WorkerProbe {
    param(
        [Parameter(Mandatory = $true)][string]$PythonBin,
        [string]$PlaywrightPath = ""
    )

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
        $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightPath
        Invoke-Native $PythonBin -I -B -X utf8 -m openprogram worker start
        Test-WorkerHealth $PythonBin $ProbePort
        Invoke-Native $PythonBin -I -B -X utf8 -m openprogram worker stop
    } finally {
        try {
            & $PythonBin -I -B -X utf8 -m openprogram worker stop *> $null
        } catch {
            Write-Verbose "worker probe was already stopped: $($_.Exception.Message)"
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
}

$Version = if ($env:OPENPROGRAM_VERSION) { $env:OPENPROGRAM_VERSION } else { "1.5.1" }
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
function Get-OSArchitecture {
    # RuntimeInformation.OSArchitecture exists only from .NET Framework 4.7.1
    # on; under strict mode an older Windows PowerShell stops on the missing
    # property, so fall back to the native processor the environment names.
    try {
        $Value = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture
        if ($Value) {
            return $Value.ToString().ToLowerInvariant()
        }
    } catch {
        Write-Verbose "RuntimeInformation.OSArchitecture is unavailable: $($_.Exception.Message)"
    }
    $Native = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ("$Native".ToUpperInvariant()) {
        "AMD64" { return "x64" }
        "ARM64" { return "arm64" }
        default { return "$Native".ToLowerInvariant() }
    }
}
$Architecture = Get-OSArchitecture
if ($Architecture -notin @("x64", "arm64")) {
    throw "unsupported CPU architecture: $Architecture"
}
$Arch = if ($Architecture -eq "x64") { "x86_64" } else { "arm64" }

$StateRoot = if ($env:OPENPROGRAM_STATE_DIR) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_STATE_DIR)
} else {
    Join-Path $env:USERPROFILE ".openprogram"
}

function Get-StorePointerVersion {
    param([Parameter(Mandatory = $true)][string]$Store)

    foreach ($Line in [IO.File]::ReadAllLines((Join-Path $Store "current"))) {
        if ($Line.StartsWith("version=")) {
            return $Line.Substring(8)
        }
    }
    return ""
}

function Get-StoreActiveVersion {
    param([Parameter(Mandatory = $true)][string]$Store)

    $Output = (& (Join-Path $Store "bin\openprogram.cmd") --version | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $Output) {
        return $Output
    }
    return (Get-StorePointerVersion $Store)
}

# The plain runtime is the working install until the store has answered a real
# worker start, so a broken seed can never take the only copy with it.
function Remove-PlainRuntimeAfterHealthCheck {
    param([Parameter(Mandatory = $true)][string]$Store)

    $PlainRuntime = Join-Path $Store "cli"
    if (-not (Test-Path -LiteralPath $PlainRuntime)) {
        return
    }
    $StorePython = Join-Path $Store "versions\$(Get-StorePointerVersion $Store)\venv\Scripts\python.exe"
    try {
        Invoke-WorkerProbe $StorePython
    } catch {
        throw "The new OpenProgram did not start, so the existing installation in $PlainRuntime was kept and still works. $($_.Exception.Message)"
    }
    Remove-Item -LiteralPath $PlainRuntime -Recurse -Force -ErrorAction SilentlyContinue
}

function Publish-StoreLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][string]$Bin
    )

    Write-CmdLauncher (Join-Path $Bin "openprogram.cmd") -HandOffTo (Join-Path $Store "bin\openprogram.cmd")
    Remove-PowerShellLaunchers $Bin
    if (-not $env:OPENPROGRAM_BIN_DIR) {
        Add-DirectoryToUserPath $Bin
    }
}

# An existing store updates through its own upgrade command. What this prints
# comes from what that command did, never from the release that was asked for.
function Update-ExistingStore {
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][string]$Bin
    )

    $PointerBefore = Get-StorePointerVersion $Store
    Write-Host "OpenProgram is already installed here, active version: $(Get-StoreActiveVersion $Store). Looking for an update."
    & (Join-Path $Store "bin\openprogram.cmd") upgrade
    $UpgradeExit = $LASTEXITCODE
    Publish-StoreLauncher $Store $Bin
    if ($UpgradeExit -ne 0) {
        [Console]::Error.WriteLine("OpenProgram $Version was not installed: the update stopped for the reason above. The active version is still $(Get-StoreActiveVersion $Store).")
        exit $UpgradeExit
    }
    Remove-PlainRuntimeAfterHealthCheck $Store
    $Headline = if ((Get-StorePointerVersion $Store) -eq $PointerBefore) {
        "No new version was activated. The active version is still $(Get-StoreActiveVersion $Store)."
    } else {
        "OpenProgram updated. The active version is now $(Get-StoreActiveVersion $Store)."
    }
    Write-InstallSummary $Headline (Join-Path $Bin "openprogram.cmd") $Store
}

# A release that carries a seed installs into the version store, the same
# layout and `current` pointer the App uses. Older releases fall through to the
# plain runtime below.
# The base is unpacked beside the store and moved in only when complete, so an
# interrupted run never leaves a half base that the next run would accept.
# Windows' tar.exe (bsdtar) reads its arguments in the ANSI code page, so a
# path with a character outside it, such as a Chinese user name on English
# Windows, reaches it as '?' and the unpack fails. It therefore runs inside the
# destination and is given only a relative path to the archive, which below the
# profile folder is plain ASCII. An archive no ASCII relative path reaches (on
# another drive, or under such a folder) is copied in first.
function Expand-TarArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $From = @([IO.Path]::GetFullPath($Destination).TrimEnd('\').Split('\'))
    $To = @([IO.Path]::GetFullPath($Archive).Split('\'))
    $Common = 0
    while ($Common -lt $From.Count -and $Common -lt ($To.Count - 1) -and $From[$Common] -ieq $To[$Common]) {
        $Common++
    }
    $Relative = (@(@('..') * ($From.Count - $Common)) + @($To[$Common..($To.Count - 1)])) -join '\'
    $Copied = $null
    if ($Common -eq 0 -or $Relative -match '[^\x20-\x7E]') {
        $Copied = Join-Path $Destination (".archive-" + [guid]::NewGuid().ToString("N") + ".tar.gz")
        Copy-Item -LiteralPath $Archive -Destination $Copied
        $Relative = Split-Path -Leaf $Copied
    }
    Push-Location -LiteralPath $Destination
    try {
        Invoke-Native (Join-Path $env:SystemRoot "System32\tar.exe") -xzf $Relative
    } finally {
        Pop-Location
        if ($Copied) { Remove-Item -LiteralPath $Copied -Force -ErrorAction SilentlyContinue }
    }
}

function Install-SeedBase {
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][string]$BaseId,
        [Parameter(Mandatory = $true)][string]$Archive
    )

    $Destination = Join-Path $Store "bases\$BaseId"
    if (Test-Path -LiteralPath $Destination) { return }
    $BaseWork = Join-Path $Store ".seed-work"
    Remove-Item -LiteralPath $BaseWork -Recurse -Force -ErrorAction SilentlyContinue
    [IO.Directory]::CreateDirectory($BaseWork) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $Store "bases")) | Out-Null
    try {
        Expand-TarArchive $Archive $BaseWork
        $Unpacked = Join-Path $BaseWork "bases\$BaseId"
        # Antivirus scanners hold new files briefly, which refuses the move.
        for ($Attempt = 1; ; $Attempt++) {
            try {
                [IO.Directory]::Move($Unpacked, $Destination)
                break
            } catch {
                if ($Attempt -ge 8) { throw }
                Start-Sleep -Milliseconds (250 * $Attempt)
            }
        }
    } finally {
        Remove-Item -LiteralPath $BaseWork -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Install-FromSeed {
    param(
        [Parameter(Mandatory = $true)][string]$State,
        [Parameter(Mandatory = $true)][string]$SeedName,
        [Parameter(Mandatory = $true)][string]$ReleaseUrl
    )

    [IO.Directory]::CreateDirectory($State) | Out-Null
    $Store = Join-Path $State "runtime"
    [IO.Directory]::CreateDirectory($Store) | Out-Null
    $Bin = if ($env:OPENPROGRAM_BIN_DIR) { $env:OPENPROGRAM_BIN_DIR } else { Join-Path $env:LOCALAPPDATA "OpenProgram\bin" }
    [IO.Directory]::CreateDirectory($Bin) | Out-Null
    if (Test-Path -LiteralPath (Join-Path $Store "current")) {
        Update-ExistingStore $Store $Bin
        return
    }
    $Unrelated = @(Get-ChildItem -LiteralPath $Store -Force | Where-Object { $_.Name -notin @("cli", ".seeding", "bases", "versions") })
    if ($Unrelated.Count -gt 0 -and -not (Test-Path -LiteralPath (Join-Path $Store ".seeding"))) {
        throw "$Store already holds files that are not a version store"
    }
    $script:ArchiveCopiesOnDisk = 2
    $Work = Join-Path $State (".seed-install-" + [guid]::NewGuid().ToString("N"))
    [IO.Directory]::CreateDirectory($Work) | Out-Null
    $DownloadDirectory = Join-Path $State ".install-downloads"
    try {
        $Expected = $env:OPENPROGRAM_SEED_SHA256
        if ($env:OPENPROGRAM_SEED_ARCHIVE) {
            $Archive = $env:OPENPROGRAM_SEED_ARCHIVE
            Assert-FreeDiskSpace (Get-FileLength $Archive) $State
        } else {
            [IO.Directory]::CreateDirectory($DownloadDirectory) | Out-Null
            $Archive = Join-Path $DownloadDirectory $SeedName
            Download-ReleaseFile "$ReleaseUrl/$SeedName" $Archive "OpenProgram $Version for Windows $Arch"
            Download-ReleaseFile "$ReleaseUrl/$SeedName.sha256" "$Archive.sha256"
            $Expected = ((Get-Content -LiteralPath "$Archive.sha256" -TotalCount 1) -split '\s+')[0]
        }
        if ($Expected -notmatch '^[0-9A-Fa-f]{64}$') { throw "seed archive checksum must be exactly 64 hexadecimal characters" }
        if ((Get-Sha256 $Archive) -ne $Expected.ToLowerInvariant()) {
            if (-not $env:OPENPROGRAM_SEED_ARCHIVE) {
                Remove-Item -LiteralPath $Archive, "$Archive.sha256" -Force -ErrorAction SilentlyContinue
            }
            throw "seed archive checksum mismatch. The damaged download was removed. Run this command again."
        }
        Expand-TarArchive $Archive $Work
        $Seed = Join-Path $Work "seed"
        $Manifest = Get-Content -LiteralPath (Join-Path $Seed "seed.json") -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($Manifest.base -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw "invalid seed" }
        Set-Content -LiteralPath (Join-Path $Store ".seeding") -Value $PID -Encoding ascii
        Install-SeedBase $Store $Manifest.base (Join-Path $Seed "base.tar.gz")
        $BasePython = Join-Path $Store "bases\$($Manifest.base)\python\python.exe"
        Invoke-Native $BasePython -I -B -X utf8 (Join-Path $Seed "bootstrap\install_seed.py") --seed $Seed --root $Store --extract
        Remove-Item -LiteralPath $DownloadDirectory -Recurse -Force -ErrorAction SilentlyContinue
    } finally {
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
    }
    Invoke-Native (Join-Path $Store "bin\openprogram.cmd") --version
    Remove-PlainRuntimeAfterHealthCheck $Store
    Publish-StoreLauncher $Store $Bin
    Write-InstallSummary "OpenProgram $Version installed." (Join-Path $Bin "openprogram.cmd") $Store
}

$SeedReleaseUrl = "https://github.com/$Repository/releases/download/v$Version"

function Test-ReleaseFile {
    param([Parameter(Mandatory = $true)][string]$Name)

    $Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
    if (-not $Curl) {
        return $false
    }
    $Probe = & $Curl --disable --proto "=https" --tlsv1.2 --silent --location --head --connect-timeout 15 --output NUL --write-out "%{http_code}" "$SeedReleaseUrl/$Name"
    return $Probe -eq "200"
}

# Which package this computer installs. A release without a Windows on Arm
# package still installs there: Windows 11 on Arm (build 22000 and later) runs
# the x64 one under emulation. Windows 10 on Arm cannot, so it keeps the Arm
# name and the installer says that package is missing.
function Select-PackageArchitecture {
    param(
        [Parameter(Mandatory = $true)][string]$Native,
        [Parameter(Mandatory = $true)][int]$WindowsBuild,
        [Parameter(Mandatory = $true)][string]$ReleaseVersion,
        [Parameter(Mandatory = $true)][scriptblock]$HasFile
    )

    if ($Native -ne "arm64" -or $WindowsBuild -lt 22000) {
        return $Native
    }
    foreach ($Candidate in @("arm64", "x86_64")) {
        foreach ($Name in @("seed-windows-$Candidate.tar.gz", "runtime-windows-$Candidate.zip")) {
            if (& $HasFile "OpenProgram-$ReleaseVersion-$Name") {
                return $Candidate
            }
        }
    }
    return $Native
}

if (-not $env:OPENPROGRAM_SEED_ARCHIVE -and -not $env:OPENPROGRAM_RUNTIME_ARCHIVE) {
    $Selected = Select-PackageArchitecture $Arch ([Environment]::OSVersion.Version.Build) $Version ${function:Test-ReleaseFile}
    if ($Selected -ne $Arch) {
        Write-Host "OpenProgram $Version has no Windows on Arm package. Installing the x64 one, which Windows 11 on Arm runs under emulation."
        $Arch = $Selected
    }
}
$SeedName = "OpenProgram-$Version-seed-windows-$Arch.tar.gz"
$HasSeed = [bool]$env:OPENPROGRAM_SEED_ARCHIVE
if (-not $HasSeed -and -not $env:OPENPROGRAM_RUNTIME_ARCHIVE) {
    $HasSeed = Test-ReleaseFile $SeedName
}
if ($HasSeed) {
    Install-FromSeed $StateRoot $SeedName $SeedReleaseUrl
    return
}

$RuntimeRoot = Join-Path $StateRoot "runtime\cli"
$ReleasesRoot = Join-Path $RuntimeRoot "releases"
$ReleaseDir = Join-Path $ReleasesRoot $Version
$DownloadDirectory = Join-Path $StateRoot ".install-downloads"
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
            Assert-FreeDiskSpace (Get-FileLength $Archive) $StateRoot
        } else {
            New-Item -ItemType Directory -Path $DownloadDirectory -Force | Out-Null
            $Archive = Join-Path $DownloadDirectory $ArchiveName
            $ReleaseUrl = "https://github.com/$Repository/releases/download/v$Version"
            try {
                Download-ReleaseFile "$ReleaseUrl/$ArchiveName" $Archive "OpenProgram $Version for Windows $Arch"
            } catch {
                if ("$($_.Exception.Message)" -match "HTTP 404") {
                    throw "OpenProgram $Version has no Windows $Arch package ($ArchiveName is not attached to the release). Install from source instead: https://github.com/Fzkuji/OpenProgram/blob/main/docs/install/install.html#development-checkout"
                }
                throw
            }
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
            if (-not $env:OPENPROGRAM_RUNTIME_ARCHIVE) {
                Remove-Item -LiteralPath $Archive, "$Archive.sha256" -Force -ErrorAction SilentlyContinue
            }
            throw "runtime archive checksum mismatch. The damaged download was removed. Run this command again."
        }

        Expand-SafeArchive $Archive $Staging
        $ExtractedRuntime = Join-Path $Staging "runtime"
        if (-not (Test-Path -LiteralPath (Join-Path $ExtractedRuntime "runtime-manifest.json") -PathType Leaf)) {
            throw "runtime archive has no manifest"
        }
        $CandidateRoot = $ExtractedRuntime
        if (-not $env:OPENPROGRAM_RUNTIME_ARCHIVE) {
            Remove-Item -LiteralPath $DownloadDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
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
Invoke-Native $PythonBin -I -X utf8 (Join-Path $CandidateRoot "bin\verify-product-runtime.py") $CandidateRoot
Invoke-Native $PythonBin -I -X utf8 -m openprogram --version

Invoke-WorkerProbe $PythonBin (Join-Path $CandidateRoot "assets\playwright")

# Only a completely verified candidate becomes a reusable immutable release.
# The per-runtime lock also serializes launcher activation and rollback.
if ($CandidateRoot -ne $ReleaseDir) {
    [IO.Directory]::Move($CandidateRoot, $ReleaseDir)
    $PythonBin = [IO.Path]::GetFullPath((Join-Path $ReleaseDir $PythonRelative))
}

$LauncherCmd = Join-Path $BinDir "openprogram.cmd"
$CmdTemporary = Join-Path $BinDir (".openprogram-" + [guid]::NewGuid().ToString("N") + ".cmd")
$CmdPython = ConvertTo-CmdBatchLiteral $PythonBin
$CmdPlaywright = ConvertTo-CmdBatchLiteral (Join-Path $ReleaseDir "assets\playwright")
$CmdGit = ConvertTo-CmdBatchLiteral (Join-Path $ReleaseDir "git\cmd")
try {
    Write-CmdLauncher $CmdTemporary -Run @(
        "set `"PLAYWRIGHT_BROWSERS_PATH=$CmdPlaywright`"",
        "set `"OPENPROGRAM_IMMUTABLE_RUNTIME=1`"",
        "if exist `"$CmdGit\git.exe`" set `"PATH=$CmdGit;%PATH%`"",
        "`"$CmdPython`" -I -B -X utf8 -m openprogram %*"
    )
    # A managed install must replace launchers left by an older release or a
    # source checkout. Otherwise PATH can silently continue to run a stale
    # virtualenv even though the new runtime passed every activation probe.
    Publish-CliLauncher $CmdTemporary $LauncherCmd
} finally {
    Remove-Item -LiteralPath $CmdTemporary -Force -ErrorAction SilentlyContinue
}

if (-not $env:OPENPROGRAM_BIN_DIR) {
    Add-DirectoryToUserPath $BinDir
}

Write-InstallSummary "OpenProgram $Version installed." $LauncherCmd $ReleaseDir
} finally {
    try {
        if (Test-Path -LiteralPath $Staging) {
            $StagingFull = [IO.Path]::GetFullPath($Staging)
            $RuntimePrefix = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\') + '\'
            if (-not $StagingFull.StartsWith($RuntimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'refusing cleanup outside the CLI runtime staging directory'
            }
            try { [IO.Directory]::Delete((ConvertTo-ExtendedFileSystemPath $StagingFull), $true) }
            catch { Write-Warning "runtime staging retained at ${StagingFull}: $($_.Exception.Message)" }
        }
    } finally {
        $InstallLock.Dispose()
    }
}
