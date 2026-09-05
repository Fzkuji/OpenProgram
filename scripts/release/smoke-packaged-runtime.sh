#!/usr/bin/env bash
set -euo pipefail

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
GPA_MODEL_PATH="$resources/runtime/assets/gpa/model.pt"
export PLAYWRIGHT_BROWSERS_PATH GPA_MODEL_PATH
verify_args=()
if test "${OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER:-}" = 1; then
  verify_args+=(--allow-deferred-browser)
fi
"$embedded_python" -I "$resources/runtime/bin/verify-product-runtime.py" \
  "$resources/runtime" "${verify_args[@]}"
"$resources/runtime/bin/node" "$resources/runtime/assets/tui/index.cjs" --probe

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
  "$embedded_python" -I -B -m openprogram worker start

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

if HOME="$state_home" OPENPROGRAM_IMMUTABLE_RUNTIME=1 \
  "$embedded_python" -I -B -m openprogram programs install research \
  >"$temp_root/program-install.log" 2>&1; then
  printf 'packaged runtime unexpectedly allowed Program installation\n' >&2
  exit 1
fi
grep -q 'disabled in the packaged desktop runtime' "$temp_root/program-install.log"

printf 'packaged runtime smoke passed for %s\n' "$platform"
