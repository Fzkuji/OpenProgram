#!/usr/bin/env bash
set -euo pipefail

# The terminal install on a server or container: Debian 12 with none of the
# system libraries Chromium needs. The install must finish, say in one line
# that the browser tool waits for those libraries, and leave a working command.
archive="${1:?usage: smoke-linux-bare-install.sh RUNTIME_ARCHIVE VERSION}"
version="${2:?usage: smoke-linux-bare-install.sh RUNTIME_ARCHIVE VERSION}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
archive="$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")"
test -f "$archive" || { printf 'runtime archive not found: %s\n' "$archive" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || {
  printf 'docker is required for the bare Linux install smoke test\n' >&2
  exit 1
}
digest="$(sha256sum "$archive" | awk '{print $1}')"

# Variables in the command expand inside the container.
# shellcheck disable=SC2016
docker run --rm --pull always \
  --mount "type=bind,src=$archive,dst=/opt/openprogram/runtime.tar.gz,readonly" \
  --mount "type=bind,src=$repo_root/scripts/release/install-release.sh,dst=/opt/openprogram/install-release.sh,readonly" \
  --env "OPENPROGRAM_VERSION=$version" \
  --env "OPENPROGRAM_RUNTIME_SHA256=$digest" \
  debian:bookworm-slim bash -euo pipefail -c '
    useradd --create-home openprogram
    su openprogram -c "
      set -eu
      OPENPROGRAM_RUNTIME_ARCHIVE=/opt/openprogram/runtime.tar.gz \
        OPENPROGRAM_VERSION=$OPENPROGRAM_VERSION \
        OPENPROGRAM_RUNTIME_SHA256=$OPENPROGRAM_RUNTIME_SHA256 \
        sh /opt/openprogram/install-release.sh 2> \$HOME/install.err ||
        { cat \$HOME/install.err >&2; exit 1; }
      cat \$HOME/install.err >&2
      test \"\$(grep -c \"The browser tool will not work until\" \$HOME/install.err)\" = 1
      \$HOME/.local/bin/openprogram --version
    "
  '
printf 'bare Linux install smoke passed for %s\n' "$version"
