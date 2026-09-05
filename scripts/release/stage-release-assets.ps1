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

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WebDir = Join-Path $RepoRoot "apps\web"
$SourceDir = Join-Path $WebDir "out"
$NextBuildDir = Join-Path $WebDir ".next"
$TargetDir = Join-Path $RepoRoot "apps\server\openprogram_server\_webui\_frontend"
$LegacyTargetDir = Join-Path $RepoRoot "openprogram\webui\_frontend"
$DocsSourceDir = Join-Path $RepoRoot "docs\_site"
$DocsTargetDir = Join-Path $TargetDir "docs"

$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $Npm) {
    throw "npm is required to stage release Web assets"
}
$Uv = if ($env:OPENPROGRAM_UV_BIN) {
    $env:OPENPROGRAM_UV_BIN
} else {
    (Get-Command uv.exe -ErrorAction SilentlyContinue).Source
}
if (-not $Uv -or -not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
    throw "uv is required to stage release documentation"
}
$Python = if ($env:OPENPROGRAM_BUILD_PYTHON) {
    $env:OPENPROGRAM_BUILD_PYTHON
} else {
    (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    throw "python is required to validate staged release assets"
}

$SavedBuildEnvironment = @{}
foreach ($Name in @("npm_config_workspace", "npm_config_workspaces", "NEXT_IGNORE_INCORRECT_LOCKFILE")) {
    $SavedBuildEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}
Push-Location $RepoRoot
try {
    Remove-Item Env:npm_config_workspace -ErrorAction SilentlyContinue
    Remove-Item Env:npm_config_workspaces -ErrorAction SilentlyContinue
    # Install the complete lockfile just like the POSIX staging path. A
    # workspace-scoped `npm ci` prunes Desktop devDependencies from the shared
    # root node_modules tree, which makes the following electron-builder step
    # disappear during `dist:win`.
    Invoke-Native $Npm ci --ignore-scripts --no-audit --no-fund
    Remove-Item -LiteralPath $SourceDir, $NextBuildDir -Recurse -Force -ErrorAction SilentlyContinue
    $env:NEXT_IGNORE_INCORRECT_LOCKFILE = "1"
    try {
        Invoke-Native $Npm run build --workspace apps/web
        Invoke-Native $Npm run build:standalone --workspace apps/cli
    } finally {
        Remove-Item Env:NEXT_IGNORE_INCORRECT_LOCKFILE -ErrorAction SilentlyContinue
    }

    $DocsPythonRoot = Join-Path ([IO.Path]::GetTempPath()) (
        "openprogram-docs-python-" + [guid]::NewGuid().ToString("N")
    )
    $PreviousPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
    $env:UV_PYTHON_INSTALL_DIR = $DocsPythonRoot
    try {
        Invoke-Native $Uv run --isolated --locked --python 3.12 `
            --with markdown-it-py --with mdit-py-plugins --with pygments `
            python -m scripts.docs_site.build
    } finally {
        if ($null -eq $PreviousPythonInstallDir) {
            Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
        } else {
            $env:UV_PYTHON_INSTALL_DIR = $PreviousPythonInstallDir
        }
        Remove-Item -LiteralPath $DocsPythonRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
} finally {
    Pop-Location
    foreach ($Name in $SavedBuildEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $SavedBuildEnvironment[$Name], "Process")
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $SourceDir "index.html") -PathType Leaf)) {
    throw "Next.js export did not produce $SourceDir\index.html"
}
$TuiBundle = Join-Path $RepoRoot "apps\cli\dist\index-standalone.cjs"
if (-not (Test-Path -LiteralPath $TuiBundle -PathType Leaf)) {
    throw "CLI build did not produce $TuiBundle"
}
if (-not (Test-Path -LiteralPath (Join-Path $DocsSourceDir "index.html") -PathType Leaf)) {
    throw "Docs build did not produce $DocsSourceDir\index.html"
}

Remove-Item -LiteralPath $TargetDir, $LegacyTargetDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
Get-ChildItem -LiteralPath $SourceDir -Force |
    Copy-Item -Destination $TargetDir -Recurse -Force
New-Item -ItemType Directory -Path $DocsTargetDir -Force | Out-Null
Get-ChildItem -LiteralPath $DocsSourceDir -Force |
    Copy-Item -Destination $DocsTargetDir -Recurse -Force

$ChatPath = Join-Path $TargetDir "chat.html"
Invoke-Native $Python (Join-Path $PSScriptRoot "verify-staged-web.py") $ChatPath

Write-Host "Staged release Web and docs assets in $TargetDir"
