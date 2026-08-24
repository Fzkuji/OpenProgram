#!/usr/bin/env sh
set -eu

OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-0.8.1}"
OPENPROGRAM_REPOSITORY="${OPENPROGRAM_REPOSITORY:-Fzkuji/OpenProgram}"
state_root="${OPENPROGRAM_STATE_DIR:-$HOME/.openprogram}"
runtime_root="$state_root/runtime/cli"
release_dir="$runtime_root/releases/$OPENPROGRAM_VERSION"

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
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

mkdir -p "$runtime_root/releases"
if [ ! -d "$release_dir" ]; then
  staging="$(mktemp -d "$runtime_root/.staging-$OPENPROGRAM_VERSION.XXXXXX")"
  archive_name="OpenProgram-${OPENPROGRAM_VERSION}-runtime-${platform}-${arch}.tar.gz"
  archive="${OPENPROGRAM_RUNTIME_ARCHIVE:-$staging/$archive_name}"
  cleanup_staging() { rm -rf "$staging"; }
  trap cleanup_staging EXIT HUP INT TERM
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
  test -n "$expected" || {
    printf 'runtime archive checksum is required.\n' >&2
    exit 1
  }
  actual="$(checksum "$archive")"
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
  mv "$staging/runtime" "$release_dir"
  trap - EXIT HUP INT TERM
  rm -rf "$staging"
fi

manifest="$release_dir/runtime-manifest.json"
python_relative="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest")"
python_bin="$release_dir/$python_relative"
test -x "$python_bin" || { printf 'managed Python is missing.\n' >&2; exit 1; }
"$python_bin" -I "$release_dir/bin/verify-product-runtime.py" "$release_dir"
"$python_bin" -I -m openprogram --version

playwright_path="$release_dir/assets/playwright"
gpa_model_path="$release_dir/assets/gpa/model.pt"
probe_port="$((20000 + $$ % 10000))"
probe_home="$release_dir/.probe-home-$$"
mkdir -p "$probe_home"
probe_active=1
stop_probe() {
  if [ "$probe_active" = 1 ]; then
    HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port" \
      PLAYWRIGHT_BROWSERS_PATH="$playwright_path" \
      GPA_MODEL_PATH="$gpa_model_path" \
      "$python_bin" -I -B -m openprogram worker stop >/dev/null 2>&1 || true
  fi
  rm -rf "$probe_home"
}
trap stop_probe EXIT HUP INT TERM
HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port" \
  PLAYWRIGHT_BROWSERS_PATH="$playwright_path" \
  GPA_MODEL_PATH="$gpa_model_path" \
  "$python_bin" -I -B -m openprogram worker start
"$python_bin" -I -B - "$probe_port" <<'PY'
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
            break
    except Exception:
        if attempt == 119:
            raise
        time.sleep(0.25)
PY
HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port" \
  PLAYWRIGHT_BROWSERS_PATH="$playwright_path" \
  GPA_MODEL_PATH="$gpa_model_path" \
  "$python_bin" -I -B -m openprogram worker stop
probe_active=0
rm -rf "$probe_home"
trap - EXIT HUP INT TERM

ln -sfn "$python_bin" "$release_dir/bin/python"
launcher_dir="${OPENPROGRAM_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$launcher_dir"
launcher_tmp="$(mktemp "$launcher_dir/.openprogram.XXXXXX")"
cleanup_launcher() { rm -f "$launcher_tmp"; }
trap cleanup_launcher EXIT HUP INT TERM
cat > "$launcher_tmp" <<EOF
#!/bin/sh
export PLAYWRIGHT_BROWSERS_PATH="$runtime_root/current/assets/playwright"
export GPA_MODEL_PATH="$runtime_root/current/assets/gpa/model.pt"
export OPENPROGRAM_IMMUTABLE_RUNTIME=1
exec "$runtime_root/current/bin/python" -I -m openprogram "\$@"
EOF
chmod 0755 "$launcher_tmp"
mv -f "$launcher_tmp" "$launcher_dir/openprogram"
trap - EXIT HUP INT TERM

next_link="$runtime_root/.current-$OPENPROGRAM_VERSION-$$"
ln -s "$release_dir" "$next_link"
"$python_bin" -I -B - "$next_link" "$runtime_root/current" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY

set +e
printf 'OpenProgram %s installed.\n' "$OPENPROGRAM_VERSION"
printf 'Executable: %s/openprogram\n' "$launcher_dir"
printf 'Runtime: %s\n' "$release_dir"
exit 0
