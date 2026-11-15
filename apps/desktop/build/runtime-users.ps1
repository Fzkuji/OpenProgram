# The App's entry in the version store's list of users
# (openprogram/operations/self_update/versions/users.py, the same file format).
#
#   -Action add     the installer lists this App (customInstall) in a store that
#                   already holds a version; otherwise the worker does it on start
#   -Action remove  the uninstaller removes it; exit 10 and the store's size in GB
#                   when no install uses the store any more, otherwise exit 0
#   -Action delete  the uninstaller deletes the store, only when the owner ticked it
#
# The store is <state>\runtime: downloaded runtime versions that can be fetched
# again. Conversations, settings and credentials live beside it in <state> and
# are never touched here.
param(
    [Parameter(Mandatory = $true)][ValidateSet("add", "remove", "delete")][string]$Action,
    [string]$InstallRoot = "",
    [string]$StateDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $StateDir) { $StateDir = Join-Path $env:USERPROFILE ".openprogram" }
$Store = Join-Path ([IO.Path]::GetFullPath($StateDir)) "runtime"
$File = Join-Path $Store "users.json"

function Get-InstallId([string]$Kind, [string]$Folder) {
    $Normal = [IO.Path]::GetFullPath($Folder).TrimEnd("\").ToLowerInvariant()
    $Bytes = [Text.Encoding]::UTF8.GetBytes("$Kind`0$Normal")
    $Hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
    return (-join ($Hash | ForEach-Object { $_.ToString("x2") })).Substring(0, 16)
}

function Read-Users {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return @() }
    try {
        $Loaded = Get-Content -LiteralPath $File -Raw -Encoding UTF8 | ConvertFrom-Json
        return @($Loaded.users | Where-Object { $_ -and $_.install_id })
    } catch {
        return @()
    }
}

function Write-Users($Rows) {
    $Temporary = "$File.$PID.tmp"
    $Body = [ordered]@{ schema = 1; users = @($Rows) } | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($Temporary, $Body, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $Temporary -Destination $File -Force
}

if ($Action -eq "delete") {
    if (Test-Path -LiteralPath $Store) { Remove-Item -LiteralPath $Store -Recurse -Force -ErrorAction SilentlyContinue }
    exit 0
}

$Id = Get-InstallId "app" $InstallRoot
if ($Action -eq "add") {
    # Only a store that already holds a version. A fresh install's first launch
    # unpacks the seed into an empty <state>\runtime and refuses a folder with
    # anything else in it (apps/desktop/shell/seed.js assertSeedable); the
    # worker lists this App itself once it starts from the store.
    if (-not (Test-Path -LiteralPath (Join-Path $Store "current") -PathType Leaf)) { exit 0 }
    $Rows = @(Read-Users)
    if (-not ($Rows | Where-Object { $_.install_id -eq $Id })) {
        $Rows += [ordered]@{ install_id = $Id; kind = "app"; path = $InstallRoot }
        Write-Users $Rows
    }
    exit 0
}

# remove
if (-not (Test-Path -LiteralPath $Store)) { exit 0 }
$Rows = @(Read-Users)
$Left = @($Rows | Where-Object { $_.install_id -ne $Id })
if ($Left.Count -ne $Rows.Count) { Write-Users $Left }
if ($Left.Count -gt 0) { exit 0 }
$Bytes = (Get-ChildItem -LiteralPath $Store -Recurse -File -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum
if (-not $Bytes) { $Bytes = 0 }
# No line break: the uninstaller puts this text into its question as it is.
[Console]::Out.Write([Math]::Round($Bytes / 1GB, 1).ToString([Globalization.CultureInfo]::InvariantCulture))
exit 10
