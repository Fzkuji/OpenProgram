<#
=============================================================================
 OpenProgram - legacy source-development installer (Windows / PowerShell)
-----------------------------------------------------------------------------
 Windows release installation is handled by the root `install.ps1` bootstrap
 and packaged release installer. This legacy script remains the editable
 source-development workflow.
 Brings up the OpenProgram HOST so `openprogram` just works:
   1. Verify (or winget-install) the system toolchain: Python 3.11+, Node 20+, git
   2. Python env (creates/reuses .\.venv unless -Python is supplied)
   3. OpenProgram (editable) + its deps
   4. Web + terminal UI: builds apps/web and the full Ink TUI in apps/cli
   5. Default extras [all]: browser tool (Playwright + Chromium) + channels

 Agentic programs (GUI / Research / Wiki) are NOT installed here - the
 first run of `openprogram` opens the setup wizard, whose "Agent
 programs" step lets the user pick which to install (sizes shown).
 Manual: openprogram programs install <gui|research|wiki|all>
 Non-interactive: pass -Programs <gui|research|wiki|all> (comma-separated
 or repeated) to install them right after the main install.

 -Minimal skips the Web build and optional extras - a bare host for servers; everything it
 skipped can be added later (openprogram programs install all,
 pip install -e .[all], npm ci --include-workspace-root,
 npm run build --workspace apps/web, npm run build --workspace apps/cli).

 The GUI harness's torch build is whatever pip resolves. For an explicit
 CUDA/CPU variant run the harness's own installer afterwards:
   openprogram\programs\applications\gui_harness\scripts\install.ps1 -Cuda cu124

 Re-runnable: every step is idempotent.

 Run it straight off the web - no clone needed:
   iwr -useb https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.ps1 | iex
 It clones OpenProgram to $HOME\OpenProgram (override with -Target DIR), then
 hands off to the cloned copy and offers a menu to pick which agentic programs
 (GUI / Research / Wiki) to install.

 Usage:
   .\scripts\install.ps1                  # full install (everything above)
   .\scripts\install.ps1 -Minimal         # bare host only
   .\scripts\install.ps1 -Stealth         # + stealth browsers
   .\scripts\install.ps1 -AgentBrowser    # + agent-browser (global npm)
   .\scripts\install.ps1 -Programs all    # + install agentic programs non-interactively
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
  [switch]$Stealth,
  [switch]$AgentBrowser,
  [string[]]$Programs = @(),       # install agentic programs non-interactively (gui|research|wiki|all)
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
function Die($m){ Write-Host "ERROR $m" -ForegroundColor Red; exit 1 }

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

