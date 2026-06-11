<#
=============================================================================
 OpenProgram - one-command installer (Windows / PowerShell)
-----------------------------------------------------------------------------
 Brings up the OpenProgram HOST so `openprogram` just works:
   1. Verify (or winget-install) the system toolchain: Python 3.11+, Node 20+, git
   2. Python env (uses an active venv/conda, else creates .\.venv)
   3. OpenProgram (editable) + its deps
   4. Web UI deps:  web\  -> npm install   (Next.js frontend on :18100)
      (Windows uses the Rich REPL, not the Ink TUI, so cli\ is not built)
   5. Browser tool + channels installed by DEFAULT; -Stealth / -AgentBrowser are
      heavier opt-ins (-Minimal skips the default extras)

 The GUI agent is NOT installed here - it is a separate program, added like any
 other harness (clone into openprogram\functions\agentics\ and run its own
 installer). Pass -Gui to also install it as a convenience.

 PyTorch (only relevant with -Gui) is auto-selected: an NVIDIA GPU (nvidia-smi)
 gets the matching CUDA build, otherwise CPU. Force it with -Cpu or -Cuda cuXXX.

 Re-runnable: every step is idempotent.

 Usage:
   .\scripts\install.ps1                  # host only (web + browser/channels)
   .\scripts\install.ps1 -Gui             # also install the GUI agent (auto GPU/CPU torch)
   .\scripts\install.ps1 -Cpu             # force CPU torch (with -Gui)
   .\scripts\install.ps1 -Cuda cu124      # force a specific CUDA tag (with -Gui)
   .\scripts\install.ps1 -Browser         # + Playwright browser tool
=============================================================================
#>
[CmdletBinding()]
param(
  [switch]$Gui,                   # opt-in: also install the GUI agent
  [switch]$NoGui,                 # default already (host only); kept for back-compat
  [string]$Cuda = "auto",         # auto-detect GPU; "cpu" or "cuXXX" to force
  [switch]$Cpu,                   # force the CPU torch build
  [string]$Python = "",
  [switch]$BuildWeb,
  [switch]$Browser,
  [switch]$Stealth,
  [switch]$AgentBrowser,
  [switch]$Channels,
  [switch]$Minimal                # opt-out: skip the default browser + channels extras
)
# NOTE: 'Continue', not 'Stop'. Under 'Stop', Windows PowerShell 5.1 turns a
# native exe's stderr line (e.g. pip's harmless "Scripts not on PATH" warning)
# into a terminating NativeCommandError. We gate on $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
if ($Cpu) { $Cuda = "cpu" }   # -Cpu forces the CPU torch build (passed through to the harness)

function Step($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "  ok $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  !! $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "ERROR $m" -ForegroundColor Red; exit 1 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HostRoot  = (Resolve-Path "$ScriptDir\..").Path
$HarnessRel = "openprogram\functions\agentics\GUI-Agent-Harness"
$HarnessDir = Join-Path $HostRoot $HarnessRel
$HarnessRepo = "https://github.com/Fzkuji/GUI-Agent-Harness"

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
  try { cmd /c "npm install"; if ($BuildWeb) { Step "building web production bundle"; cmd /c "npm run build" } }
  finally { Pop-Location }
  Ok "web UI deps installed (frontend :18100, backend :18109)"
}

# ---- 5. GUI harness (delegates to the harness's own installer) --------------
function Install-Gui {
  if (-not $Gui -or $NoGui) { return }
  if (-not (Test-Path $HarnessDir)) {
    Step "cloning GUI-Agent-Harness into $HarnessRel"
    git clone --depth 1 $HarnessRepo $HarnessDir
    if ($LASTEXITCODE -ne 0) { Die "git clone of harness failed" }
  }
  $hInstall = Join-Path $HarnessDir "scripts\install.ps1"
  if (-not (Test-Path $hInstall)) { Die "harness installer not found at $hInstall" }
  Step "running GUI-Agent-Harness installer"
  & powershell -NoProfile -ExecutionPolicy Bypass -File $hInstall -Python $PY -Cuda $Cuda -NoHost
}

# ---- 5b. bundled programs: the three agent harnesses ------------------------
# `openprogram programs install all` git-clones GUI-Agent-Harness,
# Research-Agent-Harness and Wiki-Agent-Harness into
# openprogram\functions\agentics\ and pip-installs each one's own
# declared deps. Idempotent: an existing clone is left alone.
function Install-Programs {
  if ($Minimal) { Warn "skipping bundled programs (-Minimal)"; return }
  Step "installing bundled programs (gui_agent / research_agent / wiki_agent)"
  & $PY -m openprogram programs install all
  if ($LASTEXITCODE -ne 0) { Warn "program install failed - re-run later: openprogram programs install all" }
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
  if ($Browser) {
    Step "installing browser tool (Playwright)"; Pip install -e "${HostRoot}[browser]"
    & $PY -m playwright install chromium; if ($LASTEXITCODE -ne 0) { Warn "playwright install chromium failed" }
  }
  if ($Stealth) {
    Step "installing stealth browser (patchright + camoufox)"; Pip install -e "${HostRoot}[browser-stealth]"
    & $PY -m patchright install chromium 2>$null; & $PY -m camoufox fetch 2>$null
  }
  if ($AgentBrowser) {
    Step "installing agent-browser (global npm)"
    if (Have npm) { cmd /c "npm install -g agent-browser"; cmd /c "agent-browser install" } else { Warn "npm missing" }
  }
  if ($Channels) {
    Step "installing channel deps (discord / slack / wechat-qr)"; Pip install -e "${HostRoot}[channels]"
    Warn "channels need tokens - configure in ~/.openprogram/config.json"
  }
}

# ---- run --------------------------------------------------------------------
Step "OpenProgram setup  (os=Windows, gui=$($Gui -and -not $NoGui), torch=$Cuda)"
Install-Web
Install-Gui
Install-Programs
Install-DefaultExtras
Install-Extras

Write-Host "`nOpenProgram ready." -ForegroundColor Green
Write-Host "  Start:     openprogram           # first run walks you through provider setup, then opens the chat"
Write-Host "  Web UI:    openprogram web        # -> http://localhost:18100"
Write-Host "  Programs:  gui_agent / research_agent / wiki_agent installed under openprogram\functions\agentics\"
Write-Host "             manage with: openprogram programs list | install | uninstall"
else { Write-Host "  Add a harness: clone it into openprogram\functions\agentics\ and run its installer"; Write-Host "                 (GUI agent: https://github.com/Fzkuji/GUI-Agent-Harness)" }
