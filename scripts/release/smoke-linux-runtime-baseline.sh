#!/usr/bin/env bash
set -euo pipefail

runtime_root="${1:?usage: smoke-linux-runtime-baseline.sh RUNTIME_ROOT ARCH}"
expected_arch="${2:?usage: smoke-linux-runtime-baseline.sh RUNTIME_ROOT ARCH}"

case "$expected_arch" in
  x86_64|arm64) ;;
  *) printf 'unsupported Linux runtime architecture: %s\n' "$expected_arch" >&2; exit 1 ;;
esac
command -v docker >/dev/null 2>&1 || {
  printf 'docker is required for the Linux glibc baseline smoke test\n' >&2
  exit 1
}
test -f "$runtime_root/runtime-manifest.json" || {
  printf 'runtime manifest not found: %s\n' "$runtime_root" >&2
  exit 1
}
runtime_root="$(cd "$runtime_root" && pwd -P)"
python_relative="$(sed -n \
  's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$runtime_root/runtime-manifest.json")"
case "$python_relative" in
  ''|/*|../*|*/../*|*/..)
    printf 'runtime manifest has an invalid managed Python path: %s\n' \
      "$python_relative" >&2
    exit 1
    ;;
esac

# The release runtime is built on Ubuntu 24.04, then consumed read-only by the
# oldest Linux userspace in the public support contract.  This catches copied
# host binaries or native wheels that accidentally acquire a newer glibc ABI.
# Variables in the command expand inside the container.
# shellcheck disable=SC2016
docker run --rm --pull always \
  --mount "type=bind,src=$runtime_root,dst=/opt/openprogram/runtime,readonly" \
  --env "OPENPROGRAM_EXPECTED_ARCH=$expected_arch" \
  --env "OPENPROGRAM_PYTHON_RELATIVE=$python_relative" \
  ubuntu:22.04 bash -euxo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install --no-install-recommends --yes \
      ca-certificates git \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 \
      libdbus-1-3 libdrm2 libexpat1 libfontconfig1 libgbm1 libglib2.0-0 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 \
      libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2

    test "$(getconf GNU_LIBC_VERSION)" = "glibc 2.35"
    case "$(uname -m)" in
      x86_64|amd64) actual_arch=x86_64 ;;
      arm64|aarch64) actual_arch=arm64 ;;
      *) printf "unsupported container architecture: %s\n" "$(uname -m)" >&2; exit 1 ;;
    esac
    test "$actual_arch" = "$OPENPROGRAM_EXPECTED_ARCH"

    runtime=/opt/openprogram/runtime
    python="$runtime/$OPENPROGRAM_PYTHON_RELATIVE"
    test -x "$python"
    test -x "$runtime/bin/python"
    test -x "$runtime/bin/node"
    test -f "$runtime/assets/tui/index.cjs"

    mkdir -p /tmp/openprogram-home
    export HOME=/tmp/openprogram-home
    export OPENPROGRAM_IMMUTABLE_RUNTIME=1
    export PLAYWRIGHT_BROWSERS_PATH="$runtime/assets/playwright"
    export GPA_MODEL_PATH="$runtime/assets/gpa/model.pt"
    "$python" -I -B "$runtime/bin/verify-product-runtime.py" "$runtime"
    "$runtime/bin/python" -I -B -m openprogram --version
  '
