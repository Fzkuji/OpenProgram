# The one recipe for the dependency layers: Python packages, Playwright and Git.
#
#   build-base.ps1 -Dest DIR [-CloneFrom BASE]
#     A complete base for a version store. Layers whose pins are unchanged are
#     copied from BASE; only a changed Python pin runs `uv python install`.
#   build-base.ps1 -EmbeddedRuntime DIR -PythonBin BIN
#     Only the package, browser and Git layers, into a complete runtime.
#     build-product-runtime.ps1 calls this.
param(
    [string]$Dest = "",
    [string]$CloneFrom = "",
    [string]$EmbeddedRuntime = "",
    [string]$PythonBin = ""
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

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Uv = if ($env:OPENPROGRAM_UV_BIN) { $env:OPENPROGRAM_UV_BIN } else { (Get-Command uv.exe -ErrorAction SilentlyContinue).Source }
if (-not $Uv -or -not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
    throw "missing build command: uv"
}

function Install-Packages {
    param([string]$Verb, [string]$Python, [string]$Requirements)

    Invoke-Native $Uv export --project $RepoRoot --frozen --no-dev `
        --extra all --extra search --no-emit-project --output-file $Requirements
    # Wheels only, picked for the target rather than this runner; runtime-wheels.py
    # names the few packages this target builds from source (sourceBuilds).
    $TargetArgs = @(& $Python -I (Join-Path $RepoRoot "scripts\release\runtime-wheels.py") uv-args)
    if ($LASTEXITCODE -ne 0 -or $TargetArgs.Count -eq 0) { throw "no wheel target for this machine" }
    # `uv pip sync` takes the file as an argument and refuses --requirements;
    # `uv pip install` needs --requirements, since its arguments are packages.
    $Source = @(if ($Verb -eq "sync") { $Requirements } else { "--requirements"; $Requirements })
    Invoke-Native $Uv pip $Verb --python $Python --strict --break-system-packages `
        --require-hashes @TargetArgs @Source
    Invoke-Native $Python -I (Join-Path $RepoRoot "scripts\release\trim-runtime.py") packages $Python
}

function Install-Browsers {
    param([string]$Python, [string]$Browsers)

    $PreviousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
    $env:PLAYWRIGHT_BROWSERS_PATH = $Browsers
    try {
        # Every launch asks for the full browser first; the headless shell would ship unused.
        Invoke-Native $Python -m playwright install chromium --no-shell
    } finally {
        [Environment]::SetEnvironmentVariable("PLAYWRIGHT_BROWSERS_PATH", $PreviousBrowserPath, "Process")
    }
}

$ProductConfig = Join-Path $RepoRoot "scripts\release\product-runtime.json"
$Product = Get-Content -LiteralPath $ProductConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$GitPin = [string]$Product.git

# A pinned Git, so a new machine needs none installed, for the architecture of
# the Python it ships beside rather than this machine's: an x64 base built on
# Windows on Arm runs on x64 PCs, where an Arm Git cannot start.
function Install-Git {
    param([string]$Target, [string]$Python)

    $PackagedTarget = (& $Python -I (Join-Path $RepoRoot "scripts\release\runtime-wheels.py") target)
    if ($LASTEXITCODE -ne 0) { throw "could not read the architecture of $Python" }
    $GitPlatform = if ($PackagedTarget -eq "windows-arm64") { "windows-arm64" } else { "windows-x64" }
    $GitAsset = "$($Product.gitAsset)-$GitPlatform.tar.gz"
    $GitArchive = "$Target.archive.tar.gz"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/desktop/dugite-native/releases/download/$GitPin/$GitAsset" -OutFile $GitArchive
    $GitSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $GitArchive).Hash.ToLowerInvariant()
    if ($GitSha -ne $Product.gitSha256.$GitPlatform) { throw "Git archive checksum mismatch: $GitAsset" }
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    # Windows' own tar: under Git Bash a bare tar.exe is GNU tar, which reads "C:" as a remote host.
    & (Join-Path $env:SystemRoot "System32\tar.exe") -xzf $GitArchive -C $Target
    if ($LASTEXITCODE -ne 0) { throw "could not unpack $GitAsset" }
    Remove-Item -LiteralPath $GitArchive -Force
}

if ($EmbeddedRuntime) {
    if (-not $PythonBin) { throw "-EmbeddedRuntime needs -PythonBin" }
    Install-Packages install $PythonBin (Join-Path $EmbeddedRuntime "product-requirements.txt")
    Install-Browsers $PythonBin (Join-Path $EmbeddedRuntime "assets\playwright")
    Install-Git (Join-Path $EmbeddedRuntime "git") $PythonBin
    exit 0
}

if (-not $Dest) { throw "give -Dest or -EmbeddedRuntime" }
if (Test-Path -LiteralPath $Dest) { throw "base staging already exists: $Dest" }

$PythonPin = [string]$Product.python
$UvPin = [string]$Product.uv
if ((& $Uv --version).Split(" ")[1] -ne $UvPin) { throw "uv version mismatch: expected $UvPin" }
$LockText = Get-Content -LiteralPath (Join-Path $RepoRoot "uv.lock") -Raw -Encoding UTF8
if ($LockText -notmatch '(?m)^name = "playwright"\r?\nversion = "([^"]+)"') { throw "uv.lock has no playwright" }
$PlaywrightPin = $Matches[1]
$Node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
if (-not $Node) { throw "missing build command: node.exe" }
$NodePin = (& $Node --version)
$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $Npm) { throw "missing build command: npm" }
$NpmPin = (& $Npm --version)

