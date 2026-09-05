<# Prepare the native OpenSSL build input used by Windows ARM64 Python wheels. #>
param(
    [ValidateSet("x64", "arm64")]
    [string]$Architecture = "arm64"
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Architecture -ne "arm64") { return }
# cryptography's locked release has no Windows ARM64 wheel. Build against
# architecture-matched static OpenSSL; do not substitute x64 Python or omit it.
$VcpkgRoot = $env:VCPKG_INSTALLATION_ROOT
if (-not $VcpkgRoot) {
    $VcpkgCommand = Get-Command vcpkg -ErrorAction Stop
    $VcpkgRoot = Split-Path -Parent $VcpkgCommand.Source
}
$VcpkgRoot = [IO.Path]::GetFullPath($VcpkgRoot)
$VcpkgExe = Join-Path $VcpkgRoot "vcpkg.exe"
if (-not (Test-Path -LiteralPath $VcpkgExe -PathType Leaf)) {
    throw "vcpkg executable not found: $VcpkgExe"
}
& $VcpkgExe install "openssl:arm64-windows-static-md"
if ($LASTEXITCODE -ne 0) { throw "building ARM64 OpenSSL failed" }
$OpenSslRoot = Join-Path $VcpkgRoot "installed\arm64-windows-static-md"
if (-not (Test-Path -LiteralPath (Join-Path $OpenSslRoot "include\openssl\ssl.h"))) {
    throw "ARM64 OpenSSL headers were not installed"
}
$env:OPENSSL_DIR = $OpenSslRoot
$env:OPENSSL_STATIC = "1"
if ($env:GITHUB_ENV) {
    Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8 -Value "OPENSSL_DIR=$OpenSslRoot"
    Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8 -Value "OPENSSL_STATIC=1"
}
Write-Output "ARM64 native Python build inputs prepared: $OpenSslRoot"
