#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app_path="${OPENPROGRAM_APP_PATH:-/Applications/OpenProgram.app}"
runtime_root="$app_path/Contents/Resources/runtime"
manifest="$runtime_root/runtime-manifest.json"
installed_asar="$app_path/Contents/Resources/app.asar"
uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"

if test -n "${OPENPROGRAM_LOCAL_PYTHON:-}"; then
  local_python="$OPENPROGRAM_LOCAL_PYTHON"
else
  openprogram_bin="$(command -v openprogram || true)"
  local_python=""
  if test -n "$openprogram_bin"; then
    local_python="$(sed -n '1s/^#!//p' "$openprogram_bin")"
  fi
  if test -z "$local_python"; then
    local_python="$(command -v python3 || true)"
  fi
fi

test -n "$uv_bin" && test -x "$uv_bin" || {
  printf 'uv is required to refresh the local App\n' >&2
  exit 1
}
test -n "$local_python" && test -x "$local_python" || {
  printf 'the local OpenProgram Python executable was not found: %s\n' \
    "$local_python" >&2
  exit 1
}
test -f "$manifest" || {
  printf 'the installed App runtime manifest was not found: %s\n' "$manifest" >&2
  exit 1
}
test -f "$installed_asar" || {
  printf 'the installed App archive was not found: %s\n' "$installed_asar" >&2
  exit 1
}

"$local_python" "$repo_root/scripts/release/verify-release-version.py" \
  --installed-app "$app_path" --require-source-match

