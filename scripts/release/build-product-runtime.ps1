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

function Invoke-NativeOutput {
    param([string]$FilePath)

    $Arguments = $args
    $Output = (& $FilePath @Arguments | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
    return $Output
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ProductConfig = Join-Path $RepoRoot "scripts\release\product-runtime.json"
$RuntimeRoot = if ($env:OPENPROGRAM_RUNTIME_ROOT) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_RUNTIME_ROOT)
} else {
    Join-Path $RepoRoot "apps\desktop\build\runtime"
}
if ((Split-Path -Leaf $RuntimeRoot) -ne "runtime") {
    throw "OPENPROGRAM_RUNTIME_ROOT must end in \runtime: $RuntimeRoot"
}
if ($env:OPENPROGRAM_RUNTIME_ROOT -and (Test-Path -LiteralPath $RuntimeRoot)) {
    throw "custom OPENPROGRAM_RUNTIME_ROOT already exists: $RuntimeRoot"
}

$RepoUv = Join-Path $RepoRoot ".venv\Scripts\uv.exe"
$Uv = if ($env:OPENPROGRAM_UV_BIN) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_UV_BIN)
} elseif (Test-Path -LiteralPath $RepoUv -PathType Leaf) {
    [IO.Path]::GetFullPath($RepoUv)
} else {
    $UvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($UvCommand) { $UvCommand.Source } else { $null }
}
$RepoPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$BuildPython = if ($env:OPENPROGRAM_BUILD_PYTHON) {
    [IO.Path]::GetFullPath($env:OPENPROGRAM_BUILD_PYTHON)
} elseif (Test-Path -LiteralPath $RepoPython -PathType Leaf) {
    [IO.Path]::GetFullPath($RepoPython)
} else {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCommand) { $PythonCommand.Source } else { $null }
}
foreach ($Command in @("npm.cmd", "git.exe")) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "missing build command: $Command"
    }
}
if (-not $Uv -or -not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
    throw "missing build command: uv"
}
if (-not $BuildPython -or -not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    throw "missing build command: python"
}

$Product = Get-Content -LiteralPath $ProductConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$PythonVersion = [string]$Product.python
$UvVersion = [string]$Product.uv
$ActualUvVersion = (Invoke-NativeOutput $Uv --version).Split(" ")[1]
if ($ActualUvVersion -ne $UvVersion) {
    throw "uv version mismatch: expected $UvVersion, got $ActualUvVersion"
}

$PreviousUv = $env:OPENPROGRAM_UV_BIN
$PreviousBuildPython = $env:OPENPROGRAM_BUILD_PYTHON
$env:OPENPROGRAM_UV_BIN = $Uv
$env:OPENPROGRAM_BUILD_PYTHON = $BuildPython
try {
    & (Join-Path $PSScriptRoot "stage-release-assets.ps1")
} finally {
    [Environment]::SetEnvironmentVariable("OPENPROGRAM_UV_BIN", $PreviousUv, "Process")
    [Environment]::SetEnvironmentVariable("OPENPROGRAM_BUILD_PYTHON", $PreviousBuildPython, "Process")
}

Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $RepoRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
foreach ($Directory in @(
    "assets\playwright",
    "assets\gpa",
    "assets\tui",
    "bin",
    ".python-build",
    "wheel"
)) {
    New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot $Directory) -Force | Out-Null
}

