"""``openprogram completion <shell>`` — emit shell autocompletion scripts.

Generates static completion scripts by walking the argparse subparser
tree. No runtime dependency on the user's shell beyond the standard
completion hooks (``complete`` on bash, ``compdef`` on zsh,
``Register-ArgumentCompleter`` on PowerShell). Output goes to stdout
so users can pipe it into their rc file or ``eval`` it in-place.

Three supported shells:

- **bash** — ``eval "$(openprogram completion bash)"`` or save to
  ``/etc/bash_completion.d/openprogram``.
- **zsh** — ``openprogram completion zsh > ~/.zsh/completions/_openprogram``,
  then ensure ``fpath`` includes that dir and ``compinit`` has been
  called.
- **powershell** — ``openprogram completion powershell | Out-String |
  Invoke-Expression``, or append to ``$PROFILE``.

Implementation note: we don't introspect at completion-time (no
``argcomplete`` dependency, no eval-on-tab cost) — the script is
emitted once with a static list of verbs and flags drawn from the
parser. That means re-emit after every CLI change, but startup
latency for the user is zero.
"""
from __future__ import annotations

import argparse
from typing import Iterable


def _collect_verbs(parser: argparse.ArgumentParser) -> list[tuple[str, list[str]]]:
    """Walk the parser tree → ``[(verb, [sub-verbs...]), ...]``.

    Only goes one level deep — sufficient for our CLI shape where
    most subcommands have one layer of verbs (``providers list``,
    ``programs run``, ...) and the leaf flags don't need completion.
    """
    verbs: list[tuple[str, list[str]]] = []
    for action in parser._actions:  # noqa: SLF001 — argparse internal
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            sub_verbs: list[str] = []
            for sub_action in sub._actions:  # noqa: SLF001
                if isinstance(sub_action, argparse._SubParsersAction):
                    sub_verbs.extend(sub_action.choices.keys())
                    break
            verbs.append((name, sub_verbs))
    return verbs


def _collect_top_flags(parser: argparse.ArgumentParser) -> list[str]:
    """Top-level ``--flag`` options that work with bare ``openprogram``."""
    flags: list[str] = []
    for action in parser._actions:  # noqa: SLF001
        if not action.option_strings:
            continue
        for opt in action.option_strings:
            if opt.startswith("--"):
                flags.append(opt)
    return flags


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------

def _emit_bash(verbs: list[tuple[str, list[str]]], top_flags: list[str]) -> str:
    verb_list = " ".join(v for v, _ in verbs)
    cases: list[str] = []
    for verb, sub_verbs in verbs:
        if not sub_verbs:
            continue
        cases.append(
            f'        {verb})\n'
            f'            COMPREPLY=( $(compgen -W "{ " ".join(sub_verbs) }" -- "$cur") )\n'
            f'            return 0\n'
            f'            ;;'
        )
    cases_block = "\n".join(cases) if cases else "        *)\n            ;;"
    flags = " ".join(top_flags)
    return f"""# bash completion for openprogram
# Source this file or add to /etc/bash_completion.d/
_openprogram() {{
    local cur prev verb
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # First positional → top-level verb (or a --flag)
    if [ "$COMP_CWORD" -eq 1 ]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=( $(compgen -W "{flags}" -- "$cur") )
        else
            COMPREPLY=( $(compgen -W "{verb_list}" -- "$cur") )
        fi
        return 0
    fi

    # Find the verb (skip --flag VALUE pairs)
    verb=""
    for ((i=1; i<COMP_CWORD; i++)); do
        if [[ "${{COMP_WORDS[i]}}" != -* ]]; then
            verb="${{COMP_WORDS[i]}}"
            break
        fi
    done

    case "$verb" in
{cases_block}
    esac
}}
complete -F _openprogram openprogram
"""


# ---------------------------------------------------------------------------
# zsh
# ---------------------------------------------------------------------------

