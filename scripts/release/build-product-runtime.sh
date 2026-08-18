#!/usr/bin/env bash
set -euo pipefail

# Pinned first-party inputs are declared beside this script in product-runtime.json:
# GUI-Agent-Harness, Research-Agent-Harness, and Wiki-Agent-Harness.

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

for command_name in npm git; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'missing build command: %s\n' "$command_name" >&2
    exit 1
  }
done
test -n "$uv_bin" && test -x "$uv_bin" || {
  printf 'missing build command: uv\n' >&2
  exit 1
}
test -n "$json_python" && test -x "$json_python" || {
  printf 'missing build command: python3\n' >&2
  exit 1
}

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
rm -rf "$runtime_root"
rm -rf "$repo_root/build"
mkdir -p \
  "$runtime_root/assets/playwright" \
  "$runtime_root/assets/easyocr" \
  "$runtime_root/assets/gpa" \
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

"$uv_bin" export --project "$repo_root" --frozen --no-dev \
  --extra all --extra search --no-emit-project \
  --output-file "$runtime_root/product-requirements.txt" >/dev/null
"$uv_bin" pip install --python "$python_bin" --strict --break-system-packages \
  --require-hashes --requirements "$runtime_root/product-requirements.txt"
"$uv_bin" pip install --python "$python_bin" --strict --break-system-packages \
  --no-deps "$wheel"

# Keep GUI inference CPU-only in distributable Linux runtimes. PyPI's Linux
# Torch wheel can pull CUDA libraries even though the default product does not
# require a GPU.
numpy_version="$(read_config programs.gui.numpy)"
opencv_version="$(read_config programs.gui.opencv)"
torch_version="$(read_config programs.gui.torch)"
torchvision_version="$(read_config programs.gui.torchvision)"
torch_install=(
  "$uv_bin" pip install --python "$python_bin" --strict
  --break-system-packages
  "numpy==$numpy_version" "torch==$torch_version" "torchvision==$torchvision_version"
)
if test "$(uname -s)" = Linux; then
  torch_install+=(--index https://download.pytorch.org/whl/cpu)
fi
"${torch_install[@]}"

program_staging="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-programs.XXXXXX")"
cleanup() { rm -rf "$program_staging"; }
trap cleanup EXIT HUP INT TERM
program_constraints="$program_staging/product-constraints.txt"
printf '%s\n' \
  "numpy==$numpy_version" \
  "opencv-python==$opencv_version" \
  "torch==$torch_version" \
  "torchvision==$torchvision_version" \
  > "$program_constraints"

for program_name in gui research wiki; do
  program_repo="$(read_config "programs.$program_name.repository")"
  program_commit="$(read_config "programs.$program_name.commit")"
  program_dir="$program_staging/$program_name"
  git init -q "$program_dir"
  git -C "$program_dir" remote add origin "$program_repo"
  git -C "$program_dir" fetch -q --depth 1 origin "$program_commit"
  git -C "$program_dir" checkout -q --detach FETCH_HEAD
  if test "$program_name" = gui; then
    "$uv_bin" pip install --python "$python_bin" --strict \
      --break-system-packages --constraint "$program_constraints" \
      "${program_dir}[ocr]"
  elif test "$program_name" = research; then
    "$uv_bin" pip install --python "$python_bin" --strict \
      --break-system-packages --constraint "$program_constraints" \
      "${program_dir}[pdf]"
  else
    "$uv_bin" pip install --python "$python_bin" --strict \
      --break-system-packages --constraint "$program_constraints" \
      "$program_dir"
  fi
done

PLAYWRIGHT_BROWSERS_PATH="$runtime_root/assets/playwright" \
  "$python_bin" -m playwright install chromium

gpa_repository="$(read_config assets.gpa_detector.repository)"
gpa_revision="$(read_config assets.gpa_detector.revision)"
gpa_filename="$(read_config assets.gpa_detector.filename)"
"$python_bin" - "$runtime_root/assets/gpa" \
  "$gpa_repository" "$gpa_revision" "$gpa_filename" <<'PY'
import pathlib
import shutil
import sys
from huggingface_hub import hf_hub_download

target, repository, revision, filename = sys.argv[1:]
path = hf_hub_download(repository, filename, revision=revision)
destination = pathlib.Path(target) / filename
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(path, destination)
PY

EASYOCR_MODULE_PATH="$runtime_root/assets/easyocr" \
  "$python_bin" -c \
  "import easyocr; easyocr.Reader(['en', 'ch_sim'], gpu=False, verbose=False)"

# uv creates a convenience alias whose target is the absolute staging path.
# Remove only aliases that would escape after the runtime is relocated.
while IFS= read -r -d '' python_alias; do
  case "$(readlink "$python_alias")" in
    /*) unlink "$python_alias" ;;
  esac
done < <(find "$runtime_root/python" -maxdepth 1 -type l -print0)

cp "$uv_bin" "$runtime_root/bin/uv"
cp "$product_config" "$runtime_root/product-runtime.json"
cp "$repo_root/uv.lock" "$runtime_root/product-uv.lock"
cp "$repo_root/scripts/release/verify-product-runtime.py" \
  "$runtime_root/bin/verify-product-runtime.py"
chmod 0755 "$runtime_root/bin/uv" "$runtime_root/bin/verify-product-runtime.py"

python_relative="${python_bin#"$runtime_root/"}"
test "$python_relative" != "$python_bin" || {
  printf 'managed Python resolved outside runtime: %s\n' "$python_bin" >&2
  exit 1
}
package_version="$("$python_bin" -I -c \
  'from importlib.metadata import version; print(version("openprogram"))')"
"$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" \
  "$runtime_root" \
  --write \
  --python-relative "$python_relative" \
  --openprogram-version "$package_version" \
  --uv-version "$UV_VERSION"

trap - EXIT HUP INT TERM
cleanup
printf 'prepared complete OpenProgram runtime %s at %s\n' \
  "$package_version" "$runtime_root"
