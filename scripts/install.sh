#!/usr/bin/env bash
# =============================================================================
# OpenProgram — source-development installer (macOS / Linux)
# -----------------------------------------------------------------------------
# This script creates an editable checkout and builds local assets. It is for
# contributors and source development, not stable release installation. See
# docs/install/install.md for the macOS DMG and managed CLI installation.
# It can still be run straight off the web for a development checkout:
#   curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash
# It clones OpenProgram to ~/OpenProgram (override with --target DIR), then
# hands off to the cloned copy. Already inside a checkout? It skips the clone
# and installs in place.
#
# The default install brings up EVERYTHING `openprogram` ships with:
#   1. System toolchain: Python 3.11+, Node 20+, git (installed if missing)
#   2. Python env (uses an active venv/conda, else creates ./.venv)
#   3. OpenProgram (editable) + its deps
#   4. Web UI:   web/ -> npm install && npm run build  (served on :18100)
#   5. TUI:      apps/cli/ -> npm install && npm run build  (Ink TUI; POSIX)
#   6. Product extras [all,search] + Playwright Chromium
#   7. GUI / Research / Wiki first-party Programs, default OCR/model data,
#      and the Research PDF dependency
#
# The GUI harness's torch build is whatever pip resolves. If you need an
# explicit CUDA/CPU variant, run the harness's own installer afterwards:
#   openprogram/programs/applications/gui_harness/scripts/install.sh --cuda cu124
#
# Re-runnable: every step is idempotent.
#
# Usage:
#   curl -fsSL .../scripts/install.sh | bash    # full development install
#   ./scripts/install.sh                   # full install (everything above)
#   ./scripts/install.sh --python /p/bin/python   # pick the interpreter
#   ./scripts/install.sh --stealth         # + stealth browsers (patchright/camoufox)
#   ./scripts/install.sh --agent-browser   # + agent-browser (global npm)
#   ./scripts/install.sh --target DIR      # where to clone when run off the web (default ~/OpenProgram)
#   ./scripts/install.sh --yes             # skip every prompt, use defaults (-y)
#
# AI-agent / non-interactive: pass --yes (or set CI / DEBIAN_FRONTEND=noninteractive
# / OPENPROGRAM_INSTALL_YES) to take every default with no prompts. Even without
# it, no prompt can hang: each /dev/tty read times out after 60s (override with
# OPENPROGRAM_PROMPT_TIMEOUT=<seconds>) and falls back to the default.
#   curl -fsSL .../scripts/install.sh | bash -s -- --yes
# =============================================================================
set -euo pipefail

c_blue='\033[1;34m'; c_green='\033[1;32m'; c_yellow='\033[1;33m'; c_red='\033[1;31m'; c_reset='\033[0m'
step() { printf "${c_blue}==>${c_reset} %s\n" "$*"; }
ok()   { printf "${c_green}  ok${c_reset} %s\n" "$*"; }
warn() { printf "${c_yellow}  !!${c_reset} %s\n" "$*" >&2; }
die()  { printf "${c_red}ERROR${c_reset} %s\n" "$*" >&2; exit 1; }

OS="$(uname -s)"
REPO_URL="https://github.com/Fzkuji/OpenProgram.git"

# Every /dev/tty prompt reads with this timeout so an agent driving the default
# command inside a pty (and never answering) can't hang forever — on expiry we
# default exactly as if Enter was pressed. Override for tests via the env var.
PROMPT_TIMEOUT_SECONDS="${OPENPROGRAM_PROMPT_TIMEOUT:-60}"

# Standard non-interactive signals — treated exactly like --yes (defaults, no
# prompts). CI/DEBIAN_FRONTEND=noninteractive are ecosystem conventions;
# OPENPROGRAM_INSTALL_YES is our own escape hatch.
is_noninteractive() {
  [ "$ASSUME_YES" = "1" ] && return 0
  [ -n "${CI:-}" ] && return 0
  [ "${DEBIAN_FRONTEND:-}" = "noninteractive" ] && return 0
  [ -n "${OPENPROGRAM_INSTALL_YES:-}" ] && return 0
  return 1
}

