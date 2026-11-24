#!/usr/bin/env bash
# Sync `dev` to GitHub's `main`, and tag a release.
#
# Two local lines: `dev` takes every commit and feeds the development
# instance; `main` is the line GitHub carries, one squashed commit per sync,
# each holding a `dev` tree on top of the previous `main` commit, so it never
# contains dev's history. A sync without --tag is the daily upload: pushing
# `main` starts no workflow. Only a `v*` tag starts the Release workflow,
# whose published App is the stable install everyone updates to in the App.
# refs/publish/last records the commit the latest sync was made from, so the
# next one lists only what is new.
#
# Usage: scripts/publishing/publish-github.sh -m "summary" [--source <ref>] [--tag vX.Y.Z]
#                                  [--office-archive <file>] [--no-push] [--dry-run]
#   -m         what this sync brings; required when the source differs from main
#   --source   commit to squash (default: dev)
#   --tag      after pushing main, tag that commit and push the tag, which
#              starts the Release workflow; needs every version place
#              (scripts/release/release-version.py), the notes, and the
#              pinned Office archive on GitHub
#   --office-archive
#              the local copy of the pinned Office archive: checked against
#              its pin, and put on the tag's draft release when neither the
#              newest release nor that draft has it
#   --no-push  add the sync to local main only
#   --dry-run  show the sync's message and checks, change nothing
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

summary=""
source_ref="dev"
tag=""
office_archive=""
push=1
dry_run=0
while test "$#" -gt 0; do
  case "$1" in
    -m) summary="${2:-}"; shift 2 ;;
    --source) source_ref="${2:-}"; shift 2 ;;
    --tag) tag="${2:-}"; shift 2 ;;
    --office-archive) office_archive="${2:-}"; shift 2 ;;
    --no-push) push=0; shift ;;
    --dry-run) dry_run=1; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if test -n "$office_archive" && test -z "$tag"; then
  printf -- '--office-archive is for a release, so it needs --tag\n' >&2
  exit 2
fi
if test -n "$tag" && test "$push" -eq 0 && test "$dry_run" -eq 0; then
  printf -- '--tag publishes the tag, so it cannot be combined with --no-push\n' >&2
  exit 2
fi

# The guard hooks live in the shared .git/hooks, so every worktree gets them.
if test "$dry_run" -eq 0; then
  "$repo_root/scripts/git-hooks/install.sh"
fi

publish_name="FzKuji"
publish_email="fzkuji@gmail.com"
zero=0000000000000000000000000000000000000000
empty_tree="$(git hash-object -t tree /dev/null)"
source_commit="$(git rev-parse "$source_ref^{commit}")"
main_head="$(git rev-parse --verify --quiet 'refs/heads/main^{commit}' || true)"

if ! git merge-base --is-ancestor "$source_commit" refs/heads/dev; then
  printf '%s is not on dev\n' "$source_ref" >&2
  exit 1
fi

python="${OPENPROGRAM_DOCS_PYTHON:-$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.venv/bin/python}"
if ! test -x "$python"; then
  printf 'cannot check the version: no Python at %s; refusing to publish\n' "$python" >&2
  exit 1
fi

# A tag starts the Release workflow, whose builds stop when any version place,
# the notes or the Office archive is wrong. Refuse here, before anything has moved.
check_tag() {
  if ! printf '%s\n' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf 'a release tag looks like v1.2.3, not %s\n' "$tag" >&2
    exit 2
  fi
  local version="${tag#v}" notes
  "$python" -B "$repo_root/scripts/release/release-version.py" check "$tag" --commit "$source_commit" || exit 1
  notes=".github/release-notes/$tag.md"
  if ! git cat-file -e "$source_commit:$notes" 2>/dev/null; then
    printf 'this source has no release notes for %s; write %s and commit it first.\n' "$tag" "$notes" >&2
    exit 1
  fi
  if test "$(git show "$source_commit:$notes" | head -n 1)" != "# OpenProgram $version Release Notes"; then
    printf '%s must start with the line: # OpenProgram %s Release Notes\n' "$notes" "$version" >&2
    exit 1
  fi
  if git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null; then
    printf 'the tag %s already exists here, and a released tag never moves.\n' "$tag" >&2
    exit 1
  fi
}
# The Office archive cannot be built in CI; the Release workflow's office job
# carries the newest release's copy forward or takes the one on the tag's
# draft, and nothing builds without it. A local copy is checked against its
# pin on every call; GitHub is asked only by a real run, before anything moves.
office_tool="$repo_root/scripts/release/office-archive.py"
check_office_local() {
  if test -z "$office_archive"; then
    printf 'Office archive: no local copy given (--office-archive /path/to/%s); a real run requires the newest release or the %s draft to have it\n' \
      "$("$python" -B "$office_tool" name)" "$tag"
    return
  fi
  if ! "$python" -B "$office_tool" check "$office_archive"; then
    printf 'refusing to tag %s: the Office archive given is not the pinned one\n' "$tag" >&2
    exit 1
  fi
}
ensure_office_released() {
  if "$python" -B "$office_tool" released "$tag"; then
    return
  fi
  if test -z "$office_archive"; then
    printf 'refusing to tag %s: nothing on GitHub has the Office archive and no local copy was given; pass --office-archive /path/to/%s\n' \
      "$tag" "$("$python" -B "$office_tool" name)" >&2
    exit 1
  fi
  "$python" -B "$office_tool" seed "$tag" "$office_archive"
}

