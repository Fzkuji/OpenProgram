#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
runtime_root="$repo_root/apps/desktop/build/runtime"
archive="${OPENPROGRAM_RUNTIME_ARCHIVE:-}"

if test -z "$archive"; then
  OPENPROGRAM_RUNTIME_ROOT="$runtime_root" \
    "$repo_root/scripts/release/build-product-runtime.sh"
  exit 0
fi

case "$archive" in
  /*.tar.gz) ;;
  *) printf 'OPENPROGRAM_RUNTIME_ARCHIVE must be an absolute .tar.gz path\n' >&2; exit 1 ;;
esac
test -f "$archive" || {
  printf 'runtime archive not found: %s\n' "$archive" >&2
  exit 1
}

expected="${OPENPROGRAM_RUNTIME_SHA256:-}"
if test -z "$expected" && test -f "$archive.sha256"; then
  expected="$(awk 'NR == 1 {print $1}' "$archive.sha256")"
fi
test -n "$expected" || {
  printf 'runtime archive checksum is required\n' >&2
  exit 1
}
if command -v shasum >/dev/null 2>&1; then
  actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
else
  actual="$(sha256sum "$archive" | awk '{print $1}')"
fi
test "$actual" = "$expected" || {
  printf 'runtime archive checksum mismatch\n' >&2
  exit 1
}
tar -tzf "$archive" | while IFS= read -r entry; do
  case "$entry" in runtime|runtime/*) ;; *) printf 'invalid archive path: %s\n' "$entry" >&2; exit 1 ;; esac
  case "/$entry/" in */../*) printf 'invalid archive path: %s\n' "$entry" >&2; exit 1 ;; esac
done

rm -rf "$runtime_root"
mkdir -p "$(dirname "$runtime_root")"
tar -C "$(dirname "$runtime_root")" -xzf "$archive"
test -f "$runtime_root/runtime-manifest.json" || {
  printf 'archive does not contain runtime/runtime-manifest.json\n' >&2
  exit 1
}
python_relative="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$runtime_root/runtime-manifest.json")"
python_bin="$runtime_root/$python_relative"
test -x "$python_bin"
"$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" "$runtime_root"
printf 'prepared desktop from complete runtime archive: %s\n' "$archive"