def _emit_zsh(verbs: list[tuple[str, list[str]]], top_flags: list[str]) -> str:
    verb_lines = "\n".join(f"            '{v}:OpenProgram subcommand'" for v, _ in verbs)
    sub_cases: list[str] = []
    for verb, sub_verbs in verbs:
        if not sub_verbs:
            continue
        sub_lines = " ".join(f"'{sv}'" for sv in sub_verbs)
        sub_cases.append(
            f'        {verb})\n'
            f'            _values "{verb} verb" {sub_lines}\n'
            f'            ;;'
        )
    sub_block = "\n".join(sub_cases) if sub_cases else ""
    return f"""#compdef openprogram
# Save to a dir on $fpath, e.g. ~/.zsh/completions/_openprogram

_openprogram() {{
    local context state state_descr line
    typeset -A opt_args

    _arguments -C \\
        '1: :->verb' \\
        '*::arg:->args'

    case $state in
        verb)
            _values 'openprogram verb' \\
{verb_lines}
            ;;
        args)
            case $words[1] in
{sub_block}
            esac
            ;;
    esac
}}

_openprogram "$@"
"""


# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------

def _emit_powershell(verbs: list[tuple[str, list[str]]], top_flags: list[str]) -> str:
    verb_list = ", ".join(f"'{v}'" for v, _ in verbs)
    flag_list = ", ".join(f"'{f}'" for f in top_flags)
    sub_branches: list[str] = []
    for verb, sub_verbs in verbs:
        if not sub_verbs:
            continue
        sv_list = ", ".join(f"'{sv}'" for sv in sub_verbs)
        sub_branches.append(
            f"        '{verb}' {{ @({sv_list}) }}"
        )
    sub_block = "\n".join(sub_branches) if sub_branches else "        # no sub-verbs"
    return f"""# PowerShell completion for openprogram
# Append to $PROFILE, or run once via:
#   openprogram completion powershell | Out-String | Invoke-Expression

Register-ArgumentCompleter -Native -CommandName openprogram -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)

    $tokens = $commandAst.CommandElements | ForEach-Object {{ $_.Extent.Text }}
    $count = $tokens.Count
    $current = $wordToComplete

    # First positional → top-level verb (or a top flag)
    if ($count -le 2) {{
        if ($current.StartsWith('-')) {{
            $candidates = @({flag_list})
        }} else {{
            $candidates = @({verb_list})
        }}
        $candidates | Where-Object {{ $_ -like "$current*" }} |
            ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }}
        return
    }}

    # Find the verb (first non-flag positional after openprogram)
    $verb = $null
    for ($i = 1; $i -lt $count; $i++) {{
        if (-not $tokens[$i].StartsWith('-')) {{ $verb = $tokens[$i]; break }}
    }}

    $subVerbs = switch ($verb) {{
{sub_block}
        default {{ @() }}
    }}

    $subVerbs | Where-Object {{ $_ -like "$current*" }} |
        ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }}
}}
"""


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

EMITTERS = {
    "bash": _emit_bash,
    "zsh": _emit_zsh,
    "powershell": _emit_powershell,
    "pwsh": _emit_powershell,  # alias
}


def _build_root_parser_for_completion() -> argparse.ArgumentParser:
    """Reconstruct the root parser so we can walk it without
    actually running ``main()``.

    Imports ``openprogram.cli`` and invokes the parser-building
    section — same code path as a real CLI invocation, except we
    intercept right before ``parser.parse_args()``.
    """
    from openprogram import cli as _cli
    # Re-run main()'s parser construction in a controlled way by
    # patching parse_args to capture the parser then bail. Less
    # invasive than refactoring main() to expose the parser.
    captured: list[argparse.ArgumentParser] = []
    real_parse_args = argparse.ArgumentParser.parse_args

    def _capture(self, args=None, namespace=None):
        captured.append(self)
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = _capture  # type: ignore[method-assign]
    try:
        try:
            _cli.main()
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real_parse_args  # type: ignore[method-assign]

    if not captured:
        raise RuntimeError("Could not capture root parser from openprogram.cli.main")
    return captured[0]


def _cmd_completion(shell: str) -> int:
    emit = EMITTERS.get(shell.lower())
    if emit is None:
        import sys
        print(
            f"Unknown shell: {shell!r}. Supported: " + ", ".join(sorted(EMITTERS)),
            file=sys.stderr,
        )
        return 2

    parser = _build_root_parser_for_completion()
    verbs = _collect_verbs(parser)
    top_flags = _collect_top_flags(parser)

    out = emit(verbs, top_flags)
    print(out, end="")
    return 0


__all__ = ["_cmd_completion"]
