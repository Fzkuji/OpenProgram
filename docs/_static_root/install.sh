#!/usr/bin/env sh
set -eu

repository="${OPENPROGRAM_REPOSITORY:-Fzkuji/OpenProgram}"
version="${OPENPROGRAM_VERSION:-}"

if [ -z "$version" ]; then
  latest_url="$(curl --proto '=https' --tlsv1.2 -LsSf \
    -o /dev/null -w '%{url_effective}' \
    "https://github.com/$repository/releases/latest")"
  version="${latest_url##*/v}"
fi
version="${version#v}"

case "$version" in
  ""|*[!0-9.]*|.*|*.|*..*)
    printf 'invalid OpenProgram release version: %s\n' "$version" >&2
    exit 1
    ;;
esac
major="${version%%.*}"
remainder="${version#*.}"
minor="${remainder%%.*}"
patch="${remainder#*.}"
case "$patch" in
  *.*)
    printf 'invalid OpenProgram release version: %s\n' "$version" >&2
    exit 1
    ;;
esac
test -n "$major" && test -n "$minor" && test -n "$patch" || {
  printf 'invalid OpenProgram release version: %s\n' "$version" >&2
  exit 1
}

installer="$(mktemp "${TMPDIR:-/tmp}/openprogram-install.XXXXXX")"
cleanup() { rm -f "$installer"; }
trap cleanup EXIT HUP INT TERM

if ! curl --proto '=https' --tlsv1.2 -LsSf \
  "https://raw.githubusercontent.com/$repository/v$version/scripts/install-release.sh" \
  -o "$installer"; then
  printf 'OpenProgram %s has no complete release installer.\n' "$version" >&2
  exit 1
fi
OPENPROGRAM_VERSION="$version" OPENPROGRAM_REPOSITORY="$repository" sh "$installer"
