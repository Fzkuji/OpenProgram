"""What a documentation page refers to, and what the repository holds.

Pages are read once, by their code spans, code blocks, GitHub links and
``<meta name="covers">``. The claim check and the impact report both work from
the result: one asks whether each reference is still true, the other which
pages a changed file touches. A ``code`` or ``pre`` element with
``data-claim="example"`` is an illustration and yields nothing.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from typing import Iterator

from ..html_source import Node

CLI_PARSER = "openprogram/interfaces/cli/parser.py"
CLI_COMMANDS = "openprogram/interfaces/cli/commands"
CLI_AREA = "openprogram/interfaces/cli/"
SERVER_AREA = "openprogram/interfaces/server/"
SETTINGS_FILE = "openprogram/state/settings.py"
CLI_PROVIDER_VERBS = "openprogram/interfaces/cli/commands/providers.py"
DEEP_DIRECTORY = 3
DESIGN_RECORDS = "docs/reference/design/"
NO_CODE = "none"  # <meta name="covers" content="none">: an index or study that describes no code

_NOT_MODULE_ENDINGS = {"ai", "app", "cmd", "com", "db", "dev", "dmg", "exe", "io", "json", "lock", "log",
                       "net", "org", "plist", "ps1", "service", "sh", "toml", "yaml", "yml"}
_FILE_SUFFIXES = {"py", "ts", "tsx", "js", "mjs", "cjs", "jsx", "sh", "json", "toml", "css",
                  "md", "html", "yaml", "yml"}
_STOP_WORDS = {
    "None", "True", "False", "self", "from", "import", "return", "class", "const", "function",
    "this", "that", "with", "else", "elif", "async", "await", "export", "default", "type",
    "true", "false", "null", "name", "path", "value", "string", "list", "dict", "args", "data",
    "text", "file", "line", "code", "test", "tests", "docs", "openprogram", "html", "http",
    "https",
}
_PLACEHOLDER_MARK = re.compile(r"[<>{}*$…]|\.\.\.|X\.Y\.Z")
_ALTERNATION = re.compile(r"^[\w.-]+(?:\|[\w.-]+)+$")
_MOST_EXPANSIONS = 32
_METAVARIABLE = re.compile(r"^[A-Z][A-Z0-9_-]*$")
_CODE_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{5,}")
_TOKEN = re.compile(r"[^\s\"'`()=,;]+")
_PATH_CHARACTERS = r"[\w./@+{}<>*$…-]"
_MODULE = re.compile(r"(?<![\w./-])(openprogram(?:\.[A-Za-z_]\w*)+)(?![\w/-])")
_SCRIPT_MODULE = re.compile(r"\bpython[\d.]*\s+-m\s+(scripts(?:\.[A-Za-z_]\w*)+)")
_ROUTE = re.compile(r"(?<![\w/.-])(/api/[\w{}:<>./*…,-]*|/healthz)(?![\w-])")
_PLACEHOLDER_ROUTE = re.compile(r"\.\.\.|…|,")
_ENVIRONMENT = re.compile(r"(?<![A-Z0-9_])(OPENPROGRAM_[A-Z0-9_]*[A-Z0-9])(?![A-Z0-9_*])")
_GITHUB = re.compile(r"github\.com/[^/\s]+/OpenProgram/(?:blob|tree)/[^/\s]+/([^\s#?\"']+)", re.I)
_SERVER_ROUTE = re.compile(r"[\"'`](/api(?:/[^\"'`\s?]*)?|/healthz)[\"'`?]")
_SETTING_KEY = re.compile(r"SettingSpec\(\s*key=\"([^\"]+)\"")
_COMMAND_SEPARATOR = re.compile(r"\s(?:&&|\|\||;|\|)\s")
_PROMPT = re.compile(r"^\s*(?:[$%>]\s+)?")
_ASSIGNMENT = re.compile(r"^(?:[A-Za-z_]\w*=\S*\s+)+")
_PYTHON_MODULE_PREFIX = re.compile(r"^(?:\S*python[\d.]*\s+-m\s+)(?=openprogram(?:\s|$))")
_RUNNER_PREFIX = re.compile(r"^(?:sudo\s+(?:-\S+\s+)*)?(?:uv\s+run\s+(?:--?\S+\s+)*)?")
_BINARY_PATH = re.compile(r"^\S*/openprogram(?=\s|$)")
_SETTING_ANYWHERE = re.compile(r'\bkey="([^"]+)"')
_STATE_PATH = re.compile(r"(?:~|\$HOME)/\.openprogram(?:-[\w-]+)?/([\w.+-]+)|<state>/([\w.+-]+)")
_SOURCE_NAMES_PAGE = re.compile(r"\bdocs/[\w./-]+\.html\b")
_NOT_SOURCES_OF_PAGES = ("tests/", "scripts/docs_site/", "docs/")
_STRING_LITERAL = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`''')
_CALL_KEYWORD = re.compile(r"\b[A-Za-z_]\w*=(?=[^=\s])")
_COMMENT = {".py": re.compile(r"#.*$"), ".ts": re.compile(r"//.*$"), ".tsx": re.compile(r"//.*$"),
            ".js": re.compile(r"//.*$"), ".mjs": re.compile(r"//.*$")}
# A bare file name or partial path with one of these suffixes must exist.
CHECKED_SUFFIXES = {"py", "ts", "tsx", "mjs", "cjs", "jsx", "sh"}


@dataclass(frozen=True)
class Reference:
    kind: str     # command, path, partial, bare, module, script-module, route, env, setting
    value: str
    line: int


@dataclass(frozen=True)
class CommandProblem:
    kind: str     # command, flag, setting
    token: str
    expected: str = ""


@dataclass
class PageReferences:
    references: list[Reference] = field(default_factory=list)
    covers: list[tuple[str, int]] = field(default_factory=list)
    code_text: str = ""
    fragments: list[tuple[str, int]] = field(default_factory=list)

    @property
    def code_words(self) -> set[str]:
        return code_words(self.code_text)


def code_words(text: str) -> set[str]:
    return {word for word in _CODE_WORD.findall(text) if word not in _STOP_WORDS}


def _text_of(node: Node | str) -> str:
    if isinstance(node, str):
        return node
    return "".join(_text_of(child) for child in node.children)


def _fragments(node: Node) -> Iterator[tuple[str, int]]:
    """Each code span, code block and figure command, with its line."""
    for child in node.elements():
        if child.attrs.get("data-claim") in {"example", "invalid"} or child.tag in {"script", "style"}:
            continue
        if child.tag in {"code", "kbd", "samp"}:
            yield _text_of(child), child.line
        elif child.tag == "pre":
            if not any(part.attrs.get("data-claim") in {"example", "invalid"} for part in child.elements()):
                yield _text_of(child), child.line
        elif child.tag == "text" and "t-cmd" in child.classes:
            yield _text_of(child), child.line
        else:
            if child.tag == "a" and "github.com" in child.attrs.get("href", "").lower():
                yield child.attrs["href"], child.line
            yield from _fragments(child)


def _logical_lines(text: str, start: int) -> Iterator[tuple[str, int]]:
    pending = ""
    pending_line = start
    for offset, raw in enumerate(text.split("\n")):
        if not pending:
            pending_line = start + offset
        if raw.rstrip().endswith("\\"):
            pending += raw.rstrip()[:-1] + " "
            continue
        yield pending + raw, pending_line
        pending = ""
    if pending:
        yield pending, pending_line


def _commands_in(text: str, line: int) -> Iterator[Reference]:
    for logical, number in _logical_lines(text, line):
        for part in _COMMAND_SEPARATOR.split(logical):
            command = _ASSIGNMENT.sub("", _PROMPT.sub("", part, count=1).strip())
            command = _PYTHON_MODULE_PREFIX.sub("", command)
            command = _BINARY_PATH.sub("openprogram", _RUNNER_PREFIX.sub("", command, count=1), count=1)
            if (command == "openprogram" or command.startswith("openprogram ")) \
                    and not command.startswith("openprogram @"):
                for one in _expanded(command):
                    yield Reference("command", one, number)


def _expanded(command: str) -> list[str]:
    """``dev status|deploy`` is two commands; each is verified on its own."""
    out = [command]
    for index, token in enumerate(command.split(" ")):
        if _ALTERNATION.match(token) and len(out) * (token.count("|") + 1) <= _MOST_EXPANSIONS:
            out = [" ".join([*one.split(" ")[:index], choice, *one.split(" ")[index + 1:]])
                   for one in out for choice in token.split("|")]
    return out


def _clean_path(token: str) -> str:
    token = token.rstrip(".,:;!?")
    token = re.sub(r":\d+(?:[-:]\d+)*$", "", token)
    token = token.split("#", 1)[0].split("?", 1)[0]
    return token.rstrip(".,:;")


def _paths_in(text: str, line: int, tops: frozenset[str]) -> Iterator[Reference]:
    pattern = re.compile(rf"(?<![\w./~:@-]){_PATH_CHARACTERS}+")
    for match in pattern.finditer(text):
        raw = _clean_path(match.group(0)).removeprefix("./")
        if not raw or raw.startswith("/") or _PLACEHOLDER_MARK.search(raw):
            continue
        number = line + text.count("\n", 0, match.start())
        if "/" not in raw:
            suffix = raw.rsplit(".", 1)[-1] if "." in raw else ""
            if suffix in _FILE_SUFFIXES and not raw.startswith("."):
                yield Reference("bare", raw, number)
            continue
        first = raw.split("/", 1)[0]
        if first in tops:
            yield Reference("path", raw, number)
        elif re.fullmatch(r"[\w.-]+(?:/[\w.-]+)+/?", raw):
            yield Reference("partial", raw, number)


def _references_in(text: str, line: int, tops: frozenset[str]) -> Iterator[Reference]:
    yield from _commands_in(text, line)
    for match in _GITHUB.finditer(text):
        yield Reference("path", match.group(1).rstrip("/"), line)
    stripped = _GITHUB.sub(" ", text)
    yield from _paths_in(stripped, line, tops)
    for match in _MODULE.finditer(stripped):
        value = match.group(1)
        if value.rsplit(".", 1)[1] in _NOT_MODULE_ENDINGS:
            continue
        yield Reference("module", value, line + stripped.count("\n", 0, match.start()))
    for match in _SCRIPT_MODULE.finditer(stripped):
        yield Reference("script-module", match.group(1), line + stripped.count("\n", 0, match.start()))
    for match in _ROUTE.finditer(stripped):
        if _PLACEHOLDER_ROUTE.search(match.group(1)):
            continue
        yield Reference("route", match.group(1), line + stripped.count("\n", 0, match.start()))
    for match in _ENVIRONMENT.finditer(stripped):
        yield Reference("env", match.group(1), line + stripped.count("\n", 0, match.start()))
    for match in _STATE_PATH.finditer(stripped):
        yield Reference("state", (match.group(1) or match.group(2)).rstrip("."), line + stripped.count("\n", 0, match.start()))


def covers_of(tree: Node) -> list[tuple[str, int]]:
    """The explicit ``<meta name="covers">`` targets of a page."""
    out: list[tuple[str, int]] = []
    pending = list(tree.elements())
    while pending:
        node = pending.pop(0)
        if node.tag == "meta" and node.attrs.get("name", "").lower() == "covers":
            out.extend((value, node.line) for value in node.attrs.get("content", "").split())
        pending[0:0] = node.elements()
    return out


def read_page(tree: Node, tops: frozenset[str], settings: frozenset[str] | set[str] = frozenset()) -> PageReferences:
    result = PageReferences(covers=covers_of(tree))
    code: list[str] = []
    seen: set[tuple[str, str, int]] = set()
    for text, line in _fragments(tree):
        code.append(text)
        result.fragments.append((text, line))
        extra = [Reference("setting", text.strip(), line)] if text.strip() in settings else []
        for reference in [*_references_in(text, line, tops), *extra]:
            key = (reference.kind, reference.value, reference.line)
            if key not in seen:
                seen.add(key)
                result.references.append(reference)
    result.code_text = "\n".join(code)
    return result


# ── the repository side ─────────────────────────────────────────────────────


def normalize_route(route: str) -> str:
    parts = route.split("?", 1)[0].rstrip("/").split("/")
    parts = ["{}" if re.fullmatch(r"\{[^}]*\}|<[^>]*>|:\w+", part) else part for part in parts]
    return "/".join(parts) or "/"


def _is_placeholder(token: str) -> bool:
    # ``|`` alone is alternation, expanded before this; inside brackets it is metasyntax.
    return bool(_PLACEHOLDER_MARK.search(token) or _METAVARIABLE.match(token)
                or ("|" in token and "[" in token))


def _flag_of(token: str) -> str:
    return token.split("=", 1)[0]


def _takes_value(action: argparse.Action) -> bool:
    return action.nargs != 0


def _is_remainder(action: argparse.Action) -> bool:
    return action.nargs in {argparse.REMAINDER, argparse.PARSER}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def verify_command(root: argparse.ArgumentParser, command: str,
                   settings: set[str]) -> list[CommandProblem]:
    """Walk one documented command line through the real parser."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=False)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)[1:]
    except ValueError:
        return []
    parser = root
    positionals = [a for a in parser._actions if not a.option_strings
                   and not isinstance(a, argparse._SubParsersAction)]
    path: list[str] = []
    problems: list[CommandProblem] = []
    index = 0
    while index < len(tokens):
        token = tokens[index].strip("[]") if tokens[index].startswith("[") else tokens[index]
        index += 1
        if token == "--":
            break
        if token.startswith("-") and len(token) > 1 and not re.fullmatch(r"-\d+(\.\d+)?", token):
            if _is_placeholder(tokens[index - 1]):
                continue
            action = parser._option_string_actions.get(_flag_of(token))
            if action is None:
                known = sorted(s for s in parser._option_string_actions if s.startswith("--"))
                problems.append(CommandProblem("flag", _flag_of(token), ", ".join(known[:12])))
                continue
            if action.nargs in (0, None) and "=" in token or action.nargs == 0:
                continue
            if index < len(tokens) and not tokens[index].startswith("-"):
                value = tokens[index]
                if action.choices is not None and not _is_placeholder(value) and value not in action.choices:
                    problems.append(CommandProblem("flag", f"{_flag_of(token)} {value}",
                                                   ", ".join(map(str, action.choices))))
                index += 1
            continue
        if _is_placeholder(tokens[index - 1]):
            if positionals:
                positionals.pop(0)
                continue
            break
        action_sub = _subparsers(parser)
        if action_sub is not None:
            if token not in action_sub.choices:
                problems.append(CommandProblem("command", token, ", ".join(sorted(action_sub.choices))))
                break
            path.append(token)
            parser = action_sub.choices[token]
            positionals = [a for a in parser._actions if not a.option_strings
                           and not isinstance(a, argparse._SubParsersAction)]
            continue
        if not positionals:
            continue
        action = positionals[0]
        if _is_remainder(action):
            break
        if action.choices is not None and token not in action.choices:
            problems.append(CommandProblem("command", token, ", ".join(map(str, action.choices))))
            break
        if path[:1] == ["config"] and len(path) == 2 and path[1] in {"get", "set"} \
                and action.dest == "key" and token not in settings:
            problems.append(CommandProblem("setting", token))
        if action.nargs not in {argparse.ZERO_OR_MORE, argparse.ONE_OR_MORE}:
            positionals.pop(0)
    return problems


