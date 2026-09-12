from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.release.office import font_assets


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(font_assets, "EXPECTED_FONT_FILES", 3)
    input_root, pack = tmp_path / "input", tmp_path / "pack"
    (input_root / "family").mkdir(parents=True)
    (pack / "fonts").mkdir(parents=True)
    (pack / "sdkjs/common/Images").mkdir(parents=True)
    (pack / "server/FileConverter/bin").mkdir(parents=True)
    (input_root / "family/LICENSE.txt").write_text("fixture license\n", encoding="utf-8")
    names = [f"font-{i}.ttf" for i in range(3)]
    for i, name in enumerate(names):
        data = f"font-{i}".encode()
        (input_root / "family" / name).write_bytes(data)
        (pack / "fonts" / f"{i:03d}.ttf").write_bytes(data)
    all_fonts_path = pack / "sdkjs/common/AllFonts.js"
    all_fonts_path.write_text(
        'window["__fonts_files"] = ["000.ttf","001.ttf","002.ttf"];\n'
        'window["__fonts_infos"] = [["Fixture",0,0]];\n'
        'window["__fonts_ranges"] = [0,127,0];\n', encoding="utf-8"
    )
    selection_path = pack / "server/FileConverter/bin/font_selection.bin"
    selection_path.write_bytes(b"selection")
    for i in range(10):
        (pack / "sdkjs/common/Images" / f"fonts_thumbnail{i}.png").write_bytes(b"png")
    source_map = {"fontSet": "fixture", "fonts": [
        {"index": i, "file": f"fonts/{i:03d}.ttf", "source": f"input/{name}"}
        for i, name in enumerate(names)
    ]}
    (pack / "onlyoffice-browser-font-source-map.json").write_text(json.dumps(source_map), encoding="utf-8")
    manifest = {"version": 1, "fontSet": "fixture", "allFonts": "sdkjs/common/AllFonts.js",
                "fontSelection": "server/FileConverter/bin/font_selection.bin",
                "fontSourceMap": "onlyoffice-browser-font-source-map.json",
                "fontThumbnails": [f"sdkjs/common/Images/fonts_thumbnail{i}.png" for i in range(10)],
                "fonts": [f"fonts/{i:03d}.ttf" for i in range(3)]}
    (pack / "onlyoffice-browser-font-assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(font_assets, "EXPECTED_ALLFONTS_SHA256", _sha(all_fonts_path))
    monkeypatch.setattr(font_assets, "EXPECTED_SELECTION_SHA256", _sha(selection_path))
    return pack, input_root


def test_synthetic_fontpack_has_portable_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pack, input_root = _fixture(tmp_path, monkeypatch)
    result = font_assets.validate_font_assets(pack, input_root)
    assert result["fonts"] == 3
    assert result["thumbnails"] == 10
    assert [entry["source"] for entry in result["provenance"]] == [f"family/font-{i}.ttf" for i in range(3)]
    assert result["licenses"] == ["family/LICENSE.txt"]


@pytest.mark.parametrize("target", ["allfonts", "selection"])
def test_allfonts_and_selection_tampering_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str):
    pack, input_root = _fixture(tmp_path, monkeypatch)
    path = pack / ("sdkjs/common/AllFonts.js" if target == "allfonts" else "server/FileConverter/bin/font_selection.bin")
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="digest"):
        font_assets.validate_font_assets(pack, input_root)


def test_source_map_and_declared_manifest_digest_tampering_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pack, input_root = _fixture(tmp_path, monkeypatch)
    source_map_path = pack / "onlyoffice-browser-font-source-map.json"
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    source_map["fonts"][0]["source"] = "input/external.ttf"
    source_map_path.write_text(json.dumps(source_map), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        font_assets.validate_font_assets(pack, input_root)
    pack, input_root = _fixture(tmp_path / "digest", monkeypatch)
    manifest_path = pack / "onlyoffice-browser-font-assets.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifestDigest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        font_assets.validate_font_assets(pack, input_root)
