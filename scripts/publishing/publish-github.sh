#!/usr/bin/env bash
# Sync `dev` to GitHub's `main`, and tag a release.
#
# Two local lines: `dev` takes every commit and feeds the development
# instance; `main` is the line GitHub carries, one squashed commit per sync,
# each holding a `dev` tree on top of the previous `main` commit, so it never
# contains dev's history. refs/publish/last records the commit the latest
# sync was made from, so the next one lists only what is new. Pushing `main`
# or a tag starts no workflow.
#
# A push of main is followed by scripts/release/publish-updates.py run, which
# builds that main here and puts its code on the rolling `updates` release
# under a signed latest.json: the pointer development and testing installs
# follow. It notifies nobody. Every update needs a runtime layer per platform
# for its base; when the base changed, the run is refused until the Release
# workflow uploaded them (dispatched with -f runtime_layers_only=true), and
# running this again then publishes it. Only a release notifies anyone:
#   vX.Y.0           a feature release: main and the tag are pushed and the
#                    Release workflow is dispatched; it builds the installers,
#                    the runtime layers and the release page. Once it
#                    finished, publish-updates.py announce vX.Y.0 signs
#                    channel.json, which every install offers.
#   vX.Y.Z (Z > 0), vX.Y.Z.W
#                    a code-only release: no workflow; the run also signs
#                    channel.json, then publish-updates.py release-page creates
#                    the page with the notes. One whose runtime or shell inputs
#                    changed needs a feature release and is refused before
#                    anything moves.
#
# Usage: scripts/publishing/publish-github.sh -m "summary" [--source <ref>] [--tag vX.Y.Z[.W]]
#                                  [--office-archive <file>] [--no-push] [--dry-run]
#   -m         what this sync brings; required when the source differs from main
#   --source   commit to squash (default: dev)
#   --tag      release main: needs the version places
#              (scripts/release/release-version.py) and the notes; a feature
#              release also needs the pinned Office archive on GitHub
#   --office-archive
#              a feature release's local copy of the pinned Office archive:
#              checked against its pin, and put on the tag's draft release
#              when neither the newest release nor that draft has it
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
# vX.Y.0 builds installers in Actions; any other release is code only.
feature_release=0
if printf '%s\n' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.0$'; then
  feature_release=1
fi
if test -n "$office_archive" && test "$feature_release" -eq 0; then
  printf -- '--office-archive is for a feature release (vX.Y.0); %s builds no installers\n' "$tag" >&2
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

publish_name="FuzzyKuji"
publish_email="9404270+FuzzyKuji@users.noreply.github.com"
# GitHub is reached as this account only, the collaborator that publishes,
# whichever gh account is active, so the owner's own token never uploads.
publish_account="FuzzyKuji"
zero=0000000000000000000000000000000000000000

# Every git and gh call to GitHub below uses publish_account's token: gh reads
# GH_TOKEN, and git asks gh for credentials instead of the keychain.
use_publish_account() {
  case "$(git remote get-url origin 2>/dev/null)" in
    https://github.com/*) ;;
    *) return 0 ;;
  esac
  local token
  if ! token="$(gh auth token --hostname github.com --user "$publish_account" 2>/dev/null)" || test -z "$token"; then
    printf 'refusing to publish: gh has no token for %s; run gh auth login and sign in as %s\n' "$publish_account" "$publish_account" >&2
    exit 1
  fi
  export GH_TOKEN="$token"
  export GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=""
  export GIT_CONFIG_KEY_1=credential.helper GIT_CONFIG_VALUE_1="!gh auth git-credential"
}
if test "$dry_run" -eq 0; then
  use_publish_account
fi
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

# A release is refused here, before anything has moved, when a version place,
# the notes or (for a feature release) the Office archive is wrong.
check_tag() {
  if ! printf '%s\n' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$'; then
    printf 'a release tag looks like v1.2.3 or v1.2.3.4, not %s\n' "$tag" >&2
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
  if test "$feature_release" -eq 1; then
    check_office_local
  fi
fi

# What the update publisher would put on the `updates` release. A code-only
# release it refuses (exit 3: runtime or shell inputs changed) stops here,
# before anything has moved. A feature release's new base has no runtime
# layers until its Release workflow built them, so a refusal is expected and
# only shown; any other failure stops it. For a plain sync it is shown only.
updates_tool="$repo_root/scripts/release/publish-updates.py"
updates_plan() {
  local status=0
  "$python" -B "$updates_tool" plan --commit "$source_commit" ${tag:+--release "$tag"} || status=$?
  if test -z "$tag" || test "$status" -eq 0; then
    return 0
  fi
  if test "$feature_release" -eq 1 && test "$status" -eq 3; then
    printf 'the Release workflow of %s builds the runtime layers its update needs; announce it once that finished\n' "$tag"
    return 0
  fi
  printf 'refusing to release %s: the update publisher said no (exit %s)\n' "$tag" "$status" >&2
  exit 1
}
if test -n "$tag" && test "$dry_run" -eq 0; then
  updates_plan
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
  if test -n "$tag" && test "$feature_release" -eq 1 && test "$dry_run" -eq 0; then
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
      printf '\nwould release the new main commit as %s\n' "$tag"
    fi
    updates_plan
    exit 0
  fi

  if test -n "$tag" && test "$feature_release" -eq 1; then
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
  printf 'would release main %s as %s\n' "$(git rev-parse --short "$main_head")" "$tag"
fi
if test "$dry_run" -eq 1; then
  updates_plan
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

short_main="$(git rev-parse --short "$main_head")"

# A feature release is announced only after its Release workflow published
# the installers and every platform's runtime layer: tag and dispatch here,
# announce later.
if test "$feature_release" -eq 1; then
  git tag "$tag" "$main_head"
  OPENPROGRAM_GITHUB_PUBLISH=1 git push origin "refs/tags/$tag:refs/tags/$tag"
  gh workflow run release.yml -R Fzkuji/OpenProgram --ref main -f tag="$tag"
  printf 'tagged %s as %s; the Release workflow builds its installers, runtime layers and release page from here.\n' \
    "$short_main" "$tag"
  printf 'next, once that workflow finished: python scripts/release/publish-updates.py announce %s\n' "$tag"
  exit 0
fi

# latest.json always; channel.json too for a code-only release, which announces it.
updates_status=0
"$python" -B "$updates_tool" run --commit "$main_head" ${tag:+--release "$tag"} || updates_status=$?
if test "$updates_status" -ne 0; then
  printf 'main %s is published; its update was not (exit %s)%s\n' "$short_main" \
    "$updates_status" "${tag:+, and $tag was not tagged}" >&2
  if test "$updates_status" -eq 3 && test -z "$tag"; then
    printf 'if it lacks runtime layers for a new base, build them from main, then run this again when it finished:\n  gh workflow run release.yml -R Fzkuji/OpenProgram --ref main -f runtime_layers_only=true\n' >&2
  fi
  exit "$updates_status"
fi

if test -n "$tag"; then
  git tag "$tag" "$main_head"
  OPENPROGRAM_GITHUB_PUBLISH=1 git push origin "refs/tags/$tag:refs/tags/$tag"
  "$python" -B "$updates_tool" release-page --tag "$tag"
  printf 'released %s as %s\n' "$short_main" "$tag"
fi
