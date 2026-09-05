$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Version = if ($env:OPENPROGRAM_VERSION) { $env:OPENPROGRAM_VERSION } else { "0.8.1" }
$Repository = if ($env:OPENPROGRAM_REPOSITORY) { $env:OPENPROGRAM_REPOSITORY } else { "Fzkuji/OpenProgram" }
if ($Version -notmatch "^\d+\.\d+\.\d+$") {
    throw "invalid OpenProgram version: $Version"
}
if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "invalid OpenProgram repository: $Repository"
}

$CheckoutInstaller = Join-Path $PSScriptRoot "release\install-release.ps1"
if (Test-Path -LiteralPath $CheckoutInstaller -PathType Leaf) {
    & $CheckoutInstaller @args
    exit $LASTEXITCODE
}

$Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if (-not $Curl) {
    throw "curl.exe is required to download OpenProgram"
}
$TemporaryDir = Join-Path ([IO.Path]::GetTempPath()) ("openprogram-installer-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TemporaryDir | Out-Null
try {
    $Installer = Join-Path $TemporaryDir "install-release.ps1"
    $Url = "https://raw.githubusercontent.com/$Repository/v$Version/scripts/release/install-release.ps1"
    & $Curl --disable --proto "=https" --tlsv1.2 --fail --silent --show-error `
        --connect-timeout 15 --speed-limit 1024 --speed-time 120 `
        --output $Installer $Url
    if ($LASTEXITCODE -ne 0) {
        throw "failed to download the versioned OpenProgram installer"
    }
    $FirstLine = Get-Content -LiteralPath $Installer -TotalCount 1 -Encoding UTF8
    if ($FirstLine -ne '$ErrorActionPreference = "Stop"') {
        throw "downloaded OpenProgram installer is invalid"
    }
    & $Installer @args
    exit $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $TemporaryDir -Recurse -Force -ErrorAction SilentlyContinue
}