$Previous = $null
if ($CloneFrom -and (Test-Path -LiteralPath (Join-Path $CloneFrom "base.json"))) {
    $Previous = Get-Content -LiteralPath (Join-Path $CloneFrom "base.json") -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Test-Pin {
    param([string]$Name, [string]$Value)
    return ($null -ne $Previous) -and ($Previous.$Name -eq $Value)
}

New-Item -ItemType Directory -Path (Join-Path $Dest "assets"), (Join-Path $Dest "bin") -Force | Out-Null
$PythonHome = Join-Path $Dest "python"
if (Test-Pin python $PythonPin) {
    Copy-Item -LiteralPath (Join-Path $CloneFrom "python") -Destination $PythonHome -Recurse
    $Verb = "sync"
} else {
    $InstallDir = Join-Path $Dest ".python-install"
    $PreviousInstallDir = $env:UV_PYTHON_INSTALL_DIR
    $env:UV_PYTHON_INSTALL_DIR = $InstallDir
    try {
        Invoke-Native $Uv python install $PythonPin --install-dir $InstallDir --no-bin
        $Found = & $Uv python find --managed-python $PythonPin
    } finally {
        [Environment]::SetEnvironmentVariable("UV_PYTHON_INSTALL_DIR", $PreviousInstallDir, "Process")
    }
    Move-Item -LiteralPath (Split-Path -Parent ([IO.Path]::GetFullPath($Found))) -Destination $PythonHome
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    $Verb = "install"
}
$BasePython = Join-Path $PythonHome "python.exe"
if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) { throw "base Python is missing: $BasePython" }

Install-Packages $Verb $BasePython (Join-Path $Dest "product-requirements.txt")

# The docs build needs one package the product does not; same lock, same hashes.
$DocsAll = Join-Path $Dest ".docs-all.txt"
$DocsOnly = Join-Path $Dest "docs-requirements.txt"
Invoke-Native $Uv export --project $RepoRoot --frozen --no-dev --extra dev --no-emit-project --output-file $DocsAll
$Keep = $false
$Lines = foreach ($Line in Get-Content -LiteralPath $DocsAll -Encoding UTF8) {
    if ($Line -match '^mdit-py-plugins==') { $Keep = $true; $Line; continue }
    if ($Keep -and $Line -match '^\s+--hash') { $Line; continue }
    $Keep = $false
}
Set-Content -LiteralPath $DocsOnly -Value $Lines -Encoding ascii
Remove-Item -LiteralPath $DocsAll -Force
Invoke-Native $Uv pip install --python $BasePython --break-system-packages --no-deps --require-hashes --requirements $DocsOnly

$Browsers = Join-Path $Dest "assets\playwright"
if (Test-Pin playwright $PlaywrightPin) {
    Copy-Item -LiteralPath (Join-Path $CloneFrom "assets\playwright") -Destination $Browsers -Recurse
} else {
    Install-Browsers $BasePython $Browsers
}
Copy-Item -LiteralPath $(if (Test-Pin node $NodePin) { Join-Path $CloneFrom "bin\node.exe" } else { $Node }) -Destination (Join-Path $Dest "bin\node.exe")
# npm sits beside node.exe, where npm.cmd looks for it.
$NpmSource = if (Test-Pin npm $NpmPin) { Join-Path $CloneFrom "bin\node_modules\npm" } else { Join-Path (Split-Path -Parent $Node) "node_modules\npm" }
New-Item -ItemType Directory -Path (Join-Path $Dest "bin\node_modules") -Force | Out-Null
Copy-Item -LiteralPath $NpmSource -Destination (Join-Path $Dest "bin\node_modules\npm") -Recurse
foreach ($Shim in @("npm", "npm.cmd", "npm.ps1")) {
    $Source = Join-Path (Split-Path -Parent $Node) $Shim
    if (Test-Path -LiteralPath $Source) { Copy-Item -LiteralPath $Source -Destination (Join-Path $Dest "bin\$Shim") }
}
Copy-Item -LiteralPath $(if (Test-Pin uv $UvPin) { Join-Path $CloneFrom "bin\uv.exe" } else { $Uv }) -Destination (Join-Path $Dest "bin\uv.exe")
if (Test-Pin git $GitPin) {
    Copy-Item -LiteralPath (Join-Path $CloneFrom "git") -Destination (Join-Path $Dest "git") -Recurse
} else {
    Install-Git (Join-Path $Dest "git") $BasePython
}
# The Git above is dugite-native. When its archive carries a bash, a machine
# without Git for Windows runs commands in it rather than in PowerShell; the
# command shell probe reads this field (relative to <base>\git, or null).
$GitBash = $null
foreach ($Candidate in @("bin\bash.exe", "usr\bin\bash.exe")) {
    if (Test-Path -LiteralPath (Join-Path (Join-Path $Dest "git") $Candidate) -PathType Leaf) { $GitBash = $Candidate; break }
}
Copy-Item -LiteralPath $ProductConfig -Destination (Join-Path $Dest "product-runtime.json")
Copy-Item -LiteralPath (Join-Path $RepoRoot "uv.lock") -Destination (Join-Path $Dest "product-uv.lock")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-product-runtime.py") -Destination (Join-Path $Dest "bin\verify-product-runtime.py")

@{ schema = "1"; python = $PythonPin; uv = $UvPin; playwright = $PlaywrightPin; node = $NodePin; npm = $NpmPin; git = $GitPin; gitBash = $GitBash } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Dest "base.json") -Encoding ascii
Write-Host "Prepared base at $Dest"
