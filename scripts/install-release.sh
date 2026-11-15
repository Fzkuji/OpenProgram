#!/usr/bin/env sh
set -eu

OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-}"
OPENPROGRAM_REPOSITORY="${OPENPROGRAM_REPOSITORY:-FuzzyKuji/OpenProgram}"

case "$OPENPROGRAM_VERSION" in
  "") ;;
  *[!0-9.]*|.*|*.|*..*|*.*.*.*)
    printf 'invalid OpenProgram version: %s\n' "$OPENPROGRAM_VERSION" >&2
    exit 1
    ;;
  *.*.*) ;;
  *)
    printf 'invalid OpenProgram version: %s\n' "$OPENPROGRAM_VERSION" >&2
    exit 1
    ;;
esac
case "$OPENPROGRAM_REPOSITORY" in
  *[!A-Za-z0-9_./-]*|*..*|/*|*/|*/*/*)
    printf 'invalid OpenProgram repository: %s\n' "$OPENPROGRAM_REPOSITORY" >&2
    exit 1
    ;;
  */*) ;;
  *)
    printf 'invalid OpenProgram repository: %s\n' "$OPENPROGRAM_REPOSITORY" >&2
    exit 1
    ;;
esac

script_dir=$(if CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null; then pwd; fi)
checkout_installer="$script_dir/release/install-release.sh"
if [ -f "$checkout_installer" ]; then
  exec sh "$checkout_installer" "$@"
fi

if [ -z "$OPENPROGRAM_VERSION" ]; then
  printf 'No OpenProgram version was given. Set OPENPROGRAM_VERSION to a release number such as 1.2.3 and run this again,\n' >&2
  printf 'or use the short install command, which finds the latest release: curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh\n' >&2
  exit 1
fi
command -v curl >/dev/null 2>&1 || {
  printf 'curl is required to download OpenProgram.\n' >&2
  exit 1
}
temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/openprogram-installer.XXXXXX")
cleanup() { rm -rf "$temporary_dir"; }
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
installer="$temporary_dir/install-release.sh"
url="https://raw.githubusercontent.com/$OPENPROGRAM_REPOSITORY/v$OPENPROGRAM_VERSION/scripts/release/install-release.sh"
if ! curl --disable --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  --connect-timeout 15 --speed-limit 1024 --speed-time 120 --retry 3 \
  --output "$installer" "$url"; then
  printf 'Could not download the OpenProgram %s installer. Check that the release exists and this computer is online.\n' "$OPENPROGRAM_VERSION" >&2
  printf 'If it reaches the internet through a proxy, set HTTPS_PROXY (and NO_PROXY if some hosts skip the proxy), then run this again.\n' >&2
  exit 1
fi
OPENPROGRAM_VERSION="$OPENPROGRAM_VERSION" sh "$installer" "$@"
