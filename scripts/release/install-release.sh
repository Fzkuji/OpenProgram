#!/usr/bin/env sh
set -eu

OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-0.8.1}"
OPENPROGRAM_REPOSITORY="${OPENPROGRAM_REPOSITORY:-Fzkuji/OpenProgram}"
state_root="${OPENPROGRAM_STATE_DIR:-$HOME/.openprogram}"
runtime_root="$state_root/runtime/cli"
release_dir="$runtime_root/releases/$OPENPROGRAM_VERSION"

case "$OPENPROGRAM_VERSION" in
  *[!0-9.]*|.*|*.|*..*|*.*.*.*)
    printf 'invalid OpenProgram version: %s\n' "$OPENPROGRAM_VERSION" >&2
    exit 1
    ;;
  *.*.*) ;;
  *)
    printf 'invalid OpenProgram version: %s\n' "$OPENPROGRAM_VERSION" >&2
    exit 1
    ;;
esac
case "$OPENPROGRAM_REPOSITORY" in
  *[!A-Za-z0-9_./-]*|*..*|/*|*/|*/*/*)
    printf 'invalid OpenProgram repository: %s\n' "$OPENPROGRAM_REPOSITORY" >&2
    exit 1
    ;;
  */*) ;;
  *)
    printf 'invalid OpenProgram repository: %s\n' "$OPENPROGRAM_REPOSITORY" >&2
    exit 1
    ;;
esac

case "$(uname -s)" in
  Darwin) platform="macos" ;;
  Linux) platform="linux" ;;
  *) printf 'OpenProgram release installer supports macOS and Linux only.\n' >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch="x86_64" ;;
  arm64|aarch64) arch="arm64" ;;
  *) printf 'unsupported CPU architecture: %s\n' "$(uname -m)" >&2; exit 1 ;;
esac

download() {
  current_url="$1"
  destination="$2"
  command -v curl >/dev/null 2>&1 || {
    printf 'curl is required to download OpenProgram.\n' >&2
    exit 1
  }
  redirects=0
  while [ "$redirects" -le 5 ]; do
    case "$current_url" in
      https://github.com/*|https://release-assets.githubusercontent.com/*) ;;
      *) printf 'release URL is not allowed: %s\n' "$current_url" >&2; exit 1 ;;
    esac
    headers="$(mktemp "$(dirname "$destination")/.openprogram-headers.XXXXXX")"
    if ! status="$(curl --disable --proto '=https' --tlsv1.2 \
      --silent --show-error --connect-timeout 15 \
      --speed-limit 1024 --speed-time 120 \
      --dump-header "$headers" --output "$destination" \
      --write-out '%{http_code}' "$current_url")"; then
      rm -f "$headers"
      exit 1
    fi
    case "$status" in
      200|201|202|203|204|205|206)
        rm -f "$headers"
        return 0
        ;;
      301|302|303|307|308)
        next_url="$(awk 'tolower($1) == "location:" {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); value=$0} END {print value}' "$headers")"
        rm -f "$headers"
        test -n "$next_url" || { printf 'release redirect has no location.\n' >&2; exit 1; }
        current_url="$next_url"
        redirects="$((redirects + 1))"
        ;;
      *)
        rm -f "$headers"
        printf 'release download failed with HTTP %s.\n' "$status" >&2
        exit 1
        ;;
    esac
  done
  printf 'release redirect limit exceeded.\n' >&2
  exit 1
}

checksum() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    printf 'shasum or sha256sum is required to verify OpenProgram.\n' >&2
    exit 1
  fi
}

mkdir -p "$runtime_root/releases"
state_root="$(cd "$state_root" && pwd -P)"
runtime_root="$state_root/runtime/cli"
release_dir="$runtime_root/releases/$OPENPROGRAM_VERSION"
install_lock="$runtime_root/.install.lock"
staging=""
probe_home=""
probe_pid_file=""
probe_port=""
probe_active=0
launcher_tmp=""
python_bin=""

