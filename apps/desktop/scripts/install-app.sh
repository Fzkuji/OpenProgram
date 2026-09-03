#!/usr/bin/env bash
set -euo pipefail

action="install"
defer_commit=0
prepare_only=0
source_app=""
transaction_dir=""
case "${1:-}" in
  --prepare)
    prepare_only=1
    defer_commit=1
    source_app="${2:-}"
    [[ $# == 2 ]] || source_app=""
    ;;
  --activate)
    action="activate"
    defer_commit=1
    transaction_dir="${2:-}"
    [[ $# == 2 ]] || transaction_dir=""
    ;;
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
  printf '       %s --prepare /path/to/OpenProgram.app | --activate /path/to/transaction\n' "$0" >&2
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

validate_app_metadata() {
  local app_path="$1"
  local plist="$app_path/Contents/Info.plist"
  local identifier version executable runtime_manifest runtime_python package_version
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
  package_version="$(app_package_version "$app_path")" || return 1
  [[ "$package_version" == "$version" ]]
}

validate_app() {
  local app_path="$1"
  local plist="$app_path/Contents/Info.plist"
  local version runtime_python metadata_version
  validate_app_metadata "$app_path" || return 1
  version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist" 2>/dev/null)" || return 1
  runtime_python="$(app_runtime_python "$app_path")" || return 1
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

app_package_version() {
  local app_path="$1"
  node - "$app_path/Contents/Resources/runtime" <<'NODE'
const fs = require("fs");
const path = require("path");
const root = fs.realpathSync(process.argv[2]);
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, "runtime-manifest.json"), "utf8"),
);
const python = path.resolve(root, manifest.python);
if (!python.startsWith(`${root}${path.sep}`)) process.exit(1);
const prefix = path.dirname(path.dirname(python));
const lib = path.join(prefix, "lib");
const metadata = [];
for (const pythonDir of fs.readdirSync(lib)) {
  if (!pythonDir.startsWith("python")) continue;
  const sitePackages = path.join(lib, pythonDir, "site-packages");
  if (!fs.existsSync(sitePackages)) continue;
  for (const entry of fs.readdirSync(sitePackages)) {
    if (/^openprogram-[0-9][A-Za-z0-9.]*\.dist-info$/.test(entry)) {
      metadata.push(path.join(sitePackages, entry, "METADATA"));
    }
  }
}
if (metadata.length !== 1) process.exit(1);
const metadataPath = fs.realpathSync(metadata[0]);
if (!metadataPath.startsWith(`${root}${path.sep}`)) process.exit(1);
const fields = Object.fromEntries(
  fs.readFileSync(metadataPath, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.includes(":"))
    .map((line) => {
      const index = line.indexOf(":");
      return [line.slice(0, index).toLowerCase(), line.slice(index + 1).trim()];
    }),
);
if (fields.name?.toLowerCase().replaceAll("_", "-") !== "openprogram") {
  process.exit(1);
}
if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(fields.version ?? "")) process.exit(1);
process.stdout.write(fields.version);
NODE
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
  validate_app_metadata "$source_app" || {
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
  [[ "$action" == "install" ]] || return 0
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

if [[ "$defer_commit" == 1 && ! -d "$target_app" ]]; then
  printf 'deferred App installation requires an existing OpenProgram App\n' >&2
  exit 1
fi
reject_downgrade "$source_app"

mkdir -p "$applications_dir"
install_lock_file="$applications_dir/.openprogram-app-install.lock"
install_lock_owned=0
acquire_pid_lock() {
  local path="$1" stale_pid
  if [[ -x /usr/bin/shlock ]]; then
    if /usr/bin/shlock -p "$$" -f "$path"; then return 0; fi
    # shlock refuses same-second stale locks (second-resolution ctime guard).
    # Retry once after a dead owner; shlock still owns atomic removal/races.
    [[ -f "$path" && ! -L "$path" ]] || return 1
    stale_pid="$(sed -n '1p' "$path")"
    [[ "$stale_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$stale_pid" 2>/dev/null || return 1
    sleep 1
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

validate_transaction_location() {
  local candidate="$1"
  [[ "$candidate" == /* && -d "$candidate" && ! -L "$candidate" ]] || return 1
  [[ "$(dirname -- "$candidate")" == "$applications_dir" ]] || return 1
  [[ "$(basename -- "$candidate")" == .openprogram-app-install.* ]] || return 1
  [[ -O "$candidate" ]] || return 1
  [[ "$(cd -- "$candidate" && pwd -P)" == "$candidate" ]]
}

validate_transaction_dir() {
  local candidate="$1" expected actual
  validate_transaction_location "$candidate" || return 1
  [[ -f "$candidate/deferred" && ! -L "$candidate/deferred" ]] || return 1
  [[ -f "$candidate/active.sha256" && ! -L "$candidate/active.sha256" ]] || return 1
  [[ -f "$candidate/had-previous" && ! -L "$candidate/had-previous" ]] || return 1
  [[ -d "$candidate/previous.app" && ! -L "$candidate/previous.app" ]] || return 1
  [[ -f "$candidate/previous.sha256" && ! -L "$candidate/previous.sha256" ]] || return 1
  expected="$(sed -n '1p' "$candidate/previous.sha256")"
  [[ "$expected" =~ ^[a-f0-9]{64}$ ]] || return 1
  actual="$(app_identity "$candidate/previous.app")" || return 1
  [[ "$actual" == "$expected" ]]
}

# The prepared protocol keeps a durable receipt after finalization. Legacy
# --defer-commit transactions keep their existing cleanup behavior below.
transaction_journal() {
  node - "$transaction_dir" "$@" <<'NODE'
const fs = require("fs");
const path = require("path");
const [root, command, ...args] = process.argv.slice(2);
const file = path.join(root, "transaction.json");
const directory = fs.lstatSync(root);
if (!directory.isDirectory() || directory.uid !== process.getuid() || (directory.mode & 0o077)) process.exit(1);
const phases = ["prepared", "activating", "activated", "rolling_back", "rolled_back", "committing", "committed"];
const hex = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
function syncPath(file) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function syncTree(directory) {
  for (const entry of fs.readdirSync(directory)) {
    const file = path.join(directory, entry), stat = fs.lstatSync(file);
    if (stat.isDirectory()) syncTree(file);
    else if (stat.isFile()) syncPath(file);
  }
  syncPath(directory);
}
let data;
if (command === "init") {
  if (fs.existsSync(file) || !hex(args[0]) || !hex(args[1])) process.exit(1);
  syncTree(path.join(root, "OpenProgram.app"));
  data = {schema: 1, phase: "prepared", previous_sha256: args[0], active_sha256: args[1], app: false, worker: false, launchd: false};
} else {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > 4096 || stat.uid !== process.getuid() || (stat.mode & 0o077)) process.exit(1);
    data = JSON.parse(fs.readFileSync(fd, "utf8"));
  } finally { fs.closeSync(fd); }
  if (Object.keys(data).sort().join() !== "active_sha256,app,launchd,phase,previous_sha256,schema,worker" ||
      data.schema !== 1 || !phases.includes(data.phase) || !hex(data.previous_sha256) || !hex(data.active_sha256) ||
      ![data.app, data.worker, data.launchd].every(x => typeof x === "boolean")) process.exit(1);
  if (command === "read") {
    process.stdout.write([data.phase, data.previous_sha256, data.active_sha256, +data.app, +data.worker, +data.launchd].join(" "));
    process.exit(0);
  }
  if (command === "sync") {
    syncPath(root); syncPath(path.dirname(root));
    process.exit(0);
  }
  if (command === "runtime") {
    if (data.phase !== "prepared" || args.length !== 3 || !args.every(x => x === "0" || x === "1")) process.exit(1);
    [data.app, data.worker, data.launchd] = args.map(x => x === "1");
  } else if (command === "phase") {
    const edges = {prepared: ["activating", "rolling_back"], activating: ["activated", "rolling_back"],
      activated: ["rolling_back", "committing"], rolling_back: ["rolled_back"], committing: ["committed"]};
    if (!edges[data.phase]?.includes(args[0])) process.exit(1);
    data.phase = args[0];
  } else process.exit(1);
}
const temp = `${file}.${process.pid}.tmp`;
const fd = fs.openSync(temp, "wx", 0o600);
try { fs.writeFileSync(fd, JSON.stringify(data) + "\n"); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
fs.renameSync(temp, file);
for (const dir of [root, path.dirname(root)]) {
  syncPath(dir);
}
NODE
}

load_prepared_transaction() {
  validate_transaction_location "$transaction_dir" || return 1
  local snapshot
  snapshot="$(transaction_journal read)" || return 1
  read -r transaction_phase previous_identity active_identity app_was_running worker_was_running launchd_was_installed <<< "$snapshot"
}

matches_identity() {
  [[ -d "$1" && ! -L "$1" ]] && [[ "$(app_identity "$1")" == "$2" ]]
}

active_app_matches_transaction() {
  local expected actual
  validate_app_metadata "$target_app" || return 1
  expected="$(sed -n '1p' "$transaction_dir/active.sha256")"
  [[ "$expected" =~ ^[a-f0-9]{64}$ ]] || return 1
  actual="$(app_identity "$target_app")" || return 1
  [[ "$actual" == "$expected" ]]
}

stop_active_runtime() {
  local control_python worker_pid worker_state_file
  if pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1; then
    osascript -e 'tell application id "ai.openprogram.desktop" to quit' >/dev/null 2>&1 || :
    for _ in {1..40}; do
      pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1 || break
      sleep 0.25
    done
    pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1 && return 1
  fi
  control_python="$(app_runtime_python "$transaction_dir/previous.app")" || return 1
  if [[ -f "$HOME/Library/LaunchAgents/ai.openprogram.worker.plist" ]]; then
    "$control_python" -I -B -m openprogram worker uninstall >/dev/null 2>&1 || return 1
  fi
  for worker_state_file in "${OPENPROGRAM_HOME:-$HOME/.openprogram}/worker.lock" \
                           "${OPENPROGRAM_HOME:-$HOME/.openprogram}/worker.pid"; do
    worker_pid="$(sed -n '1p' "$worker_state_file" 2>/dev/null || :)"
    if [[ "$worker_pid" =~ ^[0-9]+$ ]] && kill -0 "$worker_pid" 2>/dev/null; then
      "$control_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || return 1
      break
    fi
  done
}

resume_previous_runtime() {
  local restored_python
  [[ -d "$target_app" ]] || return 0
  restored_python="$(app_runtime_python "$target_app")" || return 1
  if [[ "${launchd_was_installed:-0}" == 1 || -f "$transaction_dir/launchd-was-installed" ]]; then
    "$restored_python" -I -B -m openprogram worker install >/dev/null 2>&1 || return 1
    wait_for_worker_health || return 1
  elif [[ "${worker_was_running:-0}" == 1 || -f "$transaction_dir/worker-was-running" ]]; then
    "$restored_python" -I -B -m openprogram worker start >/dev/null 2>&1 || return 1
  fi
  if [[ "${app_was_running:-0}" == 1 || -f "$transaction_dir/app-was-running" ]]; then
    open "$target_app"
  fi
}

finish_prepared_transaction() {
  local previous="$transaction_dir/previous.app" failed="$transaction_dir/failed.app"
  if [[ "$action" == "commit" ]]; then
    matches_identity "$target_app" "$active_identity" || return 1
    [[ "$transaction_phase" != "committed" ]] || return 0
    [[ "$transaction_phase" == "activated" || "$transaction_phase" == "committing" ]] || return 1
    if [[ -e "$previous" || -L "$previous" ]]; then
      if [[ "$transaction_phase" == "activated" ]]; then
        matches_identity "$previous" "$previous_identity" || return 1
      else
        # The durable committing decision precedes deletion. A retry may see
        # only part of this exact backup; never follow a substituted symlink.
        [[ -d "$previous" && ! -L "$previous" && -O "$previous" ]] || return 1
      fi
    elif [[ "$transaction_phase" != "committing" ]]; then
      return 1
    fi
    [[ "$transaction_phase" == "committing" ]] || transaction_journal phase committing || return 1
    # Exact validated transaction child; the terminal journal remains durable.
    rm -rf "$previous" || return 1
    transaction_journal phase committed
    return
  fi
  [[ "$transaction_phase" != "committed" && "$transaction_phase" != "committing" ]] || return 1
  if [[ "$transaction_phase" == "rolled_back" ]]; then
    matches_identity "$target_app" "$previous_identity"
    return
  fi
  if [[ -e "$failed" || -L "$failed" ]]; then
    matches_identity "$failed" "$active_identity" || return 1
  fi
  if [[ -e "$transaction_dir/OpenProgram.app" || -L "$transaction_dir/OpenProgram.app" ]]; then
    matches_identity "$transaction_dir/OpenProgram.app" "$active_identity" || return 1
  fi
  if [[ -e "$previous" || -L "$previous" ]]; then
    matches_identity "$previous" "$previous_identity" || return 1
    if [[ -e "$target_app" || -L "$target_app" ]]; then
      matches_identity "$target_app" "$active_identity" || return 1
      [[ ! -e "$failed" && ! -L "$failed" ]] || return 1
    fi
  else
    matches_identity "$target_app" "$previous_identity" || return 1
  fi
  [[ "$transaction_phase" == "rolling_back" ]] || transaction_journal phase rolling_back || return 1
  if [[ -d "$previous" ]]; then
    [[ -n "$install_root" ]] || stop_active_runtime || return 1
    if [[ -e "$target_app" ]]; then
      mv "$target_app" "$failed" || return 1
      transaction_journal sync || return 1
    fi
    mv "$previous" "$target_app" || return 1
    transaction_journal sync || return 1
  fi
  matches_identity "$target_app" "$previous_identity" || return 1
  if [[ -z "$install_root" ]]; then
    register_app "$target_app" >/dev/null 2>&1 || return 1
    resume_previous_runtime || return 1
  fi
  transaction_journal phase rolled_back
}

if [[ "$action" != "install" ]] && \
   [[ "$action" == "activate" || -e "$transaction_dir/transaction.json" || -L "$transaction_dir/transaction.json" ]]; then
  load_prepared_transaction || { printf 'invalid prepared App transaction\n' >&2; exit 1; }
  if [[ "$action" == "activate" ]]; then
    [[ "$transaction_phase" == "prepared" ]] && \
      matches_identity "$target_app" "$previous_identity" && \
      matches_identity "$transaction_dir/OpenProgram.app" "$active_identity" && \
      [[ ! -e "$transaction_dir/previous.app" && ! -L "$transaction_dir/previous.app" ]] || {
      printf 'prepared App transaction identity or phase changed\n' >&2; exit 1;
    }
  else
    finish_prepared_transaction || {
      printf 'prepared App transaction could not be finalized; recovery files preserved\n' >&2; exit 1;
    }
    printf 'OpenProgram prepared transaction %s finished\n' "$action"
    printf 'OPENPROGRAM_TRANSACTION_DIR=%s\n' "$transaction_dir"
    exit 0
  fi
elif [[ "$action" != "install" ]]; then
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

if [[ "$action" == "install" ]]; then
  transaction_dir="$(mktemp -d "$applications_dir/.openprogram-app-install.XXXXXX")"
fi
staged_app="$transaction_dir/OpenProgram.app"
previous_app="$transaction_dir/previous.app"
old_moved=0
activated=0
app_was_running=0
worker_was_running=0
launchd_was_installed=0
resume_after_failure=0
preserve_transaction=0
[[ "$action" != "activate" ]] || preserve_transaction=1

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

if [[ "$action" == "install" ]]; then
  ditto "$source_app" "$staged_app"
fi
validate_app_metadata "$staged_app" || {
  printf 'staged OpenProgram app failed validation\n' >&2
  exit 1
}
# The source and canonical App may have changed since the initial fast check.
# Compare the immutable staged copy under the lock before stopping workers or
# moving files.
reject_downgrade "$staged_app"

if [[ "$prepare_only" == 1 ]]; then
  transaction_journal init "$(app_identity "$target_app")" "$(app_identity "$staged_app")"
  preserve_transaction=1
  printf 'OPENPROGRAM_TRANSACTION_DIR=%s\n' "$transaction_dir"
  exit 0
fi

if [[ "$action" == "activate" ]]; then
  # Save original launch state and activation intent before any stop/rename.
  if [[ -z "$install_root" ]]; then
    if pgrep -f "$target_app/Contents/MacOS/OpenProgram" >/dev/null 2>&1; then app_was_running=1; fi
    if [[ -f "$HOME/Library/LaunchAgents/ai.openprogram.worker.plist" ]]; then launchd_was_installed=1; fi
    for worker_state_file in "${OPENPROGRAM_HOME:-$HOME/.openprogram}/worker.lock" "${OPENPROGRAM_HOME:-$HOME/.openprogram}/worker.pid"; do
      worker_pid="$(sed -n '1p' "$worker_state_file" 2>/dev/null || :)"
      if [[ "$worker_pid" =~ ^[0-9]+$ ]] && kill -0 "$worker_pid" 2>/dev/null; then worker_was_running=1; fi
    done
  fi
  transaction_journal runtime "$app_was_running" "$worker_was_running" "$launchd_was_installed"
  transaction_journal phase activating
fi

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
  [[ "$action" != "activate" ]] || transaction_journal sync
  : > "$transaction_dir/had-previous"
  app_identity "$previous_app" > "$transaction_dir/previous.sha256"
fi
if ! mv "$staged_app" "$target_app"; then
  printf 'failed to activate the new OpenProgram app\n' >&2
  exit 1
fi
activated=1
[[ "$action" != "activate" ]] || transaction_journal sync

if ! validate_app_metadata "$target_app"; then
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
if [[ "$action" == "activate" ]]; then
  transaction_journal phase activated
elif [[ "$defer_commit" == 1 ]]; then
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
