#!/usr/bin/env bash
# =============================================================================
# OpenProgram — one-command installer (macOS / Linux)
# -----------------------------------------------------------------------------
# Brings up the OpenProgram HOST so `openprogram` just works:
#   1. Verify (or install) the system toolchain: Python 3.11+, Node 20+, git
#   2. Python env (uses an active venv/conda, else creates ./.venv)
#   3. OpenProgram (editable) + its deps
#   4. Web UI deps:  web/  -> npm install   (Next.js frontend on :18100)
#   5. TUI deps:     cli/  -> npm install && npm run build  (Ink TUI; POSIX)
#   6. Browser tool + channels installed by DEFAULT; --stealth / --agent-browser
#               are heavier opt-ins (--minimal skips the default extras)
#
# The GUI agent is NOT installed here — it is a separate program, added like any
# other harness (clone into openprogram/functions/agentics/ and run its own
# installer). Pass --gui to also install it as a convenience.
#
# PyTorch (only relevant with --gui) is auto-selected: an NVIDIA GPU (nvidia-smi)
# gets the matching CUDA build, otherwise CPU. Force it with --cpu or --cuda cuXXX.
#
# Re-runnable: every step is idempotent.
#
# Usage:
#   ./scripts/install.sh                 # host only (web + TUI + browser/channels)
#   ./scripts/install.sh --gui           # also install the GUI agent (auto GPU/CPU torch)
#   ./scripts/install.sh --cpu           # force CPU torch (with --gui)
#   ./scripts/install.sh --cuda cu124    # force a specific CUDA tag (with --gui)
#   ./scripts/install.sh --browser       # + Playwright browser tool
# =============================================================================
set -euo pipefail

c_blue='\033[1;34m'; c_green='\033[1;32m'; c_yellow='\033[1;33m'; c_red='\033[1;31m'; c_reset='\033[0m'
step() { printf "${c_blue}==>${c_reset} %s\n" "$*"; }
ok()   { printf "${c_green}  ok${c_reset} %s\n" "$*"; }
warn() { printf "${c_yellow}  !!${c_reset} %s\n" "$*" >&2; }
die()  { printf "${c_red}ERROR${c_reset} %s\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HARNESS_REL="openprogram/functions/agentics/GUI-Agent-Harness"
HARNESS_DIR="$HOST_ROOT/$HARNESS_REL"
HARNESS_REPO="https://github.com/Fzkuji/GUI-Agent-Harness"
OS="$(uname -s)"

# ---- args -------------------------------------------------------------------
WITH_GUI=0; TORCH_VARIANT="auto"; PYTHON_BIN=""; BUILD_WEB=0   # host only by default; --gui to add the GUI agent
WITH_BROWSER=0; WITH_STEALTH=0; WITH_AGENT_BROWSER=0; WITH_CHANNELS=0; NO_TUI=0; MINIMAL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --gui) WITH_GUI=1; shift ;;                 # opt-in: also install the GUI agent
    --no-gui) WITH_GUI=0; shift ;;              # default already; kept for back-compat
    --cuda) TORCH_VARIANT="${2:?--cuda needs your CUDA tag, e.g. cu121 or cu124}"; shift 2 ;;
    --cpu) TORCH_VARIANT="cpu"; shift ;;
    --python) PYTHON_BIN="${2:?--python needs a path}"; shift 2 ;;
    --build-web) BUILD_WEB=1; shift ;;
    --browser) WITH_BROWSER=1; shift ;;
    --stealth) WITH_STEALTH=1; shift ;;
    --agent-browser) WITH_AGENT_BROWSER=1; shift ;;
    --channels) WITH_CHANNELS=1; shift ;;
    --minimal) MINIMAL=1; shift ;;              # opt-out: skip the default browser + channels extras
    --no-tui) NO_TUI=1; shift ;;
    -h|--help) sed -n '/^# Usage:/,/^# ==/p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

