$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Version = $env:OPENPROGRAM_VERSION
$Repository = if ($env:OPENPROGRAM_REPOSITORY) { $env:OPENPROGRAM_REPOSITORY } else { "Fzkuji/OpenProgram" }
if ($Version -and $Version -notmatch "^\d+\.\d+\.\d+$") {
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

if (-not $Version) {
    throw "No OpenProgram version was given. Set OPENPROGRAM_VERSION to a release number such as 1.2.3 and run this again, or use the short install command, which finds the latest release: irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex"
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
        --connect-timeout 15 --speed-limit 1024 --speed-time 120 --retry 3 `
        --output $Installer $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download the OpenProgram $Version installer. Check that the release exists and this computer is online. If it reaches the internet through a proxy, set HTTPS_PROXY (and NO_PROXY if some hosts skip the proxy), then run this again."
    }
    $FirstLine = Get-Content -LiteralPath $Installer -TotalCount 1 -Encoding UTF8
    if ($FirstLine -ne '$ErrorActionPreference = "Stop"') {
        throw "downloaded OpenProgram installer is invalid"
    }
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $Installer @args
    exit $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $TemporaryDir -Recurse -Force -ErrorAction SilentlyContinue
}
