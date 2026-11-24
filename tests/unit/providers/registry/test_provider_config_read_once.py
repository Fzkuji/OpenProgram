"""The provider section of the config is parsed once per change of the file."""
from __future__ import annotations

import json
import os

from openprogram.models.providers.settings import config_read


def test_the_config_is_parsed_again_only_when_the_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"providers": {"acme": {"source": "custom"}}}), encoding="utf-8")
    monkeypatch.setattr(config_read, "get_config_path", lambda: path)
    monkeypatch.setattr(config_read, "_parsed", None)
    parsed = []
    loads = json.loads
    monkeypatch.setattr(config_read.json, "loads", lambda text: parsed.append(1) or loads(text))

    for _ in range(50):
        assert config_read.read_providers_config() == {"acme": {"source": "custom"}}
    assert len(parsed) == 1

    replacement = tmp_path / "config.json.tmp"
    replacement.write_text(json.dumps({"providers": {"acme": {"source": "custom", "local": True}}}),
                           encoding="utf-8")
    os.replace(replacement, path)
    assert config_read.read_providers_config()["acme"]["local"] is True
    assert len(parsed) == 2

    path.unlink()
    assert config_read.read_providers_config() == {}
    path.write_text("{not json", encoding="utf-8")
    assert config_read.read_providers_config() == {}
