#!/usr/bin/env bash
set -euo pipefail

platform="${1:?usage: smoke-packaged-runtime.sh <mac|linux> <artifact-dir>}"
artifact_dir="${2:?usage: smoke-packaged-runtime.sh <mac|linux> <artifact-dir>}"
artifact_dir="$(cd "$artifact_dir" && pwd)"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-packaged-smoke.XXXXXX")"
state_home="$temp_root/home"
mkdir -p "$state_home"
app_pid=""

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
    desktop_file="$(find "$temp_root/squashfs-root" -type f -name '*.desktop' -print -quit)"
    test -n "$desktop_file"
    test "$(basename "$desktop_file")" = "ai.openprogram.OpenProgram.desktop"
    grep -qx 'StartupWMClass=ai.openprogram.OpenProgram' "$desktop_file"
    ;;
  *) printf 'unsupported packaged-runtime platform: %s\n' "$platform" >&2; exit 1 ;;
esac

manifest="$resources/runtime/runtime-manifest.json"
test -f "$manifest"
runtime_python="$(sed -n 's/.*"python":"\([^"]*\)".*/\1/p' "$manifest")"
test -n "$runtime_python"
embedded_python="$resources/runtime/$runtime_python"
test -x "$embedded_python"

port="$((19000 + RANDOM % 500))"
cleanup() {
  if test -n "$app_pid"; then
    kill "$app_pid" >/dev/null 2>&1 || true
    wait "$app_pid" >/dev/null 2>&1 || true
  fi
  HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" OPENPROGRAM_IMMUTABLE_RUNTIME=1 \
    "$embedded_python" -I -B -m openprogram worker stop >/dev/null 2>&1 || true
  rm -rf "$temp_root"
}
trap cleanup EXIT

if test "$platform" = mac; then
  codesign --verify --deep --strict "$app_path"
  HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" OPENPROGRAM_IMMUTABLE_RUNTIME=1 \
    "$embedded_python" -I -B -m openprogram worker start
else
  command -v xvfb-run >/dev/null 2>&1 || {
    printf 'xvfb-run is required for the Linux AppImage smoke test\n' >&2
    exit 1
  }
  HOME="$state_home" OPENPROGRAM_WEB_PORT="$port" APPIMAGE_EXTRACT_AND_RUN=1 \
    xvfb-run -a "$appimage" >"$temp_root/appimage.log" 2>&1 &
  app_pid=$!
fi

ready=0
for _attempt in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$port/healthz" >"$temp_root/health.json"; then
    ready=1
    break
  fi
  sleep 0.25
done
if test "$ready" != 1; then
  if test "$platform" = linux; then
    sed -n '1,200p' "$temp_root/appimage.log" >&2
  fi
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
