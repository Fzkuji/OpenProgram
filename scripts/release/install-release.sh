#!/usr/bin/env sh
set -eu

OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-1.6.0}"
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

# A terminal running under Rosetta reports x86_64 on an Apple Silicon Mac.
if [ "$platform" = "macos" ] && [ "$arch" = "x86_64" ] &&
  [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" = "1" ]; then
  arch="arm64"
  printf 'This terminal runs under Rosetta on an Apple Silicon Mac. Installing the Apple Silicon version.\n'
fi

# The oldest macOS the runtime runs on: product-runtime.json's macosMinimum,
# which the App's own minimum and the install page also name.
macos_minimum="13.5"
if [ "$platform" = "macos" ]; then
  macos_version="$(sw_vers -productVersion 2>/dev/null || true)"
  case "$macos_version" in
    *.*) macos_rest="${macos_version#*.}" ;;
    *) macos_rest="0" ;;
  esac
  macos_major="${macos_version%%.*}"
  macos_minor="${macos_rest%%.*}"
  case "$macos_major$macos_minor" in
    ''|*[!0-9]*) ;;
    *)
      if [ "$macos_major" -lt "${macos_minimum%%.*}" ] ||
        { [ "$macos_major" -eq "${macos_minimum%%.*}" ] && [ "$macos_minor" -lt "${macos_minimum#*.}" ]; }; then
        printf 'OpenProgram needs macOS %s or later, and this Mac runs macOS %s.\n' "$macos_minimum" "$macos_version" >&2
        printf 'Update macOS in System Settings > General > Software Update, then run this command again.\n' >&2
        exit 1
      fi
      ;;
  esac
fi

linux_preflight() {
  if ldd --version 2>&1 | grep -qi musl; then
    printf 'OpenProgram needs a Linux system built on glibc, and this one uses musl (Alpine, for example).\n' >&2
    printf 'Use a glibc system such as Ubuntu, Debian or Fedora, then run this command again.\n' >&2
    exit 1
  fi

  linker_cache=""
  linker_tool="$(command -v ldconfig 2>/dev/null || true)"
  if [ -z "$linker_tool" ] && [ -x /sbin/ldconfig ]; then
    linker_tool="/sbin/ldconfig"
  fi
  if [ -n "$linker_tool" ]; then
    linker_cache="$("$linker_tool" -p 2>/dev/null || true)"
  fi
  # Ubuntu 24.04 and Debian 13 renamed these packages for 64-bit time.
  time64_suffix=""
  if command -v apt-cache >/dev/null 2>&1 &&
    apt-cache show libasound2t64 >/dev/null 2>&1; then
    time64_suffix="t64"
  elif [ -r /etc/os-release ]; then
    system_id="$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')"
    system_version="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')"
    case "$system_id:${system_version%%.*}" in
      ubuntu:2[4-9]|ubuntu:[3-9][0-9]|debian:1[3-9]|debian:[2-9][0-9])
        time64_suffix="t64"
        ;;
    esac
  fi
  missing_packages=""
  if [ -n "$linker_cache" ]; then
    for library_and_package in \
      "libnss3.so:libnss3" \
      "libnspr4.so:libnspr4" \
      "libatk-1.0.so.0:libatk1.0-0$time64_suffix" \
      "libatk-bridge-2.0.so.0:libatk-bridge2.0-0$time64_suffix" \
      "libcups.so.2:libcups2$time64_suffix" \
      "libglib-2.0.so.0:libglib2.0-0$time64_suffix" \
      "libdrm.so.2:libdrm2" \
      "libdbus-1.so.3:libdbus-1-3" \
      "libxkbcommon.so.0:libxkbcommon0" \
      "libXcomposite.so.1:libxcomposite1" \
      "libXdamage.so.1:libxdamage1" \
      "libXfixes.so.3:libxfixes3" \
      "libXrandr.so.2:libxrandr2" \
      "libgbm.so.1:libgbm1" \
      "libpango-1.0.so.0:libpango-1.0-0" \
      "libcairo.so.2:libcairo2" \
      "libX11.so.6:libx11-6" \
      "libxcb.so.1:libxcb1" \
      "libXext.so.6:libxext6" \
      "libatspi.so.0:libatspi2.0-0$time64_suffix" \
      "libasound.so.2:libasound2$time64_suffix"; do
      case "$linker_cache" in
        *"${library_and_package%%:*}"*) ;;
        *) missing_packages="$missing_packages ${library_and_package#*:}" ;;
      esac
    done
  fi
  # Only the browser tool needs these, so their absence never stops the
  # install: the runtime check below then leaves the Chromium launch to the
  # browser tool's first use.
  if [ -n "$missing_packages" ]; then
    if command -v apt-get >/dev/null 2>&1; then
      printf 'The browser tool will not work until the system libraries Chromium needs are added; everything else does. Add them any time with: sudo apt-get update && sudo apt-get install -y%s\n' "$missing_packages" >&2
    else
      printf 'The browser tool will not work until the system libraries Chromium needs are added; everything else does. Add them any time with your package manager:%s\n' "$missing_packages" >&2
    fi
  fi

  # OpenProgram carries its own Git, which links against the system's libcurl
  # for HTTPS remotes; a machine's own Git may use a different libcurl build.
  case "$linker_cache" in
    ''|*libcurl.so.4*) ;;
    *)
      printf 'Git over HTTPS needs libcurl, which this computer does not have yet. Local Git works without it.\n' >&2
      if command -v apt-get >/dev/null 2>&1; then
        printf 'To add it, run: sudo apt-get update && sudo apt-get install -y libcurl4%s\n' "$time64_suffix" >&2
      else
        printf 'Install the package your system names for libcurl.so.4.\n' >&2
      fi
      ;;
  esac
}

