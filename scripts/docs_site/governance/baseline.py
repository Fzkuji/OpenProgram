"""Content findings that cannot be fixed, each with a reason. The list can only shrink."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable

from . import policy, ratchet
from .model import ERROR, WARNING, Corpus, Finding

PATH = "scripts/docs_site/governance/baseline.json"
_ALLOWED_PREFIX = "content."
_PAGE_RULES = {"content.page-prose", "content.prose-share"}  # measured per page: the entry caps the measure


def _parse(text: str) -> list[dict]:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _key(entry: dict) -> tuple[str, str, str]:
    return entry.get("rule", ""), entry.get("path", ""), entry.get("subject", "")


def _error(message: str, fix: str, entry: dict) -> Finding:
    subject = "/".join(_key(entry))
    return Finding("baseline.invalid", ERROR, PATH, 0, message, fix, subject)


def _over_ceiling(finding: Finding, entry: dict | None) -> bool:
    return (entry is not None and finding.measure is not None
            and finding.measure > float(entry.get("max", 0)))


def _grown(finding: Finding, entry: dict) -> Finding:
    return Finding(finding.rule, ERROR, finding.path, finding.line,
                   f"{finding.message} The baseline allows at most {entry.get('max')}.",
                   finding.fix, finding.subject, finding.measure)


def apply(findings: Iterable[Finding], corpus: Corpus) -> list[Finding]:
    """Drop baselined content findings and report every way the baseline itself is wrong."""
    try:
        entries = _parse(corpus.read(PATH))
    except (OSError, subprocess.CalledProcessError):
        return list(findings)
    findings = list(findings)
    earlier_text = ratchet.fixed_text(corpus, PATH)
    earlier = None if earlier_text is None else {_key(entry): entry for entry in _parse(earlier_text)}
    admitted = {_key(entry): entry for entry in entries
                if _key(entry)[0].startswith(_ALLOWED_PREFIX) and (earlier is None or _key(entry) in earlier)}
    kept: list[Finding] = []
    for finding in findings:
        entry = admitted.get(finding.key)
        if entry is None:
            kept.append(finding)
        elif finding.rule in _PAGE_RULES and _over_ceiling(finding, entry):
            kept.append(_grown(finding, entry))
    problems: list[Finding] = []
    scope = corpus.scope
    measured = {finding.key: finding for finding in findings}
    pages_in_scope = {page.path for page in corpus.in_scope()}
    for entry in entries:
        key = _key(entry)
        in_scope = scope.mode == "all" or key[1] in pages_in_scope or PATH in scope.changed
        if not key[0].startswith(_ALLOWED_PREFIX):
            problems.append(_error(f"{key[0]} on {key[1]} cannot be baselined.",
                                   "fix the page; the baseline holds content rules only.", entry))
        if len(str(entry.get("reason", "")).strip()) < policy.BASELINE_REASON_MIN:
            problems.append(_error(f"the entry for {key[1]} gives no real reason.",
                                   f"write a reason of {policy.BASELINE_REASON_MIN} or more characters.", entry))
        if earlier is not None and key not in earlier:
            problems.append(_error(f"new entry for {key[1]}: the baseline only shrinks.",
                                   "fix the finding instead of adding it.", entry))
        if key[0] in _PAGE_RULES:
            problems.extend(_ceiling_problems(entry, earlier.get(key) if earlier else None))
        finding = measured.get(key)
        if finding is None:
            if scope.mode == "all" or key[1] in pages_in_scope:
                problems.append(_error(f"the entry for {key[1]} ({key[0]}) no longer matches a finding.",
                                       "delete it from the baseline.", entry))
        elif in_scope and key[0] in _PAGE_RULES and finding.measure is not None \
                and finding.measure < float(entry.get("max", 0)):
            problems.append(Finding("baseline.loose", WARNING, PATH, 0,
                                    f"{key[1]} now measures {finding.measure}, below its max of {entry.get('max')}.",
                                    "lower max to the measured value.", "/".join(key)))
    return kept + problems


def _ceiling_problems(entry: dict, before: dict | None) -> list[Finding]:
    if not isinstance(entry.get("max"), (int, float)):
        return [_error(f"the entry for {entry.get('path')} has no numeric max.",
                       "give the page-level entry the value it measured as max.", entry)]
    if before is not None and "max" in before and entry["max"] > before["max"]:
        return [_error(f"the entry for {entry.get('path')} raises max from {before['max']} to {entry['max']}.",
                       "the ceiling only comes down; cut the page instead.", entry)]
    return []