if test -n "$tag"; then
  check_tag
  check_office_local
fi

# The project's retired GitHub accounts are never named in anything published:
# not in the tree, the sync summary or the history of main. The scanner
# holds them only as hashes and reads the source commit from the object store.
check_retired_accounts() {
  local what="$1"
  shift
  local hits
  if ! hits="$("$python" -B "$repo_root/scripts/publishing/retired_accounts.py" --root "$repo_root" "$@" 2>&1)"; then
    printf 'refusing to publish: %s names a retired account; fix it on dev\n%s\n' "$what" "$hits" >&2
    exit 1
  fi
}
check_retired_accounts "$(git rev-parse --short "$source_commit")" --commit "$source_commit"
if test -n "$summary"; then
  printf '%s\n' "$summary" | check_retired_accounts "the summary" --stdin
fi

# The whole site leaves the machine, so every docs error stops a sync. The
# check reads the commit being published, not whatever this checkout holds.
check_docs() {
  local parent status=0
  parent="$(mktemp -d)"
  trap 'git worktree remove --force "$parent/tree" 2>/dev/null; rm -rf "$parent"; git worktree prune' EXIT
  git -c core.hooksPath=/dev/null worktree add --quiet --detach "$parent/tree" "$source_commit"
  (cd "$parent/tree" && "$python" -B -m scripts.docs_site.check --all) || status=$?
  git worktree remove --force "$parent/tree"
  rm -rf "$parent"
  git worktree prune
  trap - EXIT
  if test "$status" -ne 0; then
    printf 'refusing to publish: the docs check on %s exited %s; fix it on dev\n' \
      "$(git rev-parse --short "$source_commit")" "$status" >&2
    exit 1
  fi
}

if test -n "$main_head" && test "$(git rev-parse "$source_commit^{tree}")" = "$(git rev-parse "$main_head^{tree}")"; then
  printf 'main already holds this tree; nothing to sync\n'
  if test -n "$tag" && test "$dry_run" -eq 0; then
    ensure_office_released
  fi
