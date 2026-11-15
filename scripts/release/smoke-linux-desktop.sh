#!/usr/bin/env bash
# The Linux App on clean Ubuntu 22.04 and 24.04, under Xvfb, with no Git on the
# machine: first start unpacks the seed, the worker answers /healthz and /chat,
# node-pty loads, the launcher runs, and the .desktop entry is named properly.
#
#   smoke-linux-desktop.sh DIST_DIRECTORY x86_64|arm64
#
# Needs Docker. Each container is thrown away afterwards.
set -euo pipefail

fail() { printf 'smoke-linux-desktop: %s\n' "$1" >&2; exit 1; }

health_port=18100

# Waits for the started App, then checks what its first run produced.
check_first_start() {
  local label="$1" app_process="$2" home="$3"
  local root="$home/.openprogram/runtime" ready=0 gone=0
  for _attempt in $(seq 1 600); do
    if curl -fsS "http://127.0.0.1:$health_port/healthz" >"$home/health.json" 2>/dev/null; then
      ready=1
      break
    fi
    # The first start unpacks the seed and restarts the App, so one exit is normal.
    if pgrep -f openprogram-desktop >/dev/null 2>&1; then
      gone=0
    else
      gone=$((gone + 1))
      test "$gone" -lt 15 || break
    fi
    sleep 1
  done
  if test "$ready" != 1; then
    sed -n '1,200p' "$home/app.log" >&2 || true
    sed -n '1,200p' "$home/.openprogram/worker.log" >&2 || true
    fail "$label: the worker never became healthy"
  fi
  grep -q '"status":"ok"' "$home/health.json" || fail "$label: /healthz is not ok"
  test "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$health_port/chat")" = 200 \
    || fail "$label: /chat did not load"
  test -f "$root/current" || fail "$label: the seed set no current version"
  test -x "$root/identity/openprogram-launcher" || fail "$label: the launcher is missing"
  # Through the launcher, so a newer glibc than this machine's would fail here.
  HOME="$home" "$root/bin/openprogram" --version || fail "$label: the launcher does not run on $(. /etc/os-release; echo "$VERSION_ID")"
}

stop_app() {
  local app_process="$1" home="$2"
  HOME="$home" "$home/.openprogram/runtime/bin/openprogram" worker stop >/dev/null 2>&1 || true
  kill -TERM -- "-$app_process" 2>/dev/null || true
  pkill -TERM -f openprogram-desktop 2>/dev/null || true
  sleep 2
  pkill -KILL -f openprogram-desktop 2>/dev/null || true
  wait "$app_process" 2>/dev/null || true
}

check_node_pty() {
  local executable="$1"
  local module
  module="$(dirname "$(readlink -f "$executable")")/resources/app.asar.unpacked/node_modules/node-pty"
  test -d "$module" || fail "node-pty is not unpacked next to the App: $module"
  OPENPROGRAM_NODE_PTY="$module" ELECTRON_RUN_AS_NODE=1 "$executable" -e '
    const pty = require(process.env.OPENPROGRAM_NODE_PTY);
    const terminal = pty.spawn("/bin/sh", ["-c", "echo pty-works"], { cols: 80, rows: 24 });
    let output = "";
    terminal.onData((data) => { output += data; });
    terminal.onExit(() => process.exit(output.includes("pty-works") ? 0 : 1));
    setTimeout(() => process.exit(2), 15000);
  ' || fail 'node-pty could not start a terminal'
}

check_desktop_entry() {
  local entry=/usr/share/applications/ai.openprogram.OpenProgram.desktop
  test -f "$entry" || fail "the desktop entry is missing or misnamed: $entry"
  cat "$entry"
  grep -qx 'Name=OpenProgram' "$entry" || fail 'the desktop entry has the wrong name'
  grep -q '^StartupWMClass=' "$entry" || fail 'the desktop entry has no window class'
  local program
  # The first Exec line is the App; later ones are its menu actions.
  program="$(sed -n 's/^Exec=\([^ ]*\).*/\1/p' "$entry" | head -n 1 | tr -d '"')"
  test -x "$program" || fail "the desktop entry starts something that does not exist: $program"
}

# --- inside a clean container -------------------------------------------------
inside() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --no-install-recommends --yes ca-certificates curl procps xvfb "/dist/$OPENPROGRAM_DEB"
  # The package may depend on Git; the App must also work on a machine without it.
  dpkg --remove --force-depends git git-man >/dev/null 2>&1 || true
  if command -v git >/dev/null 2>&1; then fail 'Git is still on this machine'; fi

  check_desktop_entry
  check_node_pty /opt/OpenProgram/openprogram-desktop

  # A display of its own, because the App's restart must outlive the first process.
  Xvfb :99 -screen 0 1280x800x24 >/dev/null 2>&1 &
  export DISPLAY=:99
  local home pid
  home="$(mktemp -d /tmp/openprogram-deb.XXXXXX)"
  HOME="$home" setsid /opt/OpenProgram/openprogram-desktop --no-sandbox >"$home/app.log" 2>&1 &
  pid=$!
  check_first_start deb "$pid" "$home"
  stop_app "$pid" "$home"

  if test -n "${OPENPROGRAM_APPIMAGE:-}"; then
    home="$(mktemp -d /tmp/openprogram-appimage.XXXXXX)"
    HOME="$home" APPIMAGE_EXTRACT_AND_RUN=1 setsid "/dist/$OPENPROGRAM_APPIMAGE" --no-sandbox >"$home/app.log" 2>&1 &
    pid=$!
    check_first_start AppImage "$pid" "$home"
    stop_app "$pid" "$home"
  fi
  printf 'Linux App smoke passed on Ubuntu %s\n' "$(. /etc/os-release; echo "$VERSION_ID")"
}

if test "${1:-}" = --inside; then
  inside
  exit 0
fi

# --- on the build machine -----------------------------------------------------
dist="${1:?usage: smoke-linux-desktop.sh DIST_DIRECTORY x86_64|arm64}"
arch="${2:?usage: smoke-linux-desktop.sh DIST_DIRECTORY x86_64|arm64}"
case "$arch" in
  x86_64) deb_arch=amd64 ;;
  arm64) deb_arch=arm64 ;;
  *) fail "unsupported architecture: $arch" ;;
esac
command -v docker >/dev/null 2>&1 || fail 'docker is required'
dist="$(cd "$dist" && pwd -P)"
deb="$(find "$dist" -maxdepth 1 -type f -name "*-linux-$deb_arch.deb" -exec basename {} \; -quit)"
test -n "$deb" || fail "no deb package for $arch in $dist"
appimage="$(find "$dist" -maxdepth 1 -type f -name "*-linux-$arch.AppImage" -exec basename {} \; -quit)"
if test -z "$appimage"; then
  printf 'No AppImage for %s: checking the deb package alone.\n' "$arch"
fi
script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"

for image in ubuntu:22.04 ubuntu:24.04; do
  printf '=== %s\n' "$image"
  docker run --rm --pull always \
    --mount "type=bind,src=$dist,dst=/dist,readonly" \
    --mount "type=bind,src=$script,dst=/smoke-linux-desktop.sh,readonly" \
    --env "OPENPROGRAM_DEB=$deb" \
    --env "OPENPROGRAM_APPIMAGE=$appimage" \
    "$image" bash /smoke-linux-desktop.sh --inside
done
