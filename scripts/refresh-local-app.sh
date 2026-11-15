#!/usr/bin/env bash
set -euo pipefail

# Every checkout publishes to the same default App and dependency directories.
# Keep the lock in the parent until the complete build/install/restart finishes.
if test "${OPENPROGRAM_REFRESH_LOCK_HELD:-}" != "1"; then
  exec python3 - "$0" "$@" <<'PYLOCK'
import fcntl, os, subprocess, sys, tempfile
lock_path = os.path.join(tempfile.gettempdir(), f"openprogram-refresh-{os.getuid()}.lock")
# Leave the calling worker session before taking the lock. Conversational
# refresh stops that worker, and a child still in its process group is
# killed with it (`Cancelled: worker_stopping`).
if os.environ.get("OPENPROGRAM_REFRESH_DETACHED") != "1":
    env = {**os.environ, "OPENPROGRAM_REFRESH_DETACHED": "1"}
    log_path = os.path.join(tempfile.gettempdir(), f"openprogram-refresh-{os.getuid()}.log")
    log = open(log_path, "ab", buffering=0)
    child = subprocess.Popen(
        ["bash", sys.argv[1], *sys.argv[2:]],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
        close_fds=True,
    )
    print(f"detached refresh pid {child.pid}; log {log_path}", flush=True)
    # A chat-path refresh stops this worker. Waiting here would just
    # get SIGTERM with the session. Return once the child owns the work.
    if os.environ.get("OPENPROGRAM_SESSION_ID"):
        raise SystemExit(0)
    raise SystemExit(child.wait())
with open(lock_path, "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    result = subprocess.run(["bash", sys.argv[1], *sys.argv[2:]],
                            env={**os.environ, "OPENPROGRAM_REFRESH_LOCK_HELD": "1"})
    raise SystemExit(result.returncode)
PYLOCK
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve aliases before deriving either the source guard or installation lock.
# An alternate spelling of the installed App must address the same transaction.
canonicalize_app_path() {
  python3 - "$1" "${2:-}" <<'PY'
import os
import sys

target = os.path.realpath(sys.argv[1])
default = sys.argv[2]
if default:
    try:
        if os.path.samefile(target, default):
            target = default
    except FileNotFoundError:
        pass
print(target)
PY
}
default_app_path="$(canonicalize_app_path "/Applications/OpenProgram.app")"
app_path="$(canonicalize_app_path "${OPENPROGRAM_APP_PATH:-$default_app_path}" "$default_app_path")"
runtime_root="$app_path/Contents/Resources/runtime"
manifest="$runtime_root/runtime-manifest.json"
product_runtime_config="$repo_root/scripts/release/product-runtime.json"
installed_asar="$app_path/Contents/Resources/app.asar"
uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"
# `openprogram dev promote` sets OPENPROGRAM_PROMOTE_FROM to the `stable` tip
# it read (empty when the branch does not exist yet) and runs this script in
# a worktree at the commit to promote. The script then moves `stable` to that
# commit, compare-and-swap, only once the new version is healthy: a failed
# promotion leaves `stable` where it was.
promoting=0
promote_from="${OPENPROGRAM_PROMOTE_FROM-}"
test "${OPENPROGRAM_PROMOTE_FROM+set}" != set || promoting=1
# A worker started below inherits this environment; a later refresh run from
# it must not take itself for this promotion.
unset OPENPROGRAM_PROMOTE_FROM

# Phase marks in the log: the App and the worker are down between
# "quitting the App" and "worker healthy", so the marks say where that
# time goes.
stamp() {
  printf '[refresh %s] %s\n' "$(date '+%H:%M:%S')" "$*"
}
# What the refresh is doing, for the failure message.
refresh_step="checking and building"
step() {
  refresh_step="$*"
  stamp "$*"
}

