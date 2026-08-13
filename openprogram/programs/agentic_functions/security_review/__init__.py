"""security_review — a security audit of what this branch changed.

The unit of review is the diff, not the repository. Everything the
branch has not touched is out of scope, so a finding must be something
this change introduced or made worse; a pre-existing weakness sitting
next to a changed line is not a finding here, because reporting it
drowns the one thing the author can act on right now.

The baseline is picked the way a reviewer would pick it: the merge base
with the branch's upstream when one is configured, otherwise the merge
base with the repository's default branch. Everything since that point
counts — committed work, staged changes, and unstaged edits — so a
review before committing sees the same thing a review after committing
does. A branch with no changes returns "no findings" without spending
an agent turn.

The reviewer is a spawned same-session agent turn whose prompt is the
docstring of :func:`review_diff`, given read-only tools: it reads the
diff plus whatever surrounding source it needs to tell a real
vulnerability from a shape that merely looks like one, and it cannot
edit, run a build, or spawn further agents.

Registration: AGENTIC_MODULES.
"""
from __future__ import annotations

import inspect
import logging
import os
import subprocess
from typing import Optional

from openprogram.agentic_programming.function import (
    agentic_function,
    current_session_id,
)
from openprogram.programs.agentic_functions.json_parsing import parse_json

_log = logging.getLogger(__name__)

# Read-only. A reviewer that can write is a reviewer that can "fix"
# what it thinks it found, and a security opinion is worth nothing if
# producing it changed the thing under review. bash is included for
# `git show`/`git log` style inspection and is used at the agent's
# discretion; no edit, write, apply_patch, or task.
REVIEW_TOOLS = ("read", "grep", "glob", "list", "bash")

SEVERITIES = ("critical", "high", "medium", "low")

# A diff past this size stops being reviewable in one pass; it is cut
# with the rule stated so the reviewer reports on what it actually saw
# instead of silently reviewing a prefix.
DIFF_MAX_CHARS = 400_000

# Branch names tried as the baseline when the branch has no upstream.
DEFAULT_BRANCH_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master",
                             "main", "master")


class NoBaselineError(RuntimeError):
    """No baseline commit could be determined for the review."""


# ---------------------------------------------------------------------------
# Git — baseline selection and diff collection
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Optional[str]) -> tuple[int, str]:
    """``(returncode, stdout)`` for one git command. stderr is folded
    into the log, never into the returned text, so a caller comparing
    output never has to strip warnings."""
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        _log.debug("git %s failed: %s", " ".join(args), proc.stderr.strip())
    return proc.returncode, proc.stdout.strip()


def resolve_base(cwd: Optional[str] = None) -> str:
    """The commit this branch's changes are measured against.

    Tried in order, first hit wins:

    1. the merge base with the branch's configured upstream — what the
       branch will actually be compared against when it is proposed,
    2. the merge base with a default branch (``origin/HEAD``,
       ``origin/main``, ``origin/master``, then their local names) —
       the same answer for a branch that has not been pushed yet.

    Raises :class:`NoBaselineError` when neither exists, saying which
    was missing: a repository with no upstream and no default branch
    has no "what changed" to review, and guessing one (``HEAD~1``, the
    root commit) would silently review the wrong range.
    """
    code, _ = _git(["rev-parse", "--git-dir"], cwd)
    if code != 0:
        raise NoBaselineError(
            "not a git repository — a security review needs a branch to diff")

    code, upstream = _git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd)
    if code == 0 and upstream:
        code, base = _git(["merge-base", "HEAD", upstream], cwd)
        if code == 0 and base:
            return base

    for candidate in DEFAULT_BRANCH_CANDIDATES:
        code, base = _git(["merge-base", "HEAD", candidate], cwd)
        if code == 0 and base:
            return base

    raise NoBaselineError(
        "no baseline found: this branch has no upstream and none of "
        f"{', '.join(DEFAULT_BRANCH_CANDIDATES)} exists. Pass base= "
        "explicitly with the commit or branch to review against.")


def collect_diff(base: str, cwd: Optional[str] = None) -> tuple[str, list[str]]:
    """``(diff_text, changed_files)`` for everything since ``base``.

    One ``git diff base`` against the working tree, so committed,
    staged and unstaged changes all land in the same review — the
    author gets the same answer whether or not they committed first.

    Untracked files are diffed one at a time against ``/dev/null``
    rather than staged first: a new file is where a hardcoded secret is
    most likely to be, and it is invisible to a plain diff, but
    reviewing must not touch the index the author is composing.
    Ignored files are left out, so build output and vendored trees do
    not enter the review.
    """
    _, diff = _git(["diff", "--no-color", base, "--"], cwd)
    _, names = _git(["diff", "--name-only", base, "--"], cwd)
    files = [f for f in names.splitlines() if f.strip()]

    _, untracked = _git(
        ["ls-files", "--others", "--exclude-standard"], cwd)
    for path in untracked.splitlines():
        path = path.strip()
        if not path:
            continue
        # --no-index exits 1 when the files differ, which is always.
        _, added = _git(
            ["diff", "--no-color", "--no-index", "--", os.devnull, path], cwd)
        if added:
            diff = f"{diff}\n{added}" if diff else added
            files.append(path)
    return diff, files