def _command_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=False)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        return list(lexer)[1:]
    except ValueError:
        return []


def command_path(root: argparse.ArgumentParser, command: str) -> tuple[str, ...]:
    """The subcommands a documented line walks, as far as the parser knows them."""
    tokens = _command_tokens(command)
    parser = root
    path: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.startswith("-"):
            action = parser._option_string_actions.get(_flag_of(token))
            if action is not None and action.nargs != 0 and "=" not in token:
                index += 1
            continue
        children = _subparsers(parser)
        if children is None or token not in children.choices:
            break
        path.append(token)
        parser = children.choices[token]
    return tuple(path)


def _action_shape(action: argparse.Action) -> tuple:
    return (action.dest, str(action.nargs), repr(action.choices), action.required, repr(action.default),
            getattr(action.type, "__name__", repr(action.type)), action.help)


@dataclass(frozen=True)
class CommandShape:
    children: frozenset[str]
    options: dict[str, tuple]
    positionals: tuple[tuple, ...]


def command_tree(root: argparse.ArgumentParser) -> dict[tuple[str, ...], CommandShape]:
    tree: dict[tuple[str, ...], CommandShape] = {}
    pending: list[tuple[tuple[str, ...], argparse.ArgumentParser]] = [((), root)]
    while pending:
        path, parser = pending.pop()
        if path in tree:
            continue
        children = _subparsers(parser)
        names = dict(children.choices) if children is not None else {}
        options = {flag: _action_shape(action) for action in parser._actions for flag in action.option_strings}
        positionals = tuple(_action_shape(action) for action in parser._actions
                            if not action.option_strings and not isinstance(action, argparse._SubParsersAction))
        tree[path] = CommandShape(frozenset(names), options, positionals)
        pending.extend(((*path, name), child) for name, child in names.items())
    return tree