if [ "$platform" = "linux" ]; then
  linux_preflight
fi

human_size() {
  awk -v bytes="$1" 'BEGIN {
    if (bytes >= 1073741824) { printf "%.1f GB", bytes / 1073741824 }
    else { printf "%.0f MB", bytes / 1048576 }
  }'
}

# The missing amount in the unit that shows it: a need and a free amount within
# the same tenth of a gigabyte print as the same figure.
human_shortfall() {
  awk -v bytes="$1" 'BEGIN {
    if (bytes >= 1073741824) { printf "%.1f GB", bytes / 1073741824 }
    else if (bytes >= 1048576) { printf "%.0f MB", bytes / 1048576 }
    else { printf "less than 1 MB" }
  }'
}

file_size() {
  wc -c < "$1" | tr -d ' '
}

network_failure_help() {
  {
    printf 'Could not download OpenProgram from GitHub. Check that this computer is online.\n'
    printf 'If it reaches the internet through a proxy, set HTTPS_PROXY (and NO_PROXY if some hosts skip the proxy), for example:\n'
    printf '  export HTTPS_PROXY=http://proxy.example:8080\n'
    printf 'Terminal downloads do not use the proxy from system settings. Run this command again afterwards; it continues where it stopped.\n'
  } >&2
}

# Unpacking needs room for the download and for what it expands into.
# How many copies of the archive sit on disk during unpacking. A seed holds two:
# the download and the archives inside it.
archive_copies_on_disk=1

require_free_space() {
  archive_bytes="$1"
  space_directory="$2"
  already_downloaded="${3:-0}"
  available_kilobytes="$(df -Pk "$space_directory" 2>/dev/null | awk 'NR == 2 {print $4}')"
  case "$available_kilobytes" in
    ''|*[!0-9]*) return 0 ;;
  esac
  needed_bytes=$((archive_bytes * (archive_copies_on_disk + 4) + 536870912 - already_downloaded))
  if [ "$((available_kilobytes * 1024))" -lt "$needed_bytes" ]; then
    printf 'Not enough free disk space. OpenProgram needs %s on %s (room for the %s archive, about four times that once unpacked, and 512 MB to spare), and %s is free, %s short.\n' \
      "$(human_size "$needed_bytes")" "$space_directory" "$(human_size "$archive_bytes")" \
      "$(human_size "$((available_kilobytes * 1024))")" \
      "$(human_shortfall "$((needed_bytes - available_kilobytes * 1024))")" >&2
    printf 'Free up some space, then run this command again.\n' >&2
    exit 1
  fi
}

