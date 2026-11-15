#!/usr/bin/env bash
set -euo pipefail

# The static OpenSSL an Intel Mac runtime builds cryptography against.
#
# cryptography's locked release has no macOS x86_64 wheel (product-runtime.json
# sourceBuilds), so this leg builds it from source. Left alone, its build links
# the runner's Homebrew libssl.3.dylib, which no user's Mac has. vcpkg builds
# OpenSSL as static libraries for the release's oldest macOS instead, and
# OPENSSL_STATIC makes the build link those into the module itself.
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
test "$(uname -s)-$(uname -m)" = Darwin-x86_64 || {
  printf 'only the Intel Mac runtime builds against this OpenSSL\n' >&2
  exit 1
}
vcpkg_root="${VCPKG_INSTALLATION_ROOT:-}"
if test -z "$vcpkg_root"; then
  vcpkg_command="$(command -v vcpkg || true)"
  test -n "$vcpkg_command" || { printf 'missing build command: vcpkg\n' >&2; exit 1; }
  vcpkg_root="$(cd -P -- "$(dirname -- "$vcpkg_command")" && pwd)"
fi
test -x "$vcpkg_root/vcpkg" || { printf 'vcpkg executable not found: %s/vcpkg\n' "$vcpkg_root" >&2; exit 1; }
minimum="$(sed -n 's/^  "macosMinimum": "\(.*\)",*$/\1/p' "$repo_root/scripts/release/product-runtime.json")"
test -n "$minimum" || { printf 'product-runtime.json names no macosMinimum\n' >&2; exit 1; }

triplets="$(mktemp -d)"
cat > "$triplets/x64-osx-openprogram.cmake" <<CMAKE
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Darwin)
set(VCPKG_OSX_ARCHITECTURES x86_64)
set(VCPKG_OSX_DEPLOYMENT_TARGET $minimum)
CMAKE
"$vcpkg_root/vcpkg" install openssl:x64-osx-openprogram --overlay-triplets="$triplets"
openssl_root="$vcpkg_root/installed/x64-osx-openprogram"
test -f "$openssl_root/include/openssl/ssl.h" || { printf 'OpenSSL headers were not installed\n' >&2; exit 1; }
test -f "$openssl_root/lib/libssl.a" || { printf 'static OpenSSL was not built\n' >&2; exit 1; }
if test -n "${GITHUB_ENV:-}"; then
  printf 'OPENSSL_DIR=%s\nOPENSSL_STATIC=1\n' "$openssl_root" >> "$GITHUB_ENV"
fi
printf 'Intel Mac native build inputs prepared: %s\n' "$openssl_root"