@dataclass(frozen=True)
class ParserChange:
    """What this change did to the command tree."""
    paths: frozenset[tuple[str, ...]]                # commands whose own shape changed
    root_options: frozenset[str]                     # root flags changed: every command carries them
    removed: frozenset[tuple[tuple[str, ...], str]]  # (command path, verb or option string) gone from it
    vanished: frozenset[str]                         # removed tokens no command offers any more
    top_level: frozenset[str] = frozenset()          # the top-level verbs there are now


def parser_change(before: argparse.ArgumentParser, after: argparse.ArgumentParser) -> ParserChange:
    old, new = command_tree(before), command_tree(after)
    paths: set[tuple[str, ...]] = set()
    root_options: set[str] = set()
    removed: set[tuple[tuple[str, ...], str]] = set()
    for path in old.keys() | new.keys():
        was, now = old.get(path), new.get(path)
        if was is None or now is None:
            if path[:-1] in old and path[:-1] in new:
                paths.add(path)
            continue
        changed_options = {flag for flag in was.options.keys() | now.options.keys()
                           if was.options.get(flag) != now.options.get(flag)}
        removed.update((path, flag) for flag in was.options.keys() - now.options.keys())
        removed.update((path, name) for name in was.children - now.children)
        if not path:
            root_options |= changed_options
            if was.positionals != now.positionals:
                paths.add(path)
        elif changed_options or was.positionals != now.positionals or was.children != now.children:
            paths.add(path)
    offered = {token for shape in new.values() for token in (*shape.children, *shape.options)}
    return ParserChange(frozenset(paths), frozenset(root_options), frozenset(removed),
                        frozenset(token for _path, token in removed) - offered,
                        frozenset(new[()].children) if () in new else frozenset())