run_probe_python() {
  (
    cd "$probe_home"
    HOME="$probe_home" \
      XDG_CONFIG_HOME="$probe_home/.config" \
      OPENPROGRAM_STATE_DIR="$probe_home/.openprogram" \
      OPENPROGRAM_PROFILE='' \
      OPENPROGRAM_NO_WEB='' \
      OPENPROGRAM_WORKDIR="$probe_home" \
      OPENPROGRAM_WEB_PORT="$probe_port" \
      OPENPROGRAM_INSTALL_PROBE_PID_FILE="$probe_pid_file" \
      PLAYWRIGHT_BROWSERS_PATH="${playwright_path:-}" \
      GPA_MODEL_PATH="${gpa_model_path:-}" \
      "$python_bin" "$@"
  )
}

stop_probe_process_group() {
  [ -n "$probe_pid_file" ] && [ -s "$probe_pid_file" ] || return 0
  run_probe_python -I -B - "$probe_pid_file" <<'PY'
import os
import signal
import sys
import time
from pathlib import Path

try:
    pgid = int(Path(sys.argv[1]).read_text(encoding="ascii").strip())
except (OSError, ValueError):
    raise SystemExit(0)
if pgid <= 1:
    raise SystemExit("refusing to signal an invalid worker probe process group")


def group_exists() -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until_gone(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not group_exists():
            return True
        time.sleep(0.1)
    return not group_exists()


try:
    os.killpg(pgid, signal.SIGTERM)
except ProcessLookupError:
    raise SystemExit(0)
if not wait_until_gone(5.0):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        raise SystemExit(0)
    if not wait_until_gone(3.0):
        raise SystemExit(f"worker probe process group {pgid} survived SIGKILL")
PY
  rm -f "$probe_pid_file"
}

# Invoked by the EXIT trap below.
# shellcheck disable=SC2329
cleanup() {
  cleanup_status=$?
  trap - 0 HUP INT TERM
  if [ "$probe_active" = 1 ] && [ -n "$python_bin" ] && [ -n "$probe_home" ]; then
    stop_probe_process_group >/dev/null 2>&1 || true
  fi
  [ -z "$probe_home" ] || rm -rf "$probe_home"
  [ -z "$staging" ] || rm -rf "$staging"
  [ -z "$launcher_tmp" ] || rm -f "$launcher_tmp"
  exit "$cleanup_status"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

candidate_dir="$release_dir"
if [ ! -d "$release_dir" ]; then
  staging="$(mktemp -d "$runtime_root/.staging-$OPENPROGRAM_VERSION.XXXXXX")"
  archive_name="OpenProgram-${OPENPROGRAM_VERSION}-runtime-${platform}-${arch}.tar.gz"
  archive="${OPENPROGRAM_RUNTIME_ARCHIVE:-$staging/$archive_name}"
  if [ -z "${OPENPROGRAM_RUNTIME_ARCHIVE:-}" ]; then
    release_url="https://github.com/$OPENPROGRAM_REPOSITORY/releases/download/v$OPENPROGRAM_VERSION"
    download "$release_url/$archive_name" "$archive"
    download "$release_url/$archive_name.sha256" "$archive.sha256"
  else
    case "$archive" in
      /*.tar.gz) test -f "$archive" || { printf 'OPENPROGRAM_RUNTIME_ARCHIVE not found.\n' >&2; exit 1; } ;;
      *) printf 'OPENPROGRAM_RUNTIME_ARCHIVE must be an absolute .tar.gz path.\n' >&2; exit 1 ;;
    esac
  fi

  expected="${OPENPROGRAM_RUNTIME_SHA256:-}"
  if [ -z "$expected" ] && [ -f "$archive.sha256" ]; then
    expected="$(awk 'NR == 1 {print $1}' "$archive.sha256")"
  fi
  case "$expected" in
    *[!0-9A-Fa-f]*|'')
      printf 'runtime archive checksum must be exactly 64 hexadecimal characters.\n' >&2
      exit 1
      ;;
  esac
  if [ "${#expected}" -ne 64 ]; then
    printf 'runtime archive checksum must be exactly 64 hexadecimal characters.\n' >&2
    exit 1
  fi
  expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
  actual="$(checksum "$archive" | tr 'A-F' 'a-f')"
  test "$actual" = "$expected" || {
    printf 'runtime archive checksum mismatch.\n' >&2
    exit 1
  }

  tar -tzf "$archive" | while IFS= read -r entry; do
    case "$entry" in runtime|runtime/*) ;; *) printf 'invalid archive path: %s\n' "$entry" >&2; exit 1 ;; esac
    case "/$entry/" in */../*) printf 'invalid archive path: %s\n' "$entry" >&2; exit 1 ;; esac
  done

  tar -C "$staging" -xzf "$archive"
  test -f "$staging/runtime/runtime-manifest.json" || {
    printf 'runtime archive has no manifest.\n' >&2
    exit 1
  }
  candidate_dir="$staging/runtime"
fi

manifest="$candidate_dir/runtime-manifest.json"
python_relative="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest")"
python_bin="$candidate_dir/$python_relative"
test -x "$python_bin" || { printf 'managed Python is missing.\n' >&2; exit 1; }
test -x "$candidate_dir/bin/python" || {
  printf 'stable managed Python launcher is missing.\n' >&2
  exit 1
}
manifest_version="$("$python_bin" -I -B - "$manifest" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8")).get("openprogram")
print(value if isinstance(value, str) else "")
PY
)"
test "$manifest_version" = "$OPENPROGRAM_VERSION" || {
  printf 'runtime version %s does not match requested OpenProgram %s.\n' \
    "${manifest_version:-<missing>}" "$OPENPROGRAM_VERSION" >&2
  exit 1
}
"$python_bin" -I "$candidate_dir/bin/verify-product-runtime.py" "$candidate_dir"
"$python_bin" -I -m openprogram --version

playwright_path="$candidate_dir/assets/playwright"
gpa_model_path="$candidate_dir/assets/gpa/model.pt"
probe_port="$("$python_bin" -I -B - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)"
case "$probe_port" in
  ''|*[!0-9]*) printf 'failed to reserve a worker probe port.\n' >&2; exit 1 ;;
esac
probe_home="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-release-probe.XXXXXX")"
mkdir -p "$probe_home/.config"
probe_pid_file="$probe_home/worker-session.pid"
probe_active=1
run_probe_python -I -B - start <<'PY'
import os
import sys
from pathlib import Path

from openprogram.worker.lifecycle import spawn_detached

if sys.argv[1] != "start":
    raise SystemExit("invalid worker probe action")
pid_file = Path(os.environ["OPENPROGRAM_INSTALL_PROBE_PID_FILE"])


def record_probe_pid(pid: int) -> None:
    temporary = pid_file.with_suffix(".tmp")
    temporary.write_text(f"{pid}\n", encoding="ascii")
    os.replace(temporary, pid_file)


raise SystemExit(
    spawn_detached(prefer_service=False, on_spawn=record_probe_pid)
)
PY
run_probe_python -I -B - "$probe_port" <<'PY'
import json
import sys
import time
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/healthz"
for attempt in range(120):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.load(response)
        if payload.get("status") == "ok":
            raise SystemExit(0)
    except Exception:
        if attempt == 119:
            raise
    time.sleep(0.25)
raise SystemExit("worker health probe never returned status=ok")
PY
stop_probe_process_group
probe_active=0
rm -rf "$probe_home"
probe_home=""
probe_pid_file=""

launcher_dir="${OPENPROGRAM_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$launcher_dir"
launcher_dir="$(cd "$launcher_dir" && pwd -P)"
launcher_tmp="$(mktemp "$launcher_dir/.openprogram.XXXXXX")"
"$python_bin" -I -B - \
  "$launcher_tmp" "$runtime_root" "$state_root" "$launcher_dir" <<'PY'
import shlex
import sys
from pathlib import Path

launcher = Path(sys.argv[1])
runtime_root = shlex.quote(sys.argv[2])
state_root = shlex.quote(sys.argv[3])
launcher_dir = shlex.quote(sys.argv[4])
launcher.write_text(
    "#!/bin/sh\n"
    f"runtime_root={runtime_root}\n"
    f"state_root={state_root}\n"
    f"launcher_dir={launcher_dir}\n"
    'export OPENPROGRAM_STATE_DIR="$state_root"\n'
    'export OPENPROGRAM_BIN_DIR="$launcher_dir"\n'
    'export PLAYWRIGHT_BROWSERS_PATH="$runtime_root/current/assets/playwright"\n'
    'export GPA_MODEL_PATH="$runtime_root/current/assets/gpa/model.pt"\n'
    "export OPENPROGRAM_IMMUTABLE_RUNTIME=1\n"
    'exec "$runtime_root/current/bin/python" -I -B -m openprogram "$@"\n',
    encoding="utf-8",
)
PY
chmod 0755 "$launcher_tmp"
"$python_bin" -I -B - \
  "$install_lock" \
  "$candidate_dir" \
  "$release_dir" \
  "$runtime_root/current" \
  "$launcher_tmp" \
  "$launcher_dir/openprogram" \
  "$OPENPROGRAM_VERSION" \
  "$python_relative" <<'PY'
import fcntl
import json
import os
import sys
import uuid
from pathlib import Path

(
    lock_path,
    candidate_path,
    release_path,
    current_path,
    launcher_staging_path,
    launcher_path,
) = map(Path, sys.argv[1:7])
expected_version = sys.argv[7]
python_relative = Path(sys.argv[8])


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def validate_runtime(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"managed runtime is not a directory: {path}")
    if python_relative.is_absolute() or ".." in python_relative.parts:
        raise RuntimeError("managed Python path escapes the runtime")
    manifest = path / "runtime-manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"managed runtime manifest is unreadable: {manifest}") from exc
    if payload.get("openprogram") != expected_version:
        raise RuntimeError(
            f"managed runtime version changed before activation: {path}"
        )
    if not os.access(path / python_relative, os.X_OK):
        raise RuntimeError(f"managed Python is not executable: {path / python_relative}")
    if not os.access(path / "bin" / "python", os.X_OK):
        raise RuntimeError(f"stable managed Python launcher is missing: {path}")


def temporary_link(label: str) -> Path:
    return current_path.parent / f".{label}-{os.getpid()}-{uuid.uuid4().hex}"


old_current_target: str | None = None
current_switched = False
rollback_link: Path | None = None
next_link: Path | None = None

# Downloads and cold probes intentionally run concurrently.  Only the tiny
# activation transaction is serialized.  flock is owned by this live process,
# so SIGKILL and reboot release it without stale-lock recovery races.
with lock_path.open("a+b") as lock_file:
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    try:
        if launcher_path.is_dir():
            raise RuntimeError(
                f"launcher target is a directory and will not be replaced: {launcher_path}"
            )
        if lexists(current_path):
            if not current_path.is_symlink():
                raise RuntimeError(
                    f"runtime selector is not a symbolic link: {current_path}"
                )
            old_current_target = os.readlink(current_path)

        if not lexists(release_path):
            validate_runtime(candidate_path)
            os.replace(candidate_path, release_path)
        validate_runtime(release_path)

        next_link = temporary_link("current")
        os.symlink(str(release_path), next_link)
        os.replace(next_link, current_path)
        next_link = None
        current_switched = True

        if os.environ.get("OPENPROGRAM_INSTALL_TEST_FAULT") == "after-current":
            raise RuntimeError("injected activation failure after current switch")

        # This is deliberately the final fallible publication.  os.replace
        # cannot nest the file into a directory and preserves the prior
        # launcher on failure.
        os.replace(launcher_staging_path, launcher_path)
        current_switched = False
    except Exception as exc:
        if next_link is not None:
            next_link.unlink(missing_ok=True)
        if current_switched:
            try:
                if old_current_target is None:
                    current_path.unlink(missing_ok=True)
                else:
                    rollback_link = temporary_link("rollback")
                    os.symlink(old_current_target, rollback_link)
                    os.replace(rollback_link, current_path)
                    rollback_link = None
            except Exception as rollback_exc:
                print(
                    f"OpenProgram activation rollback failed: {rollback_exc}",
                    file=sys.stderr,
                )
        if rollback_link is not None:
            rollback_link.unlink(missing_ok=True)
        print(f"OpenProgram activation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
PY
launcher_tmp=""
if [ -n "$staging" ]; then
  rm -rf "$staging"
  staging=""
fi

printf 'OpenProgram %s installed.\n' "$OPENPROGRAM_VERSION"
printf 'Executable: %s/openprogram\n' "$launcher_dir"
printf 'Runtime: %s\n' "$release_dir"
exit 0
