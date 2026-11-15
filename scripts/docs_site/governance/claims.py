"""Check that the concrete things a page names still exist.

Every page in scope is checked in full. A page outside the scope is checked
only for what this change could have broken: a file the change moved, a route
when the server changed, a command when the CLI changed, an environment
variable or setting the change removed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
import difflib
import re

from .. import html_source
from . import policy
from .model import WARNING, Finding
from .state_names import BACKUP_FILE, classified
from .references import (
    CLI_AREA,
    NO_CODE,
    SERVER_AREA,
    SETTINGS_FILE,
    PageReferences,
    Reference,
    Repository,
    normalize_route,
    repository_for,
    verify_command,
)

_MARKER = re.compile(r'data-claim="(?:example|invalid)"')
_NONE = '<meta name="covers" content="none"'

_EXAMPLE_FIX = 'or mark an illustrative path with data-claim="example" on its code element.'
_SUGGEST_CUTOFF = 0.8
_PATH_RULES = {"claims.path", "claims.partial", "claims.bare"}


def _finding(rule: str, path: str, reference: Reference, message: str, fix: str, subject: str = "") -> Finding:
    if rule in _PATH_RULES:
        fix = f"{fix}, {_EXAMPLE_FIX}"
    return Finding(rule, "error", path, reference.line, message, fix, subject or reference.value)


def _distinct(text: str) -> str:
    """What tells one name from its siblings: not the shared prefix or the folders."""
    if text.startswith("/api/"):
        return text.removeprefix("/api/")
    return text.removeprefix("OPENPROGRAM_").rsplit("/", 1)[-1]


def _suggestion(value: str, candidates: Iterable[str]) -> str:
    table: dict[str, str] = {}
    for candidate in candidates:
        table.setdefault(_distinct(candidate), candidate)
    close = difflib.get_close_matches(_distinct(value), list(table), n=1, cutoff=_SUGGEST_CUTOFF)
    return f"Did you mean {table[close[0]]}? Update it" if close else "Update or remove it"


def _moved_to(repository: Repository, value: str) -> str | None:
    clean = value.rstrip("/")
    renames = repository.renames() if repository.code_changes() or repository.scope.changed else {}
    for old, new in renames.items():
        if old == clean or old.endswith("/" + clean):
            return new
    return None


def _path_suggestion(repository: Repository, value: str, candidates: Iterable[str]) -> str:
    moved = _moved_to(repository, value)
    return f"This change moved it to {moved}. Update it" if moved else _suggestion(value, candidates)


def _command_findings(repository: Repository, path: str, reference: Reference) -> Iterable[Finding]:
    for problem in verify_command(repository.parser, reference.value, repository.settings):
        if problem.kind == "setting":
            yield _finding("claims.setting", path, reference,
                           f"`{reference.value}` names the setting {problem.token}, which SETTINGS does not define.",
                           _suggestion(problem.token, repository.settings), problem.token)
        elif problem.kind == "flag":
            yield _finding("claims.flag", path, reference,
                           f"`{reference.value}` uses {problem.token}, which that command does not accept.",
                           _suggestion(problem.token, problem.expected.split(", ")), problem.token.split()[-1])
        else:
            yield _finding("claims.command", path, reference,
                           f"`{reference.value}` uses {problem.token}, which that command does not offer.",
                           _suggestion(problem.token, problem.expected.split(", ")), problem.token)


def _check_reference(repository: Repository, path: str, reference: Reference,
                     widening: "_Widening | None" = None) -> Iterable[Finding]:
    kind = reference.kind
    if kind == "command":
        yield from _command_findings(repository, path, reference)
    elif kind == "path":
        if not reference.value.startswith("references/") and not repository.path_exists(reference.value):
            yield _finding("claims.path", path, reference,
                           f"{reference.value} is not in the repository.",
                           _path_suggestion(repository, reference.value, repository.files))
    elif kind in {"partial", "bare"}:
        exists = repository.has_partial(reference.value) if kind == "partial" else repository.has_bare(reference.value)
        if not exists and (repository.checkable(reference, path) or (widening is not None and widening.broke(reference))):
            yield _finding(f"claims.{kind}", path, reference,
                           f"{reference.value} resolves to nothing in the repository.",
                           _path_suggestion(repository, reference.value, repository.files))
    elif kind in {"module", "script-module"}:
        if not repository.module_exists(reference.value):
            yield _finding("claims.module", path, reference,
                           f"{reference.value} does not resolve to a module or to a name in one.",
                           "Update the module path")
    elif kind == "route":
        if not repository.route_files(reference.value):
            yield _finding("claims.route", path, reference,
                           f"{reference.value} is not declared under {SERVER_AREA}.",
                           _suggestion(reference.value, repository.routes))
    elif kind == "state":
        if not repository.state_names(reference.value):
            yield _finding("claims.state", path, reference,
                           f"{reference.value} is not a name in the state directory.",
                           "Add it to INCLUDED, CREDENTIAL_ENTRIES or REGENERATED in openprogram/operations/backup.py if it is real; otherwise update the page",
                           reference.value)
    elif kind == "env":
        if reference.value not in repository.environment:
            yield _finding("claims.env", path, reference,
                           f"{reference.value} is not read anywhere outside docs and tests.",
                           _suggestion(reference.value, repository.environment))


def _covers_findings(repository: Repository, path: str, found: PageReferences,
                     only_gone: "_Widening | None" = None) -> Iterable[Finding]:
    for value, line in found.covers:
        if only_gone is not None and not only_gone.path_gone(value):
            continue
        if value != NO_CODE and not repository.path_exists(value):
            yield Finding("claims.covers", "error", path, line,
                          f'meta covers names {value}, which is not in the repository.',
                          "Point it at the file or directory this page now describes.", value)


class _Widening:
    """What a change could have broken on pages outside its scope."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        changed = repository.code_changes()
        self.changed = frozenset(changed)
        self.gone = frozenset(path for path in repository.scope.changed if path not in repository.files)
        self.commands = any(path.startswith(CLI_AREA) for path in changed)
        self.routes = any(path.startswith(SERVER_AREA) for path in changed)
        self.gone_modules = frozenset(
            path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
            for path in self.gone if path.endswith(".py"))
        self._removed_text = repository.removed_text() if changed else ""
        words = repository.diff_words() if changed else {}
        self.removed = frozenset().union(*words.values()) if words else frozenset()
        self.settings_changed = SETTINGS_FILE in changed
        self.state_removed = (repository.previous_state_names() - classified(repository.corpus.read(BACKUP_FILE)).known
                              if BACKUP_FILE in changed and BACKUP_FILE in repository.files else frozenset())

    def applies(self, reference: Reference) -> bool:
        kind = reference.kind
        if kind == "command":
            return self.commands
        if kind == "route":
            return self.routes
        if kind == "env":
            return reference.value in self.removed
        if kind == "state":
            return reference.value in self.state_removed
        if kind == "path":
            return self.path_gone(reference.value)
        if kind in {"partial", "bare"}:
            return self.broke(reference)
        if kind in {"module", "script-module"}:
            target = self.repository.resolve_module(reference.value)
            return self._module_gone(reference.value) or bool(target and target[1] and target[0] in self.changed)
        return False

    def path_gone(self, value: str) -> bool:
        clean = value.rstrip("/")
        return clean in self.gone or any(path.startswith(clean + "/") for path in self.gone)

    def broke(self, reference: Reference) -> bool:
        """A bare name or partial path that this change took away."""
        value = reference.value.rstrip("/")
        if reference.kind == "bare":
            return any(path.rsplit("/", 1)[-1] == value for path in self.gone)
        return any(path.endswith("/" + value) or f"/{value}/" in f"/{path}" for path in self.gone)

    def _module_gone(self, dotted: str) -> bool:
        return any(dotted == module or dotted.startswith(module + ".") for module in self.gone_modules)

    def _removed(self, token: str) -> bool:
        return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", self._removed_text) is not None

    def judged(self, finding: Finding, reference: Reference) -> Finding:
        """An out-of-scope claim blocks only when this change took the thing away."""
        kind = reference.kind
        if kind in {"module", "script-module"}:
            caused = self._module_gone(reference.value) or self._removed(reference.value.rsplit(".", 1)[-1])
        elif kind == "route":
            caused = normalize_route(reference.value) in self.repository.removed_routes or self._removed(reference.value)
        elif kind == "command":
            caused = self._removed(finding.subject)
        else:
            caused = True
        unreliable = kind == "command" and self.repository.parser_mixes_unsaved_work()
        if caused and not unreliable:
            return finding
        return replace(finding, severity=WARNING, message=f"{finding.message} This change did not remove it.",
                       fix=f"{finding.fix}, in its own change")


