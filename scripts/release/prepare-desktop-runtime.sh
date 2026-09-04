#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
runtime_root="$repo_root/apps/desktop/build/runtime"
archive="${OPENPROGRAM_RUNTIME_ARCHIVE:-}"
base="${OPENPROGRAM_SELF_UPDATE_RUNTIME_BASE:-}"

if test -n "$base"; then
  test -z "$archive" && test -d "$base" && test ! -L "$base" && test ! -e "$runtime_root" || {
    printf 'self-update requires one private runtime base and a new output directory\n' >&2
    exit 1
  }
  cmp -s "$base/product-uv.lock" "$repo_root/uv.lock" && \
    cmp -s "$base/product-runtime.json" "$repo_root/scripts/release/product-runtime.json" || {
    printf 'self-update runtime dependencies do not match candidate lockfiles\n' >&2
    exit 1
  }
  "$repo_root/scripts/release/stage-release-assets.sh"
  mkdir -p "$(dirname "$runtime_root")"
  /bin/cp -cR "$base" "$runtime_root"
  python_relative="$(sed -n 's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' "$runtime_root/runtime-manifest.json")"
  case "$python_relative" in ''|/*|*..*) printf 'invalid runtime Python path\n' >&2; exit 1 ;; esac
  python_bin="$runtime_root/$python_relative"
  uv_bin="$runtime_root/bin/uv"
  wheel_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-self-update-wheel.XXXXXX")"
  trap 'rm -rf "$wheel_dir"' EXIT
  "$uv_bin" build --offline --wheel --out-dir "$wheel_dir" "$repo_root"
  wheel="$(find "$wheel_dir" -maxdepth 1 -type f -name 'openprogram-*.whl' -print -quit)"
  test -n "$wheel"
  "$python_bin" -I "$repo_root/scripts/release/remove-stale-openprogram-packages.py" "$python_bin"
  "$uv_bin" pip install --offline --python "$python_bin" --strict \
    --break-system-packages --no-deps --force-reinstall "$wheel"
  package_version="$("$python_bin" -I -c 'from importlib.metadata import version; print(version("openprogram"))')"
  uv_version="$("$uv_bin" --version | awk '{print $2}')"
  cp "$repo_root/scripts/release/verify-product-runtime.py" "$runtime_root/bin/verify-product-runtime.py"
  "$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" "$runtime_root" \
    --write --defer-browser --python-relative "$python_relative" \
    --openprogram-version "$package_version" --uv-version "$uv_version"
  printf 'prepared self-update runtime from matching complete dependency input\n'
  exit 0
fi

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
