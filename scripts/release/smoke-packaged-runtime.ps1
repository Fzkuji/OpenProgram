param(
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory
)

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

$ArtifactDirectory = (Resolve-Path $ArtifactDirectory).Path
$Unpacked = Get-ChildItem -LiteralPath $ArtifactDirectory -Directory -Recurse |
    Where-Object { $_.Name -match '^win(?:-arm64)?-unpacked$' } |
    Select-Object -First 1
if (-not $Unpacked) {
    throw "win-unpacked Desktop directory not found: $ArtifactDirectory"
}
$Resources = Join-Path $Unpacked.FullName "resources"
$Runtime = Join-Path $Resources "runtime"
$ManifestPath = Join-Path $Runtime "runtime-manifest.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "packaged runtime manifest not found: $ManifestPath"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Python = [IO.Path]::GetFullPath((Join-Path $Runtime ([string]$Manifest.python)))
$RuntimePrefix = [IO.Path]::GetFullPath($Runtime).TrimEnd("\") + "\"
if (-not $Python.StartsWith($RuntimePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "managed Python escapes or is missing from the packaged runtime"
}
Invoke-Native $Python -I (Join-Path $Runtime "bin\verify-product-runtime.py") $Runtime
$Node = Join-Path $Runtime "bin\node.exe"
$Tui = Join-Path $Runtime "assets\tui\index.cjs"
Invoke-Native $Node $Tui --probe

# Direct invocation of the embedded interpreter is used by diagnostics and
# must still take the managed-release updater path without Desktop's env var.
$SavedImmutableMarker = $env:OPENPROGRAM_IMMUTABLE_RUNTIME
try {
    Remove-Item Env:OPENPROGRAM_IMMUTABLE_RUNTIME -ErrorAction SilentlyContinue
    Invoke-Native $Python -I -B -c "from openprogram.updater.detect import InstallMethod, detect_install_method; assert detect_install_method() is InstallMethod.MANAGED_RELEASE"
} finally {
    if ($null -eq $SavedImmutableMarker) {
        Remove-Item Env:OPENPROGRAM_IMMUTABLE_RUNTIME -ErrorAction SilentlyContinue
    } else {
        $env:OPENPROGRAM_IMMUTABLE_RUNTIME = $SavedImmutableMarker
    }
}

$ProbeRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "openprogram-packaged-smoke-" + [guid]::NewGuid().ToString("N")
)
$ProbeHome = Join-Path $ProbeRoot "home"
New-Item -ItemType Directory -Path $ProbeHome -Force | Out-Null
$Port = Get-Random -Minimum 19000 -Maximum 19999
$SavedEnvironment = @{
    HOME = $env:HOME
    USERPROFILE = $env:USERPROFILE
    OPENPROGRAM_WEB_PORT = $env:OPENPROGRAM_WEB_PORT
    OPENPROGRAM_IMMUTABLE_RUNTIME = $env:OPENPROGRAM_IMMUTABLE_RUNTIME
    PLAYWRIGHT_BROWSERS_PATH = $env:PLAYWRIGHT_BROWSERS_PATH
    GPA_MODEL_PATH = $env:GPA_MODEL_PATH
}
try {
    $env:HOME = $ProbeHome
    $env:USERPROFILE = $ProbeHome
    $env:OPENPROGRAM_WEB_PORT = [string]$Port
    $env:OPENPROGRAM_IMMUTABLE_RUNTIME = "1"
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Runtime "assets\playwright"
    $env:GPA_MODEL_PATH = Join-Path $Runtime "assets\gpa\model.pt"
    Invoke-Native $Python -I -B -m openprogram worker start

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 120; $Attempt++) {
        try {
            $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 1
            if ($Health.status -eq "ok") {
                $Ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $Ready) {
        throw "packaged Windows worker health probe did not become ready"
    }
    $Chat = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/chat" -TimeoutSec 5 -UseBasicParsing
    if ($Chat.StatusCode -ne 200) {
        throw "packaged Windows chat route returned $($Chat.StatusCode)"
    }

    $ProgramLog = Join-Path $ProbeRoot "program-install.log"
    & $Python -I -B -m openprogram programs install research *> $ProgramLog
    if ($LASTEXITCODE -eq 0) {
        throw "packaged runtime unexpectedly allowed Program installation"
    }
    if (-not (Select-String -LiteralPath $ProgramLog -Pattern "disabled in the packaged desktop runtime" -Quiet)) {
        throw "packaged runtime did not explain immutable Program installation"
    }
    Write-Output "packaged runtime smoke passed for Windows"
} finally {
    & $Python -I -B -m openprogram worker stop *> $null
    foreach ($Name in $SavedEnvironment.Keys) {
        $Value = $SavedEnvironment[$Name]
        if ($null -eq $Value) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        }
    }
    Remove-Item -LiteralPath $ProbeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
