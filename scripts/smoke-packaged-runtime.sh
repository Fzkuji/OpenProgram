#!/usr/bin/env bash
set -euo pipefail

platform="${1:?usage: smoke-packaged-runtime.sh <mac|linux> <artifact-dir>}"
artifact_dir="${2:?usage: smoke-packaged-runtime.sh <mac|linux> <artifact-dir>}"
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
  linux)
    appimage="$(find "$artifact_dir" -maxdepth 1 -type f -name '*.AppImage' -print -quit)"
    test -n "$appimage" || { printf 'AppImage not found\n' >&2; exit 1; }
    (
      cd "$temp_root"
      "$appimage" --appimage-extract >/dev/null
    )
    resources="$temp_root/squashfs-root/resources"
    ;;
  *) printf 'unsupported packaged-runtime platform: %s\n' "$platform" >&2; exit 1 ;;
esac

manifest="$resources/runtime/runtime-manifest.json"
test -f "$manifest"
runtime_python="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "$manifest")"
embedded_python="$resources/runtime/$runtime_python"
test -x "$embedded_python"

port="$((19000 + RANDOM % 500))"
cleanup() {
  HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" OPENPROGRAM_IMMUTABLE_RUNTIME=1 \
    "$embedded_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || true
  rm -rf "$temp_root"
}
trap cleanup EXIT

if test "$platform" = mac; then
  codesign --verify --deep --strict "$app_path"
fi

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

if test "$platform" = mac; then
  codesign --verify --deep --strict "$app_path"
fi
printf 'packaged runtime smoke passed for %s\n' "$platform"
