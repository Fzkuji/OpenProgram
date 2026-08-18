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
- **The two remote-write steps are gated.** `git push` and `gh pr create` run
  only when `git.allow_remote_write` is on, or when the user asked for them in
  this turn — in which case pass `allowed=True` to the argv builders below.
  Everything before the push is local and reversible and needs no gate.

### Dry run

When the user asks for a dry run, do steps 1–4 for real — branching, staging,
the message, and the commit are all local and undoable — then print the remote
actions instead of taking them, and stop:

```bash
python3 - <<'PY'
from openprogram.commands.commit_message import dry_run_plan
for line in dry_run_plan(
    default_branch="<default>", branch="<topic-branch>", title="<subject>",
):
    print(line)
PY
```

To see what the push itself would send without writing anything, run
`git push --dry-run -u origin <topic-branch>`; it contacts the remote to
report the refs that would update and pushes nothing.

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
git add openprogram/commands/commit_message.py tests/component/core/test_commit_trailers.py
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

Build the argv through the gate, so an unauthorized push fails here instead of
reaching the remote:

```bash
python3 - <<'PY'
from openprogram.commands.commit_message import git_push_argv
print(" ".join(git_push_argv(branch="<topic-branch>")))
PY
```

`RemoteWriteNotAuthorized` means `git.allow_remote_write` is off. Report that
and stop — a commit with no push is a fine place to stop. If the user asks for
the push in that same turn, pass `allowed=True`. Then run the printed command:

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
from openprogram.commands.commit_message import gh_pr_create_argv, pr_body
open("/tmp/openprogram-pr-body.md", "w").write(pr_body(
    "<why this change exists>",
    changes=["<file or area> — <what changed>"],
    testing=["python3 -m pytest tests/unit -q"],
))
print(" ".join(gh_pr_create_argv(
    base="<default>", head="<topic-branch>", title="<subject>",
    body_file="/tmp/openprogram-pr-body.md",
)))
PY
```

Opening a PR passes the same gate as the push, so an unauthorized call raises
`RemoteWriteNotAuthorized` before `gh` runs. Then run the printed command.

Pass `draft=True` when the work is not ready for review. Report the PR URL `gh`
prints back to the user.

## Stop conditions

Stop and report rather than improvising when: the working tree has changes
you cannot attribute to the request, a hook keeps rewriting, the push is
rejected twice, or `gh` is unavailable. A pushed branch with no PR is a fine
place to stop; a wrong commit on the default branch is not.
