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
    Get-CimInstance Win32_Process -ErrorAction Stop |
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
        Get-CimInstance Win32_Process -ErrorAction Stop |
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
$LegacyLongFiles = @(
    Get-ChildItem -LiteralPath $Root -Recurse -Force -File |
        Where-Object { $_.FullName.Length -ge 248 } |
        Sort-Object { $_.FullName.Length } -Descending
)
foreach ($File in $LegacyLongFiles) {
    [IO.File]::Delete((ConvertTo-ExtendedPath $File.FullName))
}

Write-Output (
    (
        "prepared existing OpenProgram install: stopped {0} process(es), " +
        "removed {1} legacy long-path file(s)"
    ) -f $Owned.Count, $LegacyLongFiles.Count
)
