#!/usr/bin/env bash
# =============================================================================
# OpenProgram — one-command installer (macOS / Linux)
# -----------------------------------------------------------------------------
# The default install brings up EVERYTHING `openprogram` ships with:
#   1. System toolchain: Python 3.11+, Node 20+, git (installed if missing)
#   2. Python env (uses an active venv/conda, else creates ./.venv)
#   3. OpenProgram (editable) + its deps
#   4. Web UI:   web/ -> npm install && npm run build  (served on :18100)
#   5. TUI:      cli/ -> npm install && npm run build  (Ink TUI; POSIX)
#   6. Default extras [all]: browser tool (Playwright + Chromium) + channels
#
#   Agentic programs (GUI / Research / Wiki) are NOT installed here — the
#   first run of `openprogram` opens the setup wizard, whose "Agent
#   programs" step lets the user pick which to install (sizes shown).
#   Manual: openprogram programs install <gui|research|wiki|all>
#
# `--minimal` skips 4(build)/5/6/7 — a bare host for servers; everything
# it skipped can be added later (`openprogram programs install all`,
# `pip install -e .[all]`, `cd web && npm run build`).
#
# The GUI harness's torch build is whatever pip resolves. If you need an
# explicit CUDA/CPU variant, run the harness's own installer afterwards:
#   openprogram/functions/agentics/GUI-Agent-Harness/scripts/install.sh --cuda cu124
#
# Re-runnable: every step is idempotent.
#
# Usage:
#   ./scripts/install.sh                   # full install (everything above)
#   ./scripts/install.sh --minimal         # bare host only
#   ./scripts/install.sh --python /p/bin/python   # pick the interpreter
#   ./scripts/install.sh --stealth         # + stealth browsers (patchright/camoufox)
#   ./scripts/install.sh --agent-browser   # + agent-browser (global npm)
# =============================================================================
set -euo pipefail

c_blue='\033[1;34m'; c_green='\033[1;32m'; c_yellow='\033[1;33m'; c_red='\033[1;31m'; c_reset='\033[0m'
step() { printf "${c_blue}==>${c_reset} %s\n" "$*"; }
ok()   { printf "${c_green}  ok${c_reset} %s\n" "$*"; }
warn() { printf "${c_yellow}  !!${c_reset} %s\n" "$*" >&2; }
die()  { printf "${c_red}ERROR${c_reset} %s\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OS="$(uname -s)"

# ---- args -------------------------------------------------------------------
PYTHON_BIN=""; MINIMAL=0; WITH_STEALTH=0; WITH_AGENT_BROWSER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --minimal) MINIMAL=1; shift ;;
    --python) PYTHON_BIN="${2:?--python needs a path}"; shift 2 ;;
    --stealth) WITH_STEALTH=1; shift ;;
    --agent-browser) WITH_AGENT_BROWSER=1; shift ;;
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

# ---- 4. web frontend (deps + production build) -------------------------------
install_web() {
  command -v npm >/dev/null 2>&1 || { warn "npm missing — skipping web UI deps"; return 0; }
  [ -f "$HOST_ROOT/web/package.json" ] || { warn "web/ not found — skipping"; return 0; }
  step "installing web UI deps (web/ — Next.js)"
  ( cd "$HOST_ROOT/web" && npm install )
  if [ "$MINIMAL" = "1" ]; then
    warn "skipping web production build (--minimal) — the worker builds it on first start"
  else
    step "building web production bundle"
    ( cd "$HOST_ROOT/web" && npm run build ) || warn "web build failed — the worker retries on first start"
  fi
  ok "web UI ready (frontend :18100, backend :18109)"
}

# ---- 5. Ink TUI (deps + build; POSIX only) -----------------------------------
install_tui() {
  [ "$MINIMAL" = "1" ] && { warn "skipping TUI build (--minimal)"; return 0; }
  command -v npm >/dev/null 2>&1 || { warn "npm missing — skipping TUI"; return 0; }
  [ -f "$HOST_ROOT/cli/package.json" ] || return 0
  step "installing + building Ink TUI (cli/)"
  ( cd "$HOST_ROOT/cli" && npm install && npm run build )
  ok "TUI built (cli/dist/index.js)"
}

# ---- 7. default extras: [all] = browser + channels ----------------------------
install_default_extras() {
  [ "$MINIMAL" = "1" ] && { warn "skipping default extras (--minimal)"; return 0; }
  step "installing default extras [all] (browser tool + channels)"
  PIP install -e "$HOST_ROOT[all]"
  step "fetching Playwright Chromium (~150MB)"
  "$PY" -m playwright install chromium || warn "playwright chromium download failed — run '\"$PY\" -m playwright install chromium' later (needs network)"
}

# ---- 8. heavier opt-in extras: stealth browsers / agent-browser ---------------
install_extras() {
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
}

# ---- run --------------------------------------------------------------------
step "OpenProgram setup  (os=$OS, minimal=$MINIMAL)"
install_web
install_tui
install_default_extras
install_extras

# ---- done --------------------------------------------------------------------
printf "\n${c_green}OpenProgram ready.${c_reset}\n"
printf "  Start:     openprogram           # first run walks you through provider setup, then opens the chat\n"
printf "  Web UI:    openprogram web        # -> http://localhost:18100\n"
printf "  Programs:  pick which agentic programs to install in the first-run wizard\n"
printf "             (or any time: openprogram programs install <gui|research|wiki|all>)\n"
