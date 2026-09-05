param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function ConvertTo-ExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith("\\?\", [StringComparison]::Ordinal)) {
        return $Path
    }
    if ($Path.StartsWith("\\", [StringComparison]::Ordinal)) {
        return "\\?\UNC\" + $Path.Substring(2)
    }
    return "\\?\" + $Path
}

$Root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
)
$VolumeRoot = [IO.Path]::GetPathRoot($Root).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
)
if (-not $Root -or [StringComparer]::OrdinalIgnoreCase.Equals($Root, $VolumeRoot)) {
    throw "refusing to prepare an unsafe installation root: $Root"
}
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    return
}

# A pre-existing unrelated directory is not ours to inspect or modify. These
# two files are written by every supported Electron Desktop installation.
$App = Join-Path $Root "OpenProgram.exe"
$Asar = Join-Path $Root "resources\app.asar"
if (-not (Test-Path -LiteralPath $App -PathType Leaf) -or
    -not (Test-Path -LiteralPath $Asar -PathType Leaf)) {
    return
}

$RootPrefix = $Root + [IO.Path]::DirectorySeparatorChar
$Owned = @(
    Get-CimInstance Win32_Process -Property Name, ExecutablePath, ProcessId -OperationTimeoutSec 10 -ErrorAction Stop |
        Where-Object {
            $_.Name -ne "OpenProgram.exe" -and
            $_.ExecutablePath -and
            [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                $RootPrefix,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
)
foreach ($Process in $Owned) {
    Stop-Process -Id $Process.ProcessId -Force -ErrorAction Stop
}

$Deadline = [DateTime]::UtcNow.AddSeconds(5)
do {
    $Remaining = @(
        Get-CimInstance Win32_Process -Property Name, ExecutablePath, ProcessId -OperationTimeoutSec 10 -ErrorAction Stop |
            Where-Object {
                $_.Name -ne "OpenProgram.exe" -and
                $_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    $RootPrefix,
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    if ($Remaining.Count -eq 0) {
        break
    }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $Deadline)
if ($Remaining.Count -gt 0) {
    throw "OpenProgram background processes are still running: $($Remaining.ProcessId -join ', ')"
}

# Older electron-builder uninstallers move every file below a longer
# $PLUGINSDIR\old-install prefix. A source path around 248 characters can cross
# legacy MAX_PATH during upgrade even though the installed file is readable.
# Remove only those old, product-owned deep files through the extended-length
# API; the new runtime uses a short `runtime\py` root and does not recreate the
# legacy layout.
$ExtendedRoot = ConvertTo-ExtendedPath $Root
$ExtendedPrefix = $ExtendedRoot + [IO.Path]::DirectorySeparatorChar
$LegacyLongFiles = [Collections.Generic.List[string]]::new()
$PendingDirectories = [Collections.Generic.Stack[string]]::new()
$PendingDirectories.Push($ExtendedRoot)
while ($PendingDirectories.Count -gt 0) {
    $Directory = $PendingDirectories.Pop()
    if (([IO.File]::GetAttributes($Directory) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        continue
    }
    # Windows PowerShell 5's Get-ChildItem can fail before returning a deep
    # entry. Keep enumeration itself on the extended-length .NET API too.
    foreach ($Entry in [IO.Directory]::EnumerateFileSystemEntries($Directory)) {
        if (-not $Entry.StartsWith($ExtendedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "enumerated path escapes the installation root: $Entry"
        }
        $Attributes = [IO.File]::GetAttributes($Entry)
        if (($Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            continue
        }
        if (($Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
            $PendingDirectories.Push($Entry)
        } elseif (($Root.Length + $Entry.Length - $ExtendedRoot.Length) -ge 248) {
            $LegacyLongFiles.Add($Entry)
        }
    }
}
foreach ($File in $LegacyLongFiles) {
    [IO.File]::Delete($File)
}

Write-Output (
    (
        "prepared existing OpenProgram install: stopped {0} process(es), " +
        "removed {1} legacy long-path file(s)"
    ) -f $Owned.Count, $LegacyLongFiles.Count
)
