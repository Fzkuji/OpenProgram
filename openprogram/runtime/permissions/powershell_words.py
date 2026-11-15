"""Read a PowerShell command as the POSIX commands a rule is written for.

An owner writes ``bash(rm -rf *)`` meaning "no recursive delete", not "no
delete spelled the bash way". Where commands run in PowerShell, the deny and
ask rules also read the command lowered here: each pipeline element becomes
an argv, and ``Remove-Item``, ``Move-Item``, ``Copy-Item`` and their aliases
become ``rm``, ``mv`` and ``cp`` with their switches spelled as POSIX flags
(``Remove-Item -Recurse -Force .`` reads as ``rm -rf .``). Allow rules never
read it: they keep matching the command as written.

This follows Codex (``codex-rs/shell-command/src/command_safety``): its
tree-sitter lowering accepts only plain words and literal strings and treats
everything else as unreadable, and its alias table for deletion is the one
below. The accepted subset is small enough to read exactly without a parser,
so no grammar is loaded and nothing is downloaded while a rule is checked.

:func:`lower` returns ``None`` for anything it cannot read for certain:
variables, subexpressions, script blocks, arrays, redirections, the call
and dot-source operators, comments, backtick escapes, and Unicode quotes or
dashes that PowerShell reads as their ASCII forms.
"""
from __future__ import annotations

import re

#: PowerShell names, lower case, for the POSIX program a rule names.
ALIASES = {
    "rm": ("remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"),
    "mv": ("move-item", "mi", "mv", "move"),
    "cp": ("copy-item", "cp", "cpi", "copy"),
}
_PROGRAM = {name: program for program, names in ALIASES.items() for name in names}

# PowerShell reads these as ' " and - (``[char]0x2018`` and friends).
_UNICODE_PUNCTUATION = frozenset("‘’‚‛“”„–—―")
_BARE_REFUSED = frozenset("$`(){}@,<>&#")

#: Parameters by program: switch → POSIX flag, and which ones take a value
#: (operands for ``-Path``, ``-LiteralPath``, ``-Destination``).
_SWITCHES = {"recurse": "r", "force": "f"}
_OPERANDS = ("path", "literalpath", "destination")
_VALUED = ("filter", "include", "exclude", "credential", "stream", "passthru")


def _words(script: str) -> list[str | None] | None:
    """Words and separators (``None``), or ``None`` when a word is unreadable."""
    out: list[str | None] = []
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if ch in " \t\r":
            i += 1
            continue
        sep = next((s for s in ("&&", "||") if script.startswith(s, i)), None) or (
            ch if ch in ";|\n" else None)
        if sep:
            out.append(None)
            i += len(sep)
            continue
        word = []
        while i < n and script[i] not in " \t\r;|\n":
            ch = script[i]
            if ch == "'":
                end = i + 1
                text = []
                while True:
                    if end >= n:
                        return None
                    if script[end] == "'":
                        if end + 1 < n and script[end + 1] == "'":
                            text.append("'")
                            end += 2
                            continue
                        break
                    text.append(script[end])
                    end += 1
                word.append("".join(text))
                i = end + 1
            elif ch == '"':
                end = script.find('"', i + 1)
                if end < 0:
                    return None
                text = script[i + 1:end]
                if "$" in text or "`" in text:
                    return None
                word.append(text)
                i = end + 1
            elif ch in _BARE_REFUSED or (ch == "[" and not word):
                return None
            else:
                word.append(ch)
                i += 1
        out.append("".join(word))
    return out


def lower(script: str) -> list[list[str]] | None:
    """The argvs of ``script``'s pipeline elements, or ``None`` when unreadable."""
    if any(ch in _UNICODE_PUNCTUATION for ch in script):
        return None
    words = _words(script)
    if words is None:
        return None
    commands: list[list[str]] = [[]]
    for word in words:
        if word is None:
            if commands[-1]:
                commands.append([])
        else:
            commands[-1].append(word)
    commands = [argv for argv in commands if argv]
    if not commands or any(argv[0] in (".", "&") for argv in commands):
        return None
    return commands


def _name(word: str) -> str:
    # ``Microsoft.PowerShell.Management\\Remove-Item`` is Remove-Item.
    return word.rsplit("\\", 1)[-1].lower()


def _parameter(word: str, known: tuple[str, ...]) -> str | None:
    """The parameter ``-word`` abbreviates, as PowerShell completes a prefix."""
    if not word.startswith("-") or len(word) < 2:
        return None
    given = word[1:].split(":", 1)[0].lower()
    return next((name for name in known if name.startswith(given)), None)


def posix_argv(argv: list[str]) -> list[str]:
    """``argv`` with a delete, move or copy cmdlet spelled as ``rm``, ``mv``, ``cp``."""
    program = _PROGRAM.get(_name(argv[0]))
    if program is None:
        return list(argv)
    flags: list[str] = []
    operands: list[str] = []
    known = (*_SWITCHES, *_OPERANDS, *_VALUED, "confirm", "whatif")
    words = iter(argv[1:])
    for word in words:
        name = _parameter(word, known)
        if name is None:
            operands.append(word)
        elif name in _SWITCHES:
            if _SWITCHES[name] not in flags:
                flags.append(_SWITCHES[name])
        elif name in _OPERANDS:
            value = next(words, None)
            if value is not None:
                operands.append(value)
        elif name in _VALUED and ":" not in word:
            next(words, None)
    flags.sort(key="rf".index)
    return [program, *(["-" + "".join(flags)] if flags else []), *operands]


def posix_readings(script: str) -> list[list[str]] | None:
    """Every pipeline element of ``script`` as a POSIX argv, or ``None`` when unreadable."""
    commands = lower(script)
    if commands is None:
        return None
    return [posix_argv(argv) for argv in commands]


_NAMED = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(name) for name in sorted(_PROGRAM, key=len, reverse=True))
    + r"|invoke-expression|iex|invoke-command|icm|start-process|saps)(?![\w-])",
    re.IGNORECASE,
)


def may_run(script: str) -> bool:
    """Whether an unreadable ``script`` may delete, move or copy files.

    True when it names one of those cmdlets or aliases, or a command that runs
    a string or a computed name (``Invoke-Expression``, ``&`` before a
    subexpression or a variable).
    """
    return bool(_NAMED.search(script)) or bool(re.search(r"&\s*[($]", script))


def targets(program_word: str) -> bool:
    """Whether a rule's first word names a delete, move or copy command."""
    return _name(program_word) in _PROGRAM


__all__ = ["ALIASES", "lower", "may_run", "posix_argv", "posix_readings", "targets"]