report_missing_download() {
  printf 'release download failed with HTTP %s.\n' "$1" >&2
  case "$1" in
    404|410)
      printf 'OpenProgram %s has no package for %s %s. What each release includes is listed at https://github.com/%s/releases\n' \
        "$OPENPROGRAM_VERSION" "$platform" "$arch" "$OPENPROGRAM_REPOSITORY" >&2
      ;;
  esac
  exit 1
}

# Follows the redirects one by one, so every host is checked against the list.
resolve_release_url() {
  resolved_url="$1"
  header_directory="$2"
  resolved_size=""
  redirects=0
  while [ "$redirects" -le 5 ]; do
    case "$resolved_url" in
      # The same fixed hosts as _RELEASE_HOSTS in github_release/github.py. A signed
      # update source can add hosts for the fetcher; this script cannot check one.
      https://github.com/*|https://release-assets.githubusercontent.com/*|https://fzkuji.github.io/*) ;;
      *) printf 'release URL is not allowed: %s\n' "$resolved_url" >&2; exit 1 ;;
    esac
    headers="$(mktemp "$header_directory/.openprogram-headers.XXXXXX")"
    if ! status="$(curl --disable --proto '=https' --tlsv1.2 \
      --silent --show-error --head --connect-timeout 15 --max-time 60 \
      --retry 3 --retry-delay 2 --retry-connrefused \
      --dump-header "$headers" --output /dev/null \
      --write-out '%{http_code}' "$resolved_url")"; then
      rm -f "$headers"
      network_failure_help
      exit 1
    fi
    case "$status" in
      200)
        resolved_size="$(awk 'tolower($1) == "content-length:" {sub(/\r$/, "", $2); value=$2} END {print value}' "$headers")"
        rm -f "$headers"
        case "$resolved_size" in *[!0-9]*) resolved_size="" ;; esac
        return 0
        ;;
      301|302|303|307|308)
        next_url="$(awk 'tolower($1) == "location:" {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); value=$0} END {print value}' "$headers")"
        rm -f "$headers"
        test -n "$next_url" || { printf 'release redirect has no location.\n' >&2; exit 1; }
        resolved_url="$next_url"
        redirects="$((redirects + 1))"
        ;;
      *)
        rm -f "$headers"
        report_missing_download "$status"
        ;;
    esac
  done
  printf 'release redirect limit exceeded.\n' >&2
  exit 1
}

# A third argument names a large download: it gets a size line, a disk check, a
# progress bar and a partial file that a later attempt or a later run continues.
# curl's own --retry is left out of this transfer because it discards what the
# failed attempt wrote; this loop keeps it and continues from there.
download() {
  source_url="$1"
  destination="$2"
  label="${3:-}"
  command -v curl >/dev/null 2>&1 || {
    printf 'curl is required to download OpenProgram.\n' >&2
    exit 1
  }
  [ -n "$label" ] || rm -f "$destination"
  progress_option="--silent"
  if [ -n "$label" ] && [ -t 2 ]; then
    progress_option="--progress-bar"
  fi
  announced=0
  stalled_attempts=0
  while :; do
    resolve_release_url "$source_url" "$(dirname "$destination")"
    existing_bytes=0
    if [ -f "$destination" ]; then
      existing_bytes="$(file_size "$destination")"
    fi
    if [ -n "$resolved_size" ] && [ "$existing_bytes" -gt "$resolved_size" ]; then
      rm -f "$destination"
      existing_bytes=0
    fi
    if [ -n "$label" ] && [ "$announced" = 0 ]; then
      announced=1
      if [ -n "$resolved_size" ]; then
        if [ "$existing_bytes" -gt 0 ]; then
          printf 'Continuing the download of %s: %s of %s is already here.\n' \
            "$label" "$(human_size "$existing_bytes")" "$(human_size "$resolved_size")"
        else
          printf 'Downloading %s (%s).\n' "$label" "$(human_size "$resolved_size")"
        fi
        require_free_space "$resolved_size" "$(dirname "$destination")" "$existing_bytes"
      else
        printf 'Downloading %s.\n' "$label"
      fi
    fi
    if [ -n "$resolved_size" ] && [ "$existing_bytes" -eq "$resolved_size" ]; then
      return 0
    fi
    transfer_exit=0
    http_status="$(curl --disable --proto '=https' --tlsv1.2 --fail \
      "$progress_option" --show-error --connect-timeout 15 \
      --speed-limit 1024 --speed-time 120 --continue-at - \
      --output "$destination" --write-out '%{http_code}' "$resolved_url")" || transfer_exit=$?
    current_bytes=0
    if [ -f "$destination" ]; then
      current_bytes="$(file_size "$destination")"
    fi
    if [ "$transfer_exit" -eq 0 ]; then
      case "$http_status" in
        20[0-6]|416)
          if [ -z "$resolved_size" ] || [ "$current_bytes" -eq "$resolved_size" ]; then
            return 0
          fi
          ;;
      esac
    fi
    case "$transfer_exit" in
      22)
        case "$http_status" in
          404|410) report_missing_download "$http_status" ;;
        esac
        ;;
      23)
        printf 'Could not write %s. Check that the disk has free space, then run this command again.\n' "$destination" >&2
        exit 1
        ;;
      33|36)
        # The server cannot continue this file, so start it over.
        rm -f "$destination"
        current_bytes=0
        ;;
    esac
    if [ "$current_bytes" -gt "$existing_bytes" ]; then
      stalled_attempts=0
    else
      stalled_attempts="$((stalled_attempts + 1))"
    fi
    if [ "$stalled_attempts" -ge 5 ]; then
      network_failure_help
      exit 1
    fi
    if [ -n "$label" ]; then
      printf 'The download stopped at %s. Trying again from there.\n' "$(human_size "$current_bytes")" >&2
    fi
    sleep 2
  done
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

