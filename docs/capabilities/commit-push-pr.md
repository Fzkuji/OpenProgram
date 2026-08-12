# Commit, push, PR

The agent can take finished work all the way from your working tree to a reviewable pull request: branch off the default branch, stage what you asked for, write the commit message, push, and open the PR with `gh`. Nothing new runs it — the flow is ordinary `git` and `gh` through the shell, guided by the bundled `commit-push-pr` skill.

## Running it

Ask in plain language, or use the slash command:

```
/commit-push-pr
```

Typical phrasings that also fire it: "commit and push", "open a PR for this", "push this up", "raise a pull request for these changes".

## What it does

1. **Branches first.** It detects the repository's default branch (`git symbolic-ref refs/remotes/origin/HEAD`, falling back to `git remote show origin`, and to `main`/`master` when there is no remote). If you are sitting on it, the agent creates a topic branch before committing. It never commits onto the default branch.
2. **Stages named paths.** It reads `git status --short -uall` and adds the files the request is about. No blanket `git add -A` over a tree it did not inspect; files you did not mention stay unstaged and get reported.
3. **Writes the message** with `/commit-message`, which reads the staged diff and returns one imperative subject plus a body when the change needs one.
4. **Commits from a message file** (`git commit -F`), never a fragile inline `-m`. Hooks always run — no `--no-verify`. If a pre-commit hook rewrites files the agent amends once and stops rather than looping.
5. **Pushes** with `git push -u origin <branch>`, once the remote-write gate below allows it. A non-fast-forward rejection gets a rebase and one retry. Force-pushing needs you to ask for it explicitly, and then it uses `--force-with-lease`.
6. **Opens the PR** with `gh pr create --base <default> --head <branch> --title ... --body-file ...`, after checking `gh auth status`. The body is built from the branch's commits and its diff against the base, in summary / what changed / testing sections.

## Dry run

Ask for a dry run and the flow stops short of the remote. Steps 1–4 run for real — branching, staging, the message, and the commit are all local and undoable — and then the agent prints the remote actions it would have taken instead of taking them:

```
would push: git push -u origin fix-commit-trailers
would open PR: gh pr create --base main --head fix-commit-trailers --title ... --body-file ...
```

No `git push` and no `gh pr create` runs. To see what a push would send without writing anything, `git push --dry-run -u origin <branch>` contacts the remote, reports the refs that would update, and pushes nothing.

## Remote-write authorization

Pushing a branch and opening a pull request are the only two steps other people can see, and resetting your local tree does not undo them. They are off by default:

```
openprogram config set git.allow_remote_write true
```

With the setting off, the flow runs through the commit and then stops and tells you, which leaves you a finished local commit to push yourself. Asking for the push or the PR in the same turn authorizes that call on its own — the setting is for standing permission, not for one-off requests.

The gate lives in the argv builders, so an unauthorized call fails before `git` or `gh` runs rather than after:

```python
from openprogram.commands.commit_message import git_push_argv
git_push_argv(branch="topic")            # RemoteWriteNotAuthorized
git_push_argv(branch="topic", dry_run=True)   # fine, writes nothing
```

## AI attribution

Commits the agent writes carry a git trailer naming the model as a co-author:

```
Co-Authored-By: Claude Opus 5 <noreply@openprogram.dev>
```

The model's display name is used when it is known; otherwise the generic `OpenProgram` identity. The trailer is idempotent — re-running the flow never duplicates it, and it joins an existing trailer block (next to a `Signed-off-by:`, say) rather than opening a new one.

Generated pull-request bodies end with a single fixed line:

```
Generated with OpenProgram
```

Turn the commit trailer off with the `git.co_author` setting:

```
openprogram config set git.co_author false
```

With it off, OpenProgram adds no attribution trailer at all. The PR footer is not affected by the toggle.

The helpers are importable, so you can reproduce the exact strings yourself:

```python
from openprogram.commands.commit_message import apply_trailers, co_author_trailer, pr_body
```

## Approval and safety

Every step is a `bash` call, so `git push` and `gh pr create` go through the session's normal approval tier — under the default tier you see and approve each one, on top of the `git.allow_remote_write` gate. OpenProgram never switches you to a bypass tier to make the flow quieter.

A spawned subagent is refused `bash` entirely, so it cannot commit or push. This flow belongs to a top-level session; if you delegated the work, the delegate hands the branch back for you to ship.

## When it stops

The agent stops and reports rather than improvising when the working tree holds changes it cannot attribute to your request, a hook keeps rewriting files, the push is rejected twice, or `gh` is missing or unauthenticated. In the last case the branch is already pushed, so you can open the PR in the web UI or run `gh auth login` and ask again.

## Related

- [Skills](skills.md) — the `SKILL.md` registry this flow ships in
- [Built-in tools](tools.md) — the shell tool every step runs through
- [Configuration keys](../reference/config-keys.md) — `git.co_author`, `git.allow_remote_write`, and the rest