Invoke-Native $Uv build --wheel --out-dir (Join-Path $RuntimeRoot "wheel") $RepoRoot
$ManagedPythonRoot = Join-Path $RuntimeRoot ".python-build"
$PreviousPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
$env:UV_PYTHON_INSTALL_DIR = $ManagedPythonRoot
try {
    Invoke-Native $Uv python install $PythonVersion --install-dir $env:UV_PYTHON_INSTALL_DIR --no-bin
    $PythonBin = Invoke-NativeOutput $Uv python find --managed-python $PythonVersion
} finally {
    [Environment]::SetEnvironmentVariable("UV_PYTHON_INSTALL_DIR", $PreviousPythonInstallDir, "Process")
}
$PythonBin = [IO.Path]::GetFullPath($PythonBin)
if (-not (Test-Path -LiteralPath $PythonBin -PathType Leaf)) {
    throw "managed Python is missing: $PythonBin"
}
$PythonHome = [IO.Path]::GetFullPath((Split-Path -Parent $PythonBin)).TrimEnd("\")
foreach ($Alias in Get-ChildItem -LiteralPath $ManagedPythonRoot -Force |
    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
    $AliasTarget = [string]$Alias.Target
    if (-not [IO.Path]::IsPathRooted($AliasTarget)) {
        $AliasTarget = Join-Path $Alias.Parent.FullName $AliasTarget
    }
    $AliasTarget = [IO.Path]::GetFullPath($AliasTarget).TrimEnd("\")
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($AliasTarget, $PythonHome)) {
        throw "managed Python created an unexpected reparse point: $($Alias.FullName) -> $AliasTarget"
    }
    # uv creates a convenience junction such as cpython-3.12-windows-x86_64-none.
    # The manifest records the exact interpreter, so the alias is unnecessary and
    # must not enter the portable ZIP.
    # Windows PowerShell asks for confirmation when Remove-Item targets a
    # directory junction, even under -NonInteractive.  Delete the validated
    # reparse point through .NET so only the alias is removed and the managed
    # Python directory it points at remains intact.
    if ($Alias.PSIsContainer) {
        [IO.Directory]::Delete($Alias.FullName, $false)
    } else {
        [IO.File]::Delete($Alias.FullName)
    }
}
$ShortPythonHome = Join-Path $RuntimeRoot "py"
if (Test-Path -LiteralPath $ShortPythonHome) {
    throw "short Python runtime already exists: $ShortPythonHome"
}
Move-Item -LiteralPath $PythonHome -Destination $ShortPythonHome
Remove-Item -LiteralPath $ManagedPythonRoot -Recurse -Force
$PythonHome = [IO.Path]::GetFullPath($ShortPythonHome)
$PythonBin = Join-Path $PythonHome (Split-Path -Leaf $PythonBin)
if (-not (Test-Path -LiteralPath $PythonBin -PathType Leaf)) {
    throw "relocated managed Python is missing: $PythonBin"
}
$Wheel = Get-ChildItem -LiteralPath (Join-Path $RuntimeRoot "wheel") -Filter "openprogram-*.whl" -File | Select-Object -First 1
if (-not $Wheel) {
    throw "OpenProgram wheel was not built"
}

$Requirements = Join-Path $RuntimeRoot "product-requirements.txt"
Invoke-Native $Uv export --project $RepoRoot --frozen --no-dev `
    --extra all --extra search --no-emit-project --output-file $Requirements
Invoke-Native $Uv pip install --python $PythonBin --strict --break-system-packages `
    --require-hashes --requirements $Requirements
Invoke-Native $Uv pip install --python $PythonBin --strict --break-system-packages `
    --no-deps $Wheel.FullName

$ProgramStaging = Join-Path ([IO.Path]::GetTempPath()) ("openprogram-programs-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $ProgramStaging | Out-Null
try {
    foreach ($ProgramName in @("gui", "research", "wiki")) {
        $Program = $Product.programs.$ProgramName
        $ProgramDir = Join-Path $ProgramStaging $ProgramName
        Invoke-Native git.exe init -q $ProgramDir
        Invoke-Native git.exe -C $ProgramDir remote add origin ([string]$Program.repository)
        Invoke-Native git.exe -C $ProgramDir fetch -q --depth 1 origin ([string]$Program.commit)
        Invoke-Native git.exe -C $ProgramDir checkout -q --detach FETCH_HEAD
        if ($ProgramName -eq "gui") {
            Invoke-Native $Uv pip install --python $PythonBin --strict `
                --break-system-packages --no-deps $ProgramDir
        } elseif ($ProgramName -eq "research") {
            Invoke-Native $Uv pip install --python $PythonBin --strict `
                --break-system-packages "${ProgramDir}[pdf]"
        } else {
            Invoke-Native $Uv pip install --python $PythonBin --strict `
                --break-system-packages $ProgramDir
        }
    }
} finally {
    Remove-Item -LiteralPath $ProgramStaging -Recurse -Force -ErrorAction SilentlyContinue
}

Invoke-Native $Uv pip install --python $PythonBin --strict --break-system-packages huggingface-hub
$PlaywrightPath = Join-Path $RuntimeRoot "assets\playwright"
$PreviousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightPath
try {
    Invoke-Native $PythonBin -m playwright install chromium
} finally {
    [Environment]::SetEnvironmentVariable("PLAYWRIGHT_BROWSERS_PATH", $PreviousBrowserPath, "Process")
}

$Gpa = $Product.assets.gpa_detector
$GpaTarget = Join-Path $RuntimeRoot "assets\gpa"
Invoke-Native $PythonBin -c @'
import pathlib
import shutil
import sys
from huggingface_hub import hf_hub_download

target, repository, revision, filename = sys.argv[1:]
path = hf_hub_download(repository, filename, revision=revision)
destination = pathlib.Path(target) / filename
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(path, destination)
'@ $GpaTarget ([string]$Gpa.repository) ([string]$Gpa.revision) ([string]$Gpa.filename)

Copy-Item -LiteralPath $Uv -Destination (Join-Path $RuntimeRoot "bin\uv.exe") -Force
$Node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
if (-not $Node) {
    throw "missing build command: node.exe"
}
Copy-Item -LiteralPath $Node -Destination (Join-Path $RuntimeRoot "bin\node.exe") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "apps\cli\dist\index-standalone.cjs") `
    -Destination (Join-Path $RuntimeRoot "assets\tui\index.cjs") -Force
Copy-Item -LiteralPath $ProductConfig -Destination (Join-Path $RuntimeRoot "product-runtime.json") -Force
Copy-Item -LiteralPath (Join-Path $RepoRoot "uv.lock") -Destination (Join-Path $RuntimeRoot "product-uv.lock") -Force
$Verifier = Join-Path $RuntimeRoot "bin\verify-product-runtime.py"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-product-runtime.py") -Destination $Verifier -Force

# Product execution always uses `-B`; pre-generated bytecode only increases the
# archive, file count, Defender scan surface, and Windows path depth. Keep one
# canonical source copy and let development environments manage their own
# caches.
Get-ChildItem -LiteralPath $PythonHome -Recurse -Directory -Filter "__pycache__" |
    Sort-Object { $_.FullName.Length } -Descending |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $PythonHome -Recurse -File |
    Where-Object { $_.Extension -in ".pyc", ".pyo" } |
    Remove-Item -Force

$BuildMetadata = Invoke-NativeOutput $PythonBin -I -B `
    (Join-Path $PSScriptRoot "runtime-build-metadata.py") $PythonBin $RuntimeRoot |
    ConvertFrom-Json
$PythonRelative = [string]$BuildMetadata.python_relative
$PackageVersion = [string]$BuildMetadata.openprogram_version
Invoke-Native $PythonBin -I -B $Verifier $RuntimeRoot --write `
    --python-relative $PythonRelative --openprogram-version $PackageVersion --uv-version $UvVersion

Write-Host "Prepared complete OpenProgram runtime $PackageVersion at $RuntimeRoot"
