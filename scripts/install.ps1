<#
=============================================================================
 OpenProgram - one-command installer (Windows / PowerShell)
-----------------------------------------------------------------------------
 Brings up the OpenProgram HOST so `openprogram` just works:
   1. Verify (or winget-install) the system toolchain: Python 3.11+, Node 20+, git
   2. Python env (uses an active venv/conda, else creates .\.venv)
   3. OpenProgram (editable) + its deps
   4. Web UI:  web\ -> npm install && npm run build  (served on :18100)
      (Windows uses the Rich REPL, not the Ink TUI, so cli\ is not built)
   5. Default extras [all]: browser tool (Playwright + Chromium) + channels

 Agentic programs (GUI / Research / Wiki) are NOT installed here - the
 first run of `openprogram` opens the setup wizard, whose "Agent
 programs" step lets the user pick which to install (sizes shown).
 Manual: openprogram programs install <gui|research|wiki|all>
 Non-interactive: pass -Programs <gui|research|wiki|all> (comma-separated
 or repeated) to install them right after the main install.

 -Minimal skips 4(build)/5/6 - a bare host for servers; everything it
 skipped can be added later (openprogram programs install all,
 pip install -e .[all], cd web; npm run build).

 The GUI harness's torch build is whatever pip resolves. For an explicit
 CUDA/CPU variant run the harness's own installer afterwards:
   openprogram\functions\agentics\GUI-Agent-Harness\scripts\install.ps1 -Cuda cu124

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

function Have($name){ return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Winget-Install($id){
  if (Have winget) { winget install --silent --accept-package-agreements --accept-source-agreements -e --id $id }
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
  if (-not $Target -and -not $Yes) {
    $reply = Read-Host "Clone OpenProgram to [$dest]"
    if ($reply) { $dest = $reply }
  }
  if (Test-Path $dest) {
    if (Test-OpenProgramCheckout $dest) {
      Step "reusing existing OpenProgram checkout at $dest"
      Push-Location $dest; try { git pull --ff-only } finally { Pop-Location }
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
if (Have git)  { Ok "git: $(git --version)" } else { Warn "git missing" }
if (-not (Have node)) { Step "installing Node.js"; Winget-Install "OpenJS.NodeJS.LTS" }
if (Have node) {
  $nodeMajor = [int]((node -p "process.versions.node.split('.')[0]") 2>$null)
  if ($nodeMajor -ge 20) { Ok "node: $(node --version)" } else { Warn "node $(node --version) < 20 - upgrade to Node 20+" }
} else { Warn "node not found - the web UI needs Node 20+ (https://nodejs.org)" }

# ---- 2. Python env ----------------------------------------------------------
function Resolve-Python {
  if ($Python) { return $Python }
  if ($env:VIRTUAL_ENV -and (Test-Path "$env:VIRTUAL_ENV\Scripts\python.exe")) { return "$env:VIRTUAL_ENV\Scripts\python.exe" }
  if ($env:CONDA_PREFIX -and (Test-Path "$env:CONDA_PREFIX\python.exe"))        { return "$env:CONDA_PREFIX\python.exe" }
  if (Test-Path "$HostRoot\.venv\Scripts\python.exe")                          { return "$HostRoot\.venv\Scripts\python.exe" }
  $base = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $base) { Die "no python found - install Python 3.11+ first (https://www.python.org/downloads/)" }
  Step "creating virtualenv at $HostRoot\.venv"
  & $base -m venv "$HostRoot\.venv"
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
& $PY -m pip install --quiet --upgrade pip *> $null

# ---- 3. OpenProgram (editable) ----------------------------------------------
Step "installing OpenProgram (editable) from $HostRoot"
Pip install -e "$HostRoot"
Ok "openprogram installed"

# ---- 4. web frontend deps ---------------------------------------------------
function Install-Web {
  if (-not (Have npm)) { Warn "npm missing - skipping web UI deps"; return }
  if (-not (Test-Path "$HostRoot\web\package.json")) { Warn "web/ not found - skipping"; return }
  Step "installing web UI deps (web/ - Next.js)"
  Push-Location "$HostRoot\web"
  try {
    cmd /c "npm install"
    if ($Minimal) { Warn "skipping web production build (-Minimal) - the worker builds it on first start" }
    else { Step "building web production bundle"; cmd /c "npm run build" }
  }
  finally { Pop-Location }
  Ok "web UI ready (frontend :18100, backend :18109)"
}

# ---- 6. default extras: [all] = browser + channels (opt out with -Minimal) ----
function Install-DefaultExtras {
  if ($Minimal) { Warn "skipping default extras (-Minimal)"; return }
  Step "installing default extras [all] (browser tool + channels)"
  Pip install -e "${HostRoot}[all]"
  Step "fetching Playwright Chromium (~150MB)"
  & $PY -m playwright install chromium
  if ($LASTEXITCODE -ne 0) { Warn "playwright chromium download failed - run '& `"$PY`" -m playwright install chromium' later (needs network)" }
}

# ---- 7. heavier opt-in extras: stealth browsers / agent-browser ---------------
function Install-Extras {
  if ($Stealth) {
    Step "installing stealth browser (patchright + camoufox)"; Pip install -e "${HostRoot}[browser-stealth]"
    & $PY -m patchright install chromium 2>$null; & $PY -m camoufox fetch 2>$null
  }
  if ($AgentBrowser) {
    Step "installing agent-browser (global npm)"
    if (Have npm) { cmd /c "npm install -g agent-browser"; cmd /c "agent-browser install" } else { Warn "npm missing" }
  }
}

# ---- 8a. interactive program menu -------------------------------------------
# Sizes mirror KNOWN_PROGRAMS (openprogram/functions/_programs.py).
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
  if ($Yes) { return }                            # -Yes: default (none)
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
    openprogram programs install $name
    if ($LASTEXITCODE -ne 0) { Warn "program install failed: $name" }
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