else
  if test -z "$summary"; then
    printf 'a summary is required for a sync: -m "..."\n' >&2
    exit 2
  fi
  # A sync lists the commits since the previous one; the first lists none.
  base=""
  if test -n "$main_head"; then
    base="$(git rev-parse --verify --quiet refs/publish/last || true)"
    if test -n "$base" && ! git merge-base --is-ancestor "$base" "$source_commit"; then
      printf 'refs/publish/last is not an ancestor of %s\n' "$source_ref" >&2
      exit 1
    fi
  fi

  # Refuse a sync holding anything that looks like a live credential. AWS's
  # documented example key and one-line PEM fixtures in tests are not keys.
  added="$(git diff "${main_head:-$empty_tree}" "$source_commit" -U0 | grep -E '^\+' || true)"
  hits="$( { printf '%s\n' "$added" \
      | grep -Eo '(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|sk-ant-(api|admin)[0-9]{2}-[A-Za-z0-9_-]{40,}|AKIA[0-9A-Z]{16})' \
      | grep -v 'EXAMPLE$' || true
    printf '%s\n' "$added" | grep -E '^\+[[:space:]]*-----BEGIN [A-Z ]*PRIVATE KEY-----[[:space:]]*$' || true
  } | sort -u)"
  if test -n "$hits"; then
    printf 'possible credentials, refusing to publish:\n%s\n' "$hits" >&2
    exit 1
  fi

  check_docs

  message_file="$(mktemp)"
  trap 'rm -f "$message_file"' EXIT
  {
    printf '%s\n\n' "$summary"
    if test -n "$base"; then
      printf 'Squashes %s local commits.\n' "$(git rev-list --count "$base..$source_commit")"
    else
      printf 'First sync on this line.\n'
    fi
    printf '\nLocal-Source: %s\n' "$source_commit"
  } >"$message_file"
  printf 'sync of %s on top of main %s\n\n' \
    "$(git rev-parse --short "$source_commit")" "${main_head:+$(git rev-parse --short "$main_head")}"
  cat "$message_file"
  git diff --shortstat "${main_head:-$empty_tree}" "$source_commit"
  if test "$dry_run" -eq 1; then
    if test -n "$tag"; then
      printf '\nwould tag the new main commit %s and push the tag, starting the Release workflow\n' "$tag"
    fi
    exit 0
  fi

  if test -n "$tag"; then
    ensure_office_released
  fi

  # Everything on GitHub is signed by the publishing account, never by the
  # identity this checkout commits with.
  new_commit="$(GIT_AUTHOR_NAME="$publish_name" GIT_AUTHOR_EMAIL="$publish_email" \
    GIT_COMMITTER_NAME="$publish_name" GIT_COMMITTER_EMAIL="$publish_email" \
    git commit-tree "$source_commit^{tree}" ${main_head:+-p "$main_head"} -F "$message_file")"
  git update-ref -m "publish $source_commit" refs/heads/main "$new_commit" "${main_head:-$zero}"
  git update-ref refs/publish/last "$source_commit"
  main_head="$new_commit"
  printf '\nmain is now %s\n' "$(git rev-parse --short "$main_head")"
fi

if test "$dry_run" -eq 1 && test -n "$tag"; then
  printf 'would tag main %s as %s and push the tag, starting the Release workflow\n' \
    "$(git rev-parse --short "$main_head")" "$tag"
fi
if test "$push" -eq 0 || test "$dry_run" -eq 1; then
  exit 0
fi
if ! git remote get-url origin >/dev/null 2>&1; then
  printf 'no origin remote; main stays local\n' >&2
  exit 1
fi
# GitHub's main must be an earlier point of local main, so the push only
# fast-forwards it.
# What goes up is the part of main GitHub does not have yet.
uploading="$main_head"
if git ls-remote --exit-code --heads origin main >/dev/null 2>&1; then
  git fetch --quiet origin main
  if ! git merge-base --is-ancestor FETCH_HEAD "$main_head"; then
    printf 'GitHub main %s is not part of local main; refusing to push\n' "$(git rev-parse --short FETCH_HEAD)" >&2
    exit 1
  fi
  uploading="$(git rev-parse FETCH_HEAD)..$main_head"
fi
if test -n "$tag" && git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1; then
  printf 'GitHub already has the tag %s, and a released tag never moves; nothing was pushed.\n' "$tag" >&2
  exit 1
fi
git log --format='%an %ae %cn %ce%n%B' "$uploading" | check_retired_accounts "the history being uploaded" --stdin
# Refuse to upload a sync signed by anyone else. The history GitHub
# already has (the years before 1.0.0, many authors) is not uploaded again.
foreign="$(git log --format='%h %ae %ce' "$uploading" | awk -v e="$publish_email" '$2 != e || $3 != e' | head -n 5)"
if test -n "$foreign"; then
  printf 'the syncs being uploaded hold commits not signed by %s; refusing to push:\n%s\n' "$publish_name" "$foreign" >&2
  exit 1
fi
OPENPROGRAM_GITHUB_PUBLISH=1 git push origin refs/heads/main:refs/heads/main
printf 'published %s to GitHub main\n' "$(git rev-parse --short "$main_head")"

if test -n "$tag"; then
  git tag "$tag" "$main_head"
  OPENPROGRAM_GITHUB_PUBLISH=1 git push origin "refs/tags/$tag:refs/tags/$tag"
  printf 'tagged %s as %s; the Release workflow builds it from here\n' "$(git rev-parse --short "$main_head")" "$tag"
fi
