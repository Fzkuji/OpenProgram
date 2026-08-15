#!/usr/bin/env bash
set -euo pipefail

source_app="${1:-}"
[[ "$(uname -s)" == "Darwin" ]] || {
  printf 'OpenProgram App installation requires macOS\n' >&2
  exit 1
}
[[ -n "$source_app" ]] || {
  printf 'usage: %s /path/to/OpenProgram.app\n' "$0" >&2
  exit 2
}
[[ "$source_app" == /* ]] || source_app="$(cd -- "$(dirname -- "$source_app")" && pwd)/$(basename -- "$source_app")"

install_root="${DESTDIR:-}"
if [[ -n "$install_root" ]]; then
  [[ "$install_root" == /* && "$install_root" != "/" ]] || {
    printf 'DESTDIR must be an absolute staging root other than /\n' >&2
    exit 2
  }
  mkdir -p "$install_root"
  install_root="$(cd -- "$install_root" && pwd -P)"
  [[ "$install_root" != "/" ]] || {
    printf 'DESTDIR must not resolve to /\n' >&2
    exit 2
  }
  applications_dir="$install_root/Applications"
else
  applications_dir="/Applications"
fi
target_app="$applications_dir/OpenProgram.app"

validate_app() {
  local app_path="$1"
  local plist="$app_path/Contents/Info.plist"
  local identifier version executable runtime_manifest
  [[ -d "$app_path" && -f "$plist" ]] || return 1
  identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist" 2>/dev/null)" || return 1
  version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist" 2>/dev/null)" || return 1
  executable="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist" 2>/dev/null)" || return 1
  [[ "$identifier" == "ai.openprogram.desktop" ]] || return 1
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  [[ "$executable" == "OpenProgram" ]] || return 1
  [[ -x "$app_path/Contents/MacOS/OpenProgram" ]] || return 1
  [[ -f "$app_path/Contents/Resources/icon.icns" ]] || return 1
  runtime_manifest="$app_path/Contents/Resources/runtime/runtime-manifest.json"
  [[ -f "$runtime_manifest" ]] || return 1
  node - "$runtime_manifest" "$version" <<'NODE'
const fs = require("fs");
const manifest = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (manifest.schema !== 2 || manifest.openprogram !== process.argv[3]) {
  process.exit(1);
}
NODE
}

app_runtime_python() {
  local app_path="$1"
  node - "$app_path/Contents/Resources/runtime" <<'NODE'
const fs = require("fs");
const path = require("path");
const root = path.resolve(process.argv[2]);
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, "runtime-manifest.json"), "utf8"),
);
if (typeof manifest.python !== "string" || path.isAbsolute(manifest.python)) {
  process.exit(1);
}
const python = path.resolve(root, manifest.python);
if (!python.startsWith(`${root}${path.sep}`)) process.exit(1);
process.stdout.write(python);
NODE
}

wait_for_worker_health() {
  for _ in {1..120}; do
    if /usr/bin/curl --silent --fail --connect-timeout 1 --max-time 2 \
      "http://127.0.0.1:18100/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

validate_app "$source_app" || {
  printf 'invalid OpenProgram app bundle: %s\n' "$source_app" >&2
  exit 1
}
[[ "$source_app" != "$target_app" ]] || {
  printf 'source is already the canonical installed app\n' >&2
  exit 2
}

mkdir -p "$applications_dir"
install_lock_file="$applications_dir/.openprogram-app-install.lock"
install_lock_owned=0
release_install_lock() {
  if [[ "$install_lock_owned" == 1 && "$(sed -n '1p' "$install_lock_file" 2>/dev/null || :)" == "$$" ]]; then
    rm -f "$install_lock_file" || :
  fi
}
if ! /usr/bin/shlock -p "$$" -f "$install_lock_file"; then
  lock_pid="$(sed -n '1p' "$install_lock_file" 2>/dev/null || :)"
  printf 'another OpenProgram App installation is running%s\n' \
    "${lock_pid:+ (pid $lock_pid)}" >&2
  exit 1
fi
install_lock_owned=1
trap release_install_lock EXIT

transaction_dir="$(mktemp -d "$applications_dir/.openprogram-app-install.XXXXXX")"
staged_app="$transaction_dir/OpenProgram.app"
previous_app="$transaction_dir/previous.app"
old_moved=0
activated=0
app_was_running=0
worker_was_running=0
launchd_was_installed=0
resume_after_failure=0
preserve_transaction=0

cleanup() {
  local status="$?"
  if [[ "$status" != 0 && "$old_moved" == 1 && "$activated" == 1 && -d "$target_app" ]]; then
    if mv "$target_app" "$transaction_dir/failed.app"; then
      activated=0
    else
      preserve_transaction=1
      printf 'failed to move the new App aside; old App remains at %s\n' "$previous_app" >&2
    fi
  fi
  if [[ "$old_moved" == 1 && "$activated" == 0 && ! -e "$target_app" && -d "$previous_app" ]]; then
    if mv "$previous_app" "$target_app"; then
      old_moved=0
    else
      preserve_transaction=1
      printf 'failed to restore the old App; recover it from %s\n' "$previous_app" >&2
    fi
  fi
  if [[ "$status" != 0 && "$resume_after_failure" == 1 && "$preserve_transaction" == 0 && -d "$target_app" ]]; then
    restored_python="$(app_runtime_python "$target_app" 2>/dev/null || :)"
    if [[ "$launchd_was_installed" == 1 && -x "$restored_python" ]]; then
      "$restored_python" -I -B -m openprogram worker install >/dev/null 2>&1 || :
    elif [[ "$worker_was_running" == 1 && -x "$restored_python" ]]; then
      "$restored_python" -I -B -m openprogram worker start >/dev/null 2>&1 || :
    fi
    [[ "$app_was_running" == 1 ]] && open "$target_app" || :
  fi
  if [[ "$preserve_transaction" == 0 ]]; then
    rm -rf "$transaction_dir" || :
  else
    printf 'OpenProgram recovery files were preserved at %s\n' "$transaction_dir" >&2
  fi
  release_install_lock
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

ditto "$source_app" "$staged_app"
validate_app "$staged_app" || {
  printf 'staged OpenProgram app failed validation\n' >&2
  exit 1
}

if [[ -z "$install_root" ]] && pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1; then
  app_was_running=1
  resume_after_failure=1
  osascript -e 'tell application id "ai.openprogram.desktop" to quit' >/dev/null 2>&1 || :
  for _ in {1..40}; do
    pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1 || break
    sleep 0.25
  done
  if pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1; then
    printf 'OpenProgram is still running; installation was not changed\n' >&2
    exit 1
  fi
fi

if [[ -z "$install_root" ]]; then
  control_app="$staged_app"
  [[ -d "$target_app" ]] && control_app="$target_app"
  control_python="$(app_runtime_python "$control_app")"
  worker_state_dir="${OPENPROGRAM_HOME:-$HOME/.openprogram}"
  for worker_state_file in "$worker_state_dir/worker.lock" "$worker_state_dir/worker.pid"; do
    worker_pid="$(sed -n '1p' "$worker_state_file" 2>/dev/null || :)"
    if [[ "$worker_pid" =~ ^[0-9]+$ ]] && kill -0 "$worker_pid" 2>/dev/null; then
      worker_was_running=1
      resume_after_failure=1
      break
    fi
  done
  launchd_plist="$HOME/Library/LaunchAgents/ai.openprogram.worker.plist"
  if [[ -f "$launchd_plist" ]]; then
    launchd_was_installed=1
    resume_after_failure=1
    if ! "$control_python" -I -B -m openprogram worker uninstall >/dev/null 2>&1; then
      printf 'the existing OpenProgram launchd service could not be unloaded; installation was not changed\n' >&2
      exit 1
    fi
  fi
  if [[ "$worker_was_running" == 1 ]]; then
    if ! "$control_python" -I -B -m openprogram worker stop >/dev/null 2>&1; then
      printf 'the existing OpenProgram worker could not be stopped; installation was not changed\n' >&2
      exit 1
    fi
  fi
fi

if [[ -e "$target_app" ]]; then
  mv "$target_app" "$previous_app"
  old_moved=1
fi
if ! mv "$staged_app" "$target_app"; then
  printf 'failed to activate the new OpenProgram app\n' >&2
  exit 1
fi
activated=1

if ! validate_app "$target_app"; then
  if mv "$target_app" "$transaction_dir/invalid.app"; then
    activated=0
  fi
  printf 'installed OpenProgram app failed validation\n' >&2
  exit 1
fi

version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$target_app/Contents/Info.plist")"
if [[ -z "$install_root" ]]; then
  installed_python="$(app_runtime_python "$target_app")"
  if [[ "$launchd_was_installed" == 1 ]]; then
    "$installed_python" -I -B -m openprogram worker install >/dev/null
    wait_for_worker_health || {
      printf 'OpenProgram was installed but its launchd worker did not start\n' >&2
      exit 1
    }
  fi
  if [[ "$app_was_running" == 1 ]]; then
    open "$target_app" || {
      printf 'OpenProgram was installed but could not be reopened\n' >&2
      exit 1
    }
  elif [[ "$worker_was_running" == 1 && "$launchd_was_installed" == 0 ]]; then
    "$installed_python" -I -B -m openprogram worker start >/dev/null
  fi
fi
if [[ "$old_moved" == 1 ]]; then
  rm -rf "$previous_app"
  old_moved=0
fi
resume_after_failure=0
printf 'OpenProgram %s installed at %s\n' "$version" "$target_app"
