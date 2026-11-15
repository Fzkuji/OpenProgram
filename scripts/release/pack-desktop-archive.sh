#!/usr/bin/env bash
set -euo pipefail

# Pack this checkout's Desktop shell into an app.asar, on top of an installed
# archive: its node_modules (node-pty is native and is not rebuilt here) stay,
# and every module apps/desktop/package.json build.files names is replaced.
# scripts/refresh-local-app.sh uses it for the stable App; the development App
# is built whole (scripts/release/build-desktop-shell.sh).
#
#   pack-desktop-archive.sh INSTALLED_ASAR STAGE_DIR OUTPUT_ASAR
#
# OPENPROGRAM_LOCAL_PYTHON names the Python that runs the helpers (default
# python3); the asar tool comes from the repository's node_modules.

installed_asar="$1"
desktop_stage="$2"
desktop_asar="$3"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
local_python="${OPENPROGRAM_LOCAL_PYTHON:-$(command -v python3)}"

asar_cli="$repo_root/node_modules/@electron/asar/bin/asar.js"
if ! test -f "$asar_cli"; then
  (cd "$repo_root" && npm ci --ignore-scripts)
fi
test -f "$asar_cli" || {
  printf 'the Electron asar tool was not installed: %s\n' "$asar_cli" >&2
  exit 1
}

# Copy every file apps/desktop/package.json build.files names: a relative
# file, or a whole folder written "folder/**" (dotfiles included). Do not
# duplicate that list by hand — that is how window-lifecycle.js was omitted
# from the packaged asar.
desktop_files="$(
  "$local_python" - "$repo_root/apps/desktop/package.json" <<'PY'
import json
import sys
from pathlib import Path, PurePosixPath

package = Path(sys.argv[1])
with open(package, encoding="utf-8") as stream:
    files = json.load(stream)["build"]["files"]
for name in files:
    if not isinstance(name, str) or not name:
        raise SystemExit("build.files must contain relative file paths")
    folder = name[:-3] if name.endswith("/**") else None
    path = PurePosixPath(folder or name)
    if path.is_absolute() or ".." in path.parts or any(char in str(path) for char in "*?[]!\\\n\r"):
        raise SystemExit(f"unsupported desktop module path: {name!r}")
    if folder is None:
        print(name)
        continue
    root = package.parent / folder
    if not root.is_dir():
        raise SystemExit(f"desktop folder listed in build.files is missing: {name!r}")
    for found in sorted(item for item in root.rglob("*") if item.is_file()):
        print(found.relative_to(package.parent).as_posix())
PY
)"
test -n "$desktop_files" || {
  printf 'apps/desktop/package.json build.files listed no modules\n' >&2
  exit 1
}

rm -rf "$desktop_stage"
node "$asar_cli" extract "$installed_asar" "$desktop_stage"
"$local_python" "$repo_root/scripts/release/restore-asar-permissions.py" \
  "$installed_asar" "$desktop_stage"
# main/ is replaced whole, so a module removed from it does not linger.
rm -rf "$desktop_stage/main"
while IFS= read -r desktop_file; do
  test -n "$desktop_file" || continue
  source_file="$repo_root/apps/desktop/$desktop_file"
  test -f "$source_file" || {
    printf 'desktop module listed in build.files is missing: %s\n' \
      "$desktop_file" >&2
    exit 1
  }
  mkdir -p "$(dirname "$desktop_stage/$desktop_file")"
  cp "$source_file" "$desktop_stage/$desktop_file"
done <<<"$desktop_files"
# The installed package.json stays (electron-builder trims it), but its
# version is this checkout's: app.getVersion() reads it, and the packaged
# worker refuses a runtime whose version differs, so after a version change
# the App would open no window.
"$local_python" - "$repo_root/apps/desktop/package.json" "$desktop_stage/package.json" <<'PY'
import json
import sys
from pathlib import Path

with open(sys.argv[1], encoding="utf-8") as stream:
    version = json.load(stream).get("version")
target = Path(sys.argv[2])
if version is None or not target.is_file():
    raise SystemExit(0)
with open(target, encoding="utf-8") as stream:
    staged = json.load(stream)
staged["version"] = version
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    json.dump(staged, stream, indent=2)
    stream.write("\n")
PY
rm -f "$desktop_stage/browser-extension-manager.js" \
  "$desktop_stage/self-update-ui-test-object.js"
# The installed asar may still hold modules from before they moved into
# main/; only the files build.files names stay at the top level.
for staged_module in "$desktop_stage"/*.js; do
  test -f "$staged_module" || continue
  grep -qxF "$(basename "$staged_module")" <<<"$desktop_files" \
    || rm -f "$staged_module"
done
rm -rf "$desktop_stage/tab-transfer" "$desktop_stage/shell-integration"
for obsolete_extension_module in \
  extract-zip debug ms get-stream pump end-of-stream once wrappy \
  yauzl fd-slicer pend buffer-crc32; do
  rm -rf "$desktop_stage/node_modules/$obsolete_extension_module"
done
node "$asar_cli" pack "$desktop_stage" "$desktop_asar" \
  --unpack-dir node_modules/node-pty
