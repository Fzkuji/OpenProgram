$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Repository = if ($env:OPENPROGRAM_REPOSITORY) { $env:OPENPROGRAM_REPOSITORY } else { "Fzkuji/OpenProgram" }
$Version = $env:OPENPROGRAM_VERSION
if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "invalid OpenProgram repository: $Repository"
}
$Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if (-not $Curl) {
    throw "curl.exe is required to install OpenProgram"
}
if (-not $Version) {
    $Version = (& $Curl --disable --proto "=https" --tlsv1.2 --location `
        --silent --show-error --fail --output NUL --write-out "%{url_effective}" `
        "https://github.com/$Repository/releases/latest").Trim().Split("/")[-1]
    if ($LASTEXITCODE -ne 0) {
        throw "cannot resolve the latest OpenProgram release"
    }
}
$Version = $Version.TrimStart("v")
if ($Version -notmatch "^\d+\.\d+\.\d+$") {
    throw "invalid OpenProgram release version: $Version"
}

$Temporary = Join-Path ([IO.Path]::GetTempPath()) ("openprogram-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
try {
    $Url = "https://raw.githubusercontent.com/$Repository/v$Version/scripts/install-release.ps1"
    & $Curl --disable --proto "=https" --tlsv1.2 --fail --silent --show-error `
        --connect-timeout 15 --speed-limit 1024 --speed-time 120 `
        --output $Temporary $Url
    if ($LASTEXITCODE -ne 0) {
        throw "OpenProgram $Version has no complete Windows release installer"
    }
    $FirstLine = Get-Content -LiteralPath $Temporary -TotalCount 1 -Encoding UTF8
    if ($FirstLine -ne '$ErrorActionPreference = "Stop"') {
        throw "downloaded OpenProgram installer is invalid"
    }
    $env:OPENPROGRAM_VERSION = $Version
    $env:OPENPROGRAM_REPOSITORY = $Repository
    & $Temporary
} finally {
    Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
}
