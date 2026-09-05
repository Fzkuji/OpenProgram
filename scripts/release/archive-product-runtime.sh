#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
runtime_root="${OPENPROGRAM_RUNTIME_ROOT:-$repo_root/apps/desktop/build/runtime}"
output_dir="${OPENPROGRAM_RUNTIME_OUTPUT_DIR:-$repo_root/dist}"
platform="${OPENPROGRAM_RUNTIME_PLATFORM:-}"
arch="${OPENPROGRAM_RUNTIME_ARCH:-}"

test -f "$runtime_root/runtime-manifest.json" || {
  printf 'runtime manifest not found: %s\n' "$runtime_root" >&2
  exit 1
}
test "$(basename "$runtime_root")" = runtime || {
  printf 'OPENPROGRAM_RUNTIME_ROOT must end in /runtime: %s\n' "$runtime_root" >&2
  exit 1
}
if test -z "$platform" || test -z "$arch"; then
  printf 'OPENPROGRAM_RUNTIME_PLATFORM and OPENPROGRAM_RUNTIME_ARCH are required\n' >&2
  exit 1
fi
case "$platform" in
  linux|macos) ;;
  *) printf 'unsupported POSIX runtime platform: %s\n' "$platform" >&2; exit 1 ;;
esac
case "$arch" in
  x86_64|arm64) ;;
  *) printf 'unsupported runtime architecture: %s\n' "$arch" >&2; exit 1 ;;
esac

python_relative="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$runtime_root/runtime-manifest.json")"
python_bin="$runtime_root/$python_relative"
test -x "$python_bin"
"$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" "$runtime_root"
manifest_identity="$("$python_bin" -I -B - \
  "$runtime_root/runtime-manifest.json" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"{manifest.get('platform', '')}|{manifest.get('architecture', '')}")
PY
)"
manifest_platform="${manifest_identity%%|*}"
manifest_arch="${manifest_identity#*|}"
case "$manifest_platform" in
  darwin) manifest_platform="macos" ;;
esac
case "$manifest_arch" in
  amd64) manifest_arch="x86_64" ;;
  aarch64) manifest_arch="arm64" ;;
esac
test "$manifest_platform" = "$platform" || {
  printf 'runtime manifest platform %s cannot be archived as %s\n' \
    "$manifest_platform" "$platform" >&2
  exit 1
}
test "$manifest_arch" = "$arch" || {
  printf 'runtime manifest architecture %s cannot be archived as %s\n' \
    "$manifest_arch" "$arch" >&2
  exit 1
}
version="$("$python_bin" -I -c \
  'from importlib.metadata import version; print(version("openprogram"))')"

mkdir -p "$output_dir"
archive="$output_dir/OpenProgram-${version}-runtime-${platform}-${arch}.tar.gz"
archive_tmp="$(mktemp "$output_dir/.openprogram-runtime.XXXXXX")"
checksum_tmp="$(mktemp "$output_dir/.openprogram-checksum.XXXXXX")"
cleanup() { rm -f "$archive_tmp" "$checksum_tmp"; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
tar -C "$(dirname "$runtime_root")" -czf "$archive_tmp" "$(basename "$runtime_root")"
max_bytes=2147483648
size="$(wc -c < "$archive_tmp" | tr -d ' ')"
if test "$size" -ge "$max_bytes"; then
  printf 'runtime archive exceeds GitHub Release 2GiB limit: %s (%s bytes)\n' \
    "$archive" "$size" >&2
  exit 1
fi
if command -v shasum >/dev/null 2>&1; then
  digest="$(shasum -a 256 "$archive_tmp" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
  digest="$(sha256sum "$archive_tmp" | awk '{print $1}')"
else
  printf 'shasum or sha256sum is required to archive OpenProgram.\n' >&2
  exit 1
fi
printf '%s  %s\n' "$digest" "$(basename "$archive")" > "$checksum_tmp"
# mktemp starts at 0600; release artifacts are ordinary distributable files,
# so restore the conventional read permissions before publishing them.
chmod 0644 "$archive_tmp" "$checksum_tmp"
mv -f "$archive_tmp" "$archive"
mv -f "$checksum_tmp" "$archive.sha256"
trap - EXIT HUP INT TERM
printf '%s\n' "$archive"
