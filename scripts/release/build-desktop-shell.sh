#!/usr/bin/env bash
set -euo pipefail

# Build the macOS Desktop shell of this checkout: OpenProgram.app with the
# Electron apps/desktop/package.json pins, its native modules and this
# checkout's app.asar, but no runtime. The installed Apps take their Electron
# from it: scripts/install-dev-app.sh installs it whole as the development
# App, and scripts/refresh-local-app.sh moves its Electron into the stable App
# when the pinned version changed.
#
#   build-desktop-shell.sh OUTPUT_DIR    → OUTPUT_DIR/OpenProgram.app

output_dir="$1"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
desktop_dir="$repo_root/apps/desktop"
local_python="${OPENPROGRAM_LOCAL_PYTHON:-$(command -v python3)}"

test "$(uname -s)" = Darwin || { printf 'the Desktop shell build is macOS only\n' >&2; exit 1; }
if ! test -x "$repo_root/node_modules/.bin/electron-builder"; then
  (cd "$repo_root" && npm ci --ignore-scripts)
fi
# Installs that skip scripts leave Electron without its binary; its own
# installer downloads it against the package's checksums.
if ! test -f "$repo_root/node_modules/electron/dist/version"; then
  node "$repo_root/node_modules/electron/install.js"
fi
pinned="$(node -p "require('$desktop_dir/package.json').devDependencies.electron")"
test "$(cat "$repo_root/node_modules/electron/dist/version")" = "$pinned" || {
  printf 'node_modules has Electron %s, package.json pins %s; run npm ci\n' \
    "$(cat "$repo_root/node_modules/electron/dist/version")" "$pinned" >&2
  exit 1
}

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-desktop-shell.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
# The release configuration without the runtime, which each App keeps.
"$local_python" - "$desktop_dir/package.json" "$work_dir/config.json" "$work_dir/out" <<'PY'
import json, sys
package, config_path, output = sys.argv[1:]
with open(package, encoding="utf-8") as stream:
    config = json.load(stream)["build"]
config["extraResources"] = [entry for entry in config.get("extraResources", [])
                            if entry.get("to") != "runtime"]
# The reopen protocol and the signing exclusions describe the runtime; the
# installers write the protocol once the runtime is in place.
config.pop("afterPack", None)
config["directories"] = {**config.get("directories", {}), "output": output}
mac = {key: value for key, value in config.get("mac", {}).items() if key != "signIgnore"}
config["mac"] = {**mac, "identity": "-", "target": ["dir"]}
with open(config_path, "w", encoding="utf-8") as stream:
    json.dump(config, stream)
PY
(cd "$desktop_dir" && "$repo_root/node_modules/.bin/electron-builder" \
  --dir --mac --publish never --config "$work_dir/config.json")
built="$(find "$work_dir/out" -maxdepth 2 -type d -name OpenProgram.app -print -quit)"
test -n "$built" || { printf 'electron-builder produced no OpenProgram.app\n' >&2; exit 1; }
mkdir -p "$output_dir"
rm -rf "$output_dir/OpenProgram.app"
ditto "$built" "$output_dir/OpenProgram.app"