def clip_diff(diff: str) -> str:
    """Cut an oversized diff at the limit, saying so in the text.

    The reviewer must know its input was truncated: a report that reads
    as complete over a prefix of the changes is worse than one that
    names the gap."""
    if len(diff) <= DIFF_MAX_CHARS:
        return diff
    return (diff[:DIFF_MAX_CHARS]
            + "\n\n[diff truncated at "
            + f"{DIFF_MAX_CHARS} characters — the changes below this point "
              "were NOT shown. Say so in your reply and review only what "
              "you were given.]")


# ---------------------------------------------------------------------------
# Review turn — the seam tests stub
# ---------------------------------------------------------------------------

def _run_review_turn(session_id: str, prompt: str, *, agent_id: str,
                     spawn_caller: Optional[str]) -> str:
    """One read-only review turn. Module-level so tests stub it."""
    from openprogram.agent.sub_agent_run import run_agent_turn
    res = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=None,
        label="安全审查",
        spawn_caller=spawn_caller,
        advance_head=False,
        tools_override=list(REVIEW_TOOLS),
        render_range={"callers": 0},
    )
    if res.failed:
        raise RuntimeError(res.error or "security review turn failed")
    return res.final_text or ""


def _clean_findings(raw: object) -> list[dict]:
    """Findings from a review reply, one dict per usable entry.

    An entry without a title is dropped — a finding nobody can name is
    not a finding. An unrecognised severity is normalised to ``medium``
    rather than dropped, because the vulnerability description is the
    valuable part and a bad label should not lose it."""
    out: list[dict] = []
    for item in (raw or []) if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        severity = str(item.get("severity") or "").strip().lower()
        if severity not in SEVERITIES:
            severity = "medium"
        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            line = 0
        out.append({
            "severity": severity,
            "file": str(item.get("file") or "").strip(),
            "line": line,
            "title": title,
            "scenario": str(item.get("scenario") or "").strip(),
            "recommendation": str(item.get("recommendation") or "").strip(),
        })
    out.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    return out


def _review_prompt(base: str, diff: str, files: list[str]) -> str:
    listing = "\n".join(files) or "(none)"
    return (
        f"{inspect.getdoc(review_diff)}\n\n"
        f"<baseline>\n{base}\n</baseline>\n\n"
        f"<changed_files>\n{listing}\n</changed_files>\n\n"
        f"<diff>\n{clip_diff(diff)}\n</diff>"
    )


