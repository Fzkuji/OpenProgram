"""Generate reference pages from code — the single sources of truth.

Runs as the first step of every docs build (see build.py). Three
generators, each reading a structured source that already exists:

  * CLI commands   — ``openprogram.interfaces.cli.application.build_parser()``'s argparse tree
                     → one page per top-level command under
                     ``docs/reference/cli/<command>.md``
  * Config keys    — ``openprogram.state.settings.SETTINGS``
                     → ``docs/reference/config-keys.md``
  * Provider registry — ``openprogram/models/providers/adapters/*/provider.json``
                     → ``docs/reference/provider-registry.md``

The generated files are gitignored (like ``_site``): they are build
artifacts, always regenerated, never edited by hand. Writes are
idempotent — a file is only rewritten when its content actually changed,
so the webui's mtime-based auto-rebuild doesn't loop on its own output.

Every generator emits English and Chinese pairs. Machine identifiers come
from code; Chinese prose comes from a source-keyed translation catalogue.
Missing translations abort generation so drift cannot silently reach the site.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
GENERATED_NOTE = (
    "<!-- GENERATED FILE — do not edit. Rebuilt by "
    "scripts/docs_site/generate_reference.py from {source}. -->\n\n"
)


_TRANSLATIONS = json.loads(
    Path(__file__).with_name("reference_zh.json").read_text(encoding="utf-8")
)


def _translate(text: str | None, lang: str) -> str:
    text = (text or "").strip()
    if not text or lang == "en":
        return text
    try:
        return _TRANSLATIONS[text]
    except KeyError:
        raise ValueError(f"Missing Chinese reference translation: {text}") from None



def _prose(text: str | None, lang: str) -> str:
    # Help strings are prose, not author-supplied HTML. Keep code spans literal.
    translated = _translate(text, lang).replace("%%", "%")
    return "".join(part if part.startswith("`") else html.escape(part, quote=False)
                   for part in re.split(r"(`+[^`]*`+)", translated))


def _suffix(lang: str) -> str:
    return ".zh" if lang == "zh" else ""


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    path.write_text(content, encoding="utf-8")
    return True


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# CLI commands — one page per top-level subcommand
# ---------------------------------------------------------------------------

def _option_rows(parser: argparse.ArgumentParser, lang: str = "en") -> list[str]:
    rows = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction) or a.dest == "help" or a.help == argparse.SUPPRESS:
            continue
        if a.option_strings:
            name = ", ".join(f"`{s}`" for s in a.option_strings)
            if a.metavar:
                name += f" `{a.metavar}`"
            elif not isinstance(a, (argparse._StoreTrueAction, argparse._StoreFalseAction)) \
                    and a.nargs != 0 and a.const is None:
                name += f" `{a.dest.upper()}`"
        else:
            name = f"`{a.metavar or a.dest}`"
        rows.append(f"| {_md_escape(name)} | {_md_escape(_prose(a.help, lang))} |")
    return rows


def _subparsers_of(parser: argparse.ArgumentParser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a
    return None


def _render_command_page(name: str, parser: argparse.ArgumentParser, lang: str = "en",
                         description: str = "") -> str:
    title = name if lang == "en" else _translate(f"CLI: {name}", lang)
    lines = [GENERATED_NOTE.format(source="openprogram/interfaces/cli/parser.py"),
             f"# {title}\n"]
    desc = _prose(parser.description or description, lang)
    if desc:
        lines.append(desc + "\n")
    lines.append(f"```text\n{parser.format_usage().strip()}\n```\n")

    def append_options(command):
        opts = _option_rows(command, lang)
        if opts:
            lines.extend([_translate("| Option | Description |", lang), "|---|---|", *opts, ""])

    if _option_rows(parser, lang):
        lines.append("## " + _translate("Options", lang) + "\n")
        append_options(parser)

    def append_children(command, prefix, level):
        sp = _subparsers_of(command)
        if not sp:
            return
        for verb, child in sp.choices.items():
            full_name = f"{prefix} {verb}"
            lines.append(f"{'#' * min(level, 6)} `{full_name}`\n")
            help_text = _prose(child.description or _verb_help(sp, verb), lang)
            if help_text:
                lines.append(help_text + "\n")
            append_options(child)
            append_children(child, full_name, level + 1)

    append_children(parser, name, 2)
    return "\n".join(lines)


def _verb_help(sp: argparse._SubParsersAction, verb: str) -> str:
    for ca in sp._choices_actions:
        if ca.dest == verb:
            return ca.help or ""
    return ""


def generate_cli(docs_root: Path = DOCS_ROOT) -> list[Path]:
    from openprogram.interfaces.cli.application import build_parser

    parser = build_parser()
    out_dir = docs_root / "reference" / "cli"
    sp = _subparsers_of(parser)
    pages: dict[Path, str] = {}
    for lang in ("en", "zh"):
        for name, sub in (sp.choices.items() if sp else []):
            pages[out_dir / f"{name}{_suffix(lang)}.md"] = _render_command_page(
                name, sub, lang, _verb_help(sp, name)
            )
        top = [GENERATED_NOTE.format(source="openprogram/interfaces/cli/parser.py"),
               '<a id="openprogram"></a>\n',
               "# " + _translate("Global flags", lang) + "\n",
               _prose(parser.description, lang) + "\n",
               _translate("Global flags of the bare `openprogram` command. "
                          "Each subcommand has its own page in this section.", lang) + "\n",
               _translate("| Option | Description |", lang), "|---|---|"]
        top.extend(_option_rows(parser, lang))
        top.append("")
        pages[out_dir / f"README{_suffix(lang)}.md"] = "\n".join(top)

    # Render both languages before writing, so missing translations do not publish half a pair.
    written = [path for path, text in pages.items() if _write_if_changed(path, text)]
    for stale in out_dir.glob("*.md"):
        if stale not in pages:
            stale.unlink()
    return written


# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------

def generate_config_keys(docs_root: Path = DOCS_ROOT) -> list[Path]:
    from openprogram.state.settings import SETTINGS

    by_group: dict[str, list] = {}
    for spec in SETTINGS:
        by_group.setdefault(spec.group, []).append(spec)
    pages = {}
    for lang in ("en", "zh"):
        lines = [GENERATED_NOTE.format(source="openprogram/state/settings.py"),
                 "# " + _translate("Config keys", lang) + "\n",
                 _translate("Every user-editable setting, from the single schema that the "
                            "`setup` CLI, `openprogram config`, the TUI settings screen, and "
                            "the web Settings pages all render from. `apply` says when a "
                            "change takes effect: `live` = immediately, `next_start` = on "
                            "the next worker/web start.", lang) + "\n"]
        for group, specs in by_group.items():
            lines.extend(["## " + _translate(group, lang) + "\n",
                          _translate("| Key | Default | Apply | Description |", lang),
                          "|---|---|---|---|"])
            for spec in specs:
                default = "—" if spec.default is None else f"`{spec.default}`"
                if spec.secret:
                    default = _translate("*(secret)*", lang)
                lines.append(f"| `{spec.key}` | {default} | `{spec.apply}` | "
                             f"{_md_escape(_prose(spec.help or spec.label, lang))} |")
            lines.append("")
        pages[docs_root / "reference" / f"config-keys{_suffix(lang)}.md"] = "\n".join(lines)
    return [path for path, text in pages.items() if _write_if_changed(path, text)]


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

def generate_provider_registry(docs_root: Path = DOCS_ROOT) -> list[Path]:
    providers_dir = DOCS_ROOT.parent / "openprogram" / "models" / "providers" / "adapters"
    entries = []
    for pj in sorted(providers_dir.glob("*/provider.json")):
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append((pj.parent.name, data))
    if not entries:
        return []

    introduction = [GENERATED_NOTE.format(source="openprogram/models/providers/adapters/*/provider.json"),
             "# Provider registry\n",
             "Wire-level facts for every built-in provider, straight from its "
             "`provider.json`. For how to sign in and use each one, see "
             "[Providers](../models/providers.md).\n"]
    # API-key env vars live in env_api_keys.py, not provider.json.
    try:
        from openprogram.models.providers.settings.env_api_keys import _PROVIDER_ENV_VARS
        env_of: dict = dict(_PROVIDER_ENV_VARS)
    except Exception:
        env_of = {}

    rows_by_provider = []
    for dirname, data in entries:
        pid = data.get("id", dirname)
        rows = [("Directory", f"`openprogram/models/providers/adapters/{dirname}/`")]
        endpoints = data.get("endpoints") or {}
        for ep_name, ep in endpoints.items():
            label = "Protocol" if ep_name == "default" else f"Protocol ({ep_name})"
            if ep.get("api"):
                rows.append((label, f"`{ep['api']}`"))
            if ep.get("base_url"):
                url_label = "Base URL" if ep_name == "default" else f"Base URL ({ep_name})"
                rows.append((url_label, f"`{ep['base_url']}`"))
        envs = env_of.get(pid)
        if envs:
            if isinstance(envs, str):
                envs = [envs]
            rows.append(("API-key env", ", ".join(f"`{e}`" for e in envs)))
        thinking = data.get("thinking")
        if isinstance(thinking, dict):
            if thinking.get("default_effort"):
                rows.append(("Default effort", f"`{thinking['default_effort']}`"))
            if thinking.get("effort_map"):
                rows.append(("Effort levels",
                             ", ".join(f"`{k}`" for k in thinking["effort_map"])))
        cache = data.get("cache")
        if isinstance(cache, dict) and cache:
            keys_shown = ", ".join(f"`{k}`" for k in sorted(cache))
            rows.append(("Cache policy keys", keys_shown))
        rows_by_provider.append((pid, rows))
    pages = {}
    for lang in ("en", "zh"):
        lines = [introduction[0], "# " + _translate("Provider registry", lang) + "\n",
                 _translate(introduction[2], lang).replace(
                     "../models/providers.md", f"../models/providers{_suffix(lang)}.md"
                 ) + "\n"]
        for pid, rows in rows_by_provider:
            lines.extend([f"## {pid}\n", "| | |", "|---|---|"])
            for label, value in rows:
                base, separator, endpoint = label.partition(" (")
                localized = _translate(base, lang) + (separator + endpoint if separator else "")
                lines.append(f"| **{localized}** | {value} |")
            lines.append("")
        pages[docs_root / "reference" / f"provider-registry{_suffix(lang)}.md"] = "\n".join(lines)
    return [path for path, text in pages.items() if _write_if_changed(path, text)]


def generate_all(docs_root: Path = DOCS_ROOT) -> int:
    """Run every generator; returns the count of files (re)written."""
    written: list[Path] = []
    for gen in (generate_cli, generate_config_keys, generate_provider_registry):
        written.extend(gen(docs_root))
    return len(written)


if __name__ == "__main__":
    n = generate_all()
    print(f"reference generators: {n} file(s) updated")
