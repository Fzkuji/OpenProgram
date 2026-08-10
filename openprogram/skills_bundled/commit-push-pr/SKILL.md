---
name: commit-push-pr
description: "Take finished work from the working tree to a reviewable pull request — branch off the default branch, stage only what was asked for, write the commit message, push the branch, and open the PR with gh. Covers AI co-author attribution, pre-commit hook rewrites, and what to do when gh is missing, unauthenticated, or the branch already exists. Triggers: 'commit and push', 'commit this', 'open a PR', 'create a pull request', 'push this up', 'ship it', 'raise a PR for these changes', 'push the branch and open a pull request'."
---

# Commit, push, PR

Everything here runs through `bash` and `gh` — there is no dedicated tool.
Each command is approved under the session's existing approval tier, so run
them one visible step at a time and never wrap the whole flow in one opaque
shell blob.

## 0. Preconditions

- You must be in a **top-level session**. A spawned subagent is refused
  `bash` outright, so it cannot commit, push, or open a PR. If you are a
  subagent, hand the work back rather than trying.
- Never bypass approval on the user's behalf, and never suggest switching to
  a bypass tier to make this flow quieter.

## 1. Branch first — never commit onto the default branch

Detect the default branch:

```bash
git symbolic-ref --quiet --short refs/remotes/origin/HEAD    # -> origin/main
# fallback when that ref is missing:
git remote show origin | sed -n 's/.*HEAD branch: //p'
```

With no remote at all, treat `main` (else `master`) as the default.

If `git branch --show-current` equals the default branch, branch before
committing:

```bash
git checkout -b <topic-branch>
```

Name it after the work (`fix-commit-trailers`, `docs-pr-flow`), not after the
date or the ticket alone.

## 2. Stage deliberately

```bash
git status --short -uall
```

Read it. Then add **named paths only**:

```bash
git add openprogram/commands/commit_message.py tests/unit/test_commit_trailers.py
```

Never `git add -A` / `git add .` over a tree you did not inspect. If the
status shows files the user did not ask about — scratch output, unrelated
edits, a stray `.env` — leave them unstaged and say so. When you are unsure
whether a file belongs in the commit, ask instead of committing it.

## 3. Message

Use `/commit-message` for the subject and body instead of free-styling. It
reads the staged diff and returns one imperative subject (≤72 chars) plus a
body when the change needs one.

Do not hand-write the trailers — step 4 appends them.

## 4. Commit

Write the message to a file, then commit from that file. Inline `-m` breaks
on quotes, backticks, and newlines.

```bash
python3 - <<'PY'
from openprogram.commands.commit_message import apply_trailers, co_author_trailer
msg = """<subject from /commit-message>

<body>"""
open("/tmp/openprogram-commit-msg", "w").write(
    apply_trailers(msg, co_author=co_author_trailer("<model display name>")) + "\n"
)
PY
git commit -F /tmp/openprogram-commit-msg
```

`co_author_trailer` returns `None` when the user has turned off
`git.co_author`, and `apply_trailers` is idempotent, so re-running it never
duplicates the trailer.

Rules:

- Never `--no-verify`. Hooks exist because someone was burned.
- If a pre-commit hook rewrites files, `git add` the rewritten paths and
  `git commit --amend --no-edit` **once**. If the hook rewrites again, stop
  and report — do not loop.
- If a hook fails outright, fix the reported problem or stop. Do not disable
  the hook.

## 5. Push

```bash
git push -u origin <topic-branch>
```

- **Branch already exists on the remote** and the push is a fast-forward:
  fine, it just updates.
- **Rejected as non-fast-forward**: the remote has commits you do not.
  `git pull --rebase origin <branch>`, resolve, push again.
- **Never force-push** unless the user explicitly asks. When they do, use
  `--force-with-lease`, never bare `--force`.
- Never push to the default branch.

## 6. Pull request

Check the CLI first:

```bash
gh auth status
```

- `gh` not installed → tell the user (`brew install gh`, or the platform
  equivalent) and stop after the push; the branch is already on the remote,
  so they can open the PR in the web UI.
- Not authenticated → tell them to run `gh auth login` themselves. Do not
  attempt an interactive login.

Build the body from the branch's own commits and the diff against the base:

```bash
git log --oneline <default>..<topic-branch>
git diff --stat <default>...<topic-branch>
```

Sections: a summary of why, what changed, and how it was tested. The body
must end with the `Generated with OpenProgram` line, which
`openprogram.commands.commit_message.pr_body` appends for you:

```bash
python3 - <<'PY'
from openprogram.commands.commit_message import pr_body
open("/tmp/openprogram-pr-body.md", "w").write(pr_body(
    "<why this change exists>",
    changes=["<file or area> — <what changed>"],
    testing=["python3 -m pytest tests/unit -q"],
))
PY
gh pr create --base <default> --head <topic-branch> \
  --title "<subject>" --body-file /tmp/openprogram-pr-body.md
```

Add `--draft` when the work is not ready for review. Report the PR URL `gh`
prints back to the user.

## Stop conditions

Stop and report rather than improvising when: the working tree has changes
you cannot attribute to the request, a hook keeps rewriting, the push is
rejected twice, or `gh` is unavailable. A pushed branch with no PR is a fine
place to stop; a wrong commit on the default branch is not.
