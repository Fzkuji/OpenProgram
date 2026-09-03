#!/usr/bin/env bash
set -euo pipefail

action="install"
defer_commit=0
source_app=""
transaction_dir=""
case "${1:-}" in
  --defer-commit)
    defer_commit=1
    source_app="${2:-}"
    [[ $# == 2 ]] || source_app=""
    ;;
  --commit)
    action="commit"
    transaction_dir="${2:-}"
    [[ $# == 2 ]] || transaction_dir=""
    ;;
  --rollback)
    action="rollback"
    transaction_dir="${2:-}"
    [[ $# == 2 ]] || transaction_dir=""
    ;;
  *)
    source_app="${1:-}"
    [[ $# -le 1 ]] || source_app=""
    ;;
esac
[[ "$(uname -s)" == "Darwin" ]] || {
  printf 'OpenProgram App installation requires macOS\n' >&2
  exit 1
}
if [[ "$action" == "install" && -z "$source_app" ]] || \
   [[ "$action" != "install" && -z "$transaction_dir" ]]; then
  printf 'usage: %s [--defer-commit] /path/to/OpenProgram.app\n' "$0" >&2
  printf '       %s --commit|--rollback /path/to/transaction\n' "$0" >&2
  exit 2
fi
if [[ -n "$source_app" && "$source_app" != /* ]]; then
  source_app="$(cd -- "$(dirname -- "$source_app")" && pwd)/$(basename -- "$source_app")"
fi
umask 077

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
launch_services_register="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

validate_app() {
  local app_path="$1"
  local plist="$app_path/Contents/Info.plist"
  local identifier version executable runtime_manifest runtime_python metadata_version
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
  runtime_python="$(app_runtime_python "$app_path")" || return 1
  [[ -x "$runtime_python" ]] || return 1
  metadata_version="$(
    env -i PATH=/usr/bin:/bin HOME=/dev/null \
      "$runtime_python" -I -B -c \
      'import importlib.metadata; print(importlib.metadata.version("openprogram"))'
  )" || return 1
  [[ "$metadata_version" == "$version" ]]
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

register_app() {
  "$launch_services_register" -f "$1"
}

app_identity() {
  local app_path="$1"
  node - "$app_path" <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const root = path.resolve(process.argv[2]);
const hash = crypto.createHash("sha256");
const entries = [];
function walk(directory) {
  for (const name of fs.readdirSync(directory).sort()) {
    const absolute = path.join(directory, name);
    const relative = path.relative(root, absolute);
    const stat = fs.lstatSync(absolute);
    if (stat.isSymbolicLink()) {
      entries.push([relative, "link", stat.mode, fs.readlinkSync(absolute)]);
    } else if (stat.isDirectory()) {
      entries.push([relative, "dir", stat.mode, ""]);
      walk(absolute);
    } else if (stat.isFile()) {
      entries.push([relative, "file", stat.mode, fs.readFileSync(absolute)]);
    } else {
      process.exit(1);
    }
  }
}
walk(root);
for (const [relative, type, mode, payload] of entries) {
  hash.update(relative); hash.update("\0");
  hash.update(type); hash.update("\0");
  hash.update(String(mode)); hash.update("\0");
  hash.update(payload); hash.update("\0");
}
process.stdout.write(hash.digest("hex"));
NODE
}

if [[ "$action" == "install" ]]; then
  validate_app "$source_app" || {
    printf 'invalid OpenProgram app bundle: %s\n' "$source_app" >&2
    exit 1
  }
  [[ "$source_app" != "$target_app" ]] || {
    printf 'source is already the canonical installed app\n' >&2
    exit 2
  }
fi

version_is_older() {
  node - "$1" "$2" <<'NODE'
const parse = (value) => value.split(".").map((part) => BigInt(part));
const left = parse(process.argv[2]);
const right = parse(process.argv[3]);
for (let index = 0; index < 3; index += 1) {
  if (left[index] < right[index]) process.exit(0);
  if (left[index] > right[index]) process.exit(1);
}
process.exit(1);
NODE
}

reject_downgrade() {
  local candidate_app="${1:-$source_app}"
  [[ -e "$target_app" || -L "$target_app" ]] || return 0
  validate_app "$target_app" || {
    printf 'existing OpenProgram app failed validation: %s\n' "$target_app" >&2
    exit 1
  }
  candidate_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$candidate_app/Contents/Info.plist")"
  installed_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$target_app/Contents/Info.plist")"
  if version_is_older "$candidate_version" "$installed_version"; then
    printf 'refusing to replace OpenProgram %s with older version %s\n' \
      "$installed_version" "$candidate_version" >&2
    exit 1
  fi
}

[[ "$action" != "install" ]] || reject_downgrade "$source_app"

mkdir -p "$applications_dir"
install_lock_file="$applications_dir/.openprogram-app-install.lock"
install_lock_owned=0
acquire_pid_lock() {
  local path="$1"
  if [[ -x /usr/bin/shlock ]]; then
    /usr/bin/shlock -p "$$" -f "$path"
  else
    (set -o noclobber; printf '%s\n' "$$" > "$path") 2>/dev/null
  fi
}
release_install_lock() {
  if [[ "$install_lock_owned" == 1 && "$(sed -n '1p' "$install_lock_file" 2>/dev/null || :)" == "$$" ]]; then
    rm -f "$install_lock_file" || :
  fi
}
if ! acquire_pid_lock "$install_lock_file"; then
  lock_pid="$(sed -n '1p' "$install_lock_file" 2>/dev/null || :)"
  printf 'another OpenProgram App installation is running%s\n' \
    "${lock_pid:+ (pid $lock_pid)}" >&2
  exit 1
fi
install_lock_owned=1
trap release_install_lock EXIT

validate_transaction_dir() {
  local candidate="$1"
  [[ "$candidate" == /* && -d "$candidate" && ! -L "$candidate" ]] || return 1
  [[ "$(dirname -- "$candidate")" == "$applications_dir" ]] || return 1
  [[ "$(basename -- "$candidate")" == .openprogram-app-install.* ]] || return 1
  [[ -O "$candidate" ]] || return 1
  [[ -f "$candidate/deferred" && ! -L "$candidate/deferred" ]] || return 1
  [[ -f "$candidate/active.sha256" && ! -L "$candidate/active.sha256" ]] || return 1
}

active_app_matches_transaction() {
  local expected actual
  validate_app "$target_app" || return 1
  expected="$(sed -n '1p' "$transaction_dir/active.sha256")"
  [[ "$expected" =~ ^[a-f0-9]{64}$ ]] || return 1
  actual="$(app_identity "$target_app")" || return 1
  [[ "$actual" == "$expected" ]]
}

stop_active_runtime() {
  local active_python worker_pid worker_state_file
  if pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1; then
    osascript -e 'tell application id "ai.openprogram.desktop" to quit' >/dev/null 2>&1 || :
    for _ in {1..40}; do
      pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1 || break
      sleep 0.25
    done
    pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1 && return 1
  fi
  active_python="$(app_runtime_python "$target_app")" || return 1
  if [[ -f "$HOME/Library/LaunchAgents/ai.openprogram.worker.plist" ]]; then
    "$active_python" -I -B -m openprogram worker uninstall >/dev/null 2>&1 || return 1
  fi
  for worker_state_file in "${OPENPROGRAM_HOME:-$HOME/.openprogram}/worker.lock" \
                           "${OPENPROGRAM_HOME:-$HOME/.openprogram}/worker.pid"; do
    worker_pid="$(sed -n '1p' "$worker_state_file" 2>/dev/null || :)"
    if [[ "$worker_pid" =~ ^[0-9]+$ ]] && kill -0 "$worker_pid" 2>/dev/null; then
      "$active_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || return 1
      break
    fi
  done
}

resume_previous_runtime() {
  local restored_python
  [[ -d "$target_app" ]] || return 0
  restored_python="$(app_runtime_python "$target_app")" || return 1
  if [[ -f "$transaction_dir/launchd-was-installed" ]]; then
    "$restored_python" -I -B -m openprogram worker install >/dev/null 2>&1 || return 1
    wait_for_worker_health || return 1
  elif [[ -f "$transaction_dir/worker-was-running" ]]; then
    "$restored_python" -I -B -m openprogram worker start >/dev/null 2>&1 || return 1
  fi
  [[ ! -f "$transaction_dir/app-was-running" ]] || open "$target_app"
}

if [[ "$action" != "install" ]]; then
  validate_transaction_dir "$transaction_dir" || {
    printf 'invalid OpenProgram App transaction: %s\n' "$transaction_dir" >&2
    exit 1
  }
  active_app_matches_transaction || {
    printf 'active OpenProgram app does not match the deferred transaction\n' >&2
    exit 1
  }
  if [[ "$action" == "commit" ]]; then
    rm -rf "$transaction_dir"
    printf 'OpenProgram App transaction committed\n'
    exit 0
  fi
  if [[ -z "$install_root" ]]; then
    stop_active_runtime || {
      printf 'active OpenProgram runtime could not be stopped; rollback was not started\n' >&2
      exit 1
    }
  fi
  if ! mv "$target_app" "$transaction_dir/failed.app"; then
    printf 'failed to preserve the active candidate; rollback was not completed\n' >&2
    exit 1
  fi
  if [[ -d "$transaction_dir/previous.app" ]] && \
     ! mv "$transaction_dir/previous.app" "$target_app"; then
    printf 'failed to restore the old App; recover it from %s\n' \
      "$transaction_dir/previous.app" >&2
    exit 1
  fi
  if [[ -z "$install_root" && -d "$target_app" ]]; then
    register_app "$target_app" >/dev/null 2>&1 || {
      printf 'the restored App could not be registered; transaction preserved at %s\n' \
        "$transaction_dir" >&2
      exit 1
    }
    resume_previous_runtime || {
      printf 'the previous runtime could not be resumed; transaction preserved at %s\n' \
        "$transaction_dir" >&2
      exit 1
    }
  fi
  rm -rf "$transaction_dir"
  printf 'OpenProgram App transaction rolled back\n'
  exit 0
fi

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
      if [[ -z "$install_root" && -x "$launch_services_register" ]]; then
        "$launch_services_register" -f "$target_app" >/dev/null 2>&1 || :
      fi
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
  elif [[ "$status" != 0 ]]; then
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
# The source and canonical App may have changed since the initial fast check.
# Compare the immutable staged copy under the lock before stopping workers or
# moving files.
reject_downgrade "$staged_app"

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
  : > "$transaction_dir/had-previous"
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
  if [[ ! -x "$launch_services_register" ]] || ! "$launch_services_register" -f "$target_app"; then
    printf 'OpenProgram was installed but Launch Services registration failed\n' >&2
    exit 1
  fi
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
if [[ "$defer_commit" == 1 ]]; then
  [[ "$app_was_running" != 1 ]] || : > "$transaction_dir/app-was-running"
  [[ "$worker_was_running" != 1 ]] || : > "$transaction_dir/worker-was-running"
  [[ "$launchd_was_installed" != 1 ]] || : > "$transaction_dir/launchd-was-installed"
  app_identity "$target_app" > "$transaction_dir/active.sha256"
  : > "$transaction_dir/deferred"
  preserve_transaction=1
elif [[ "$old_moved" == 1 ]]; then
  rm -rf "$previous_app"
  old_moved=0
fi
resume_after_failure=0
printf 'OpenProgram %s installed at %s\n' "$version" "$target_app"
if [[ "$defer_commit" == 1 ]]; then
  printf 'OPENPROGRAM_TRANSACTION_DIR=%s\n' "$transaction_dir"
fi