# The default App is shared by every worktree. Recheck this gate after
# packaging and under the installation lock as another checkout may advance
# a branch while this build is running. Ignored build outputs are not source.
validate_default_app_source() {
  if test "$promoting" = 1 && test "$app_path" != "$default_app_path"; then
    printf '%s\n' "refusing to promote into $app_path: promotion installs only the default App" >&2
    exit 1
  fi
  test "$app_path" = "$default_app_path" || return 0
  local expected_revision="${1:-}"
  local current_revision source_status
  current_revision="$(git -C "$repo_root" rev-parse --verify HEAD)"
  if test -n "$expected_revision" && test "$current_revision" != "$expected_revision"; then
    printf '%s\n' "refusing to refresh the default App: HEAD changed after packaging" >&2
    exit 1
  fi
  source_status="$(git -C "$repo_root" status --porcelain --untracked-files=all --ignore-submodules=none)"
  if test -n "$source_status"; then
    printf '%s\n' "refusing to refresh the default App: uncommitted source changes; commit or preserve them before refreshing" >&2
    exit 1
  fi
  # With a `stable` branch the App is the stable instance: it installs
  # exactly that branch, or, during a promotion, the fast-forward of it that
  # the promotion moves `stable` to once installed. Day-to-day commits go to
  # the development instance (scripts/refresh-dev-instance.sh) instead.
  local stable_revision
  stable_revision="$(git -C "$repo_root" rev-parse --verify --quiet 'refs/heads/stable^{commit}' || true)"
  if test "$promoting" = 1; then
    if test "$stable_revision" != "$promote_from"; then
      printf '%s\n' "refusing to promote ${current_revision:0:10}: stable is ${stable_revision:-absent}, not ${promote_from:-absent} as when the promotion began" >&2
      exit 1
    fi
    if test -n "$promote_from" && \
      ! git -C "$repo_root" merge-base --is-ancestor "$promote_from" "$current_revision"; then
      printf '%s\n' "refusing to promote ${current_revision:0:10}: it does not contain stable (${promote_from:0:10}); promotion only fast-forwards" >&2
      exit 1
    fi
    return 0
  fi
  if test -n "$stable_revision"; then
    if test "$current_revision" != "$stable_revision"; then
      printf '%s\n' "refusing to refresh the stable App from ${current_revision:0:10}: it installs only refs/heads/stable (${stable_revision:0:10}); deploy to the development instance with scripts/refresh-dev-instance.sh, then promote with \`openprogram dev promote\`" >&2
      exit 1
    fi
    return 0
  fi
  for protected_ref in refs/heads/main refs/remotes/origin/main; do
    if git -C "$repo_root" rev-parse --verify --quiet "$protected_ref" >/dev/null && \
      ! git -C "$repo_root" merge-base --is-ancestor "$protected_ref" HEAD; then
      printf '%s\n' "refusing to refresh the default App from a checkout behind $protected_ref" >&2
      exit 1
    fi
  done
}
validate_default_app_source

# Remember the source checkout (not a build worktree) for `openprogram dev`.
if test "$app_path" = "$default_app_path"; then
  common_dir="$(cd "$repo_root" && cd "$(git rev-parse --git-common-dir)" && pwd)"
  mkdir -p "$HOME/.openprogram"
  printf '%s\n' "$(dirname "$common_dir")" > "$HOME/.openprogram/source-repo"
fi

if test -n "${OPENPROGRAM_LOCAL_PYTHON:-}"; then
  local_python="$OPENPROGRAM_LOCAL_PYTHON"
