<#
=============================================================================
 OpenProgram - legacy source-development installer (Windows / PowerShell)
-----------------------------------------------------------------------------
 Windows release installation is handled by the root `install.ps1` bootstrap
 and packaged release installer. This legacy script remains the editable
 source-development workflow.
 Brings up the OpenProgram HOST so `openprogram` just works:
   1. Verify (or winget-install) the system toolchain: Python 3.11+, Node 22.12+, git
   2. Python env (creates/reuses .\.venv unless -Python is supplied)
   3. OpenProgram (editable) + its deps
   4. Web + terminal UI: builds apps/web and the full Ink TUI in apps/cli
   5. Default extras [all]: browser tool (Playwright + Chromium) + channels

 Programs are not bundled. Install any program package with
 openprogram programs install <directory | git URL | owner/repo>,
 or pass -Programs <source> (comma-separated or repeated) to install
 them right after the main install.

 -Minimal skips the Web build and optional extras - a bare host for servers; everything it
 skipped can be added later (pip install -e .[all], npm ci --include-workspace-root,
 npm run build --workspace apps/web, npm run build --workspace apps/cli).

 Re-runnable: every step is idempotent.

 Run it straight off the web - no clone needed:
   iwr -useb https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/scripts/install.ps1 | iex
 It clones OpenProgram to $HOME\OpenProgram (override with -Target DIR), then
 hands off to the cloned copy.

 Usage:
   .\scripts\install.ps1                  # full install (everything above)
   .\scripts\install.ps1 -Minimal         # bare host only
   .\scripts\install.ps1 -Programs owner/repo  # + install program packages after the main install
   .\scripts\install.ps1 -Target DIR      # where to clone when run off the web (default $HOME\OpenProgram)
   .\scripts\install.ps1 -Yes             # skip every prompt, use defaults

 AI-agent / non-interactive: pass -Yes (or set env CI / DEBIAN_FRONTEND=noninteractive
 / OPENPROGRAM_INSTALL_YES) to take every default with no prompts. Read-Host has
 no timeout, so on Windows an agent must use one of these to avoid a hang.
=============================================================================
#>
[CmdletBinding()]
param(
  [string]$Python = "",
  [string[]]$Programs = @(),       # program sources to install: directory, git URL or owner/repo
  [switch]$Minimal,               # bare host: skip web build / programs / default extras
  [string]$Target = "",           # clone destination when run off the web (default $HOME\OpenProgram)
  [switch]$Yes,                   # skip every prompt, use defaults
  [switch]$Bootstrapped           # internal: child skips re-bootstrapping
)
# NOTE: 'Continue', not 'Stop'. Under 'Stop', Windows PowerShell 5.1 turns a
# native exe's stderr line (e.g. pip's harmless "Scripts not on PATH" warning)
# into a terminating NativeCommandError. We gate on $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"

