"""Conservative command labels for process inspection, never execution."""
from __future__ import annotations

import re
import shlex

# Steps that only prepare the shell for the command that matters.
SETUP = frozenset({
    "cd", "pushd", "popd", "mkdir", "export", "source", ".", "set", "unset", "echo", "printf",
    "true", ":", "sleep", "rm", "cp", "mv", "touch", "chmod", "ln", "ulimit", "umask", "trap",
    "alias", "activate", "clear", "test", "[",
})
SEPARATORS = frozenset({"&&", "||", ";", "&", ";;"})
# Wrappers that run the command after them.
WRAPPERS = frozenset({"nohup", "time", "exec", "stdbuf", "caffeinate"})


def _main_command(words: list[str]) -> list[str]:
    """The words of the command a chain exists to run.

    ``cd proj && mkdir -p out && python eval.py | tee log`` is about
    ``python eval.py``. A heredoc's body is not part of the command line.
    """
    for index, word in enumerate(words):
        if word.startswith("<<"):
            words = words[:index + 1]
            break
    segments, current = [], []
    for word in words:
        if word in SEPARATORS or re.fullmatch(r"[;&]+", word):
            segments.append(current)
            current = []
        else:
            current.append(word)
    segments.append(current)
    candidates = []
    for segment in segments:
        if "|" in segment:
            segment = segment[:segment.index("|")]
        while segment and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", segment[0]):
            segment = segment[1:]
        while segment:
            head = re.split(r"[/\\]", segment[0])[-1]
            if head in WRAPPERS or head == "env":
                segment = segment[1:]
                while segment and (segment[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", segment[0])):
                    segment = segment[1:]
            elif head == "timeout" and len(segment) > 2:
                segment = segment[2:]
            elif head in {"uv", "poetry", "pipenv", "pdm", "hatch"} and len(segment) > 2 and segment[1] == "run":
                segment = segment[2:]
            elif head == "conda" and len(segment) > 1 and segment[1] == "activate":
                segment = []
            else:
                break
        if segment:
            candidates.append(segment)
    head = lambda segment: re.split(r"[/\\]", segment[0])[-1]
    main = [segment for segment in candidates if head(segment) not in SETUP]
    if main:
        return main[-1]
    # Only setup steps: a trailing echo is a done marker, not the work.
    quiet = [segment for segment in candidates if head(segment) not in {"echo", "printf", "true", ":", "cd"}]
    return (quiet or candidates or [[]])[0]


def command_display(command: str) -> dict[str, str]:
    """Name recognizable entrypoints without inferring what arbitrary code does."""
    if not isinstance(command, str):
        return {"name": "", "kind": "unknown"}
    try:
        # Keep Windows path separators; ordinary shell quoting uses POSIX rules.
        windows = bool(re.search(r'(?:^|\s)[\"\']?[A-Za-z]:\\', command))
        lexer = shlex.shlex(command, posix=not windows, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
        if windows:
            words = [word[1:-1] if len(word) > 1 and word[0] == word[-1] and word[0] in '\"\'' else word for word in words]
    except ValueError:
        return {"name": "", "kind": "unknown"}
    words = _main_command(words)
    if not words:
        return {"name": "", "kind": "unknown"}

    def basename(value: str) -> str:
        return re.split(r'[/\\]', value)[-1]

    executable = basename(words[0])
    name = executable.lower().removesuffix('.exe')
    result = {"name": executable, "kind": "executable"}
    args = words[1:]
    python = bool(re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', name))
    node = name in {'node', 'nodejs'}
    shell = name in {'bash', 'sh', 'zsh'}
    if name in {'npm', 'pnpm', 'yarn'} and len(args) >= 2 and args[0] == 'run' and re.fullmatch(r'[\w:.-]+', args[1]):
        return {"name": f'{executable} run {args[1]}', "kind": "script"}
    if not (python or node or shell):
        return result
    index = 0
    while index < len(args):
        arg = args[index]
        if ((python or shell) and arg == '-c') or (node and arg in {'-e', '--eval', '-p', '--print'}):
            return {"name": executable, "kind": "snippet"}
        if python and arg == '-m' and index + 1 < len(args):
            module = args[index + 1]
            return {"name": module, "kind": "module"} if re.fullmatch(r'[\w.]+', module) else result
        if arg == '-' or arg.startswith('<<'):
            # A script read from stdin, usually a heredoc.
            return {"name": executable, "kind": "snippet"} if python or node or shell else result
        if arg == '--':
            index += 1
            if index >= len(args):
                return result
            arg = args[index]
        elif arg.startswith('-'):
            if python and (re.fullmatch(r'-[uBIOEsSqiv]+', arg) or arg in {'--unbuffered', '--isolated'}):
                index += 1
                continue
            if node and (arg in {'--no-warnings', '--trace-warnings'} or arg.startswith('--inspect=')):
                index += 1
                continue
            # An unrecognized flag may consume another argument; do not guess.
            return result
        if re.fullmatch(r'[();<>|&]+', arg):
            return result
        return {"name": basename(arg), "kind": "script"}
    return result