else
  openprogram_bin="$(command -v openprogram || true)"
  local_python=""
  if test -n "$openprogram_bin"; then
    local_python="$(sed -n '1s/^#!//p' "$openprogram_bin")"
    # Under a path with spaces the entry point is a /bin/sh trampoline
    # whose second line execs the real interpreter.
    if test "$local_python" = "/bin/sh"; then
      local_python="$(sed -n "2s/^'''exec' '\\([^']*\\)'.*/\\1/p" "$openprogram_bin")"
    fi
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

# Reject foreign signatures and prepare the persistent local identity before
# any installed-App mutation. Never replace a Developer ID distribution here.
"$local_python" "$repo_root/scripts/release/local-macos-signing.py" prepare --app "$app_path"

"$local_python" "$repo_root/scripts/release/verify-release-version.py" \
  --installed-app "$app_path" --require-source-match --allow-upgrade

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

# A runtime built from an older lock keeps its old dependency versions: the
# wheel installs below only add what is missing, never move a pinned package
# forward. Sync it to the locked set a release build installs, and record
# that set as the runtime's requirements.
sync_runtime_to_lock() {
  local python_executable="$1" runtime="$2"
  local requirements="$wheel_dir/product-requirements.txt"
  "$uv_bin" export --project "$repo_root" --frozen --no-dev \
    --extra all --extra search --no-emit-project \
    --output-file "$requirements" >/dev/null
  "$uv_bin" pip install --python "$python_executable" --break-system-packages \
    --require-hashes --requirements "$requirements"
  cp "$requirements" "$runtime/product-requirements.txt"
}

hydrate_wheel_dependencies() {
  local python_executable="$1"
  "$python_executable" -I -m pip install --disable-pip-version-check \
    --break-system-packages "$wheel"
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

# The refresh is a transaction, the way the App installer
# (apps/desktop/scripts/install-app.sh) replaces an App: the new App is
# assembled, verified and signed in a directory beside the installed one
# while the old worker keeps serving; only then are the App and worker
# stopped and the two Apps swapped by rename. Until the new worker is healthy
# the previous App waits in the same directory, and any failure puts it back
# and starts its worker again.
transaction_dir=""
staged_app=""
previous_app=""
keep_transaction=0
outage=0        # the App and worker were being stopped
committed=0     # the new worker is healthy and `stable` points at it
previous_revision="$(sed -n '1p' "$app_path/Contents/Resources/openprogram-source-revision" 2>/dev/null || :)"
previous_revision="${previous_revision:-an unrecorded revision}"
health_timeout=300

cleanup() {
  local status=0
  rm -rf "$wheel_dir" || status=$?
  if test -n "$transaction_dir" && test "$keep_transaction" = 0; then
    rm -rf "$transaction_dir" || status=$?
  fi
  release_install_lock
  return "$status"
}

# Start the worker of the App at $app_path. The same steps start the new
# worker and, after a failure, the previous one.
start_worker() {
  if test -f "$HOME/Library/LaunchAgents/ai.openprogram.worker.plist"; then
    "$app_python" -I -B -m openprogram worker install
  else
    "$app_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || return
    "$app_python" -I -B -m openprogram worker start
  fi
}

# launchd load and detached start return before the web listener is ready.
# Poll only; repeated start requests can race that same pending cold start.
wait_for_health() {
  local deadline=$((SECONDS + health_timeout)) remaining probe_timeout status=7
  while test "$SECONDS" -lt "$deadline"; do
    remaining=$((deadline - SECONDS))
    test "$remaining" -gt 0 || break
    probe_timeout=2
    test "$remaining" -ge "$probe_timeout" || probe_timeout=$remaining
    if curl -fsS --connect-timeout 1 --max-time "$probe_timeout" \
      http://127.0.0.1:18100/healthz >/dev/null 2>&1; then
      return 0
    else
      status=$?
    fi
    sleep 0.2
  done
  printf 'worker health did not become ready within %s seconds: http://127.0.0.1:18100/healthz (curl exit %s); logs: %s/.openprogram/worker.log\n' \
    "$health_timeout" "$status" "$HOME" >&2
  return "$status"
}

# A healthy endpoint or matching package revision cannot prove which Python
# won startup. Verify the actual owner process before declaring a worker up.
verify_worker_owner() {
  "$app_python" -I -B - "$app_python" <<'PYTHON'
import subprocess
import sys
from pathlib import Path
from openprogram.operations.worker.lifecycle import current_worker_pid, worker_executable

pid = current_worker_pid()
if pid is None:
    raise SystemExit("refreshed worker has no live owner")
command = subprocess.check_output(
    ["ps", "-p", str(pid), "-o", "command="], text=True,
).strip()
executable = subprocess.check_output(
    ["ps", "-p", str(pid), "-o", "comm="], text=True,
).strip()
if (
    Path(executable).resolve() != Path(worker_executable()).resolve()
    or not command.endswith(" -I -B -u -m openprogram worker run")
):
    raise SystemExit(f"refreshed worker {pid} does not use the embedded App interpreter")
print(f"verified embedded App worker PID {pid}")
PYTHON
}

# Put the previous App back at $app_path and bring its worker up again.
restore_step=""
# The files say how far the switch got: a signal can land between a rename
# and any flag recording it.
restore_previous() {
  if test -e "$previous_app"; then
    if test -e "$app_path"; then
      restore_step="stopping the new worker"
      "$app_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || :
      restore_step="moving the new App aside"
      mv "$app_path" "$transaction_dir/failed.app" || return 1
    fi
    restore_step="putting the previous App back"
    mv "$previous_app" "$app_path" || return 1
  fi
  restore_step="starting the previous worker"
  stamp "$restore_step"
  start_worker || return 1
  restore_step="waiting for the previous worker to answer"
  wait_for_health || return 1
  restore_step="checking the previous worker's interpreter"
  verify_worker_owner || return 1
}

app_reopen_attempted=0
# The App at $app_path is whole and its worker is up: reopen it on exit.
app_ready_for_reopen=0
# Whether the user was in the App when the refresh closed it. Only then does
# it come back in front; otherwise it reopens behind what they are doing.
app_was_front=0
finish_refresh() {
  local status=$? reopen_status=0 cleanup_status=0
  trap - EXIT
  set +e
  if test "$status" != 0 && test "$committed" = 0; then
    if test "$outage" = 1; then
      printf 'the refresh failed while %s (exit %s); restoring the previous version\n' \
        "$refresh_step" "$status" >&2
      # Finish putting the previous version back even when interrupted again.
      trap '' HUP INT TERM
      restore_previous
      local restored=$?
      trap 'exit 129' HUP
      trap 'exit 130' INT
      trap 'exit 143' TERM
      if test "$restored" = 0; then
        stamp "previous worker healthy"
        app_ready_for_reopen=1
        printf 'the refresh failed while %s; the previous version (%s) is running again\n' \
          "$refresh_step" "${previous_revision:0:10}" >&2
      else
        keep_transaction=1
        local previous_location="$app_path"
        test ! -e "$previous_app" || previous_location="$previous_app"
        printf 'the refresh failed while %s, and restoring the previous version failed while %s; the previous App is at %s and the stable worker is not running\n' \
          "$refresh_step" "$restore_step" "$previous_location" >&2
      fi
    else
      printf 'the refresh failed while %s; nothing was stopped or replaced, so the previous version (%s) is still running\n' \
        "$refresh_step" "${previous_revision:0:10}" >&2
    fi
  elif test "$status" != 0; then
    printf 'the new version (%s) is installed and its worker is healthy\n' "${revision:0:10}" >&2
  fi
  if test "$app_ready_for_reopen" = 1 && test "$app_reopen_attempted" = 0; then
    reopen_app
    reopen_status=$?
  fi
  cleanup
  cleanup_status=$?
  if test "$cleanup_status" != 0; then
    printf 'refresh cleanup failed (exit %s)\n' "$cleanup_status" >&2
  fi
  if test "$status" = 0; then
    if test "$reopen_status" != 0; then status=$reopen_status
    elif test "$cleanup_status" != 0; then status=$cleanup_status
    fi
  fi
  if test "$status" = 0; then
    printf 'refreshed %s from %s\n' "$app_path" "$revision"
  elif test "$keep_transaction" = 1; then
    printf 'the files of this refresh are kept at %s\n' "$transaction_dir" >&2
  fi
  exit "$status"
}
trap finish_refresh EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# `openprogram dev promote` points this at what the development instance
# built for the same commit (its wheel and the standalone TUI bundle), so
# promotion does not build the Web assets a second time.
prebuilt_dir="${OPENPROGRAM_PREBUILT_DIR:-}"
# The build runs while the App is still open; it yields the CPU to it.
build_nice="nice -n 10"

attempt=0
while true; do
  attempt=$((attempt + 1))
  build_revision="$(git -C "$repo_root" rev-parse HEAD)"
  validate_default_app_source "$build_revision"
  prebuilt_wheel=""
  if test -n "$prebuilt_dir" \
    && test "$(cat "$prebuilt_dir/revision" 2>/dev/null)" = "$build_revision" \
    && test -f "$prebuilt_dir/index-standalone.cjs"; then
    prebuilt_wheel="$(find "$prebuilt_dir" -maxdepth 1 -type f \
      -name 'openprogram-*.whl' -print -quit)"
  fi
  attempt_dir="$wheel_dir/attempt-$attempt"
  mkdir -p "$attempt_dir"
  product_runtime_stage="$attempt_dir/product-runtime.json"

  cp "$product_runtime_config" "$product_runtime_stage"

  rm -rf "$repo_root/apps/desktop/dist"
  if test -n "$prebuilt_wheel"; then
    stamp "reusing the development build of ${build_revision:0:10}"
    tui_bundle="$prebuilt_dir/index-standalone.cjs"
  else
    OPENPROGRAM_DOCS_FORMAT=quarantine $build_nice "$repo_root/scripts/release/stage-release-assets.sh"
    tui_bundle="$repo_root/apps/cli/dist/index-standalone.cjs"
  fi
  # Freeze the runtime assets with the wheel, including additions required by
  # newer Desktop capability checks when refreshing an older installed App.
  runtime_assets_stage="$attempt_dir/runtime-assets"
  mkdir -p "$runtime_assets_stage"
  cp "$tui_bundle" "$runtime_assets_stage/index.cjs"
  node_candidates=()
  if test -n "${OPENPROGRAM_NODE_BIN:-}"; then
    node_candidates+=("$OPENPROGRAM_NODE_BIN")
  else
    node_candidates+=("$runtime_root/bin/node")
    path_node="$(command -v node || true)"
    test -z "$path_node" || node_candidates+=("$path_node")
  fi
  relocated_node_ok=0
  for node_candidate in "${node_candidates[@]}"; do
    test -x "$node_candidate" || continue
    cp "$node_candidate" "$runtime_assets_stage/node"
    if "$runtime_assets_stage/node" "$runtime_assets_stage/index.cjs" --probe >/dev/null 2>&1; then
      relocated_node_ok=1
      break
    fi
  done
  test "$relocated_node_ok" = 1 || {
    printf 'bundled Node cannot run after relocation; set OPENPROGRAM_NODE_BIN to a standalone Node executable\n' >&2
    exit 1
  }
  cp "$repo_root/uv.lock" "$runtime_assets_stage/product-uv.lock"
  cp "$repo_root/scripts/release/verify-product-runtime.py" "$runtime_assets_stage/verify-product-runtime.py"
  cp "$repo_root/scripts/release/build-macos-runtime-app.py" "$runtime_assets_stage/build-macos-runtime-app.py"
  cp "$repo_root/scripts/release/mac-runtime-main.c" "$runtime_assets_stage/mac-runtime-main.c"
  cp "$repo_root/apps/desktop/build/icon.icns" "$runtime_assets_stage/icon.icns"

  cp "$product_runtime_config" "$runtime_assets_stage/product-runtime.json"
  if test -n "$prebuilt_wheel"; then
    cp "$prebuilt_wheel" "$attempt_dir/"
  else
    rm -rf "$repo_root/build"
    $build_nice "$uv_bin" build --wheel --out-dir "$attempt_dir" "$repo_root"
  fi
  stamp "wheel built"
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

  desktop_asar="$attempt_dir/app.asar"
  OPENPROGRAM_LOCAL_PYTHON="$local_python" \
    "$repo_root/scripts/release/pack-desktop-archive.sh" \
    "$installed_asar" "$attempt_dir/desktop" "$desktop_asar"
  # A refresh replaces app.asar only, so the App keeps the Electron and the
  # bundle version it was installed with. When apps/desktop/package.json pins
  # another Electron or carries a newer version, build the shell and move its
  # Electron and bundle metadata in below.
  electron_shell=""
  read -r installed_electron pinned_electron upgrade_from source_version < <("$local_python" - \
    "$app_path/Contents/Frameworks/Electron Framework.framework/Resources/Info.plist" \
    "$repo_root/apps/desktop/package.json" "$app_path/Contents/Info.plist" <<'PY'
import json
import plistlib
import sys

def plist_value(path, key):
    try:
        with open(path, "rb") as stream:
            return plistlib.load(stream)[key]
    except (OSError, KeyError, ValueError):
        return "-"

def release(version):
    try:
        return tuple(int(part) for part in version.split("."))
    except (AttributeError, ValueError):
        return None

with open(sys.argv[2], encoding="utf-8") as stream:
    package = json.load(stream)
installed, source = plist_value(sys.argv[3], "CFBundleShortVersionString"), package.get("version", "-")
# Only an upgrade rebuilds the shell; any other mismatch is refused below.
older = release(installed) and release(source) and release(installed) < release(source)
print(plist_value(sys.argv[1], "CFBundleVersion"),
      package.get("devDependencies", {}).get("electron", "-"),
      installed if older else "-", source)
PY
)
  if { test "$installed_electron" != "-" && test "$pinned_electron" != "-" && \
    test "$installed_electron" != "$pinned_electron"; } || \
    test "$upgrade_from" != "-"; then
    stamp "building the Electron $pinned_electron shell for $source_version (installed: Electron $installed_electron, $upgrade_from)"
    OPENPROGRAM_LOCAL_PYTHON="$local_python" $build_nice \
      "$repo_root/scripts/release/build-desktop-shell.sh" "$attempt_dir/shell"
    electron_shell="$attempt_dir/shell/OpenProgram.app"
  fi

  validate_default_app_source
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
validate_default_app_source "$build_revision"

# Re-read all mutable version sources under the same lock as the canonical App
# installer. The wheel is the immutable payload used by both pip operations.
"$local_python" "$repo_root/scripts/release/verify-release-version.py" \
  --installed-app "$app_path" --require-source-match --allow-upgrade --wheel "$wheel"

# Freeze the installer with this build. A new runtime must not snapshot an
# obsolete installer on its next conversational update.
installer_stage="$attempt_dir/install-app.sh"
test ! -L "$app_path/Contents/Resources/update" && \
  test ! -L "$app_path/Contents/Resources/update/install-app.sh" || {
  printf 'the installed update resources must not be symlinks\n' >&2
  exit 1
}
cp "$repo_root/apps/desktop/scripts/install-app.sh" "$installer_stage"

# Refuse any source/ref change since packaging before writing anything.
validate_default_app_source "$build_revision"

# Assemble the new App beside the installed one, on the same volume so the
# switch is a rename. Everything that can fail on the new version happens to
# this copy while the old App and worker keep serving.
previous_revision="$(sed -n '1p' "$app_path/Contents/Resources/openprogram-source-revision" 2>/dev/null || :)"
previous_revision="${previous_revision:-an unrecorded revision}"
transaction_dir="$(mktemp -d "$(dirname -- "$app_path")/.openprogram-refresh.XXXXXX")"
staged_app="$transaction_dir/OpenProgram.app"
previous_app="$transaction_dir/previous.app"
staged_runtime="$staged_app/Contents/Resources/runtime"
staged_python="$staged_runtime/$app_python_relative"
step "assembling the new App beside the installed one"
# A copy-on-write clone where the volume allows it; a full copy elsewhere.
cp -cRp "$app_path" "$staged_app" 2>/dev/null || {
  rm -rf "$staged_app"
  ditto "$app_path" "$staged_app"
}

step "installing runtime packages into the new App"
# A wheel reinstall does not remove files left by an older package layout.
# Validate the runtime before deleting anything, then remove only
# OpenProgram's validated package directories before reinstalling.
validate_stale_package_tree "$staged_python"
sync_runtime_to_lock "$staged_python" "$staged_runtime"
# The embedded runtime may predate a newly declared production dependency.
# Ask pip to resolve the wheel once before replacing the same-version package;
# the second no-deps install below still performs the exact source refresh.
hydrate_wheel_dependencies "$staged_python"
remove_stale_package_tree "$staged_python"
"$staged_python" -I -m pip install --disable-pip-version-check \
  --break-system-packages --no-deps --force-reinstall "$wheel"
# Programs this runtime no longer ships; an earlier refresh may have installed them.
for retired_program in wiki-agent-harness gui-agent-harness research-agent-harness; do
  "$staged_python" -I -m pip uninstall --disable-pip-version-check -y \
    --break-system-packages "$retired_program" >/dev/null 2>&1 || true
done
if test "$(uname -s)" = Darwin; then
  "$staged_python" -I -c \
      'import AppKit, ApplicationServices, AVFoundation, EventKit, Quartz, ScreenCaptureKit'
fi

step "assembling the new runtime"
if test -n "$electron_shell"; then
  # Electron's frameworks, executable, bundle metadata and locales; the
  # runtime, app.asar and installer stay this App's own. Before the runtime
  # helper build, which adds its privacy keys to this Info.plist.
  rsync -a --delete "$electron_shell/Contents/Frameworks/" "$staged_app/Contents/Frameworks/"
  rsync -a --delete "$electron_shell/Contents/MacOS/" "$staged_app/Contents/MacOS/"
  cp "$electron_shell/Contents/Info.plist" "$staged_app/Contents/Info.plist"
  find "$staged_app/Contents/Resources" -maxdepth 1 -name '*.lproj' -exec rm -rf {} +
  rsync -a "$electron_shell/Contents/Resources/" "$staged_app/Contents/Resources/" \
    --include '*.lproj/***' --exclude '*'
  rm -f "$staged_app/Contents/Resources/default_app.asar"
fi
cp "$product_runtime_stage" "$staged_runtime/product-runtime.json"
mkdir -p "$staged_runtime/bin" "$staged_runtime/assets/tui"
# Office installations live in the user cache and are preserved across App refreshes.
rm -rf "$staged_runtime/assets/office"

install -m 755 "$runtime_assets_stage/node" "$staged_runtime/bin/node"
cp "$runtime_assets_stage/index.cjs" "$staged_runtime/assets/tui/index.cjs"
cp "$runtime_assets_stage/product-uv.lock" "$staged_runtime/product-uv.lock"
cp "$runtime_assets_stage/verify-product-runtime.py" "$staged_runtime/bin/verify-product-runtime.py"
if test ! -e "$staged_runtime/bin/python" && test ! -L "$staged_runtime/bin/python"; then
  ln -s "../$app_python_relative" "$staged_runtime/bin/python"
fi
"$staged_python" -I "$runtime_assets_stage/build-macos-runtime-app.py" \
  "$staged_runtime" --python "$staged_python" --icon "$runtime_assets_stage/icon.icns" --outer-app "$staged_app"
runtime_version="$("$staged_python" -I -c 'from importlib.metadata import version; print(version("openprogram"))')"
runtime_uv_version="$("$staged_runtime/bin/uv" --version | awk '{print $2}')"
# The verifier probes every capability before writing its manifest. Never
# retain an old manifest or mark a newly required capability verified by fiat.
step "verifying the new runtime"
"$staged_python" -I "$staged_runtime/bin/verify-product-runtime.py" "$staged_runtime" \
  --write --this-machine --python-relative "$app_python_relative" \
  --openprogram-version "$runtime_version" --uv-version "$runtime_uv_version"
step "installing the new App files"
cp "$desktop_asar" "$staged_app/Contents/Resources/app.asar"
if test -d "$desktop_asar.unpacked"; then
  rsync -a --delete "$desktop_asar.unpacked/" \
    "$staged_app/Contents/Resources/app.asar.unpacked/"
fi
mkdir -p "$staged_app/Contents/Resources/update"
cp "$installer_stage" "$staged_app/Contents/Resources/update/install-app.sh"
# The containing App's Dock and Finder icon ships with the source like the runtime's.
cp "$runtime_assets_stage/icon.icns" "$staged_app/Contents/Resources/icon.icns"
node "$repo_root/apps/desktop/scripts/write-reopen-protocol.cjs" \
  --resources "$staged_app/Contents/Resources"

revision="$build_revision"
if test -n "$(git -C "$repo_root" status --porcelain --untracked-files=no)"; then
  revision="$revision-dirty"
fi
printf '%s\n' "$revision" > \
  "$staged_app/Contents/Resources/openprogram-source-revision"

# Repair only invalid nested Framework/App bundles, deepest paths first.
# Do not recurse into valid code or touch the separately signed runtime helper.
while IFS= read -r -d '' inner_bundle; do
  if ! codesign --verify --strict "$inner_bundle" >/dev/null 2>&1; then
    inner_signature="$(codesign --display --verbose=4 "$inner_bundle" 2>&1 || true)"
    if grep -Eq '^Authority=' <<<"$inner_signature"; then
      printf '%s\n' "refusing to replace a non-ad-hoc nested signature: $inner_bundle" >&2
      exit 1
    fi
    printf 'repairing invalid nested signature: %s\n' "$inner_bundle" >&2
    codesign --force --sign - --timestamp=none \
      --preserve-metadata=entitlements,requirements,flags "$inner_bundle"
    codesign --verify --strict "$inner_bundle"
  fi
done < <(find "$staged_app/Contents/Frameworks" -depth -type d \
  \( -name '*.framework' -o -name '*.app' \) -print0)

# Reuse the same local certificate for the managed runtime and its
# TCC-responsible containing App, so permissions carry over to the new App.
step "signing the new App"
"$local_python" "$repo_root/scripts/release/local-macos-signing.py" sign --app "$staged_app"
codesign --verify --strict "$staged_app"
codesign --verify --strict \
  "$staged_app/Contents/Resources/runtime/OpenProgram.app"
codesign --verify --deep --strict "$staged_app"
stamp "App signed"

# The command-line installation is not what the stable worker runs; it takes
# the new package now, so a failure here also stops before the App does.
step "installing the command-line package"
validate_stale_package_tree "$local_python"
hydrate_wheel_dependencies "$local_python"
remove_stale_package_tree "$local_python"
"$local_python" -m pip install --disable-pip-version-check \
  --no-deps --force-reinstall "$wheel"
for retired_program in wiki-agent-harness gui-agent-harness research-agent-harness; do
  "$local_python" -m pip uninstall --disable-pip-version-check -y \
    "$retired_program" >/dev/null 2>&1 || true
done

# Last check before the outage: no source or `stable` change since packaging.
validate_default_app_source "$build_revision"

reopen_app() {
  local open_status=0
  app_reopen_attempted=1
  if test "$app_was_front" = 1 && test "${OPENPROGRAM_REFRESH_BACKGROUND:-0}" != 1; then
    open -a "$app_path" || open_status=$?
  else
    # The App shows its window when it is ready, which activates it; the
    # application the user is in gets the front straight back.
    "$local_python" "$repo_root/openprogram/host/_platform/front.py" "$app_path" 30 >/dev/null 2>&1 &
    sleep 0.3
    open -g -a "$app_path" || open_status=$?
  fi
  if test "$open_status" != 0; then
    printf 'OpenProgram did not reopen after the refresh (open exit %s)\n' "$open_status" >&2
    return "$open_status"
  fi
  for _ in {1..50}; do
    if pgrep -f "^${app_path}/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
  if ! pgrep -f "^${app_path}/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1; then
    printf 'OpenProgram did not reopen after the refresh\n' >&2
    return 1
  fi
  # A main process that throws while loading stays alive without a window.
  for _ in {1..150}; do
    if pgrep -f "^${app_path}/Contents/Frameworks/OpenProgram Helper \(Renderer\)[.]app/.*--type=renderer" >/dev/null 2>&1; then
      stamp "App reopened"
      return 0
    fi
    sleep 0.2
  done
  printf 'OpenProgram reopened without a window; its main process likely failed to load\n' >&2
  return 1
}

# From here a failure restores the previous App and worker (finish_refresh).
outage=1
if pgrep -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1; then
  if lsappinfo info -only bundleid "$(lsappinfo front 2>/dev/null)" 2>/dev/null | grep -q '"ai.openprogram.desktop"'; then
    app_was_front=1
  fi
  step "quitting the App"
  osascript -e 'tell application id "ai.openprogram.desktop" to quit' >/dev/null 2>&1 || true
  for _ in {1..50}; do
    pgrep -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1 || break
    sleep 0.2
  done
  if pgrep -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1; then
    pkill -TERM -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)"
    for _ in {1..50}; do
      pgrep -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1 || break
      sleep 0.2
    done
  fi
  pgrep -f "^/Applications/OpenProgram[.]app/Contents/MacOS/OpenProgram( |$)" >/dev/null 2>&1 && {
    printf 'OpenProgram did not quit before the refresh\n' >&2
    exit 1
  }
fi
# The installed App's own interpreter stops its worker and service.
step "stopping the worker"
"$app_python" -I -B -m openprogram worker stop >/dev/null

step "switching to the new App"
mv "$app_path" "$previous_app"
mv "$staged_app" "$app_path"
# Launch Services caches bundle icons by modification date.
touch "$app_path"

# Programs left in older locations (harness clones in the source tree, the
# former workflows/ and applications/ directories) move into the owner's
# Programs directory before the worker starts on this code.
step "moving Programs into place"
"$app_python" -I -B -c \
  'from openprogram.state.program_directory import migrate_user_directory; migrate_user_directory()'

# Rebind an existing launchd service to the same embedded interpreter used by
# the App. A detached fallback must use it too, never the PATH installation.
step "starting the worker"
start_worker
step "waiting for the worker to answer"
wait_for_health
stamp "worker healthy"
step "checking the worker's interpreter"
verify_worker_owner

# The new version is healthy: only now does a promotion move `stable`,
# compare-and-swap from the tip the promotion began with. A signal must not
# land between moving `stable` and recording it, or the restore would put the
# previous App back under a `stable` that has moved.
trap '' HUP INT TERM
if test "$promoting" = 1; then
  step "moving stable to ${build_revision:0:10}"
  git -C "$repo_root" update-ref -m "promote from dev" refs/heads/stable \
    "$build_revision" "${promote_from:-0000000000000000000000000000000000000000}"
fi
committed=1
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
app_ready_for_reopen=1
reopen_app
