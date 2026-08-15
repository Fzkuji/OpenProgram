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

validate_app "$source_app" || {
  printf 'invalid OpenProgram app bundle: %s\n' "$source_app" >&2
  exit 1
}
[[ "$source_app" != "$target_app" ]] || {
  printf 'source is already the canonical installed app\n' >&2
  exit 2
}

mkdir -p "$applications_dir"
transaction_dir="$(mktemp -d "$applications_dir/.openprogram-app-install.XXXXXX")"
staged_app="$transaction_dir/OpenProgram.app"
previous_app="$transaction_dir/previous.app"
old_moved=0
activated=0

cleanup() {
  local status="$?"
  if [[ "$old_moved" == 1 && "$activated" == 0 && ! -e "$target_app" && -d "$previous_app" ]]; then
    mv "$previous_app" "$target_app" || :
  fi
  rm -rf "$transaction_dir"
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

was_running=0
if [[ -z "$install_root" ]] && pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1; then
  was_running=1
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
  mv "$target_app" "$transaction_dir/invalid.app" || :
  activated=0
  printf 'installed OpenProgram app failed validation\n' >&2
  exit 1
fi

if [[ "$old_moved" == 1 ]]; then
  rm -rf "$previous_app"
  old_moved=0
fi

version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$target_app/Contents/Info.plist")"
printf 'OpenProgram %s installed at %s\n' "$version" "$target_app"
if [[ "$was_running" == 1 ]]; then
  open "$target_app" || printf 'OpenProgram was installed but could not be reopened\n' >&2
fi
