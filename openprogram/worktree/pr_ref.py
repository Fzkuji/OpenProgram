"""Resolve a PR reference (``123`` / ``#123`` / a GitHub PR URL) into a
local branch, ready for ``worktree_create``.

Two fetch paths, chosen by ``gh pr view --json isCrossRepository``:

  * same-repo PR — the head branch already exists on ``origin``; a
    plain ``git fetch origin <branch>:<local_branch>`` gets it.
  * fork PR — the head branch lives on the contributor's fork, which
    this repo has no remote for. ``git fetch origin pull/<n>/head:<local_branch>``
    works against GitHub's synthetic per-PR ref without needing the
    fork as a remote (the same trick ``gh pr checkout`` uses).

Requires the ``gh`` CLI, authenticated (``gh auth login``) — used only
to look up the PR's metadata (``headRefName`` / cross-repo flag), not
to do the fetch itself.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

_GIT_TIMEOUT_SECS = 60.0

# https://github.com/<owner>/<repo>/pull/<number>[...]
_PR_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+/pull/(\d+)(?:[/?#].*)?$"
)


class PrRefError(Exception):
    """Raised for bad PR input, missing/unauthenticated ``gh``, or a
    failed ``gh``/``git`` call. Message is written to be shown to the
    LLM/user as-is."""


def parse_pr_ref(ref: str) -> int:
    """Parse ``123`` / ``#123`` / a GitHub PR URL into a PR number.

    Raises :class:`PrRefError` with a clear message on anything else.
    """
    text = (ref or "").strip()
    if not text:
        raise PrRefError("empty PR reference")
    m = _PR_URL_RE.match(text)
    if m:
        return int(m.group(1))
    if text.startswith("#"):
        text = text[1:]
    if text.isdigit():
        return int(text)
    raise PrRefError(
        f"invalid_pr_ref: {ref!r} is not a PR number, '#number', or a "
        "GitHub PR URL (https://github.com/<owner>/<repo>/pull/<number>)"
    )


@dataclass
class PrInfo:
    number: int
    head_ref_name: str
    is_cross_repository: bool
    head_owner: Optional[str]  # set when cross-repository (fork owner login)


def _require_gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise PrRefError(
            "gh_not_found: the GitHub CLI (`gh`) is required to resolve a "
            "PR reference into a branch. Install it and run `gh auth login`."
        )
    return gh


def fetch_pr_info(pr_number: int, *, cwd: str) -> PrInfo:
    """``gh pr view <n> --json ...`` — raises :class:`PrRefError` if
    ``gh`` is missing, unauthenticated, or the call fails (e.g. PR not
    found)."""
    gh = _require_gh()
    try:
        proc = subprocess.run(
            [gh, "pr", "view", str(pr_number),
             "--json", "headRefName,headRepositoryOwner,isCrossRepository"],
            cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired as e:
        raise PrRefError(f"gh pr view timed out after {_GIT_TIMEOUT_SECS}s") from e
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "auth" in stderr.lower() or "gh auth login" in stderr.lower():
            raise PrRefError(
                "gh_not_authenticated: `gh` is not logged in; run "
                f"`gh auth login`. ({stderr})"
            )
        raise PrRefError(f"gh pr view failed (rc={proc.returncode}): {stderr or proc.stdout.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PrRefError(f"gh pr view returned unparsable JSON: {e}") from e
    owner = (data.get("headRepositoryOwner") or {}).get("login")
    return PrInfo(
        number=pr_number,
        head_ref_name=data["headRefName"],
        is_cross_repository=bool(data.get("isCrossRepository")),
        head_owner=owner,
    )


def _run_git(*args: str, cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        timeout=_GIT_TIMEOUT_SECS,
    )
    return proc.returncode, proc.stdout, proc.stderr


def fetch_pr_branch(
    pr_number: int, *, source_repo: str, local_branch: str, remote: str = "origin",
) -> PrInfo:
    """Resolve the PR, fetch its head commit into ``local_branch`` on
    ``source_repo``, and return the :class:`PrInfo`.

    Same-repo PR: ``git fetch <remote> <headRefName>:<local_branch>``.
    Fork PR: ``git fetch <remote> pull/<n>/head:<local_branch>`` — the
    GitHub-provided synthetic ref, avoiding the need to register the
    fork as a remote (mirrors ``gh pr checkout``).

    Raises :class:`PrRefError` on any failure. Does not create the
    worktree itself — callers pass ``local_branch`` as
    ``WorktreeManager.create_worktree``'s ``base_ref``.
    """
    info = fetch_pr_info(pr_number, cwd=source_repo)
    if info.is_cross_repository:
        refspec = f"pull/{pr_number}/head:{local_branch}"
    else:
        refspec = f"{info.head_ref_name}:{local_branch}"
    rc, out, err = _run_git("fetch", remote, refspec, cwd=source_repo)
    if rc != 0:
        raise PrRefError(
            f"git fetch failed for PR #{pr_number} "
            f"({'fork' if info.is_cross_repository else 'same-repo'}, "
            f"refspec={refspec!r}): {err.strip() or out.strip()}"
        )
    return info


__all__ = [
    "PrRefError",
    "PrInfo",
    "parse_pr_ref",
    "fetch_pr_info",
    "fetch_pr_branch",
]
