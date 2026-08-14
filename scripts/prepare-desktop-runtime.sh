#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="${OPENPROGRAM_PYTHON_VERSION:-3.12.10}"
UV_VERSION="${OPENPROGRAM_UV_VERSION:-0.11.16}"
uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="$repo_root/desktop/build/runtime"
python_install_dir="$runtime_root/python"
wheel_dir="$runtime_root/wheel"

for command_name in npm; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'missing build command: %s\n' "$command_name" >&2
    exit 1
  }
done
test -n "$uv_bin" && test -x "$uv_bin" || {
  printf 'missing build command: uv\n' >&2
  exit 1
}
actual_uv_version="$($uv_bin --version | awk '{print $2}')"
test "$actual_uv_version" = "$UV_VERSION" || {
  printf 'uv version mismatch: expected %s, got %s\n' "$UV_VERSION" "$actual_uv_version" >&2
  exit 1
}

"$repo_root/scripts/stage-release-assets.sh"
rm -rf "$runtime_root"
mkdir -p "$python_install_dir" "$wheel_dir"

"$uv_bin" build --wheel --out-dir "$wheel_dir" "$repo_root"
UV_PYTHON_INSTALL_DIR="$python_install_dir" \
  "$uv_bin" python install "$PYTHON_VERSION" --install-dir "$python_install_dir" --no-bin
python_bin="$(UV_PYTHON_INSTALL_DIR="$python_install_dir" \
  "$uv_bin" python find --managed-python "$PYTHON_VERSION")"
wheel="$(find "$wheel_dir" -maxdepth 1 -type f -name 'openprogram-*.whl' -print -quit)"
test -n "$wheel" || {
  printf 'OpenProgram wheel was not built\n' >&2
  exit 1
}

"$uv_bin" pip install --python "$python_bin" --strict --break-system-packages "$wheel"
"$python_bin" -I -c 'import openprogram; import openprogram.webui.frontend'

# uv creates a convenience alias whose target is the absolute staging path.
# The versioned runtime path recorded below is self-contained; remove absolute
# top-level aliases so no symlink can escape the final application bundle.
while IFS= read -r -d '' python_alias; do
  case "$(readlink "$python_alias")" in
    /*) unlink "$python_alias" ;;
  esac
done < <(find "$python_install_dir" -maxdepth 1 -type l -print0)

mkdir -p "$runtime_root/bin"
cp "$uv_bin" "$runtime_root/bin/uv"
chmod 0755 "$runtime_root/bin/uv"

python_relative="${python_bin#"$runtime_root/"}"
test "$python_relative" != "$python_bin" || {
  printf 'managed Python resolved outside runtime: %s\n' "$python_bin" >&2
  exit 1
}
package_version="$("$python_bin" -I -c 'from importlib.metadata import version; print(version("openprogram"))')"
cat > "$runtime_root/runtime-manifest.json" <<EOF
{"schema":1,"openprogram":"$package_version","python":"$python_relative","python_request":"$PYTHON_VERSION","uv":"$UV_VERSION"}
EOF
printf 'prepared desktop runtime for OpenProgram %s with %s\n' "$package_version" "$python_bin"