def _literal_lines(source: str) -> set[int]:
    """Lines of a Python file that sit wholly inside a string: docstrings and long help text."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.end_lineno > node.lineno:
            lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def identifiers_in(line: str, suffix: str) -> set[str]:
    """Code words on one changed line: no string contents, comments or call keywords."""
    kept = set(_ENVIRONMENT.findall(line))
    for route in _SERVER_ROUTE.findall(line):
        kept |= code_words(route)
    code = _STRING_LITERAL.sub(" ", _CALL_KEYWORD.sub(" ", line))
    if suffix in _COMMENT:
        code = _COMMENT[suffix].sub(" ", code)
    return kept | code_words(code)


def _git_output(root: Path, *arguments: str, stdin: str | None = None) -> str:
    done = subprocess.run(["git", *arguments], cwd=root, input=stdin, capture_output=True,
                          text=True, errors="replace", check=False)
    if done.returncode not in {0, 1}:
        raise RuntimeError(f"git {' '.join(arguments[:3])} failed: {done.stderr.strip()}")
    return done.stdout


class Repository:
    """Facts about one checkout, computed once per check."""

    def __init__(self, corpus) -> None:
        self.corpus = corpus
        self.scope = corpus.scope
        self.root = Path(corpus.scope.root)
        if self.scope.reads == "head":
            listed = {path for path in _git_output(
                self.root, "ls-tree", "-r", "-z", "--name-only", "HEAD").split("\0") if path}
        elif self.scope.reads == "index":
            listed = {path for path in _git_output(self.root, "ls-files", "-z", "--cached").split("\0") if path}
        else:
            listed = {path for path in _git_output(
                self.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") if path}
            listed -= set(_git_output(self.root, "ls-files", "-z", "--deleted").split("\0"))
        self.files = frozenset(listed)
        directories: set[str] = set()
        for path in self.files:
            parent = PurePosixPath(path).parent
            while str(parent) != ".":
                directories.add(str(parent))
                parent = parent.parent
        self.directories = frozenset(directories)
        self.tops = frozenset(path.split("/", 1)[0] for path in self.files if "/" in path)
        self._pages: dict[str, tuple[PageReferences, PageReferences | None]] = {}
        self._suffixes: dict[str, str | None] | None = None
        self._basenames: dict[str, str | None] | None = None
        self._routes: dict[str, set[str]] | None = None
        self._environment: set[str] | None = None
        self._parser: argparse.ArgumentParser | None = None
        self._settings: set[str] | None = None
        self._ignored: dict[str, bool] = {}
        self._diff: dict[str, set[str]] | None = None
        self._identifiers: dict[str, set[str]] = {}
        self._previous_parser: argparse.ArgumentParser | None = None
        self._parser_change: ParserChange | None = None
        self._parser_compared = False
        self._state: tuple[frozenset[str], dict[str, set[str]] | None] | None = None
        self._imports: dict[str, list[str]] = {}
        self._memo_module: dict[str, tuple[str, str] | None] = {}
        self._removed: dict[str, str] = {}
        self._hunks: dict[str, list[tuple[int, int]]] = {}
        self._renames: dict[str, str] | None = None
        self._removed_routes: set[str] | None = None
        self._directory_names = frozenset(PurePosixPath(path).name for path in self.directories)

    # -- pages

    def references(self, page) -> PageReferences:
        return self._read(page)[0]

    def zh_references(self, page) -> PageReferences | None:
        return self._read(page)[1]

    def _read(self, page) -> tuple[PageReferences, PageReferences | None]:
        if page.path not in self._pages:
            known = self.known_settings
            english = read_page(page.tree, self.tops, known)
            chinese = read_page(page.zh_tree, self.tops, known) if page.zh_tree is not None else None
            self._pages[page.path] = (english, chinese)
        return self._pages[page.path]

    # -- paths

    def path_exists(self, value: str) -> bool:
        clean = value.rstrip("/")
        return clean in self.files or clean in self.directories or self.ignored(clean)

    def ignored(self, value: str) -> bool:
        if value not in self._ignored:
            # A directory pattern matches only something beneath the directory.
            done = subprocess.run(["git", "check-ignore", "--no-index", "--", value, f"{value}/x"],
                                  cwd=self.root, capture_output=True, check=False)
            if done.returncode not in {0, 1}:
                raise RuntimeError(f"git check-ignore failed for {value}")
            self._ignored[value] = done.returncode == 0
        return self._ignored[value]

    def resolve_partial(self, value: str) -> str | None:
        """A path without its leading directories, if only one place fits."""
        return self._suffix_table.get(value.rstrip("/"))

    @property
    def _suffix_table(self) -> dict[str, str | None]:
        if self._suffixes is None:
            table: dict[str, str | None] = {}
            for full in self.files:
                self._add_suffixes(table, full, "")
            for full in self.directories:
                self._add_suffixes(table, full, "/")
            self._suffixes = table
        return self._suffixes

    def has_partial(self, value: str) -> bool:
        """Whether any file or directory ends with this path, unique or not."""
        return value.rstrip("/") in self._suffix_table

    def has_bare(self, value: str) -> bool:
        self.resolve_bare(value)
        return value in self._basenames

    def checkable(self, reference: Reference, page_path: str) -> bool:
        """A bare name or partial path specific enough that resolving nowhere is an error.

        Engineering records cite peers' source files by these forms, so only
        product pages are held to it: 7 product hits against 229 in the records.
        """
        value = reference.value
        if page_path.startswith(DESIGN_RECORDS):
            return False
        if value.rsplit(".", 1)[-1] not in CHECKED_SUFFIXES or "." not in PurePosixPath(value).name:
            return False
        return reference.kind == "bare" or value.split("/", 1)[0] in self._directory_names

    @staticmethod
    def _add_suffixes(table: dict[str, str | None], full: str, ending: str) -> None:
        parts = full.split("/")
        for start in range(1, len(parts) - 1):
            key = "/".join(parts[start:])
            table[key] = None if key in table else full + ending

    def resolve_bare(self, value: str) -> str | None:
        if self._basenames is None:
            table: dict[str, str | None] = {}
            for path in self.files:
                name = PurePosixPath(path).name
                table[name] = None if name in table else path
            self._basenames = table
        return self._basenames.get(value)

    # -- modules

    def resolve_module(self, dotted: str) -> tuple[str, str] | None:
        """The file a dotted name points into, and any attribute left over."""
        if dotted in self._memo_module:
            return self._memo_module[dotted]
        parts = dotted.split(".")
        found: tuple[str, str] | None = None
        for size in range(len(parts), 0, -1):
            base = "/".join(parts[:size])
            attribute = parts[size] if size < len(parts) else ""
            for candidate in (f"{base}.py", f"{base}/__init__.py"):
                if candidate in self.files:
                    found = (candidate, attribute)
                    break
            if found is None and base in self.directories:
                found = (f"{base}/", attribute)
            if found is not None:
                break
        self._memo_module[dotted] = found
        return found

    def module_exists(self, dotted: str) -> bool:
        target = self.resolve_module(dotted)
        if target is None:
            return False
        path, attribute = target
        if not attribute:
            return True
        if path.endswith("/"):
            return False
        return re.search(rf"\b{re.escape(attribute)}\b", self._read_file(path)) is not None

    def _read_file(self, path: str) -> str:
        try:
            return self.corpus.read(path)
        except (OSError, subprocess.CalledProcessError):
            return ""

    # -- routes, environment, settings, parser

    @property
    def routes(self) -> dict[str, set[str]]:
        if self._routes is None:
            table: dict[str, set[str]] = defaultdict(set)
            for path in self.files:
                if path.startswith(SERVER_AREA) and path.endswith(".py"):
                    for route in _SERVER_ROUTE.findall(self._read_file(path)):
                        table[normalize_route(route)].add(path)
            self._routes = dict(table)
        return self._routes

    def route_files(self, route: str) -> set[str]:
        """Files declaring a route.

        A literal match wins. A named id fits a parameter unless this change
        removed that literal route. Only a reference written as a prefix
        (ending ``*``, ``…`` or ``/``) fits what is beneath it.
        """
        is_prefix = route.endswith(("*", "…", "/"))
        stem = route.rstrip("*…").rstrip("/")
        normal = normalize_route(stem)
        if not is_prefix and normal in self.routes:
            return set(self.routes[normal])
        wanted = normal.split("/")
        exact: set[str] = set()
        beneath: set[str] = set()
        for known, files in self.routes.items():
            parts = known.split("/")
            if len(parts) >= len(wanted) and all(
                    have == want or have == "{}" for have, want in zip(parts, wanted)):
                (exact if len(parts) == len(wanted) else beneath).update(files)
        if is_prefix:
            return exact | beneath
        return set() if normal in self.removed_routes else exact

    @property
    def removed_routes(self) -> set[str]:
        """Normalized routes declared on lines this change removed and declared nowhere now."""
        if self._removed_routes is None:
            self.diff_words()
            found: set[str] = set()
            for path, text in self._removed.items():
                if path.startswith(SERVER_AREA):
                    found.update(normalize_route(route) for route in _SERVER_ROUTE.findall(text))
            self._removed_routes = found - set(self.routes)
        return self._removed_routes

    @property
    def environment(self) -> set[str]:
        if self._environment is None:
            flag = {"disk": ["--untracked"], "index": ["--cached"], "head": []}[self.scope.reads]
            revision = ["HEAD"] if self.scope.reads == "head" else []
            output = _git_output(self.root, "grep", "-hoIE", *flag, "OPENPROGRAM_[A-Z0-9_]+", *revision,
                                 "--", ":!docs", ":!tests")
            self._environment = set(output.split())
        return self._environment

    @property
    def parser(self) -> argparse.ArgumentParser:
        if self._parser is None:
            from .corpus import snapshot_module

            snapshot = snapshot_module(self.scope, CLI_PARSER, "cli_parser")
            if snapshot is not None:
                self._parser = snapshot.build_parser()
            else:
                from openprogram.interfaces.cli.parser import build_parser

                self._parser = build_parser()
        return self._parser

    @property
    def previous_parser(self) -> argparse.ArgumentParser | None:
        self.parser_change()
        return self._previous_parser

    def parser_change(self) -> ParserChange | None:
        """What this change did to the command tree; None when parser.py did not change or cannot be read."""
        if not self._parser_compared:
            self._parser_compared = True
            if CLI_PARSER in self.code_changes():
                from .corpus import module_at

                try:
                    old = module_at(self.root, self.scope.base or "HEAD", CLI_PARSER, "cli_parser_before")
                    if old is not None:
                        self._previous_parser = old.build_parser()
                        self._parser_change = parser_change(self._previous_parser, self.parser)
                except (Exception, SystemExit):  # the old source may need a module this tree no longer has
                    self._previous_parser = self._parser_change = None
        return self._parser_change

    def command_imports(self, path: str) -> list[str]:
        """The openprogram files one command module imports directly, lazily or not."""
        if path not in self._imports:
            found: dict[str, None] = {}
            try:
                tree = ast.parse(self._read_file(path))
            except (SyntaxError, ValueError):
                tree = ast.Module(body=[], type_ignores=[])
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
                for name in names:
                    target = self.resolve_module(name) if name.startswith("openprogram.") else None
                    if target and not target[0].endswith("/") and not target[0].startswith(CLI_AREA):
                        found[target[0]] = None
            self._imports[path] = list(found)
        return self._imports[path]

    def declared_links(self, changed: list[str]) -> dict[str, set[str]]:
        """Pages a changed source file names outright: its own 'keep this in sync' note."""
        links: dict[str, set[str]] = {}
        for path in changed:
            if path.startswith(_NOT_SOURCES_OF_PAGES) or path not in self.files:
                continue
            named = set(_SOURCE_NAMES_PAGE.findall(self._read_file(path)))
            if named:
                links[path] = named
        return links

    def state_names(self, value: str) -> bool:
        """Whether a top-level name under the state directory is one the code knows."""
        from .state_names import BACKUP_FILE, classified, scan

        if self._state is None:
            self._state = (classified(self._read_file(BACKUP_FILE)).known, None)
        if value in self._state[0]:
            return True
        if self._state[1] is None:
            self._state = (self._state[0], scan(self.root))
        return value in self._state[1]

    def previous_state_names(self) -> frozenset[str]:
        from .state_names import BACKUP_FILE, classified

        return classified(_git_output(self.root, "show", f"{self.scope.base or 'HEAD'}:{BACKUP_FILE}") or "").known

    def parser_mixes_unsaved_work(self) -> bool:
        """The parser pulls provider verbs from disk; another session's edit there can't be judged."""
        if self.scope.reads == "disk":
            return False
        return bool(_git_output(self.root, "diff", "--name-only", *(["HEAD"] if self.scope.reads == "head" else []),
                                "--", CLI_PROVIDER_VERBS).strip())

    @property
    def settings(self) -> set[str]:
        if self._settings is None:
            self._settings = set(_SETTING_KEY.findall(self._read_file(SETTINGS_FILE)))
        return self._settings

    @property
    def known_settings(self) -> set[str]:
        """Keys a page can name: today's, and any this change removed."""
        if SETTINGS_FILE in self.code_changes():
            return self.settings | self.previous_settings()
        return self.settings

    def previous_settings(self) -> set[str]:
        revision = self.scope.base or "HEAD"
        return set(_SETTING_KEY.findall(_git_output(self.root, "show", f"{revision}:{SETTINGS_FILE}")))

    # -- the change

    def code_changes(self) -> list[str]:
        return [path for path in self.scope.changed if not path.startswith("docs/")]

    def diff_words(self) -> dict[str, set[str]]:
        """Code words on the changed lines of each changed non-docs file."""
        if self._diff is None:
            self._diff = self._compute_diff()
        return self._diff

    def _compute_diff(self) -> dict[str, set[str]]:
        changed = self.code_changes()
        words: dict[str, set[str]] = {}
        if not changed:
            return words
        command = ["diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames",
                   "--src-prefix=a/", "--dst-prefix=b/"]
        if self.scope.reads == "index":
            command.append("--cached")
        command.append(self.scope.base or "HEAD")
        if self.scope.reads == "head":
            command.append("HEAD")
        command.append("--")
        command.extend(changed if len(changed) <= 200 else [":!docs"])
        current: str | None = None
        old_line = new_line = 0
        python = [path for path in changed if path.endswith(".py")]
        literal_old = {path: _literal_lines(text or "") for path, text in self.corpus.base_texts(python).items()}
        literal_new = {path: _literal_lines(self._read_file(path)) if path in self.files else set() for path in python}
        for line in _git_output(self.root, "-c", "core.quotepath=off", *command).splitlines():
            if line.startswith("diff --git "):
                current = None
            elif line.startswith("+++ b/") or line.startswith("--- a/"):
                current = line[6:].strip()
                words.setdefault(current, set())
                self._identifiers.setdefault(current, set())
            elif line.startswith("@@ ") and current is not None:
                start, _, count = line.split("+", 1)[1].split(" ", 1)[0].partition(",")
                self._hunks.setdefault(current, []).append((int(start), int(count or 1)))
                old_line, new_line = int(line[4:].split(" ", 1)[0].split(",", 1)[0]), int(start)
            elif current is not None and line[:1] in {"+", "-"}:
                words[current] |= code_words(line[1:])
                inside = (literal_new if line[0] == "+" else literal_old).get(current, ())
                if (new_line if line[0] == "+" else old_line) not in inside:
                    self._identifiers[current] |= identifiers_in(line[1:], PurePosixPath(current).suffix)
                new_line += line[0] == "+"
                old_line += line[0] == "-"
                if line[0] == "-":
                    self._removed[current] = self._removed.get(current, "") + line[1:] + "\n"
        for path in changed:
            if path not in words and path in self.files:
                try:
                    words[path] = code_words(self.corpus.read(path))
                    self._identifiers[path] = set(words[path])
                except (OSError, UnicodeDecodeError, subprocess.CalledProcessError):
                    continue
        if SETTINGS_FILE in words:
            words[SETTINGS_FILE] |= self._changed_setting_keys()
            self._identifiers[SETTINGS_FILE] = self._identifiers.get(SETTINGS_FILE, set()) | self._changed_setting_keys()
        return {path: found for path, found in words.items() if path in set(changed)}

    def diff_identifiers(self) -> dict[str, set[str]]:
        """Like diff_words, without string contents, comments or call keywords."""
        return {path: self._identifiers.get(path, set()) for path in self.diff_words()}

    def _changed_setting_keys(self) -> set[str]:
        """Keys of the SettingSpec blocks this change touched: -U0 drops the key line of a default= edit."""
        keys = set(_SETTING_ANYWHERE.findall(self._removed.get(SETTINGS_FILE, "")))
        text = self._read_file(SETTINGS_FILE)
        specs = list(_SETTING_KEY.finditer(text))
        for index, match in enumerate(specs):
            first = text.count("\n", 0, match.start()) + 1
            last = text.count("\n", 0, specs[index + 1].start()) if index + 1 < len(specs) else text.count("\n") + 1
            for start, count in self._hunks.get(SETTINGS_FILE, ()):
                if start <= last and start + max(count, 1) - 1 >= first:
                    keys.add(match.group(1))
        return keys

    def removed_text(self) -> str:
        """Every line this change removed."""
        self.diff_words()
        return "".join(self._removed.values())

    def renames(self) -> dict[str, str]:
        """Old path -> new path for what this change moved, by content."""
        if self._renames is None:
            command = ["diff", "-M", "--name-status", "--diff-filter=R", "-z"]
            if self.scope.reads == "index":
                command.append("--cached")
            command.append(self.scope.base or "HEAD")
            if self.scope.reads == "head":
                command.append("HEAD")
            fields = _git_output(self.root, *command).split("\0")
            self._renames = {fields[i + 1]: fields[i + 2] for i in range(0, len(fields) - 2, 3)}
        return self._renames


_current: tuple[object, Repository] | None = None


def repository_for(corpus) -> Repository:
    """One Repository per corpus, so the claim and impact passes share it."""
    global _current
    if _current is None or _current[0] is not corpus:
        _current = (corpus, Repository(corpus))
    return _current[1]
