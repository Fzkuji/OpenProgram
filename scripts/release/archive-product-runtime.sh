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
test -n "$platform" && test -n "$arch" || {
  printf 'OPENPROGRAM_RUNTIME_PLATFORM and OPENPROGRAM_RUNTIME_ARCH are required\n' >&2
  exit 1
}

python_relative="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$runtime_root/runtime-manifest.json")"
python_bin="$runtime_root/$python_relative"
test -x "$python_bin"
"$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" "$runtime_root"
version="$("$python_bin" -I -c \
  'from importlib.metadata import version; print(version("openprogram"))')"

mkdir -p "$output_dir"
archive="$output_dir/OpenProgram-${version}-runtime-${platform}-${arch}.tar.gz"
tar -C "$(dirname "$runtime_root")" -czf "$archive" "$(basename "$runtime_root")"
max_bytes=2147483648
size="$(wc -c < "$archive" | tr -d ' ')"
if test "$size" -ge "$max_bytes"; then
  printf 'runtime archive exceeds GitHub Release 2GiB limit: %s (%s bytes)\n' \
    "$archive" "$size" >&2
  exit 1
fi
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$archive" > "$archive.sha256"
else
  sha256sum "$archive" > "$archive.sha256"
fi
printf '%s\n' "$archive"