function Step($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  ok $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  !! $m" -ForegroundColor Yellow }
# Off the web this runs inside the person's own window, where `exit` would close it.
function Die($m){
  if ($ScriptPath) { Write-Host "ERROR $m" -ForegroundColor Red; exit 1 }
  throw "ERROR $m"
}

function Invoke-CheckedNative {
  param(
    [Parameter(Mandatory=$true)][string]$Description,
    [Parameter(Mandatory=$true)][string]$Command,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs
  )
  & $Command @CommandArgs
  $code = $LASTEXITCODE
  if ($code -ne 0) { Die "$Description failed (exit $code)" }
}

function Have($name){ return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# Non-interactive signals - treated exactly like -Yes (defaults, no prompts).
# CI / DEBIAN_FRONTEND=noninteractive are ecosystem conventions;
# OPENPROGRAM_INSTALL_YES is our own escape hatch.
# NOTE: Read-Host has no timeout, so (unlike install.sh) the ps1 prompts can't
# self-default on expiry - an agent must pass -Yes or one of these env vars.
function Test-NonInteractive {
  if ($Yes) { return $true }
  if ($env:CI) { return $true }
  if ($env:DEBIAN_FRONTEND -eq 'noninteractive') { return $true }
  if ($env:OPENPROGRAM_INSTALL_YES) { return $true }
  return $false
}
# A program winget just installed is in the registry's PATH, not this session's.
function Update-SessionPath {
  $separator = [IO.Path]::PathSeparator
  $merged = New-Object System.Collections.Generic.List[string]
  foreach ($value in @($env:Path, [Environment]::GetEnvironmentVariable("Path", "User"), [Environment]::GetEnvironmentVariable("Path", "Machine"))) {
    foreach ($entry in @("$value" -split [Regex]::Escape([string]$separator))) {
      if ($entry -and -not ($merged | Where-Object { $_ -ieq $entry })) { $merged.Add($entry) }
    }
  }
  $env:Path = $merged -join $separator
}
function Winget-Install($id){
  if (Have winget) {
    winget install --silent --accept-package-agreements --accept-source-agreements -e --id $id
    if ($LASTEXITCODE -ne 0) { Die "winget could not install $id (exit $LASTEXITCODE)" }
    Update-SessionPath
  }
  else { Warn "winget not available - install $id manually" }
}

# When run via `iwr | iex` there is no script file, so $MyInvocation...Path is
# empty. A real checkout is detected by pyproject.toml next to us, not the path.
$RepoUrl = "https://github.com/Fzkuji/OpenProgram.git"
$ScriptPath = $MyInvocation.MyCommand.Path
$HostRoot = $null
if ($ScriptPath) {
  $ScriptDir = Split-Path -Parent $ScriptPath
  $HostRoot  = (Resolve-Path "$ScriptDir\..").Path
}
function Test-OpenProgramCheckout($dir){
  return ($dir -and (Test-Path "$dir\pyproject.toml") -and (Test-Path "$dir\scripts\install.ps1") `
          -and (Select-String -Path "$dir\pyproject.toml" -Pattern '^name = "openprogram"' -Quiet))
}

# ---- 0. self-bootstrap (clone + re-invoke when not inside a checkout) --------
if (-not $Bootstrapped -and -not (Test-OpenProgramCheckout $HostRoot)) {
  if (-not (Have git)) { Step "installing git"; Winget-Install "Git.Git" }
  if (-not (Have git)) { Die "git is required to install off the web - install Git for Windows from https://git-scm.com/download/win, or clone the repo and run scripts\install.ps1 from inside it." }
  $dest = if ($Target) { $Target } else { Join-Path $HOME "OpenProgram" }
  if (-not $Target -and -not (Test-NonInteractive)) {
    $reply = Read-Host "Clone OpenProgram to [$dest]"
    if ($reply) { $dest = $reply }
  }
  if (Test-Path $dest) {
    if (Test-OpenProgramCheckout $dest) {
      Step "reusing existing OpenProgram checkout at $dest"
      Push-Location $dest
      try {
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) { Die "git pull --ff-only failed in $dest" }
      }
      finally { Pop-Location }
    } else {
      Die "target exists but is not an OpenProgram checkout: $dest (remove it or pass -Target DIR)"
    }
  } else {
    Step "cloning OpenProgram into $dest"
    git clone --depth 1 $RepoUrl $dest
    if ($LASTEXITCODE -ne 0) { Die "git clone failed: $RepoUrl" }
  }
  $child = Join-Path $dest "scripts\install.ps1"
  if (-not (Test-Path $child)) { Die "cloned repo has no scripts\install.ps1 - unexpected layout at $dest" }
  Step "handing off to the cloned installer: $child"
  $forward = @("-Bootstrapped")
  if ($Minimal)      { $forward += "-Minimal" }
  if ($Yes)          { $forward += "-Yes" }
  if ($Python)       { $forward += @("-Python", $Python) }
  if ($Programs)     { $forward += @("-Programs", ($Programs -join ',')) }
  & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $child @forward
  $childExit = $LASTEXITCODE
  if ($ScriptPath) { exit $childExit }
  $global:LASTEXITCODE = $childExit
  if ($childExit -ne 0) { Die "the cloned installer stopped (exit $childExit); the message above says why" }
  return
}
Step "checking system toolchain (python3.11+, node22.12+, git)"
if (-not (Have git))  { Step "installing git";    Winget-Install "Git.Git" }
if (Have git)  { Ok "git: $(git --version)" } else { Die "git is still missing - install Git for Windows from https://git-scm.com/download/win, then run this installer again" }
if (-not $Minimal -and -not (Have node)) { Step "installing Node.js"; Winget-Install "OpenJS.NodeJS.LTS" }
if (Have node) {
  $nodeVersion = (node -p "process.versions.node") 2>$null
  $nodeMajor = [int]((node -p "process.versions.node.split('.')[0]") 2>$null)
  $nodeMinor = [int]((node -p "process.versions.node.split('.')[1]") 2>$null)
  if ($nodeMajor -eq 22 -and $nodeMinor -ge 12) { Ok "node: $(node --version)" }
  elseif ($nodeMajor -gt 22) { Warn "node $(node --version) is newer than the validated Node 22 LTS; continuing with workspace-scoped installs" }
  else { Die "node $nodeVersion < 22.12 - upgrade to Node 22.12+ (Node 22 LTS recommended)" }
} elseif (-not $Minimal) { Die "node is still missing - install Node.js 22 from https://nodejs.org, then run this installer again" }
else { Warn "node not found (allowed by -Minimal)" }

# ---- 2. Python env ----------------------------------------------------------
function Test-IsolatedCheckoutVenv {
  $python = "$HostRoot\.venv\Scripts\python.exe"
  $config = "$HostRoot\.venv\pyvenv.cfg"
  if (-not (Test-Path $python) -or -not (Test-Path $config)) { return $false }
  if (Select-String -LiteralPath $config -Pattern '^include-system-site-packages\s*=\s*true\s*$' -Quiet) {
    return $false
  }
  & $python -c "import sys; assert sys.version_info[:2] >= (3,11)" *> $null
  return ($LASTEXITCODE -eq 0)
}

function Get-PythonCandidates {
  $candidates = New-Object System.Collections.Generic.List[object]
  if ($env:CONDA_PREFIX -and (Test-Path "$env:CONDA_PREFIX\python.exe")) {
    $candidates.Add([pscustomobject]@{ Command="$env:CONDA_PREFIX\python.exe"; Prefix=@() })
  }
  $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($launcher) {
    $candidates.Add([pscustomobject]@{ Command=$launcher.Source; Prefix=@("-3") })
  }
  $pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($pathPython) {
    $candidates.Add([pscustomobject]@{ Command=$pathPython.Source; Prefix=@() })
  }
  foreach ($common in @(
    "$HOME\miniconda3\python.exe",
    "$HOME\anaconda3\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
  )) {
    if (Test-Path $common) {
      $candidates.Add([pscustomobject]@{ Command=$common; Prefix=@() })
    }
  }
  return $candidates
}

function Test-PythonCandidate($candidate) {
  if ($candidate.Command.StartsWith("$HostRoot\.venv\", [StringComparison]::OrdinalIgnoreCase)) {
    return $false
  }
  & $candidate.Command @($candidate.Prefix) -c "import sys; assert sys.version_info[:2] >= (3,11)" *> $null
  return ($LASTEXITCODE -eq 0)
}

function New-CheckoutVenv([switch]$Clear) {
  $candidates = @(Get-PythonCandidates)
  if (-not ($candidates | Where-Object { Test-PythonCandidate $_ })) {
    Step "installing Python 3.12"
    Winget-Install "Python.Python.3.12"
    $candidates = @(Get-PythonCandidates)
  }
  foreach ($candidate in $candidates) {
    if (-not (Test-PythonCandidate $candidate)) { continue }
    $venvArgs = @($candidate.Prefix) + @("-m", "venv")
    if ($Clear) { $venvArgs += "--clear" }
    $venvArgs += "$HostRoot\.venv"
    & $candidate.Command @venvArgs
    if ($LASTEXITCODE -eq 0 -and (Test-IsolatedCheckoutVenv)) { return }
  }
  Die "no working Python 3.11+ found - install Python 3.12 from https://www.python.org/downloads/windows/, then run this installer again"
}

function Resolve-Python {
  if ($Python) { return $Python }
  if (Test-IsolatedCheckoutVenv) { return "$HostRoot\.venv\Scripts\python.exe" }
  $clear = Test-Path "$HostRoot\.venv"
  if ($clear) { Warn "existing .venv is invalid or exposes system site-packages; rebuilding it" }
  Step "creating virtualenv at $HostRoot\.venv"
  New-CheckoutVenv -Clear:$clear
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$HostRoot\.venv\Scripts\python.exe")) {
    Die "could not create virtualenv at $HostRoot\.venv"
  }
  return "$HostRoot\.venv\Scripts\python.exe"
}
$PY = Resolve-Python
& $PY -c "import sys; assert sys.version_info[:2] >= (3,11), sys.version" 2>$null
if ($LASTEXITCODE -ne 0) { Die "Python 3.11+ required (got: $(& $PY --version 2>&1))" }
Ok "python: $(& $PY --version 2>&1)  [$PY]"
function Pip {
  & $PY -m pip @args 2>&1 | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { Die "pip $($args -join ' ') failed (exit $LASTEXITCODE)" }
}
Pip install --quiet --upgrade pip

# ---- 3. OpenProgram (editable) ----------------------------------------------
Step "installing OpenProgram (editable) from $HostRoot"
Pip install -e "$HostRoot"
Ok "openprogram installed"

function Install-CliLauncher {
  $binDir = Join-Path $env:LOCALAPPDATA "OpenProgram\bin"
  New-Item -ItemType Directory -Force -Path $binDir | Out-Null
  $launcher = Join-Path $binDir "openprogram.cmd"
  $quotedPython = $PY.Replace('%', '%%')
  # Parse embedded Unicode paths as UTF-8 even on legacy console code pages,
  # then restore the caller's code page without losing Python's exit status.
  # Python runs in UTF-8 mode, as the released launchers start it, so text
  # with no encoding named reads the same on every Windows language.
  $content = @"
@echo off
setlocal DisableDelayedExpansion
set "_OPENPROGRAM_CODEPAGE=%_OPENPROGRAM_CALLER_CODEPAGE%"
set "_OPENPROGRAM_CALLER_CODEPAGE="
if not defined _OPENPROGRAM_CODEPAGE for /f "tokens=2 delims=:." %%P in ('chcp') do set "_OPENPROGRAM_CODEPAGE=%%P"
chcp 65001 >nul
"$quotedPython" -X utf8 -m openprogram %*
set "_OPENPROGRAM_EXIT=%ERRORLEVEL%"
chcp %_OPENPROGRAM_CODEPAGE% >nul
exit /b %_OPENPROGRAM_EXIT%
"@
  [System.IO.File]::WriteAllText($launcher, $content, [System.Text.UTF8Encoding]::new($false))

  # PowerShell prefers a .ps1 over a .cmd of the same name and then refuses it
  # under the default Restricted policy, so an old one has to go.
  Remove-Item -LiteralPath ([IO.Path]::ChangeExtension($launcher, '.ps1')) -Force -ErrorAction SilentlyContinue

  $separator = [IO.Path]::PathSeparator
  $currentParts = @($env:Path -split [Regex]::Escape([string]$separator))
  if (-not ($currentParts | Where-Object { $_ -ieq $binDir })) {
    $env:Path = "$binDir$separator$env:Path"
  }
  # The registry value is read and written as stored: the Environment calls
  # would expand an entry like %JAVA_HOME%\bin and freeze it at today's value.
  $environmentKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
  try {
    $hasUserPath = $null -ne $environmentKey.GetValue("Path")
    $userPath = [string]$environmentKey.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    $userParts = @($userPath -split [Regex]::Escape([string]$separator))
    if (-not ($userParts | Where-Object { [Environment]::ExpandEnvironmentVariables($_) -ieq $binDir })) {
      $updated = if ($userPath) { "$($userPath.TrimEnd($separator))$separator$binDir" } else { $binDir }
      $kind = if ($hasUserPath) { $environmentKey.GetValueKind("Path") } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
      $environmentKey.SetValue("Path", $updated, $kind)
      # Deleting an absent variable writes nothing, and this call still tells
      # Explorer and new terminals that the environment changed.
      [Environment]::SetEnvironmentVariable("OPENPROGRAM_PATH_CHANGED", $null, "User")
    }
  } finally {
    $environmentKey.Dispose()
  }
  Ok "CLI launcher: $launcher (added to your user PATH)"
}
Install-CliLauncher

# ---- 4. web + terminal frontend deps ----------------------------------------
function Install-Web {
  if ($Minimal) { Warn "skipping web/TUI dependencies and builds (-Minimal)"; return }
  if (-not (Have npm)) { Die "npm missing - cannot install the web/TUI" }
  if (-not (Test-Path "$HostRoot\apps\web\package.json")) { Warn "apps/web/ not found - skipping"; return }
  if (-not (Test-Path "$HostRoot\apps\cli\package.json")) { Warn "apps/cli/ not found - skipping"; return }
  Step "installing web and terminal UI dependencies"
  Push-Location "$HostRoot"
  try {
    Invoke-CheckedNative "frontend dependency install" "npm.cmd" ci --include-workspace-root --ignore-scripts --no-audit --no-fund
    Step "building web production bundle"
    Invoke-CheckedNative "web production build" "npm.cmd" run build --workspace apps/web
    Step "building terminal UI bundle"
    Invoke-CheckedNative "terminal UI production build" "npm.cmd" run build --workspace apps/cli
  }
  finally { Pop-Location }
  Ok "web UI and terminal UI ready"
}

# ---- 6. default extras: [all] = browser + channels (opt out with -Minimal) ----
function Install-DefaultExtras {
  if ($Minimal) { Warn "skipping default extras (-Minimal)"; return }
  Step "installing default extras [all] (browser tool + channels)"
  Pip install -e "${HostRoot}[all]"
  Step "fetching Playwright Chromium (~150MB)"
  & $PY -m playwright install chromium
  if ($LASTEXITCODE -ne 0) { Die "playwright chromium download failed (needs network)" }
}

# ---- 8. optional: program packages (-Programs) -------------------------------
function Install-Programs {
  if (-not $Programs) { return }
  # Accept repeated flags and comma-separated values; each source is one call.
  $sources = ($Programs -join ',').Split(',', [StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { $_.Trim() }
  foreach ($source in $sources) {
    Step "installing program: $source"
    & $PY -m openprogram programs install $source
    if ($LASTEXITCODE -ne 0) { Die "program install failed: $source" }
  }
}

# ---- run --------------------------------------------------------------------
Step "OpenProgram setup  (os=Windows, minimal=$Minimal)"
Install-Web
Install-DefaultExtras
Install-Programs

Write-Host "`nOpenProgram ready. Open a new terminal so the openprogram command is found." -ForegroundColor Green
Write-Host "  Start:     openprogram           # first run walks you through provider setup, then opens the chat"
Write-Host "  Web UI:    openprogram web        # opens the browser signed in (port 18100)"
Write-Host "  Programs:  openprogram programs install <directory | git URL | owner/repo>"