path_contains() {
  case ":${PATH:-}:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

shell_profile() {
  case "${SHELL:-}" in
    */zsh) printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
    */fish) printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish" ;;
    */bash)
      if [ "$platform" = "linux" ]; then
        printf '%s\n' "$HOME/.bashrc"
      elif [ ! -e "$HOME/.bash_profile" ] && [ -e "$HOME/.profile" ]; then
        printf '%s\n' "$HOME/.profile"
      else
        printf '%s\n' "$HOME/.bash_profile"
      fi
      ;;
    *) printf '%s\n' "$HOME/.profile" ;;
  esac
}

# Sets path_note and next_step. New terminals get the launcher through one
# marked line in the shell profile; OPENPROGRAM_NO_MODIFY_PATH=1 opts out and an
# explicit OPENPROGRAM_BIN_DIR is the person's own layout.
prepare_path_lines() {
  launcher_directory="$1"
  if path_contains "$launcher_directory"; then
    path_note="PATH: $launcher_directory is already on your PATH."
    next_step="Next, run: openprogram"
    return 0
  fi
  path_note="PATH: $launcher_directory is not on your PATH. To add it, run: export PATH=\"$launcher_directory:\$PATH\""
  next_step="Next, run: $launcher_directory/openprogram"
  if [ -n "${OPENPROGRAM_BIN_DIR:-}" ] || [ -n "${OPENPROGRAM_NO_MODIFY_PATH:-}" ]; then
    return 0
  fi
  case "$launcher_directory" in
    *[\"\\\`\$]*) return 0 ;;
  esac
  physical_home="$(if cd "$HOME" 2>/dev/null; then pwd -P; fi)"
  profile_directory="$launcher_directory"
  case "$physical_home:$launcher_directory" in
    :*) ;;
    *:"$physical_home"/*) profile_directory="\$HOME${launcher_directory#"$physical_home"}" ;;
  esac
  profile="$(shell_profile)"
  profile_marker="# Added by the OpenProgram installer"
  case "$profile" in
    */config.fish) profile_line="set -gx PATH \"$profile_directory\" \$PATH $profile_marker" ;;
    *) profile_line="export PATH=\"$profile_directory:\$PATH\" $profile_marker" ;;
  esac
  if [ -f "$profile" ] && grep -Fxq "$profile_line" "$profile"; then
    path_note="PATH: $profile already adds $launcher_directory to your PATH."
    next_step="Next, open a new terminal and run: openprogram"
    return 0
  fi
  if ! {
    mkdir -p "$(dirname "$profile")" &&
      if [ -s "$profile" ] && [ -n "$(tail -c 1 "$profile")" ]; then
        printf '\n' >> "$profile"
      fi &&
      printf '%s\n' "$profile_line" >> "$profile"
  } 2>/dev/null; then
    return 0
  fi
  path_note="PATH: added $launcher_directory to $profile. Set OPENPROGRAM_NO_MODIFY_PATH=1 before installing to skip this."
  next_step="Next, open a new terminal and run: openprogram"
}

