#!/usr/bin/env bash
set -euo pipefail

# Install "/Applications/OpenProgram Dev.app": the Desktop shell of this
# checkout over the development instance (profile `dev`, port 18200,
# ~/.openprogram-dev). It carries no runtime of its own; the worker runs from
# the runtime scripts/refresh-dev-instance.sh keeps under
# ~/.openprogram-dev/runtime, and Resources/instance.json points the shell at
# it (apps/desktop/main/instance.js). The shell is built whole from this
# checkout (scripts/release/build-desktop-shell.sh), Electron included, so a
# new Electron runs here before promotion brings it to the stable App. The
# stable App, its runtime and its worker are never touched.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dev_app="/Applications/OpenProgram Dev.app"
dev_name="OpenProgram Dev"
dev_identifier="ai.openprogram.desktop.dev"
state_dir="$HOME/.openprogram-dev"
port=18200
# The stable App's browser debugging port is 9223 (apps/desktop/main/instance.js).
debug_port=9224
local_python="${OPENPROGRAM_LOCAL_PYTHON:-$(command -v python3)}"

stamp() {
  printf '[dev app %s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

test "$(uname -s)" = Darwin || { printf 'the development App is macOS only\n' >&2; exit 1; }
test -f "$state_dir/runtime/runtime-manifest.json" || {
  printf 'the development runtime is missing; run scripts/refresh-dev-instance.sh\n' >&2
  exit 1
}
revision="$(git -C "$repo_root" rev-parse HEAD)"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-dev-app.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
stage="$work_dir/$dev_name.app"

stamp "building the Desktop shell of ${revision:0:10}"
OPENPROGRAM_LOCAL_PYTHON="$local_python" \
  "$repo_root/scripts/release/build-desktop-shell.sh" "$work_dir/shell"
mv "$work_dir/shell/OpenProgram.app" "$stage"
plist="$stage/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string "$dev_identifier" "$plist"
# CFBundleName stays: Electron finds "<CFBundleName> Helper.app" by it.
plutil -replace CFBundleDisplayName -string "$dev_name" "$plist"
"$local_python" -I - "$stage/Contents/Resources/instance.json" "$dev_name" "$port" "$debug_port" "$state_dir/runtime" <<'PY'
import json, sys
path, name, port, debug_port, runtime = sys.argv[1:]
with open(path, "w", encoding="utf-8") as stream:
    json.dump({"name": name, "profile": "dev", "port": int(port),
               "debugPort": int(debug_port), "runtime": runtime},
              stream, indent=2)
PY
printf '%s\n' "$revision" > "$stage/Contents/Resources/openprogram-source-revision"
"$local_python" "$repo_root/scripts/release/local-macos-signing.py" prepare --development --app "$stage"
"$local_python" "$repo_root/scripts/release/local-macos-signing.py" sign --development --app "$stage"
codesign --verify --deep --strict "$stage"

# Swap the bundle while the App is closed, and bring it back if it was open.
was_running=0
was_front=0
if pgrep -f "^${dev_app}/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1; then
  was_running=1
  if lsappinfo info -only bundleid "$(lsappinfo front 2>/dev/null)" 2>/dev/null | grep -q "\"$dev_identifier\""; then
    was_front=1
  fi
  stamp "quitting the development App"
  osascript -e "tell application id \"$dev_identifier\" to quit" >/dev/null 2>&1 || true
  for _ in $(seq 1 100); do
    pgrep -f "^${dev_app}/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1 || break
    sleep 0.1
  done
  pkill -f "^${dev_app}/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1 || true
fi
rm -rf "$dev_app.previous"
test ! -e "$dev_app" || mv "$dev_app" "$dev_app.previous"
mv "$stage" "$dev_app"
rm -rf "$dev_app.previous"
# Launch Services caches bundle names and icons by modification date.
touch "$dev_app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$dev_app" >/dev/null 2>&1 || true
if test "$was_running" = 1; then
  if test "$was_front" = 1; then
    open -a "$dev_app" || true
  else
    # It comes back behind what the user is doing: the App activates itself
    # when its window is ready, and the front is handed straight back.
    "$local_python" "$repo_root/openprogram/host/_platform/front.py" "$dev_app" 30 >/dev/null 2>&1 &
    sleep 0.3
    open -g -a "$dev_app" || true
  fi
fi
printf 'installed %s from %s\n' "$dev_app" "$revision"
