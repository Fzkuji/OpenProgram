"""Stop a commit or merge in a linked worktree whose own docs changes have errors.

Written for a Claude Code PreToolUse hook on Bash: it reads the hook's JSON on
stdin and exits 2 to block. It uses only the standard library, needs no
node, and compares with the point this branch left dev, so another session's
pages cannot fail it. A commit in the main checkout passes through: the
pre-commit hook already covers that case.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

_SEGMENTS = re.compile(r"&&|\|\||;|\n")
_TIMEOUT_SECONDS = 180
EXIT_BLOCK = 2


def gated_directory(command: str, cwd: str) -> Path | None:
    """Where a ``git commit`` or ``git merge`` in this shell line would run; None when there is none."""
    here = Path(cwd)
    for segment in _SEGMENTS.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        if len(words) >= 2 and words[0] == "cd":
            here = here / Path(words[1]).expanduser()
        if not words or words[0] != "git":
            continue
        place, index = here, 1
        while index < len(words) and words[index] in {"-C", "-c"}:
            if words[index] == "-C":
                place = place / Path(words[index + 1]).expanduser()
            index += 2
        rest = words[index:]
        if rest[:1] == ["commit"] or (rest[:1] == ["merge"] and not {"--abort", "--quit"} & set(rest)):
            return place
    return None


def _git(root: Path, *arguments: str) -> str | None:
    done = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def _python(common: Path) -> str:
    venv = common.parent / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def run_gate(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    root_text = _git(directory, "rev-parse", "--show-toplevel")
    common_text = _git(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")
    own = _git(directory, "rev-parse", "--absolute-git-dir")
    if not root_text or not common_text or own == common_text:
        return 0
    root = Path(root_text)
    point = _git(root, "merge-base", "HEAD", "dev")
    if not point or not (root / "scripts/docs_site/check.py").is_file():
        return 0
    try:
        done = subprocess.run([_python(Path(common_text)), "-m", "scripts.docs_site.check", "--base", point, "--json"],
                              cwd=root, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as error:
        print(f"docs check did not finish, not blocking: {error}", file=sys.stderr)
        return 0
    if done.returncode not in {0, 1}:
        print(f"docs check could not run, not blocking: {done.stderr.strip()[:400]}", file=sys.stderr)
        return 0
    report = json.loads(done.stdout)
    pending = sum(1 for item in report["impact"] if not item["acknowledged"])
    if pending:
        print(f"docs check: {pending} affected page(s) not yet read; run python -m scripts.docs_site.check --base {point[:10]}")
    if done.returncode == 0:
        return 0
    for finding in report["findings"]:
        if finding["severity"] == "error":
            print(f"{finding['path']}:{finding['line']}: {finding['rule']}: {finding['message']} Fix: {finding['fix']}", file=sys.stderr)
    print(f"Blocked: {report['errors']} docs error(s) in this branch's changes.", file=sys.stderr)
    return EXIT_BLOCK


def main() -> int:
    try:
        event = json.load(sys.stdin)
        command = event["tool_input"]["command"]
        cwd = event.get("cwd") or "."
    except (ValueError, KeyError, TypeError):
        return 0
    directory = gated_directory(command, cwd)
    return 0 if directory is None else run_gate(directory)


if __name__ == "__main__":
    raise SystemExit(main())