print_install_summary() {
  summary_headline="$1"
  summary_launcher_directory="$2"
  summary_runtime="$3"
  prepare_path_lines "$summary_launcher_directory"
  printf '%s\n' "$summary_headline"
  printf 'Executable: %s/openprogram\n' "$summary_launcher_directory"
  printf 'Runtime: %s\n' "$summary_runtime"
  printf '%s\n' 'Desktop control is optional. Before using it, open Settings > System on the execution computer. Installation does not request screen access.'
  printf '%s\n' "$path_note"
  printf '%s\n' "$next_step"
}

staging=""
seed_work=""
base_work=""
probe_home=""
probe_pid_file=""
probe_port=""
probe_active=0
launcher_tmp=""
python_bin=""
failure_note=""

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

# Starts the candidate worker in a throwaway home and waits until it answers.
run_worker_probe() {
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

from openprogram.operations.worker.lifecycle import spawn_detached

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
}

# Invoked by the EXIT trap below.
# shellcheck disable=SC2329,SC2317
cleanup() {
  cleanup_status=$?
  trap - 0 HUP INT TERM
  if [ "$probe_active" = 1 ] && [ -n "$python_bin" ] && [ -n "$probe_home" ]; then
    stop_probe_process_group >/dev/null 2>&1 || true
  fi
  [ -z "$probe_home" ] || rm -rf "$probe_home"
  [ -z "$staging" ] || rm -rf "$staging"
  [ -z "$seed_work" ] || rm -rf "$seed_work"
  [ -z "$base_work" ] || rm -rf "$base_work"
  [ -z "$launcher_tmp" ] || rm -f "$launcher_tmp"
  if [ "$cleanup_status" -ne 0 ] && [ -n "$failure_note" ]; then
    printf '%s\n' "$failure_note" >&2
  fi
  exit "$cleanup_status"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# A release that carries a seed installs into the version store, the same
# layout and `current` pointer the App uses. Older releases fall through to the
# plain runtime below.
seed_url_base="https://github.com/$OPENPROGRAM_REPOSITORY/releases/download/v$OPENPROGRAM_VERSION"
seed_name="OpenProgram-${OPENPROGRAM_VERSION}-seed-${platform}-${arch}.tar.gz"
seed_available() {
  [ -n "${OPENPROGRAM_SEED_ARCHIVE:-}" ] && return 0
  [ -n "${OPENPROGRAM_RUNTIME_ARCHIVE:-}" ] && return 1
  command -v curl >/dev/null 2>&1 || return 1
  [ "$(curl --disable --proto '=https' --tlsv1.2 --silent --location --head \
    --connect-timeout 15 --output /dev/null --write-out '%{http_code}' \
    "$seed_url_base/$seed_name" 2>/dev/null || true)" = 200 ]
}

store_pointer_version() {
  sed -n 's/^version=//p' "$store_root/current" 2>/dev/null | head -n 1
}

store_active_version() {
  "$store_root/bin/openprogram" --version 2>/dev/null || store_pointer_version
}

# The plain runtime is the working install until the store has answered a real
# worker start, so a broken seed can never take the only copy with it.
retire_plain_runtime() {
  [ -d "$store_root/cli" ] || return 0
  failure_note="The new OpenProgram did not start, so the existing installation in $store_root/cli was kept and still works."
  python_bin="$store_root/versions/$(store_pointer_version)/venv/bin/python"
  run_worker_probe
  failure_note=""
  rm -rf "$store_root/cli"
}

publish_store_launcher() {
  launcher_tmp="$(mktemp "$launcher_dir/.openprogram.XXXXXX")"
  {
    printf '#!/bin/sh\n'
    printf 'exec %s "$@"\n' "'$store_root/bin/openprogram'"
  } > "$launcher_tmp"
  chmod 0755 "$launcher_tmp"
  mv -f "$launcher_tmp" "$launcher_dir/openprogram"
  launcher_tmp=""
}

# An existing store updates through its own upgrade command. What this prints
# comes from what that command did, never from the release that was asked for.
update_existing_store() {
  pointer_before="$(store_pointer_version)"
  printf 'OpenProgram is already installed here, active version: %s. Looking for an update.\n' "$(store_active_version)"
  upgrade_exit=0
  "$store_root/bin/openprogram" upgrade || upgrade_exit=$?
  publish_store_launcher
  if [ "$upgrade_exit" -ne 0 ]; then
    printf 'OpenProgram %s was not installed: the update stopped for the reason above. The active version is still %s.\n' \
      "$OPENPROGRAM_VERSION" "$(store_active_version)" >&2
    exit "$upgrade_exit"
  fi
  retire_plain_runtime
  if [ "$(store_pointer_version)" = "$pointer_before" ]; then
    summary_headline="No new version was activated. The active version is still $(store_active_version)."
  else
    summary_headline="OpenProgram updated. The active version is now $(store_active_version)."
  fi
  print_install_summary "$summary_headline" "$launcher_dir" "$store_root"
  exit 0
}

install_from_seed() {
  mkdir -p "$state_root"
  absolute_state="$(cd "$state_root" && pwd -P)"
  store_root="$absolute_state/runtime"
  launcher_dir="${OPENPROGRAM_BIN_DIR:-$HOME/.local/bin}"
  mkdir -p "$launcher_dir" "$store_root"
  launcher_dir="$(cd "$launcher_dir" && pwd -P)"
  if [ -e "$store_root/current" ]; then
    update_existing_store
  fi
  for entry in "$store_root"/* "$store_root"/.seeding; do
    case "$entry" in
      "$store_root/*"|"$store_root/.seeding"|"$store_root/cli"|"$store_root/bases"|"$store_root/versions") ;;
      *) [ -e "$store_root/.seeding" ] || {
           printf '%s already holds files that are not a version store.\n' "$store_root" >&2
           exit 1
         } ;;
    esac
  done
  archive_copies_on_disk=2
  seed_work="$(mktemp -d "$absolute_state/.seed-install.XXXXXX")"
  download_directory="$absolute_state/.install-downloads"
  if [ -n "${OPENPROGRAM_SEED_ARCHIVE:-}" ]; then
    seed_archive="$OPENPROGRAM_SEED_ARCHIVE"
    require_free_space "$(file_size "$seed_archive")" "$absolute_state"
  else
    mkdir -p "$download_directory"
    seed_archive="$download_directory/$seed_name"
    download "$seed_url_base/$seed_name" "$seed_archive" "OpenProgram $OPENPROGRAM_VERSION for $platform $arch"
    download "$seed_url_base/$seed_name.sha256" "$seed_archive.sha256"
  fi
  seed_expected="${OPENPROGRAM_SEED_SHA256:-}"
  if [ -z "$seed_expected" ] && [ -f "$seed_archive.sha256" ]; then
    seed_expected="$(awk 'NR == 1 {print $1}' "$seed_archive.sha256")"
  fi
  test "$(printf '%s' "$seed_expected" | wc -c | tr -d ' ')" = 64 || {
    printf 'seed archive checksum must be exactly 64 hexadecimal characters.\n' >&2
    exit 1
  }
  test "$(checksum "$seed_archive" | tr 'A-F' 'a-f')" = "$(printf '%s' "$seed_expected" | tr 'A-F' 'a-f')" || {
    if [ -z "${OPENPROGRAM_SEED_ARCHIVE:-}" ]; then
      rm -f "$seed_archive" "$seed_archive.sha256"
    fi
    printf 'seed archive checksum mismatch. The damaged download was removed. Run this command again.\n' >&2
    exit 1
  }
  tar -tzf "$seed_archive" | while IFS= read -r entry; do
    case "/$entry/" in */../*) printf 'invalid archive path: %s\n' "$entry" >&2; exit 1 ;; esac
    case "$entry" in /*) printf 'invalid archive path: %s\n' "$entry" >&2; exit 1 ;; esac
  done
  tar -C "$seed_work" -xzf "$seed_archive"
  seed_dir="$seed_work/seed"
  seed_base="$(sed -n 's/^  "base": "\(.*\)",*$/\1/p' "$seed_dir/seed.json")"
  case "$seed_base" in ''|*[!A-Za-z0-9._-]*) printf 'invalid seed.\n' >&2; exit 1 ;; esac
  printf '%s\n' "$$" > "$store_root/.seeding"
  # The base is unpacked beside the store and moved in only when complete, so an
  # interrupted run never leaves a half base that the next run would accept.
  if [ ! -d "$store_root/bases/$seed_base" ]; then
    base_work="$store_root/.seed-work"
    rm -rf "$base_work"
    mkdir -p "$base_work" "$store_root/bases"
    tar -C "$base_work" -xzf "$seed_dir/base.tar.gz"
    mv "$base_work/bases/$seed_base" "$store_root/bases/$seed_base"
    rm -rf "$base_work"
    base_work=""
  fi
  "$store_root/bases/$seed_base/python/bin/python3" -I -B "$seed_dir/bootstrap/install_seed.py" \
    --seed "$seed_dir" --root "$store_root" --extract >/dev/null
  rm -rf "$seed_work"
  seed_work=""
  rm -rf "$download_directory"
  "$store_root/bin/openprogram" --version
  retire_plain_runtime
  publish_store_launcher
  print_install_summary "OpenProgram $OPENPROGRAM_VERSION installed." "$launcher_dir" "$store_root"
  exit 0
}

if seed_available; then
  install_from_seed
fi

mkdir -p "$runtime_root/releases"
state_root="$(cd "$state_root" && pwd -P)"
runtime_root="$state_root/runtime/cli"
release_dir="$runtime_root/releases/$OPENPROGRAM_VERSION"
install_lock="$runtime_root/.install.lock"

candidate_dir="$release_dir"
if [ ! -d "$release_dir" ]; then
  staging="$(mktemp -d "$runtime_root/.staging-$OPENPROGRAM_VERSION.XXXXXX")"
  archive_name="OpenProgram-${OPENPROGRAM_VERSION}-runtime-${platform}-${arch}.tar.gz"
  download_directory="$state_root/.install-downloads"
  archive="${OPENPROGRAM_RUNTIME_ARCHIVE:-$download_directory/$archive_name}"
  if [ -z "${OPENPROGRAM_RUNTIME_ARCHIVE:-}" ]; then
    mkdir -p "$download_directory"
    release_url="https://github.com/$OPENPROGRAM_REPOSITORY/releases/download/v$OPENPROGRAM_VERSION"
    download "$release_url/$archive_name" "$archive" "OpenProgram $OPENPROGRAM_VERSION for $platform $arch"
    download "$release_url/$archive_name.sha256" "$archive.sha256"
  else
    case "$archive" in
      /*.tar.gz) test -f "$archive" || { printf 'OPENPROGRAM_RUNTIME_ARCHIVE not found.\n' >&2; exit 1; } ;;
      *) printf 'OPENPROGRAM_RUNTIME_ARCHIVE must be an absolute .tar.gz path.\n' >&2; exit 1 ;;
    esac
    require_free_space "$(file_size "$archive")" "$state_root"
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
    if [ -z "${OPENPROGRAM_RUNTIME_ARCHIVE:-}" ]; then
      rm -f "$archive" "$archive.sha256"
    fi
    printf 'runtime archive checksum mismatch. The damaged download was removed. Run this command again.\n' >&2
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
  if [ -z "${OPENPROGRAM_RUNTIME_ARCHIVE:-}" ]; then
    rm -rf "$download_directory"
  fi
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
verify_options=""
if [ -n "${missing_packages:-}" ]; then
  verify_options="--allow-deferred-browser"
fi
# shellcheck disable=SC2086 # one optional flag, or none
"$python_bin" -I "$candidate_dir/bin/verify-product-runtime.py" "$candidate_dir" $verify_options
"$python_bin" -I -m openprogram --version

playwright_path="$candidate_dir/assets/playwright"
run_worker_probe

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
    "export OPENPROGRAM_IMMUTABLE_RUNTIME=1\n"
    'if [ -d "$runtime_root/current/git/bin" ]; then\n'
    '  export PATH="$runtime_root/current/git/bin:$PATH"\n'
    '  export GIT_EXEC_PATH="$runtime_root/current/git/libexec/git-core"\n'
    '  export GIT_TEMPLATE_DIR="$runtime_root/current/git/share/git-core/templates"\n'
    "fi\n"
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

print_install_summary "OpenProgram $OPENPROGRAM_VERSION installed." "$launcher_dir" "$release_dir"
exit 0
