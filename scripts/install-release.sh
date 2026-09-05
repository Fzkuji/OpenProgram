#!/usr/bin/env sh
set -eu

OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-0.8.1}"
OPENPROGRAM_REPOSITORY="${OPENPROGRAM_REPOSITORY:-Fzkuji/OpenProgram}"

case "$OPENPROGRAM_VERSION" in
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

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
checkout_installer="$script_dir/release/install-release.sh"
if [ -f "$checkout_installer" ]; then
  exec sh "$checkout_installer" "$@"
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
curl --disable --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  --connect-timeout 15 --speed-limit 1024 --speed-time 120 \
  --output "$installer" "$url"
sh "$installer" "$@"
