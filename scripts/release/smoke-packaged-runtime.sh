#!/usr/bin/env bash
set -euo pipefail

# Checks a built App before it is released, in a temporary HOME. A thin shell
# carries a seed instead of a runtime: it is unpacked with the shell's own code
# and started the way a first launch would.

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
platform="${1:?usage: smoke-packaged-runtime.sh mac <artifact-dir>}"
artifact_dir="${2:?usage: smoke-packaged-runtime.sh mac <artifact-dir>}"
artifact_dir="$(cd "$artifact_dir" && pwd)"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-packaged-smoke.XXXXXX")"
state_home="$temp_root/home"
mkdir -p "$state_home"

case "$platform" in
  mac)
    app_path="$(find "$artifact_dir" -type d -name OpenProgram.app -print -quit)"
    test -n "$app_path" || { printf 'OpenProgram.app not found\n' >&2; exit 1; }
    resources="$app_path/Contents/Resources"
    ;;
  *) printf 'unsupported packaged-runtime platform: %s\n' "$platform" >&2; exit 1 ;;
esac

run_with_timeout() {
  local seconds="$1"
  shift
  local pid elapsed=0
  "$@" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if test "$elapsed" -ge "$seconds"; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      sleep 2
      pkill -KILL -P "$pid" 2>/dev/null || true
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      printf 'timed out after %ss: %s\n' "$seconds" "$*" >&2
      return 124
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  wait "$pid"
}

if test -f "$resources/seed/seed.json"; then
  seed="$resources/seed"
  for item in base.tar.zst version.tar.zst repo.bundle shell-compat.json bootstrap/install_seed.py \
    helper/OpenProgram.app/Contents/MacOS/OpenProgram; do
    test -e "$seed/$item" || { printf 'the seed is missing %s\n' "$item" >&2; exit 1; }
  done
  command -v node >/dev/null 2>&1 || { printf 'node is required to unpack the seed\n' >&2; exit 1; }
  store="$state_home/.openprogram/runtime"
  port="${OPENPROGRAM_SMOKE_PORT:-$((19000 + RANDOM % 500))}"
  cleanup() {
    HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" "$store/bin/openprogram" worker stop >/dev/null 2>&1 || true
    rm -rf "$temp_root"
  }
  trap cleanup EXIT
  SEED_ROOT="$store" SEED_DIRECTORY="$seed" SHELL_SEED="$repository/apps/desktop/shell/seed.js" \
    run_with_timeout 600 node -e '
      const { seed } = require(process.env.SHELL_SEED);
      seed({ root: process.env.SEED_ROOT, seedDirectory: process.env.SEED_DIRECTORY })
        .catch((error) => { console.error(error); process.exit(1); });
    '
  test -f "$store/current" || { printf 'the seed set no current version\n' >&2; exit 1; }
  # After signing, a compiled package must still load through the version's
  # Python, including one built on this machine.
  version_id="$(sed -n 's/^version=//p' "$store/current")"
  base_root="$store/bases/$(sed -n 's/^base=//p' "$store/current")"
  run_with_timeout 60 "$store/versions/$version_id/venv/bin/python" -I -B \
    "$base_root/bin/verify-product-runtime.py" "$base_root" --compiled-import-only
  HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" run_with_timeout 90 "$store/bin/openprogram" worker start
  ready=0
  for _attempt in $(seq 1 240); do
    if curl -fs "http://127.0.0.1:$port/healthz" >"$temp_root/health.json"; then
      ready=1
      break
    fi
    sleep 0.25
  done
  if test "$ready" != 1; then
    sed -n '1,200p' "$state_home/.openprogram/worker.log" >&2 || true
    exit 1
  fi
  grep -q '"status":"ok"' "$temp_root/health.json"
  test "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/chat")" = 200
  printf 'packaged thin shell smoke passed for %s\n' "$platform"
  exit 0
fi

manifest="$resources/runtime/runtime-manifest.json"
test -f "$manifest" || {
  printf 'packaged runtime manifest not found: %s\n' "$manifest" >&2
  exit 1
}
runtime_python="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$manifest")"
test -n "$runtime_python" || {
  printf 'managed Python path missing from packaged runtime manifest\n' >&2
  exit 1
}
embedded_python="$resources/runtime/$runtime_python"
test -x "$embedded_python" || {
  printf 'managed Python is not executable: %s\n' "$embedded_python" >&2
  exit 1
}
PLAYWRIGHT_BROWSERS_PATH="$resources/runtime/assets/playwright"
# Packaged desktop smoke runs after Developer ID signing. Launching Playwright
# Chromium on ARM GitHub runners hangs until killed; the product-runtime job
# already launched Chromium before signing.
: "${OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER:=1}"
export PLAYWRIGHT_BROWSERS_PATH

verify_args=("$resources/runtime")
if test "${OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER:-}" = 1; then
  verify_args+=(--allow-deferred-browser)
fi
run_with_timeout 180 "$embedded_python" -I "$resources/runtime/bin/verify-product-runtime.py" \
  "${verify_args[@]}"
run_with_timeout 60 "$embedded_python" -I -B "$resources/runtime/bin/verify-product-runtime.py" \
  "$resources/runtime" --compiled-import-only
# `worker start` re-executes into the named helper, which must load them too.
worker_helper="$(sed -n \
  's/^[[:space:]]*"worker_python":[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest")"
if test -n "$worker_helper"; then
  run_with_timeout 60 "$resources/runtime/$worker_helper" -I -B "$resources/runtime/bin/verify-product-runtime.py" \
    "$resources/runtime" --compiled-import-only
fi
run_with_timeout 30 "$resources/runtime/bin/node" "$resources/runtime/assets/tui/index.cjs" --probe

port="${OPENPROGRAM_SMOKE_PORT:-$((19000 + RANDOM % 500))}"
case "$port" in ''|*[!0-9]*) printf 'invalid packaged smoke port\n' >&2; exit 1 ;; esac
test "$port" -ge 1024 && test "$port" -le 65535 && test "$port" != 18100 || {
  printf 'invalid packaged smoke port\n' >&2
  exit 1
}
cleanup() {
  HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" OPENPROGRAM_IMMUTABLE_RUNTIME=1 \
    "$embedded_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || true
  rm -rf "$temp_root"
}
trap cleanup EXIT

HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" OPENPROGRAM_IMMUTABLE_RUNTIME=1 \
  run_with_timeout 90 "$embedded_python" -I -B -m openprogram worker start

ready=0
for _attempt in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$port/healthz" >"$temp_root/health.json"; then
    ready=1
    break
  fi
  sleep 0.25
done
if test "$ready" != 1; then
  sed -n '1,200p' "$state_home/.openprogram/worker.log" >&2
  exit 1
fi
grep -q '"status":"ok"' "$temp_root/health.json"
test "$(curl -sS -o "$temp_root/chat.html" -w '%{http_code}' "http://127.0.0.1:$port/chat")" = 200

printf 'packaged runtime smoke passed for %s\n' "$platform"
