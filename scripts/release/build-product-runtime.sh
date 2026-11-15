#!/usr/bin/env bash
set -euo pipefail

# Pinned inputs (Python, uv, Git, the office runtime) are declared beside this
# script in product-runtime.json. build-base.sh unpacks the pinned Git into the
# runtime, and verification below fails without it. The runtime bundles no
# Programs.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
product_config="$repo_root/scripts/release/product-runtime.json"
runtime_root="${OPENPROGRAM_RUNTIME_ROOT:-$repo_root/apps/desktop/build/runtime}"
uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"
json_python="${OPENPROGRAM_BUILD_PYTHON:-$(command -v python3 || true)}"

test "$(basename "$runtime_root")" = runtime || {
  printf 'OPENPROGRAM_RUNTIME_ROOT must end in /runtime: %s\n' "$runtime_root" >&2
  exit 1
}
if test -n "${OPENPROGRAM_RUNTIME_ROOT:-}" && test -e "$runtime_root"; then
  printf 'custom OPENPROGRAM_RUNTIME_ROOT already exists: %s\n' "$runtime_root" >&2
  exit 1
fi

for command_name in npm node git; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'missing build command: %s\n' "$command_name" >&2
    exit 1
  }
done
if test -z "$uv_bin" || ! test -x "$uv_bin"; then
  printf 'missing build command: uv\n' >&2
  exit 1
fi
if test -z "$json_python" || ! test -x "$json_python"; then
  printf 'missing build command: python3\n' >&2
  exit 1
fi

read_config() {
  "$json_python" - "$product_config" "$1" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
for key in sys.argv[2].split("."):
    value = value[key]
print(value)
PY
}

PYTHON_VERSION="$(read_config python)"
UV_VERSION="$(read_config uv)"
actual_uv_version="$($uv_bin --version | awk '{print $2}')"
test "$actual_uv_version" = "$UV_VERSION" || {
  printf 'uv version mismatch: expected %s, got %s\n' \
    "$UV_VERSION" "$actual_uv_version" >&2
  exit 1
}

"$repo_root/scripts/release/stage-release-assets.sh"

previous_runtime="${runtime_root}.previous.$$"
if test -e "$runtime_root"; then
  test ! -e "$previous_runtime" || { printf 'runtime backup path already exists\n' >&2; exit 1; }
  mv "$runtime_root" "$previous_runtime"
fi
restore_previous_runtime() {
  if test -e "$previous_runtime"; then
    rm -rf "$runtime_root"
    mv "$previous_runtime" "$runtime_root"
  fi
}
trap restore_previous_runtime EXIT HUP INT TERM
rm -rf "$repo_root/build"
mkdir -p \
  "$runtime_root/assets/playwright" \
  "$runtime_root/assets/tui" \
  "$runtime_root/bin" \
  "$runtime_root/python" \
  "$runtime_root/wheel"



"$uv_bin" build --wheel --out-dir "$runtime_root/wheel" "$repo_root"
UV_PYTHON_INSTALL_DIR="$runtime_root/python" \
  "$uv_bin" python install "$PYTHON_VERSION" \
    --install-dir "$runtime_root/python" --no-bin
python_bin="$(UV_PYTHON_INSTALL_DIR="$runtime_root/python" \
  "$uv_bin" python find --managed-python "$PYTHON_VERSION")"
wheel="$(find "$runtime_root/wheel" -maxdepth 1 -type f \
  -name 'openprogram-*.whl' -print -quit)"
test -n "$wheel" || {
  printf 'OpenProgram wheel was not built\n' >&2
  exit 1
}

OPENPROGRAM_UV_BIN="$uv_bin" "$repo_root/scripts/release/build-base.sh" \
  --embedded-runtime "$runtime_root" --python "$python_bin"
"$uv_bin" pip install --python "$python_bin" --strict --break-system-packages \
  --no-deps "$wheel"

# uv creates a convenience alias whose target is the absolute staging path.
# Remove only aliases that would escape after the runtime is relocated.
while IFS= read -r -d '' python_alias; do
  case "$(readlink "$python_alias")" in
    /*) unlink "$python_alias" ;;
  esac
done < <(find "$runtime_root/python" -maxdepth 1 -type l -print0)

cp "$uv_bin" "$runtime_root/bin/uv"
cp "$(command -v node)" "$runtime_root/bin/node"
cp "$repo_root/apps/cli/dist/index-standalone.cjs" \
  "$runtime_root/assets/tui/index.cjs"
cp "$product_config" "$runtime_root/product-runtime.json"
cp "$repo_root/uv.lock" "$runtime_root/product-uv.lock"
cp "$repo_root/scripts/release/verify-product-runtime.py" \
  "$runtime_root/bin/verify-product-runtime.py"
cp "$repo_root/scripts/release/smoke-ink-tui-pty.py" \
  "$runtime_root/bin/smoke-ink-tui-pty.py"
chmod 0755 "$runtime_root/bin/uv" "$runtime_root/bin/node" \
  "$runtime_root/bin/verify-product-runtime.py" \
  "$runtime_root/bin/smoke-ink-tui-pty.py"

python_relative="${python_bin#"$runtime_root/"}"
test "$python_relative" != "$python_bin" || {
  printf 'managed Python resolved outside runtime: %s\n' "$python_bin" >&2
  exit 1
}
ln -s "../$python_relative" "$runtime_root/bin/python"
test -x "$runtime_root/bin/python" || {
  printf 'stable managed Python launcher is not executable\n' >&2
  exit 1
}
if [ "$(uname -s)" = Darwin ]; then
  "$python_bin" -I "$repo_root/scripts/release/build-macos-runtime-app.py" \
    "$runtime_root" --python "$python_bin" --icon "$repo_root/apps/desktop/build/icon.icns"
fi
package_version="$("$python_bin" -I -c \
  'from importlib.metadata import version; print(version("openprogram"))')"
"$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" \
  "$runtime_root" \
  --write \
  --require-git \
  --python-relative "$python_relative" \
  --openprogram-version "$package_version" \
  --uv-version "$UV_VERSION"

trap - EXIT HUP INT TERM
rm -rf "$previous_runtime"
printf 'prepared complete OpenProgram runtime %s at %s\n' \
  "$package_version" "$runtime_root"
