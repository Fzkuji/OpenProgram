"""Which documentation pages a code change may have outdated."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import acknowledgements
from .corpus import visible_words
from .references import (
    CLI_COMMANDS,
    CLI_PARSER,
    DEEP_DIRECTORY,
    SETTINGS_FILE,
    PageReferences,
    ParserChange,
    Repository,
    command_path,
    repository_for,
)

CLI_APPLICATION = "openprogram/interfaces/cli/application.py"

# Above this many pages on one file, a page counts only if a code word on it
# appears in the changed lines. Replayed over 252 commits: p50 6 pages, p90 17.
FILTER_ABOVE = 3

_STRENGTH = {"covers": 0, "path": 1, "module": 2, "partial": 3, "bare": 3, "route": 4, "setting": 4, "command": 5, "import": 6}


@dataclass(frozen=True)
class ImpactItem:
    page: str                  # docs/x.html
    changed: str               # the changed file
    matched: tuple[str, ...]   # references and diff tokens, strongest first
    declared: bool             # named by meta covers
    acknowledged: bool
    files: tuple[str, ...] = ()  # every changed file pointing at the page
    edited: bool = False       # the page changed, but its English text reads the same


@dataclass(frozen=True)
class Filtered:
    """Pages citing a changed file that the diff filter left out, so the report can say so."""
    file: str
    cited: int
    shared: int


def _depth(path: str) -> int:
    return len(PurePosixPath(path).parts)


def _command_targets(repository: Repository, value: str) -> list[str]:
    targets = [CLI_PARSER]
    parts = value.split()
    word = next((part for part in parts[1:] if not part.startswith("-")), "")
    if word:
        file = f"{CLI_COMMANDS}/{word.replace('-', '_')}.py"
        directory = f"{CLI_COMMANDS}/{word.replace('-', '_')}"
        if file in repository.files:
            targets.append(file)
        elif directory in repository.directories:
            targets.append(directory + "/")
        elif CLI_APPLICATION in repository.files:
            targets.append(CLI_APPLICATION)  # verbs with no module of their own are handled here
    return targets


def _deep_directory(repository: Repository, value: str) -> list[str]:
    clean = value.rstrip("/")
    if clean in repository.files:
        return [clean]
    if clean in repository.directories and _depth(clean) >= DEEP_DIRECTORY:
        return [clean + "/"]
    return []


def _command_file(repository: Repository, value: str) -> str | None:
    word = next((part for part in value.split()[1:] if not part.startswith("-")), "")
    file = f"{CLI_COMMANDS}/{word.replace('-', '_')}.py"
    return file if word and file in repository.files else None


def targets_of(repository: Repository, found: PageReferences) -> dict[str, tuple[int, str]]:
    """Repository file or directory -> (strength, the reference on the page)."""
    out: dict[str, tuple[int, str]] = {}

    def add(target: str, kind: str, value: str) -> None:
        strength = _STRENGTH[kind]
        if target not in out or strength < out[target][0]:
            out[target] = (strength, value)

    for reference in found.references:
        kind, value = reference.kind, reference.value
        if kind == "path":
            for target in _deep_directory(repository, value):
                add(target, kind, value)
        elif kind == "partial":
            resolved = repository.resolve_partial(value)
            if resolved and (not resolved.endswith("/") or _depth(resolved) >= DEEP_DIRECTORY):
                add(resolved, kind, value)
        elif kind == "bare":
            resolved = repository.resolve_bare(value)
            if resolved:
                add(resolved, kind, value)
        elif kind in {"module", "script-module"}:
            target = repository.resolve_module(value)
            if target and (not target[0].endswith("/") or _depth(target[0]) >= DEEP_DIRECTORY):
                add(target[0], "module", value)
        elif kind == "route":
            for file in repository.route_files(value):
                add(file, kind, value)
        elif kind == "setting":
            add(SETTINGS_FILE, kind, value)
        elif kind == "command":
            for target in _command_targets(repository, value):
                add(target, kind, value)
            file = _command_file(repository, value)
            for target in repository.command_imports(file) if file else ():
                add(target, "import", value)
    return out


def _ancestors(path: str) -> list[str]:
    return [f"{parent}/" for parent in PurePosixPath(path).parents if str(parent) != "."]


def _under_changed_command(repository: Repository, found: PageReferences, change: ParserChange) -> bool:
    parsers = [repository.parser, *([repository.previous_parser] if repository.previous_parser is not None else [])]
    commands = [r.value for r in found.references if r.kind == "command"]
    if any(flag in command.split() for flag in change.root_options for command in commands):
        return True
    walked = {command_path(parser, command) for parser in parsers for command in commands}
    return any(path[:len(changed)] == changed for path in walked for changed in change.paths)


def affected(repository: Repository, pages: list, changed: list[str], diff_words: dict[str, set[str]],
             filtered: list[Filtered] | None = None, unfiltered: bool = False,
             links: dict[str, set[str]] | None = None,
             ) -> dict[str, list[tuple[str, int, tuple[str, ...], bool]]]:
    """page -> [(changed file, strength, matched, declared)]. ``unfiltered`` turns the diff filter off."""
    change = repository.parser_change() if CLI_PARSER in changed else None
    by_page = {page.path: page for page in pages}
    index: dict[str, list[tuple[str, int, str, bool]]] = defaultdict(list)
    words: dict[str, set[str]] = {}
    for page in pages:
        found = repository.references(page)
        words[page.path] = found.code_words | {r.value for r in found.references if r.kind == "setting"}
        for value, _line in found.covers:
            index[value].append((page.path, _STRENGTH["covers"], value, True))
        for target, (strength, value) in targets_of(repository, found).items():
            index[target].append((page.path, strength, value, False))
    hits: dict[str, list[tuple[str, int, tuple[str, ...], bool]]] = defaultdict(list)
    for file in changed:
        keys = [file, *_ancestors(file), *[key.rstrip("/") for key in _ancestors(file)]]
        best: dict[str, tuple[int, str, bool]] = {}
        for key in keys:
            for page, strength, value, declared in index.get(key, ()):
                current = best.get(page)
                if current is None or (not current[2] and declared) or (declared == current[2] and strength < current[0]):
                    best[page] = (strength, value, declared)
        for named in (links or {}).get(file, ()):
            if named in by_page:
                best[named] = (_STRENGTH["covers"], file, True)
        changed_words = diff_words.get(file, set())
        kept = 0
        for page, (strength, value, declared) in best.items():
            shared = tuple(sorted(words[page] & changed_words))
            if not unfiltered and not declared:
                if file == CLI_PARSER and change is not None and strength == _STRENGTH["command"]:
                    if not _under_changed_command(repository, repository.references(by_page[page]), change):
                        continue
                elif len(best) > FILTER_ABOVE and file in diff_words and not shared:
                    continue
            kept += 1
            hits[page].append((file, strength, (value, *shared[:5]), declared))
        if filtered is not None and kept < len(best):
            filtered.append(Filtered(file, len(best), kept))
    return hits


def _reread(corpus, touched: set[str]) -> tuple[list, set[str]]:
    """Pages still owing a read, and those of them edited with their English text unchanged."""
    pages = []
    edited: set[str] = set()
    changed_pages = [page.path for page in corpus.pages if page.path in touched]
    before = corpus.base_texts(changed_pages) if changed_pages else {}
    for page in corpus.pages:
        if page.path in touched:
            old = before[page.path]
            if old is None or visible_words(old) != visible_words(page.text):
                continue  # really updated, or new
            edited.add(page.path)
        elif page.zh_path in touched:
            edited.add(page.path)
        pages.append(page)
    return pages, edited


def analyse(corpus, unfiltered: bool = False) -> tuple[list[ImpactItem], list[Filtered]]:
    """Pages this change may have outdated, unacknowledged first, strongest first, and what the filter left out."""
    scope = corpus.scope
    repository = repository_for(corpus)
    changed = repository.code_changes()
    if not changed:
        return [], []
    pages, edited = _reread(corpus, set(scope.changed))
    filtered: list[Filtered] = []
    hits = affected(repository, pages, changed, repository.diff_identifiers(), filtered, unfiltered,
                    repository.declared_links(changed))
    tips = acknowledgements.blobs(scope)
    starts = acknowledgements.base_blobs(scope)
    known = acknowledgements.acknowledged(scope)
    items: list[tuple[tuple, ImpactItem]] = []
    for page, entries in hits.items():
        entries.sort(key=lambda entry: (not entry[3], entry[1], -len(entry[2]), entry[0]))
        open_entries = [entry for entry in entries if not acknowledgements.settled(
            known, page, entry[0], starts.get(entry[0], ""), tips.get(entry[0], ""))]
        lead = (open_entries or entries)[0]
        matched = tuple(dict.fromkeys(word for entry in entries for word in entry[2]))
        item = ImpactItem(page, lead[0], matched, any(entry[3] for entry in entries), not open_entries,
                          tuple(dict.fromkeys(entry[0] for entry in entries)), page in edited)
        items.append(((item.acknowledged, not item.declared, lead[1], -len(matched), page), item))
    return [item for _key, item in sorted(items, key=lambda pair: pair[0])], filtered


def report(corpus) -> list[ImpactItem]:
    return analyse(corpus)[0]
