"""One command for everything a docs page must pass.

Run: python -m scripts.docs_site.check [PATH ...] [--base REV | --staged | --all]
                                       [--report-only] [--json] [--unfiltered]
     python -m scripts.docs_site.check [PATH ...] [--base REV] --ack PAGE [PAGE ...] --reason TEXT
Exit: 0 no error in scope, or report only; 1 an error in scope; 2 the tool failed, or bad usage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.testing.affected_tests import changed_files, is_linked_worktree

from . import hook_status
from .governance import (acknowledgements, baseline, claims, content_checks, corpus as loader, format_checks,
                         impact, links, ratchet)
from .governance.model import ERROR, WARNING, Corpus, Finding, Scope

REPO_ROOT = Path(__file__).resolve().parents[2]
# About the change, not about text it left alone: shown whatever line they sit on.
_ALWAYS_SHOWN = {"format.zh-stale", "baseline.loose", "content.duplicate-across-pages"}
Check = Callable[[Corpus], Iterable[Finding]]


@dataclass
class Report:
    scope: Scope
    findings: list[Finding] = field(default_factory=list)
    impact: list = field(default_factory=list)
    filtered: list = field(default_factory=list)
    seconds: float = 0.0
    untouched: dict[str, int] = field(default_factory=dict)  # warnings per page on lines this change left alone

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == WARNING]

    @property
    def blocks(self) -> bool:
        """The shared checkout's dirty tree holds other sessions' pages, so it only reports."""
        return bool(self.errors) and self.scope.mode != "shared"


def default_checks() -> list[Check]:
    return [format_checks.check, content_checks.check, links.check, hook_status.check, claims.check, ratchet.check]


class UsageProblem(Exception):
    """A named path the check cannot know about: nothing would be checked."""


def _touched(scope: Scope, paths: list[str]) -> dict[str, set[int] | None]:
    """Lines each file changed against the base; None for a file that is new."""
    if not paths:
        return {}
    command = ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames"]
    command += ["--cached"] if scope.reads == "index" else []
    command += [scope.base or "HEAD", *(["HEAD"] if scope.reads == "head" else []), "--", *paths]
    touched: dict[str, set[int]] = {}
    current = ""
    for line in _git(scope.root, "-c", "core.quotepath=off", *command).splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
        elif line.startswith("@@ ") and current:
            first, _comma, count = line.split("+", 1)[1].split(" ", 1)[0].partition(",")
            touched.setdefault(current, set()).update(range(int(first), int(first) + int(count or 1)))
    known = loader.read_many(scope.root, scope.base or "HEAD", paths)
    return {path: None if known[path] is None else touched.get(path, set()) for path in paths}


def _keep_touched_warnings(report: Report) -> None:
    """A routine check reports what it changed; the rest of the page's warnings become a count."""
    if report.scope.mode == "all":
        return
    held = [finding for finding in report.warnings
            if finding.rule not in _ALWAYS_SHOWN and finding.path in report.scope.changed]
    if not held:
        return
    touched = _touched(report.scope, sorted({finding.path for finding in held}))
    for finding in held:
        lines = touched.get(finding.path, set())
        if lines is None or finding.line in lines:
            continue
        report.findings.remove(finding)
        report.untouched[finding.path] = report.untouched.get(finding.path, 0) + 1