_STATE_ENTRY = re.compile(r"[a-z_][\w.+-]*(?:/|\.json|\.jsonl|\.db)")
_NAMED = '(?<![\\w./-]){}(?![\\w./-])'


def _setting_lines(found: PageReferences, key: str) -> Iterable[int]:
    """Lines where a removed key is really named: a whole dotted token, or a whole span for a plain word."""
    pattern = re.compile(_NAMED.format(re.escape(key)))
    for text, line in found.fragments:
        if "." in key:
            for match in pattern.finditer(text):
                yield line + text.count("\n", 0, match.start())
        elif text.strip() == key:
            yield line


def _vanished_settings(repository: Repository) -> set[str]:
    if SETTINGS_FILE not in repository.code_changes():
        return set()
    return repository.previous_settings() - repository.settings


def _whole_span_lines(found: PageReferences, token: str) -> Iterable[int]:
    for text, line in found.fragments:
        if text.strip() == token:
            yield line


def _names_command(found: PageReferences, path: tuple[str, ...]) -> bool:
    if not path:
        return True
    pattern = re.compile(rf"(?<![\w-]){re.escape(' '.join(path))}(?![\w-])")
    return any(pattern.search(text) for text, _line in found.fragments)


def _beside_commands(found: PageReferences, line: int, commands: frozenset[str]) -> bool:
    return len({text.strip() for text, at in found.fragments if at == line and text.strip() in commands}) >= 2


