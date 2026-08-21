"""Foreign plugin manifests parse without rewriting the author's files."""
from __future__ import annotations

import json
from pathlib import Path

from openprogram.plugins.manifest import parse_manifest_dir


def test_claude_code_nested_plugin_json(tmp_path: Path):
    plugin = tmp_path / "code-assistant"
    plugin.mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "code-assistant",
        "version": "1.2.0",
        "description": "AI-powered code assistance tools",
        "commands": "./extra-commands/*.md",
        "hooks": "./custom-hooks.json",
        "skills": "./skills",
    }), encoding="utf-8")
    m = parse_manifest_dir(plugin)
    assert m is not None
    assert m.name == "code-assistant"
    assert m.manifest_form == "claude-plugin.json"
    assert m.entrypoints["commands"] == "./extra-commands/*.md"
    assert m.entrypoints["hooks"] == "./custom-hooks.json"
    assert m.entrypoints["skills"] == "./skills"


def test_legacy_root_plugin_json_still_works(tmp_path: Path):
    plugin = tmp_path / "legacy"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(json.dumps({
        "name": "legacy",
        "entrypoints": {"commands": "./cmd"},
    }), encoding="utf-8")
    m = parse_manifest_dir(plugin)
    assert m is not None
    assert m.manifest_form == "plugin.json"
    assert m.entrypoints["commands"] == "./cmd"


def test_opencode_package_json_field(tmp_path: Path):
    plugin = tmp_path / "oc-plug"
    plugin.mkdir()
    (plugin / "package.json").write_text(json.dumps({
        "name": "oc-plug",
        "version": "0.3.0",
        "description": "an opencode plugin",
        "opencode": {
            "providers": "./src/provider.ts",
        },
    }), encoding="utf-8")
    m = parse_manifest_dir(plugin)
    assert m is not None
    assert m.manifest_form == "package.json#opencode"
    assert m.version == "0.3.0"
    assert m.entrypoints["providers"] == "./src/provider.ts"


def test_openprogram_field_still_wins_over_opencode(tmp_path: Path):
    plugin = tmp_path / "both"
    plugin.mkdir()
    (plugin / "package.json").write_text(json.dumps({
        "name": "both",
        "openprogram": {"name": "ours", "entrypoints": {"web": "./dist"}},
        "opencode": {"name": "theirs"},
    }), encoding="utf-8")
    m = parse_manifest_dir(plugin)
    assert m is not None
    assert m.name == "ours"
    assert m.manifest_form == "package.json#openprogram"
    assert m.entrypoints["web"] == "./dist"