app_python_relative="$("$local_python" - "$manifest" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["python"])
PY
)"
app_python="$runtime_root/$app_python_relative"
case "$app_python" in
  "$runtime_root"/*) ;;
  *) printf 'the App Python path escapes its runtime: %s\n' "$app_python" >&2; exit 1 ;;
esac
test -x "$app_python" || {
  printf 'the App Python executable was not found: %s\n' "$app_python" >&2
  exit 1
}

remove_stale_package_tree() {
  local python_executable="$1"
  "$python_executable" -I \
    "$repo_root/scripts/release/remove-stale-openprogram-packages.py" \
    "$python_executable"
}

validate_stale_package_tree() {
  local python_executable="$1"
  "$python_executable" -I \
    "$repo_root/scripts/release/remove-stale-openprogram-packages.py" \
    "$python_executable" --check
}

wheel_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-local-wheel.XXXXXX")"
install_lock_file="$(dirname -- "$app_path")/.openprogram-app-install.lock"
install_lock_owned=0
acquire_pid_lock() {
  local path="$1"
  if test -x /usr/bin/shlock; then
    /usr/bin/shlock -p "$$" -f "$path"
  else
    (set -o noclobber; printf '%s\n' "$$" > "$path") 2>/dev/null
  fi
}
release_install_lock() {
  if test "$install_lock_owned" = 1 && \
    test "$(sed -n '1p' "$install_lock_file" 2>/dev/null || :)" = "$$"; then
    rm -f "$install_lock_file" || :
  fi
}
cleanup() {
  release_install_lock
  rm -rf "$wheel_dir"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

asar_cli="$repo_root/node_modules/@electron/asar/bin/asar.js"
if ! test -f "$asar_cli"; then
  (cd "$repo_root" && npm ci --ignore-scripts)
fi
test -f "$asar_cli" || {
  printf 'the Electron asar tool was not installed: %s\n' "$asar_cli" >&2
  exit 1
}

# Copy every top-level file named in apps/desktop/package.json build.files.
# Do not duplicate that list by hand — that is how window-lifecycle.js
# was omitted from the packaged asar.
desktop_files="$(
  "$local_python" - "$repo_root/apps/desktop/package.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    files = json.load(stream)["build"]["files"]
for name in files:
    if not isinstance(name, str) or "/" in name or "!" in name:
        continue
    print(name)
PY
)"
test -n "$desktop_files" || {
  printf 'apps/desktop/package.json build.files listed no top-level modules\n' >&2
  exit 1
}

attempt=0
while true; do
  attempt=$((attempt + 1))
  build_revision="$(git -C "$repo_root" rev-parse HEAD)"
  attempt_dir="$wheel_dir/attempt-$attempt"
  mkdir -p "$attempt_dir"

  rm -rf "$repo_root/apps/desktop/dist"
  "$repo_root/scripts/release/stage-release-assets.sh"
  rm -rf "$repo_root/build"
  "$uv_bin" build --wheel --out-dir "$attempt_dir" "$repo_root"
  wheel="$(find "$attempt_dir" -maxdepth 1 -type f \
    -name 'openprogram-*.whl' -print -quit)"
  test -n "$wheel" || {
    printf 'OpenProgram wheel was not built\n' >&2
    exit 1
  }
  "$local_python" - "$wheel" <<'PY'
import re
import sys
import zipfile

wheel = sys.argv[1]
with zipfile.ZipFile(wheel) as archive:
    names = [name for name in archive.namelist() if name.endswith("_frontend/chat.html")]
    if not names:
        raise SystemExit(f"wheel is missing chat.html: {wheel}")
    html = archive.read(names[0]).decode("utf-8")
if 'aria-label="Authenticating"' in html:
    raise SystemExit(f"wheel chat.html still ships Authenticating: {wheel}")
start = html.lower().find("<body")
body = html[start:] if start >= 0 else html
match = re.search(r"<script[\s>]", body, flags=re.I)
paint = body[: match.start()] if match else body
if 'id="sidebar"' not in paint:
    raise SystemExit(f"wheel chat.html first-paint lacks id=\"sidebar\": {wheel}")
PY

  desktop_stage="$attempt_dir/desktop"
  desktop_asar="$attempt_dir/app.asar"
  node "$asar_cli" extract "$installed_asar" "$desktop_stage"
  while IFS= read -r desktop_file; do
    source_file="$repo_root/apps/desktop/$desktop_file"
    test -f "$source_file" || {
      printf 'desktop module listed in build.files is missing: %s\n' \
        "$desktop_file" >&2
      exit 1
    }
    cp "$source_file" "$desktop_stage/$desktop_file"
  done <<<"$desktop_files"
  rm -f "$desktop_stage/browser-extension-manager.js"
  for obsolete_extension_module in \
    extract-zip debug ms get-stream pump end-of-stream once wrappy \
    yauzl fd-slicer pend buffer-crc32; do
    rm -rf "$desktop_stage/node_modules/$obsolete_extension_module"
  done
  node "$asar_cli" pack "$desktop_stage" "$desktop_asar" \
    --unpack-dir node_modules/node-pty

  test "$(git -C "$repo_root" rev-parse HEAD)" = "$build_revision" && break
  printf 'HEAD changed during packaging; rebuilding the current checkout\n'
done

if ! acquire_pid_lock "$install_lock_file"; then
  lock_pid="$(sed -n '1p' "$install_lock_file" 2>/dev/null || :)"
  printf 'another OpenProgram App installation is running%s\n' \
    "${lock_pid:+ (pid $lock_pid)}" >&2
  exit 1
fi
install_lock_owned=1

# Re-read all mutable version sources under the same lock as the canonical App
# installer. The wheel is the immutable payload used by both pip operations.
"$local_python" "$repo_root/scripts/release/verify-release-version.py" \
  --installed-app "$app_path" --require-source-match --wheel "$wheel"

# Freeze the installer with this build before stopping the App. A new runtime
# must not snapshot an obsolete installer on its next conversational update.
installer_stage="$attempt_dir/install-app.sh"
test ! -L "$app_path/Contents/Resources/update" && \
  test ! -L "$app_path/Contents/Resources/update/install-app.sh" || {
  printf 'the installed update resources must not be symlinks\n' >&2
  exit 1
}
cp "$repo_root/apps/desktop/scripts/install-app.sh" "$installer_stage"

if pgrep -x OpenProgram >/dev/null 2>&1; then
  osascript -e 'tell application "OpenProgram" to quit' >/dev/null 2>&1 || true
  for _ in {1..50}; do
    pgrep -x OpenProgram >/dev/null 2>&1 || break
    sleep 0.2
  done
  if pgrep -x OpenProgram >/dev/null 2>&1; then
    pkill -TERM -x OpenProgram
    for _ in {1..50}; do
      pgrep -x OpenProgram >/dev/null 2>&1 || break
      sleep 0.2
    done
  fi
  pgrep -x OpenProgram >/dev/null 2>&1 && {
    printf 'OpenProgram did not quit before the refresh\n' >&2
    exit 1
  }
fi
"$local_python" -m openprogram worker stop >/dev/null 2>&1 || true

# A wheel reinstall does not remove files left by an older package layout.
# Validate both runtimes before deleting either, then remove only OpenProgram's
# validated package directories before reinstalling.
validate_stale_package_tree "$local_python"
validate_stale_package_tree "$app_python"
remove_stale_package_tree "$local_python"
remove_stale_package_tree "$app_python"
"$local_python" -m pip install --disable-pip-version-check \
  --no-deps --force-reinstall "$wheel"
"$app_python" -I -m pip install --disable-pip-version-check \
  --break-system-packages --no-deps --force-reinstall "$wheel"
cp "$desktop_asar" "$installed_asar"
if test -d "$desktop_asar.unpacked"; then
  rsync -a --delete "$desktop_asar.unpacked/" \
    "$app_path/Contents/Resources/app.asar.unpacked/"
fi
mkdir -p "$app_path/Contents/Resources/update"
cp "$installer_stage" "$app_path/Contents/Resources/update/install-app.sh"
node "$repo_root/apps/desktop/scripts/write-reopen-protocol.cjs" \
  --resources "$app_path/Contents/Resources"

revision="$build_revision"
if test -n "$(git -C "$repo_root" status --porcelain --untracked-files=no)"; then
  revision="$revision-dirty"
fi
printf '%s\n' "$revision" > \
  "$app_path/Contents/Resources/openprogram-source-revision"

# A KeepAlive launchd service can restart the worker while the wheel is still
# being replaced. Stop that interim process after installation so the next
# worker necessarily imports the refreshed runtime.
"$local_python" -m openprogram worker stop >/dev/null 2>&1

for _ in {1..50}; do
  curl -fsS http://127.0.0.1:18100/healthz >/dev/null 2>&1 && break
  "$local_python" -m openprogram worker start >/dev/null 2>&1 || true
  sleep 0.2
done
curl -fsS http://127.0.0.1:18100/healthz >/dev/null
if test "${OPENPROGRAM_REFRESH_BACKGROUND:-0}" = 1; then
  open -g -a "$app_path"
else
  open -a "$app_path"
fi

cleanup
trap - EXIT HUP INT TERM
printf 'refreshed %s from %s\n' "$app_path" "$revision"
