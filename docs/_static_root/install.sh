#!/usr/bin/env sh
set -eu

repository="${OPENPROGRAM_REPOSITORY:-FuzzyKuji/OpenProgram}"
version="${OPENPROGRAM_VERSION:-}"
releases_page="https://github.com/$repository/releases"

network_failure_help() {
  {
    printf 'Could not reach GitHub to install OpenProgram. Check that this computer is online.\n'
    printf 'If it reaches the internet through a proxy, set HTTPS_PROXY (and NO_PROXY if some hosts skip the proxy), for example:\n'
    printf '  export HTTPS_PROXY=http://proxy.example:8080\n'
    printf 'Then run this command again.\n'
  } >&2
}

if [ -z "$version" ]; then
  latest_url="$(curl --proto '=https' --tlsv1.2 -LsSf \
    -o /dev/null -w '%{url_effective}' \
    "https://github.com/$repository/releases/latest")" || {
    network_failure_help
    exit 1
  }
  version="${latest_url##*/v}"
fi
version="${version#v}"

case "$version" in
  ""|*[!0-9.]*|.*|*.|*..*)
    printf 'invalid OpenProgram release version: %s\n' "$version" >&2
    printf 'Published releases are listed at %s\n' "$releases_page" >&2
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

case "$(uname -s)" in
  Darwin) platform="macos"; platform_name="macOS" ;;
  Linux) platform="linux"; platform_name="Linux" ;;
  *)
    printf 'This command installs OpenProgram on macOS and Linux. On Windows, use the PowerShell command in the install guide:\n' >&2
    printf 'https://github.com/FuzzyKuji/OpenProgram/blob/main/docs/install/install.html\n' >&2
    exit 1
    ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch="x86_64" ;;
  arm64|aarch64) arch="arm64" ;;
  *)
    printf 'OpenProgram has no package for %s on this processor (%s).\n' "$platform_name" "$(uname -m)" >&2
    printf 'Published packages are listed at %s\n' "$releases_page" >&2
    exit 1
    ;;
esac
if [ "$platform" = "macos" ] && [ "$arch" = "x86_64" ] &&
  [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" = "1" ]; then
  arch="arm64"
fi

# The manifest lists every file of the release, so a system the release does not
# cover stops here with its name instead of failing later on a missing download.
manifest="$(curl --proto '=https' --tlsv1.2 -LsSf --connect-timeout 15 \
  "https://github.com/$repository/releases/download/v$version/release-manifest.json" \
  2>/dev/null || true)"
if [ -n "$manifest" ]; then
  case "$manifest" in
    *"OpenProgram-$version-runtime-$platform-$arch.tar.gz"*) ;;
    *"OpenProgram-$version-seed-$platform-$arch.tar.gz"*) ;;
    *)
      printf 'OpenProgram %s has no command-line package for %s on %s.\n' "$version" "$platform_name" "$arch" >&2
      printf 'What this release includes is listed at %s/tag/v%s\n' "$releases_page" "$version" >&2
      exit 1
      ;;
  esac
fi

installer="$(mktemp "${TMPDIR:-/tmp}/openprogram-install.XXXXXX")"
cleanup() { rm -f "$installer"; }
trap cleanup EXIT HUP INT TERM

if ! curl --proto '=https' --tlsv1.2 -LsSf \
  "https://raw.githubusercontent.com/$repository/v$version/scripts/install-release.sh" \
  -o "$installer"; then
  printf 'OpenProgram %s has no complete release installer.\n' "$version" >&2
  printf 'Published releases are listed at %s\n' "$releases_page" >&2
  network_failure_help
  exit 1
fi
OPENPROGRAM_VERSION="$version" OPENPROGRAM_REPOSITORY="$repository" sh "$installer"