def _removed_cli_tokens(repository: Repository, path: str, found: PageReferences) -> Iterable[Finding]:
    """A verb or flag this change removed, written bare in a table or sentence."""
    change = repository.parser_change()
    if change is None:
        return
    for command, token in sorted(change.removed):
        # A flag other commands still take only counts on a page about this command.
        if token not in change.vanished and not _names_command(found, command):
            continue
        lines = list(dict.fromkeys(_whole_span_lines(found, token)))
        if not command and not token.startswith("-") and not _names_command(found, ("openprogram", token)):
            # A top-level verb is often an ordinary word as well (an application, a target):
            # it is the command where the page writes it in a list of top-level verbs.
            lines = [line for line in lines if _beside_commands(found, line, change.top_level)]
        for line in lines:
            name = " ".join(("openprogram", *command))
            yield Finding("claims.flag" if token.startswith("-") else "claims.command", "error", path, line,
                          f"{name} no longer offers {token}, and this page still names it.",
                          "Update the page for the new name, or remove it.", token)


def _backup_scope_findings(repository: Repository, path: str, tree, found: PageReferences) -> Iterable[Finding]:
    """The row headers of a page that covers backup.py name state entries; each must be real."""
    if not any(value == BACKUP_FILE for value, _line in found.covers):
        return
    for node in html_source.walk(tree):
        if node.tag != "th" or node.attrs.get("scope") != "row":
            continue
        for part in html_source.walk(node):
            text = html_source.text_of(part).strip() if part.tag == "code" else ""
            if _STATE_ENTRY.fullmatch(text) and not repository.state_names(text.rstrip("/")):
                yield Finding("claims.state", "error", path, part.line,
                              f"{text} is not a name in the state directory.",
                              "Add it to INCLUDED, CREDENTIAL_ENTRIES or REGENERATED in openprogram/operations/backup.py if it is real; otherwise update the page.",
                              text)