# tty_prompt "<prompt>" -> echoes the user's line, or empty on timeout/EOF.
# read -t returns >128 on timeout; the || true keeps set -e happy either way.
tty_prompt() {
  local reply=""
  printf '%s' "$1" > /dev/tty
  if ! read -t "$PROMPT_TIMEOUT_SECONDS" -r reply < /dev/tty; then
    reply=""
    printf '\n(no input in %ss — using default)\n' "$PROMPT_TIMEOUT_SECONDS" > /dev/tty
  fi
  printf '%s' "$reply"
}

# When piped (curl | bash) BASH_SOURCE is "bash" or empty and dirname resolves
# to "." — so a real checkout is detected by pyproject.toml sitting next to us,
# not by the path alone.
SCRIPT_SRC="${BASH_SOURCE[0]:-}"
if [ -n "$SCRIPT_SRC" ] && [ -f "$SCRIPT_SRC" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
  HOST_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  SCRIPT_DIR=""; HOST_ROOT=""
fi

# ---- args -------------------------------------------------------------------
PYTHON_BIN=""; WITH_STEALTH=0; WITH_AGENT_BROWSER=0
CLONE_TARGET=""; ASSUME_YES=0; BOOTSTRAPPED=0; BOOTSTRAP_ONLY=0
FORWARD_ARGS=()   # rebuilt to forward across the bootstrap exec
while [ $# -gt 0 ]; do
  case "$1" in
    --minimal) die "--minimal was removed: development installs include the complete product" ;;
    --python) PYTHON_BIN="${2:?--python needs a path}"; FORWARD_ARGS+=("$1" "$2"); shift 2 ;;
    --stealth) WITH_STEALTH=1; FORWARD_ARGS+=("$1"); shift ;;
    --agent-browser) WITH_AGENT_BROWSER=1; FORWARD_ARGS+=("$1"); shift ;;
    --programs) [ $# -ge 2 ] || die "--programs needs a value"; warn "--programs is no longer needed; all first-party Programs are installed"; shift 2 ;;
    --target) CLONE_TARGET="${2:?--target needs a directory}"; shift 2 ;;   # consumed by bootstrap, not forwarded
    -y|--yes) ASSUME_YES=1; FORWARD_ARGS+=("$1"); shift ;;
    --bootstrapped) BOOTSTRAPPED=1; shift ;;   # internal: child skips re-bootstrapping
    --bootstrap-only) BOOTSTRAP_ONLY=1; shift ;;   # internal/test: clone + exec --help, then stop
    -h|--help) sed -n '/^# Usage:/,/^# ==/p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

# ---- 0. self-bootstrap (clone + re-exec when not inside a checkout) ----------
is_openprogram_checkout() { [ -f "$1/pyproject.toml" ] && [ -f "$1/scripts/install.sh" ] && grep -q '^name = "openprogram"' "$1/pyproject.toml" 2>/dev/null; }

if [ "$BOOTSTRAPPED" = "0" ] && { [ -z "$HOST_ROOT" ] || ! is_openprogram_checkout "$HOST_ROOT"; }; then
  command -v git >/dev/null 2>&1 || die "git is required to install off the web — install git first (macOS: brew install git; Debian/Ubuntu: sudo apt-get install git), or clone the repo and run scripts/install.sh from inside it."

  target="${CLONE_TARGET:-$HOME/OpenProgram}"
  if [ -z "$CLONE_TARGET" ] && ! is_noninteractive && [ -r /dev/tty ] && [ -w /dev/tty ] && { : </dev/tty; } 2>/dev/null; then
    reply="$(tty_prompt "$(printf 'Clone OpenProgram to [%s]: ' "$target")")"
    [ -n "$reply" ] && target="$reply"
  fi
  # Expand a leading ~ (read gives a literal tilde).
  case "$target" in "~") target="$HOME" ;; "~/"*) target="$HOME/${target#\~/}" ;; esac

  if [ -e "$target" ]; then
    if is_openprogram_checkout "$target"; then
      step "reusing existing OpenProgram checkout at $target"
      ( cd "$target" && git pull --ff-only ) || warn "git pull --ff-only failed — using the checkout as-is"
    else
      die "target exists but is not an OpenProgram checkout: $target (remove it or pass --target DIR)"
    fi
  else
    step "cloning OpenProgram into $target"
    git clone --depth 1 "$REPO_URL" "$target" || die "git clone failed: $REPO_URL"
  fi

  child="$target/scripts/install.sh"
  [ -f "$child" ] || die "cloned repo has no scripts/install.sh — unexpected layout at $target"
  if [ "$BOOTSTRAP_ONLY" = "1" ]; then
    step "bootstrap-only: handing off to $child --help"
    exec bash "$child" --bootstrapped --help
  fi
  step "handing off to the cloned installer: $child"
  exec bash "$child" --bootstrapped ${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}