# ---- 1. system toolchain ----------------------------------------------------
pm_install() {  # best-effort cross-distro package install
  local pkgs="$*"
  if [ "$OS" = "Darwin" ]; then
    command -v brew >/dev/null 2>&1 && brew install $pkgs || warn "install manually: brew install $pkgs"
  elif command -v apt-get >/dev/null 2>&1; then sudo_run apt-get update -qq && sudo_run apt-get install -y $pkgs
  elif command -v dnf >/dev/null 2>&1; then sudo_run dnf install -y $pkgs
  elif command -v pacman >/dev/null 2>&1; then sudo_run pacman -S --noconfirm $pkgs
  else warn "unknown package manager — install manually: $pkgs"; fi
}
sudo_run() { if [ "$(id -u)" = "0" ]; then "$@"; elif command -v sudo >/dev/null 2>&1; then sudo "$@"; else warn "no sudo — run as root: $*"; return 1; fi; }

step "checking system toolchain (python3.11+, node20+, git)"
command -v git >/dev/null 2>&1 || { step "installing git"; pm_install git; }
command -v git >/dev/null 2>&1 && ok "git: $(git --version)" || warn "git missing"
if ! command -v node >/dev/null 2>&1; then
  step "installing Node.js"
  if [ "$OS" = "Darwin" ]; then pm_install node
  else pm_install nodejs npm || warn "install Node 20+ from https://nodejs.org"; fi
fi
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  [ "$NODE_MAJOR" -ge 20 ] && ok "node: $(node --version)" || warn "node $(node --version) < 20 — web/TUI may fail; upgrade to Node 20+"
else
  warn "node not found — the web UI and TUI need Node 20+ (https://nodejs.org)"
fi

