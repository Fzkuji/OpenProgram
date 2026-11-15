#!/bin/sh
# Copy the versioned hooks into the .git/hooks every worktree shares. Hooks
# live outside the repository, so scripts/refresh-dev-instance.sh and
# scripts/publish-github.sh run this each time; the docs check warns if they lag.
set -eu
here="$(cd -- "$(dirname -- "$0")" && pwd)"
hooks_dir="$(git -C "$here" rev-parse --path-format=absolute --git-common-dir)/hooks"
mkdir -p "$hooks_dir"
# A deploy from an older commit must not downgrade the guards.
stamp="$hooks_dir/.installed-from"
head="$(git -C "$here" rev-parse HEAD)"
if test -f "$stamp"; then
  last="$(cat "$stamp")"
  if git -C "$here" cat-file -e "$last^{commit}" 2>/dev/null \
    && ! git -C "$here" merge-base --is-ancestor "$last" "$head"; then
    echo "git hooks left as installed: this checkout is not at or ahead of ${last%"${last#??????????}"}" >&2
    exit 0
  fi
fi
for hook in pre-commit pre-push post-merge; do
  test -f "$here/$hook" || continue
  incoming="$hooks_dir/.$hook.$$"
  cp "$here/$hook" "$incoming"
  chmod 0755 "$incoming"
  mv -f "$incoming" "$hooks_dir/$hook"
done
printf '%s\n' "$head" > "$stamp"