fi

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
  command -v npm >/dev/null 2>&1 || die "npm is required to build the Web UI"
  [ -f "$HOST_ROOT/web/package.json" ] || die "web/package.json is missing"
  step "installing web UI deps (web/ — Next.js)"
  ( cd "$HOST_ROOT/web" && npm install )
  step "building web production bundle"
  ( cd "$HOST_ROOT/web" && npm run build )
  ok "web UI ready (single port :18100)"
}

# ---- 5. Ink TUI (deps + build; POSIX only) -----------------------------------
install_tui() {
  command -v npm >/dev/null 2>&1 || { warn "npm missing — skipping TUI"; return 0; }
  [ -f "$HOST_ROOT/apps/cli/package.json" ] || return 0
  step "installing + building Ink TUI (apps/cli/)"
  ( cd "$HOST_ROOT/apps/cli" && npm install && npm run build )
  ok "TUI built (apps/cli/dist/index.js)"
}

# ---- 7. default extras: [all] = browser + channels ----------------------------
install_default_extras() {
  step "installing product extras [all,search]"
  PIP install -e "$HOST_ROOT[all,search]"
  step "fetching Playwright Chromium (~150MB)"
  "$PY" -m playwright install chromium
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

# ---- 9. complete first-party Programs ---------------------------------------
install_first_party_programs() {
  step "installing GUI, Research, and Wiki Programs"
  "$PY" -m openprogram programs install all
  local applications="$HOST_ROOT/openprogram/programs/applications"
  local gui_installer="$applications/gui_harness/scripts/install.sh"
  [ -f "$gui_installer" ] || die "GUI Program source is missing after install"
  bash "$gui_installer" --no-host --python "$PY"
  PIP install -e "$applications/research_harness[pdf]"

  local gpa_model="${GPA_MODEL_PATH:-$HOME/GPA-GUI-Detector/model.pt}"
  [ -s "$gpa_model" ] || die "GPA detector model is missing: $gpa_model"
  "$PY" - <<'PY'
from pathlib import Path

import discord
import easyocr
import easyocr.config
import pymupdf
import qrcode
import semble
import slack_sdk
from openprogram.programs._programs import import_installed_programs

if not any(Path(easyocr.config.MODULE_PATH).rglob("*.pth")):
    raise SystemExit("default EasyOCR model data is missing")
required = {"gui_agent", "research_agent", "wiki_agent"}
registered = set(import_installed_programs())
missing = required - registered
if missing:
    raise SystemExit(f"first-party Programs failed to register: {sorted(missing)}")
PY
  ok "complete first-party Programs ready"
}

# ---- run --------------------------------------------------------------------
step "OpenProgram development setup  (os=$OS)"
install_web
install_tui
install_default_extras
install_extras
install_first_party_programs

# ---- done --------------------------------------------------------------------
printf "\n${c_green}OpenProgram ready.${c_reset}\n"
printf "  Start:     openprogram           # first run walks you through provider setup, then opens the chat\n"
printf "  Web UI:    openprogram web        # -> http://localhost:18100\n"
printf "  Programs:  GUI, Research, and Wiki are installed; developer backend flags are additive\n"