def review_diff(base: str, diff: str, files: list[str], session_id: str = "",
                *, agent_id: str = "main",
                spawn_caller: Optional[str] = None) -> list[dict]:
    """You are performing a security review of one change. The diff
    below is the entire scope of the review.

    Report ONLY vulnerabilities this diff introduces or makes worse. A
    weakness that already existed and that this change merely moves,
    reformats, or sits next to is NOT in scope — the author can only
    act on what they just wrote, and burying that in pre-existing
    issues is how a review gets ignored. If a changed line makes an old
    weakness reachable, exploitable, or worse, that IS in scope: say
    which change did it.

    The diff is a window, not the whole program. Before reporting
    anything, read the surrounding source with your tools and follow
    the data: where does this input come from, is it already validated
    upstream, who calls this function, what does the sink actually do.
    A pattern that looks dangerous in isolation is often fine in
    context, and a plain-looking line is often the bug once you see its
    caller. You have read-only tools; nothing you do may modify the
    working tree.

    Work through these classes against the changed code:

    * Injection — SQL and other query languages built by concatenation
      or f-string; shell and subprocess invocation with interpolated
      or shell=True arguments; path traversal in file paths built from
      request data or archive entries; SSRF where a fetched URL, host,
      or redirect target is caller-controlled; template, LDAP, XPath,
      and header injection (CRLF into logs or responses).
    * Authentication and authorization — a new route, handler, tool,
      or RPC added without the authorization check its siblings have;
      an object looked up by an id from the request without checking
      the caller owns it; a role, tenant, or scope check dropped,
      weakened, or moved after the effect it guards; session tokens
      that do not rotate on privilege change; comparisons of secrets
      with ``==`` instead of a constant-time compare.
    * Secrets and sensitive data — API keys, tokens, passwords, and
      private keys hardcoded or committed to a config, test, or
      fixture; credentials, tokens, cookies, or personal data written
      to logs, error messages, telemetry, or exception text; secrets
      passed as command-line arguments or baked into a URL.
    * Unsafe deserialization and dynamic execution — pickle, PyYAML
      ``load`` without SafeLoader, marshal, ``eval``, ``exec``,
      ``getattr``-driven dispatch on caller-supplied names, dynamic
      import of a caller-supplied module, XML parsed by a parser that
      resolves external entities.
    * Concurrency and TOCTOU — a check separated from the action it
      guards (exists-then-open, permission-then-use, balance-then-
      debit); shared mutable state touched without a lock; a temp file
      created by name and opened later; a lock acquired but released
      on some paths only.
    * Resource exhaustion — unbounded reads of a request body, file,
      or archive (zip bombs); user-controlled iteration counts,
      allocation sizes, or recursion depth; regular expressions with
      catastrophic backtracking on caller-supplied input; missing
      timeouts on outbound requests; unbounded caches keyed by
      caller-supplied values.
    * Dependencies and supply chain — a newly added dependency that is
      unpinned, typo-squatted, or fetched from a non-canonical index;
      an installer or build step piping a remote script to a shell; a
      lockfile change that silently downgrades a package; verification
      of signatures or certificates disabled.
    * Error handling and information disclosure — stack traces,
      internal paths, SQL, or configuration returned to a caller;
      exceptions swallowed such that a failed authorization or
      integrity check reads as success; verbose debug output enabled
      on a production path.

    Every finding must carry a concrete triggering scenario: who sends
    what, through which entry point, and what they get. If you cannot
    write that scenario, you do not have a finding — drop it. Do not
    report style, missing tests, or code you dislike; this is a
    security review, not a code review.

    Severity is about consequence, not how interesting the bug is:

    * critical — remote code execution, authentication bypass, or mass
      data exposure, reachable by an unauthenticated caller.
    * high — privilege escalation, exposure of another user's data, or
      a leaked live credential.
    * medium — exploitable only with valid credentials, an unlikely
      configuration, or another bug chained in.
    * low — hardening: defence in depth, a weak default, information
      that is mildly useful to an attacker.

    An empty findings list is a legitimate and common result. Say so
    plainly rather than promoting an observation to a vulnerability to
    have something to report — a review that invents findings costs the
    reader more than one that finds nothing.

    End your reply with STRICT JSON only, no markdown fence, no prose
    after it:
    {"findings": [{"severity": "critical|high|medium|low",
                   "file": "<path as it appears in the diff>",
                   "line": <line number in the changed file>,
                   "title": "<one line naming the vulnerability>",
                   "scenario": "<who triggers it, how, and what they get>",
                   "recommendation": "<the specific fix>"}, …]}
    Use {"findings": []} when there is nothing to report.
    """
    sid = session_id or current_session_id()
    raw = _run_review_turn(sid, _review_prompt(base, diff, files),
                           agent_id=agent_id, spawn_caller=spawn_caller)
    data = parse_json(raw or "")
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("security review reply was not valid JSON")
    return _clean_findings(data["findings"])


# ---------------------------------------------------------------------------
# run_security_review — the entry point
# ---------------------------------------------------------------------------

@agentic_function(input={
    "base": {"description": "Commit or branch to review against "
                            "(empty: pick the branch's baseline automatically)"},
    "session_id": {"hidden": True},
    "spawn_caller": {"hidden": True},
    "agent_id": {"hidden": True},
})
def run_security_review(base: str = "", session_id: str = "",
                        spawn_caller: Optional[str] = None,
                        agent_id: str = "main") -> dict:
    """Review this branch's changes for security vulnerabilities.

    Collects everything that changed since the baseline — commits,
    staged changes, unstaged edits, and newly added files — and has a
    read-only agent audit it for injection, broken authentication and
    authorization, leaked secrets, unsafe deserialization and dynamic
    execution, concurrency and TOCTOU bugs, resource exhaustion, risky
    dependencies, and information disclosure through errors.

    Only what this change introduced or made worse is reported. A
    weakness the branch did not touch is left alone, so the result is a
    list the author can act on now.

    ``base`` empty picks the baseline automatically: the merge base
    with the branch's upstream, or with the repository's default branch
    when there is no upstream. A repository with neither raises
    ``NoBaselineError`` naming what was missing rather than guessing a
    range.

    A branch with no changes returns no findings immediately, without
    spawning a review agent.

    Returns ``{"findings", "base", "files_reviewed"}``, findings
    ordered critical first, each carrying ``severity``, ``file``,
    ``line``, ``title``, ``scenario`` and ``recommendation``.
    """
    from openprogram.worktree.context import current_worktree_path
    sid = session_id or current_session_id()
    cwd = current_worktree_path()

    base = (base or "").strip() or resolve_base(cwd)
    diff, files = collect_diff(base, cwd)
    if not diff.strip():
        return {"findings": [], "base": base, "files_reviewed": 0}

    findings = review_diff(diff=diff, base=base, files=files, session_id=sid,
                           agent_id=agent_id, spawn_caller=spawn_caller)
    return {"findings": findings, "base": base, "files_reviewed": len(files)}


__all__ = [
    "run_security_review", "review_diff", "resolve_base", "collect_diff",
    "clip_diff", "NoBaselineError", "REVIEW_TOOLS", "SEVERITIES",
    "DIFF_MAX_CHARS", "DEFAULT_BRANCH_CANDIDATES",
]
