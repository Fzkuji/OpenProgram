"""Generate reference pages from code — the single sources of truth.

Runs as the first step of every docs build (see build.py). Three
generators, each reading a structured source that already exists:

  * CLI commands   — ``openprogram.cli.build_parser()``'s argparse tree
                     → one page per top-level command under
                     ``docs/reference/cli/<command>.md``
  * Config keys    — ``openprogram.config_schema.SETTINGS``
                     → ``docs/reference/config-keys.md``
  * Provider registry — ``openprogram/providers/*/provider.json``
                     → ``docs/reference/provider-registry.md``

The generated files are gitignored (like ``_site``): they are build
artifacts, always regenerated, never edited by hand. Writes are
idempotent — a file is only rewritten when its content actually changed,
so the webui's mtime-based auto-rebuild doesn't loop on its own output.

Everything emitted is English-only (the strings come from code); there
are no ``.zh.md`` pairs, so the language toggle simply doesn't show on
these pages.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
GENERATED_NOTE = (
    "<!-- GENERATED FILE — do not edit. Rebuilt by "
    "tools/docs_site/generate_reference.py from {source}. -->\n\n"
)


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

def _option_rows(parser: argparse.ArgumentParser) -> list[str]:
    rows = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction) or a.dest == "help":
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
        rows.append(f"| {name} | {_md_escape(a.help)} |")
    return rows


def _subparsers_of(parser: argparse.ArgumentParser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a
    return None


def _render_command_page(name: str, parser: argparse.ArgumentParser) -> str:
    lines = [GENERATED_NOTE.format(source="openprogram/cli.py"), f"# {name}\n"]
    desc = parser.description or ""
    if desc:
        lines.append(desc.strip() + "\n")
    lines.append(f"```\n{parser.format_usage().strip()}\n```\n")

    opts = _option_rows(parser)
    if opts:
        lines.append("## Options\n")
        lines.append("| Option | Description |")
        lines.append("|---|---|")
        lines.extend(opts)
        lines.append("")

    sp = _subparsers_of(parser)
    if sp:
        for verb, vp in sp.choices.items():
            lines.append(f"## `{name} {verb}`\n")
            vhelp = (vp.description or _verb_help(sp, verb) or "").strip()
            if vhelp:
                lines.append(vhelp + "\n")
            vopts = _option_rows(vp)
            if vopts:
                lines.append("| Option | Description |")
                lines.append("|---|---|")
                lines.extend(vopts)
                lines.append("")
            vsp = _subparsers_of(vp)
            if vsp:
                lines.append("| Subcommand | Description |")
                lines.append("|---|---|")
                for sub_verb in vsp.choices:
                    lines.append(f"| `{name} {verb} {sub_verb}` | "
                                 f"{_md_escape(_verb_help(vsp, sub_verb))} |")
                lines.append("")
    return "\n".join(lines)


def _verb_help(sp: argparse._SubParsersAction, verb: str) -> str:
    for ca in sp._choices_actions:
        if ca.dest == verb:
            return ca.help or ""
    return ""


def generate_cli(docs_root: Path = DOCS_ROOT) -> list[Path]:
    from openprogram.cli import build_parser

    parser = build_parser()
    out_dir = docs_root / "reference" / "cli"
    written: list[Path] = []
    sp = _subparsers_of(parser)
    expected: set[str] = set()

    for name, sub in sp.choices.items():
        fname = f"{name}.md"
        expected.add(fname)
        page = _render_command_page(name, sub)
        if _write_if_changed(out_dir / fname, page):
            written.append(out_dir / fname)

    # Top-level flags page (openprogram itself: --print, --resume, …)
    top = [GENERATED_NOTE.format(source="openprogram/cli.py"),
           "# openprogram\n",
           (parser.description or "").strip() + "\n",
           "Global flags of the bare `openprogram` command. "
           "Each subcommand has its own page in this section.\n",
           "| Option | Description |", "|---|---|"]
    top.extend(_option_rows(parser))
    top.append("")
    expected.add("README.md")
    if _write_if_changed(out_dir / "README.md", "\n".join(top)):
        written.append(out_dir / "README.md")

    # Remove pages for commands that no longer exist.
    for stale in out_dir.glob("*.md"):
        if stale.name not in expected:
            stale.unlink()
    return written


# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------

def generate_config_keys(docs_root: Path = DOCS_ROOT) -> list[Path]:
    from openprogram.config_schema import SETTINGS

    lines = [GENERATED_NOTE.format(source="openprogram/config_schema.py"),
             "# Config keys\n",
             "Every user-editable setting, from the single schema that the "
             "`setup` CLI, `openprogram config`, the TUI settings screen, and "
             "the web Settings pages all render from. `apply` says when a "
             "change takes effect: `live` = immediately, `next_start` = on "
             "the next worker/web start.\n"]
    by_group: dict[str, list] = {}
    for s in SETTINGS:
        by_group.setdefault(s.group, []).append(s)
    for group, specs in by_group.items():
        lines.append(f"## {group}\n")
        lines.append("| Key | Default | Apply | Description |")
        lines.append("|---|---|---|---|")
        for s in specs:
            default = "—" if s.default is None else f"`{s.default}`"
            if s.secret:
                default = "*(secret)*"
            lines.append(f"| `{s.key}` | {default} | {s.apply} | "
                         f"{_md_escape(s.help or s.label)} |")
        lines.append("")
    path = docs_root / "reference" / "config-keys.md"
    return [path] if _write_if_changed(path, "\n".join(lines)) else []


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

def generate_provider_registry(docs_root: Path = DOCS_ROOT) -> list[Path]:
    providers_dir = DOCS_ROOT.parent / "openprogram" / "providers"
    entries = []
    for pj in sorted(providers_dir.glob("*/provider.json")):
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append((pj.parent.name, data))
    if not entries:
        return []

    lines = [GENERATED_NOTE.format(source="openprogram/providers/*/provider.json"),
             "# Provider registry\n",
             "Wire-level facts for every built-in provider, straight from its "
             "`provider.json`. For how to sign in and use each one, see "
             "[Providers](../models/providers.md).\n"]
    # API-key env vars live in env_api_keys.py, not provider.json.
    try:
        from openprogram.providers.env_api_keys import _PROVIDER_ENV_VARS
        env_of: dict = dict(_PROVIDER_ENV_VARS)
    except Exception:
        env_of = {}

    for dirname, data in entries:
        pid = data.get("id", dirname)
        lines.append(f"## {pid}\n")
        rows = [("Directory", f"`openprogram/providers/{dirname}/`")]
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
        lines.append("| | |")
        lines.append("|---|---|")
        lines.extend(f"| **{k}** | {v} |" for k, v in rows)
        lines.append("")
    path = docs_root / "reference" / "provider-registry.md"
    return [path] if _write_if_changed(path, "\n".join(lines)) else []


def generate_all(docs_root: Path = DOCS_ROOT) -> int:
    """Run every generator; returns the count of files (re)written."""
    written: list[Path] = []
    for gen in (generate_cli, generate_config_keys, generate_provider_registry):
        try:
            written.extend(gen(docs_root))
        except Exception as e:  # noqa: BLE001 — a broken generator must not kill the build
            print(f"[docs] reference generator {gen.__name__} failed: {e}")
    return len(written)


if __name__ == "__main__":
    n = generate_all()
    print(f"reference generators: {n} file(s) updated")
