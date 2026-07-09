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

 Usage:
   .\scripts\install.ps1                  # full install (everything above)
   .\scripts\install.ps1 -Minimal         # bare host only
   .\scripts\install.ps1 -Stealth         # + stealth browsers
   .\scripts\install.ps1 -AgentBrowser    # + agent-browser (global npm)
   .\scripts\install.ps1 -Programs all    # + install agentic programs non-interactively
=============================================================================
#>
[CmdletBinding()]
param(
  [string]$Python = "",
  [switch]$Stealth,
  [switch]$AgentBrowser,
  [string[]]$Programs = @(),       # install agentic programs non-interactively (gui|research|wiki|all)
  [switch]$Minimal                # bare host: skip web build / programs / default extras
)
# NOTE: 'Continue', not 'Stop'. Under 'Stop', Windows PowerShell 5.1 turns a
# native exe's stderr line (e.g. pip's harmless "Scripts not on PATH" warning)
# into a terminating NativeCommandError. We gate on $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"

function Step($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  ok $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  !! $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "ERROR $m" -ForegroundColor Red; exit 1 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HostRoot  = (Resolve-Path "$ScriptDir\..").Path

# ---- 1. system toolchain (best-effort via winget) ---------------------------
function Have($name){ return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Winget-Install($id){
  if (Have winget) { winget install --silent --accept-package-agreements --accept-source-agreements -e --id $id }
  else { Warn "winget not available - install $id manually" }
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
Install-Programs

Write-Host "`nOpenProgram ready." -ForegroundColor Green
Write-Host "  Start:     openprogram           # first run walks you through provider setup, then opens the chat"
Write-Host "  Web UI:    openprogram web        # -> http://localhost:18100"
Write-Host "  Programs:  pick which agentic programs to install in the first-run wizard"
Write-Host "             (or any time: openprogram programs install <gui|research|wiki|all>,"
Write-Host "              or non-interactively at install: .\scripts\install.ps1 -Programs all)"
else { Write-Host "  Add a harness: clone it into openprogram\functions\agentics\ and run its installer"; Write-Host "                 (GUI agent: https://github.com/Fzkuji/GUI-Agent-Harness)" }