def run(scope: Scope, checks: list[Check] | None = None, unfiltered: bool = False) -> Report:
    started = time.monotonic()
    corpus = loader.load(scope)
    report = Report(scope)
    found: list[Finding] = []
    for check in default_checks() if checks is None else checks:
        found.extend(check(corpus))
    report.findings = sorted(set(baseline.apply(found, corpus)),
                             key=lambda f: (f.severity != ERROR, f.path, f.line, f.rule, f.subject))
    if checks is None:
        report.impact, report.filtered = impact.analyse(corpus, unfiltered)
    _keep_touched_warnings(report)
    report.seconds = time.monotonic() - started
    return report


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def _in_revision(root: Path, revision: str, relative: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{revision}:{relative}"], cwd=root, capture_output=True).returncode == 0


def _repository_path(root: Path, raw: str, base: str | None = None) -> list[str]:
    path = Path(raw)
    absolute = (path if path.is_absolute() else Path.cwd() / path).resolve()
    try:
        relative = absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = raw
    if absolute.is_dir():
        return [name for name in _git(root, "ls-files", "--", relative).splitlines() if name]
    if not absolute.is_file() and not any(_in_revision(root, revision, relative) for revision in (base, "HEAD") if revision):
        raise UsageProblem(f"{raw} is not on disk, in {base or 'HEAD'} or in HEAD; give each path as its own argument")
    return [relative]


def resolve_scope(args: argparse.Namespace, root: Path) -> Scope:
    if args.all:
        return Scope("all", root)
    if args.paths:
        changed = tuple(sorted({name for raw in args.paths for name in _repository_path(root, raw, args.base)}))
        if not changed:
            raise UsageProblem(f"{' '.join(args.paths)} holds no tracked file, so there is nothing to check")
        return Scope("paths", root, changed, args.base, shared_tree=not is_linked_worktree(root))
    if args.staged:
        names = _git(root, "diff", "--cached", "--name-only", "--no-renames")
        return Scope("staged", root, tuple(name for name in names.splitlines() if name))
    if args.base:
        return Scope("range", root, tuple(changed_files(root, args.base)), args.base,
                     committed=not is_linked_worktree(root))
    mode = "worktree" if is_linked_worktree(root) else "shared"
    return Scope(mode, root, tuple(changed_files(root)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.docs_site.check",
                                     description="Check documentation pages and report pages a change may have outdated.")
    parser.add_argument("paths", nargs="*", metavar="PATH", help="check these changed files")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--base", metavar="REV", help="check everything changed since this revision")
    which.add_argument("--staged", action="store_true", help="check the git index")
    which.add_argument("--all", action="store_true", help="check every page and the site-wide rules")
    parser.add_argument("--report-only", action="store_true", help="exit 0 unless the tool itself failed")
    parser.add_argument("--json", action="store_true", help="print findings and impact as JSON")
    parser.add_argument("--unfiltered", action="store_true", help="turn the diff filter off: every page citing a changed file")
    parser.add_argument("--ack", nargs="+", metavar="PAGE", help="record that these pages were checked for this change")
    parser.add_argument("--reason", help="why no doc change is needed, with --ack")
    return parser


def _as_json(report: Report) -> str:
    return json.dumps({
        "mode": report.scope.mode,
        "changed": list(report.scope.changed),
        "errors": len(report.errors),
        "warnings": len(report.warnings),
        "seconds": round(report.seconds, 3),
        "untouched_warnings": report.untouched,
        "findings": [asdict(finding) for finding in report.findings],
        "impact": [asdict(item) for item in report.impact],
        "filtered": [asdict(item) for item in report.filtered],
    }, ensure_ascii=False, indent=1)


def _print_impact(items: list, filtered: list, scope: Scope) -> None:
    for entry in filtered:
        print(f"{entry.file}: {entry.cited} pages cite this file; {entry.shared} share a changed word"
              " (--unfiltered lists them all)")
    waiting = [item for item in items if not item.acknowledged]
    if not waiting:
        return
    print(f"\nAffected pages, unchanged by this change ({len(waiting)}):")
    for item in waiting:
        note = "  (edited, please confirm)" if item.edited else ""
        print(f"  {item.page}: {item.changed}  [{', '.join(item.matched[:3])}]{note}")
    paths = "<paths> " if scope.mode == "shared" else ""
    print("Read each page. Update it, or record the answer: "
          f"python -m scripts.docs_site.check {paths}--ack PAGE ... --reason 'why no change is needed'")


def _print(report: Report) -> None:
    for finding in report.findings:
        print(finding.render())
    for path, count in sorted(report.untouched.items()):
        print(f"{path}: {count} more warning(s) on lines this change did not touch (--all shows them)")
    _print_impact(report.impact, report.filtered, report.scope)
    pending = sum(1 for item in report.impact if not item.acknowledged)
    print(f"docs check ({report.scope.mode}): {len(report.errors)} error(s), {len(report.warnings)} warning(s), "
          f"{pending} affected page(s), {report.seconds:.1f}s")
    if report.errors and report.scope.mode == "shared":
        print("Not blocking: the shared checkout also holds other sessions' pages. Name your paths to enforce.")


def _acknowledge(scope: Scope, pages: list[str], reason: str) -> int:
    if scope.mode == "shared":
        raise ValueError("name your changed files too: the shared checkout also holds other sessions' changes")
    items, _filtered = impact.analyse(loader.load(scope), unfiltered=True)
    listed = {item.page: set(item.files) for item in items}
    print(f"recorded {acknowledgements.record(scope, pages, reason, listed)} acknowledgement(s)")
    return 0


def main(argv: list[str] | None = None, *, root: Path = REPO_ROOT) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if bool(args.ack) != bool(args.reason):
        parser.error("--ack and --reason go together")
    try:
        scope = resolve_scope(args, root)
        if args.ack:
            try:
                return _acknowledge(scope, args.ack, args.reason)
            except ValueError as error:
                parser.error(str(error))
        report = run(scope, unfiltered=args.unfiltered)
    except UsageProblem as error:
        print(f"docs check: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # exit 2 says the tool failed, not that a page did
        detail = getattr(error, "stderr", "") or f"{type(error).__name__}: {error}"
        print(f"docs check could not run: {detail}", file=sys.stderr)
        return 2
    if args.json:
        print(_as_json(report))
    else:
        _print(report)
    return 1 if report.blocks and not args.report_only else 0


if __name__ == "__main__":
    raise SystemExit(main())
