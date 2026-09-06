"""Conservative command labels for process inspection, never execution."""
from __future__ import annotations

import re
import shlex


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
    while words and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=.*', words[0]):
        words.pop(0)
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
        if arg in {'&', '|', ';', '&&', '||', '-', '>'}:
            return result
        return {"name": basename(arg), "kind": "script"}
    return result