# Non-interactive signals — treated exactly like -Yes (defaults, no prompts).
# CI / DEBIAN_FRONTEND=noninteractive are ecosystem conventions;
# OPENPROGRAM_INSTALL_YES is our own escape hatch.
# NOTE: Read-Host has no timeout, so (unlike install.sh) the ps1 prompts can't
# self-default on expiry — an agent must pass -Yes or one of these env vars.
function Test-NonInteractive {
  if ($Yes) { return $true }
  if ($env:CI) { return $true }
  if ($env:DEBIAN_FRONTEND -eq 'noninteractive') { return $true }
  if ($env:OPENPROGRAM_INSTALL_YES) { return $true }
  return $false
}
function Winget-Install($id){
  if (Have winget) {
    winget install --silent --accept-package-agreements --accept-source-agreements -e --id $id
    if ($LASTEXITCODE -ne 0) { Die "winget could not install $id (exit $LASTEXITCODE)" }
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
  if (-not (Have git)) { Die "git is required to install off the web - install Git for Windows (winget install Git.Git), or clone the repo and run scripts\install.ps1 from inside it." }
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
  if ($Stealth)      { $forward += "-Stealth" }
  if ($AgentBrowser) { $forward += "-AgentBrowser" }
  if ($Yes)          { $forward += "-Yes" }
  if ($Python)       { $forward += @("-Python", $Python) }
  if ($Programs)     { $forward += @("-Programs", ($Programs -join ',')) }
  & $child @forward
  exit $LASTEXITCODE
}
Step "checking system toolchain (python3.11+, node20+, git)"
if (-not (Have git))  { Step "installing git";    Winget-Install "Git.Git" }
if (Have git)  { Ok "git: $(git --version)" } else { Die "git missing after installation; open a new PowerShell and run this installer again" }
if (-not $Minimal -and -not (Have node)) { Step "installing Node.js"; Winget-Install "OpenJS.NodeJS.LTS" }
if (Have node) {
  $nodeMajor = [int]((node -p "process.versions.node.split('.')[0]") 2>$null)
  if ($nodeMajor -ge 20 -and $nodeMajor -le 22) { Ok "node: $(node --version)" }
  elseif ($nodeMajor -gt 22) { Warn "node $(node --version) is newer than the validated Node 22 LTS; continuing with workspace-scoped installs" }
  else { Die "node $(node --version) < 20 - upgrade to Node 20+ (Node 22 LTS recommended)" }
} elseif (-not $Minimal) { Die "node not found after installation; open a new PowerShell and run this installer again" }
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

function New-CheckoutVenv([switch]$Clear) {
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
  foreach ($candidate in $candidates) {
    if ($candidate.Command.StartsWith("$HostRoot\.venv\", [StringComparison]::OrdinalIgnoreCase)) {
      continue
    }
    & $candidate.Command @($candidate.Prefix) -c "import sys; assert sys.version_info[:2] >= (3,11)" *> $null
    if ($LASTEXITCODE -ne 0) { continue }
    $venvArgs = @($candidate.Prefix) + @("-m", "venv")
    if ($Clear) { $venvArgs += "--clear" }
    $venvArgs += "$HostRoot\.venv"
    & $candidate.Command @venvArgs
    if ($LASTEXITCODE -eq 0 -and (Test-IsolatedCheckoutVenv)) { return }
  }
  Die "no working Python 3.11+ found; install Python 3.12 and run this installer again"
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
  $content = "@echo off`r`n`"$quotedPython`" -m openprogram %*`r`n"
  [System.IO.File]::WriteAllText($launcher, $content, [System.Text.Encoding]::ASCII)

  $separator = [IO.Path]::PathSeparator
  $currentParts = @($env:Path -split [Regex]::Escape([string]$separator))
  if (-not ($currentParts | Where-Object { $_ -ieq $binDir })) {
    $env:Path = "$binDir$separator$env:Path"
  }
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $userParts = @($userPath -split [Regex]::Escape([string]$separator))
  if (-not ($userParts | Where-Object { $_ -ieq $binDir })) {
    $updated = if ($userPath) { "$userPath$separator$binDir" } else { $binDir }
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
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

# ---- 7. heavier opt-in extras: stealth browsers / agent-browser ---------------
function Install-Extras {
  if ($Stealth) {
    Step "installing stealth browser (patchright + camoufox)"; Pip install -e "${HostRoot}[browser-stealth]"
    & $PY -m patchright install chromium
    if ($LASTEXITCODE -ne 0) { Die "patchright Chromium install failed" }
    & $PY -m camoufox fetch
    if ($LASTEXITCODE -ne 0) { Die "camoufox fetch failed" }
  }
  if ($AgentBrowser) {
    Step "installing agent-browser (global npm)"
    if (Have npm) {
      Invoke-CheckedNative "agent-browser npm install" "npm.cmd" install -g agent-browser
      Invoke-CheckedNative "agent-browser browser install" "agent-browser.cmd" install
    } else { Die "npm missing - cannot install agent-browser" }
  }
}

# ---- 8a. interactive program menu -------------------------------------------
# Sizes mirror KNOWN_PROGRAMS (openprogram/programs/_programs.py).
$ProgramKeys = @("gui","research","wiki")
$ProgramMenu = @(
  "GUI harness      - autonomous desktop agent (downloads PyTorch: ~300 MB CPU / ~3 GB CUDA; ~1.5 GB on disk)",
  "Research harness - topic -> submission-ready paper (repo < 1 MB, only depends on openprogram)",
  "Wiki harness     - ingest sessions into a knowledge vault (repo < 1 MB; Jinja2 + PyYAML)"
)
# Parse "1,3" / "all" / "none" / "" -> string[] of keys, or $null on invalid.
function Convert-ProgramChoice([string]$raw) {
  $r = ($raw -replace '\s','').ToLower()
  if ($r -eq '' -or $r -eq 'none') { return @() }
  if ($r -eq 'all') { return $ProgramKeys }
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($part in $r.Split(',', [StringSplitOptions]::RemoveEmptyEntries)) {
    if ($ProgramKeys -contains $part) { $key = $part }
    elseif ($part -match '^\d+$') {
      $idx = [int]$part
      if ($idx -lt 1 -or $idx -gt $ProgramKeys.Count) { return $null }
      $key = $ProgramKeys[$idx-1]
    } else { return $null }
    if (-not $out.Contains($key)) { $out.Add($key) }
  }
  return $out.ToArray()
}
function Prompt-Programs {
  if ($Programs) { return }                       # -Programs wins, no prompt
  if (Test-NonInteractive) { return }             # -Yes / CI / etc: default (none)
  if (-not [Environment]::UserInteractive) { return }
  Write-Host "`nAgentic programs - pick which to install now (or later via the first-run wizard):"
  for ($i = 0; $i -lt $ProgramMenu.Count; $i++) { Write-Host ("  {0}) {1}" -f ($i+1), $ProgramMenu[$i]) }
  Write-Host "  all)  install every harness"
  Write-Host '  none) skip (default - pick later, or: openprogram programs install <gui|research|wiki|all>)'
  while ($true) {
    $reply = Read-Host 'Choose (comma-separated numbers, "all", or "none") [none]'
    $picked = Convert-ProgramChoice $reply
    if ($null -ne $picked) { if ($picked.Count) { $script:Programs = $picked }; return }
    Write-Host "  invalid selection: $reply"
  }
}

# ---- 8. optional: agentic programs (-Programs) -------------------------------
function Install-Programs {
  if (-not $Programs) { return }
  # Accept repeated flags and comma-separated values: -Programs gui,research
  # and -Programs gui -Programs research both fan out to one call each.
  foreach ($name in ($Programs -join ',').Split(',', [StringSplitOptions]::RemoveEmptyEntries)) {
    Step "installing agentic program: $name"
    & $PY -m openprogram programs install $name
    if ($LASTEXITCODE -ne 0) { Die "program install failed: $name" }
  }
}

# ---- run --------------------------------------------------------------------
Step "OpenProgram setup  (os=Windows, minimal=$Minimal)"
Install-Web
Install-DefaultExtras
Install-Extras
Prompt-Programs
Install-Programs

Write-Host "`nOpenProgram ready." -ForegroundColor Green
Write-Host "  Start:     openprogram           # first run walks you through provider setup, then opens the chat"
Write-Host "  Web UI:    openprogram web        # -> http://localhost:18100"
Write-Host "  Programs:  pick which agentic programs to install in the first-run wizard"
Write-Host "             (or any time: openprogram programs install <gui|research|wiki|all>,"
Write-Host "              or non-interactively at install: .\scripts\install.ps1 -Programs all)"