# ---- 2. Python env ----------------------------------------------------------
resolve_python() {
  if [ -n "$PYTHON_BIN" ]; then echo "$PYTHON_BIN"; return; fi
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then echo "$VIRTUAL_ENV/bin/python"; return; fi
  if [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then echo "$CONDA_PREFIX/bin/python"; return; fi
  if [ -x "$HOST_ROOT/.venv/bin/python" ]; then echo "$HOST_ROOT/.venv/bin/python"; return; fi
  local base; base="$(command -v python3 || command -v python || true)"
  [ -n "$base" ] || die "no python3 found — install Python 3.11+ first"
  step "creating virtualenv at $HOST_ROOT/.venv"
  "$base" -m venv "$HOST_ROOT/.venv"
  echo "$HOST_ROOT/.venv/bin/python"
}
PY="$(resolve_python)"
"$PY" -c 'import sys; assert sys.version_info[:2] >= (3,11), sys.version' \
  || die "Python 3.11+ required (got: $("$PY" --version 2>&1))"
ok "python: $("$PY" --version 2>&1)  [$PY]"
PIP() { "$PY" -m pip "$@"; }
PIP install --quiet --upgrade pip >/dev/null 2>&1 || true

# ---- 3. OpenProgram (editable) ----------------------------------------------
step "installing OpenProgram (editable) from $HOST_ROOT"
PIP install -e "$HOST_ROOT"
ok "openprogram installed"

# ---- 4. web frontend deps ---------------------------------------------------
install_web() {
  command -v npm >/dev/null 2>&1 || { warn "npm missing — skipping web UI deps"; return 0; }
  [ -f "$HOST_ROOT/web/package.json" ] || { warn "web/ not found — skipping"; return 0; }
  step "installing web UI deps (web/ — Next.js)"
  ( cd "$HOST_ROOT/web" && npm install )
  if [ "$BUILD_WEB" = "1" ]; then step "building web production bundle"; ( cd "$HOST_ROOT/web" && npm run build ); fi
  ok "web UI deps installed (frontend :18100, backend :18109)"
}

# ---- 5. Ink TUI deps (POSIX only) -------------------------------------------
install_tui() {
  [ "$NO_TUI" = "1" ] && { warn "skipping TUI build (--no-tui)"; return 0; }
  command -v npm >/dev/null 2>&1 || { warn "npm missing — skipping TUI"; return 0; }
  [ -f "$HOST_ROOT/cli/package.json" ] || return 0
  step "installing + building Ink TUI (cli/)"
  ( cd "$HOST_ROOT/cli" && npm install && npm run build )
  ok "TUI built (cli/dist/index.js)"
}

# ---- 6. GUI harness (delegates to the harness's own installer) --------------
install_gui() {
  [ "$WITH_GUI" = "1" ] || return 0
  if [ ! -d "$HARNESS_DIR" ]; then
    step "cloning GUI-Agent-Harness into $HARNESS_REL"
    git clone --depth 1 "$HARNESS_REPO" "$HARNESS_DIR" || die "git clone of harness failed"
  fi
  [ -f "$HARNESS_DIR/scripts/install.sh" ] || die "harness installer not found at $HARNESS_DIR/scripts/install.sh"
  step "running GUI-Agent-Harness installer"
  bash "$HARNESS_DIR/scripts/install.sh" --python "$PY" --cuda "$TORCH_VARIANT" --no-host
}

# ---- 7. default extras: [all] = browser + channels (opt out with --minimal) ----
install_default_extras() {
  [ "$MINIMAL" = "1" ] && { warn "skipping default extras (--minimal)"; return 0; }
  step "installing default extras [all] (browser tool + channels)"
  PIP install -e "$HOST_ROOT[all]"
  step "fetching Playwright Chromium (~150MB)"
  "$PY" -m playwright install chromium || warn "playwright chromium download failed — run '\"$PY\" -m playwright install chromium' later (needs network)"
}

# ---- 8. heavier opt-in extras: stealth browsers / agent-browser ---------------
install_extras() {
  if [ "$WITH_BROWSER" = "1" ]; then
    step "installing browser tool (Playwright)"; PIP install -e "$HOST_ROOT[browser]"
    "$PY" -m playwright install chromium || warn "playwright install chromium failed"
  fi
  if [ "$WITH_STEALTH" = "1" ]; then
    step "installing stealth browser (patchright + camoufox)"; PIP install -e "$HOST_ROOT[browser-stealth]"
    "$PY" -m patchright install chromium || warn "patchright install chromium failed"
    "$PY" -m camoufox fetch || warn "camoufox fetch failed"
  fi
  if [ "$WITH_AGENT_BROWSER" = "1" ]; then
    step "installing agent-browser (global npm)"
    if command -v npm >/dev/null 2>&1; then npm install -g agent-browser && agent-browser install || warn "agent-browser setup failed"
    else warn "npm missing — cannot install agent-browser"; fi
  fi
  if [ "$WITH_CHANNELS" = "1" ]; then
    step "installing channel deps (discord / slack / wechat-qr)"; PIP install -e "$HOST_ROOT[channels]"
    warn "channels need tokens — configure in ~/.openprogram/config.json"
  fi
}

# ---- run --------------------------------------------------------------------
step "OpenProgram setup  (os=$OS, gui=$WITH_GUI, torch=$TORCH_VARIANT)"
install_web
install_tui
install_gui
install_default_extras
install_extras

# ---- done --------------------------------------------------------------------
printf "\n${c_green}OpenProgram ready.${c_reset}\n"
printf "  Start:     openprogram           # first run walks you through provider setup, then opens the chat\n"
printf "  Web UI:    openprogram web        # -> http://localhost:18100\n"
if [ "$WITH_GUI" = "1" ]; then
  printf "  GUI agent: gui-agent --work-dir /tmp/gui --app firefox \"Open Firefox\"\n"
else
  printf "  Add a harness: clone it into openprogram/functions/agentics/ and run its installer\n"
  printf "                 (GUI agent: https://github.com/Fzkuji/GUI-Agent-Harness)\n"
fi