def _page_findings(repository: Repository, path: str, found: PageReferences, widening: _Widening) -> Iterable[Finding]:
    for reference in found.references:
        yield from _check_reference(repository, path, reference, widening)
    yield from _covers_findings(repository, path, found)


def _example_commands(path: str, tree) -> Iterable[Finding]:
    """A command cannot be waved through as an example; only one deliberately wrong can be marked."""
    for node in html_source.walk(tree):
        if node.attrs.get("data-claim") == "example" and html_source.text_of(node).strip().startswith("openprogram "):
            yield Finding("claims.example-command", "error", path, node.line,
                          "a real command cannot be marked as an example, so nothing would check it.",
                          'remove the marker and let it be verified; a command shown as deliberately wrong takes data-claim="invalid".',
                          html_source.text_of(node).strip())


def _exemption_counts(page) -> tuple[int, int]:
    return _counts(page.text)


def _counts(text: str) -> tuple[int, int]:
    return len(_MARKER.findall(text)), 1 if _NONE in text else 0


def _exemption_findings(corpus, repository: Repository) -> Iterable[Finding]:
    """Past the ceiling, a page that adds an exemption has to go through policy.py with a reason."""
    if corpus.scope.mode == "all":
        return
    markers = sum(_exemption_counts(page)[0] for page in corpus.pages)
    nones = sum(_exemption_counts(page)[1] for page in corpus.pages)
    ceiling = policy.CLAIM_EXEMPTIONS
    scoped = corpus.in_scope()
    before = corpus.base_texts([page.path for page in scoped]) if scoped else {}
    for page in scoped:
        was, now = _counts(before[page.path] or ""), _exemption_counts(page)
        added = (now[0] - was[0], now[1] - was[1])
        if (added[0] > 0 and markers > ceiling[0]) or (added[1] > 0 and nones > ceiling[1]):
            yield Finding("claims.exemption-ceiling", "error", page.path, 0,
                          f"this page adds a claim exemption past the ceiling of {ceiling} ({markers} markers, {nones} none pages).",
                          "verify the claim instead, or raise CLAIM_EXEMPTIONS with a reason in LOOSENED_BECAUSE.", page.path)


def _has_target(repository: Repository, found: PageReferences) -> bool:
    from .impact import targets_of

    return bool(found.covers) or bool(targets_of(repository, found))


def check(corpus) -> Iterable[Finding]:
    repository = repository_for(corpus)
    in_scope = {page.path for page in corpus.in_scope()}
    widening = _Widening(repository)
    vanished = _vanished_settings(repository)
    for page in corpus.pages:
        versions = [(page.path, repository.references(page))]
        chinese = repository.zh_references(page)
        if chinese is not None and page.zh_path:
            versions.append((page.zh_path, chinese))
        if page.path in in_scope:
            for path, found in versions:
                yield from _page_findings(repository, path, found, widening)
                yield from _example_commands(path, page.zh_tree if path == page.zh_path else page.tree)
                yield from _backup_scope_findings(repository, path, page.zh_tree if path == page.zh_path else page.tree, found)
            if not _has_target(repository, versions[0][1]):
                yield Finding(
                    "claims.no-covers", "error", page.path, 1,
                    "No reference ties this page to code, so no change will ever point at it.",
                    'Add <meta name="covers" content="openprogram/x/ scripts/y.py"> after the description, or content="none" if the page describes no code.',
                    page.key)
        else:
            for path, found in versions:
                for reference in found.references:
                    if widening.applies(reference):
                        for finding in _check_reference(repository, path, reference, widening):
                            yield widening.judged(finding, reference)
                yield from _covers_findings(repository, path, found, only_gone=widening)
        for path, found in versions:
            yield from _removed_cli_tokens(repository, path, found)
            for key in vanished:
                for line in dict.fromkeys(_setting_lines(found, key)):
                    yield Finding("claims.setting", "error", path, line,
                                  f"{key} was removed from SETTINGS, and this page still names it.",
                                  "Update the page for the new setting.", key)
    yield from _exemption_findings(corpus, repository)
