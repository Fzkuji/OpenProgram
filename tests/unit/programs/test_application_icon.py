"""An application names its glyph in the manifest; the host draws it."""
import json

import pytest
from pathlib import Path

from openprogram.programs._host.ui import catalog


def _package(tmp_path, **extra):
    (tmp_path / "index.html").write_text("<!doctype html><title>App</title>")
    (tmp_path / "application.json").write_text(json.dumps({"id": "test.icon", "title": "App", "version": "1", **extra}))
    return tmp_path


def test_icon_is_optional_and_kept(tmp_path):
    assert "icon" not in catalog.manifest(_package(tmp_path))
    assert catalog.manifest(_package(tmp_path, icon="book-open"))["icon"] == "book-open"


@pytest.mark.parametrize("icon", ["", "Book", "../x.svg", "a" * 41, 3])
def test_icon_must_be_a_glyph_name(tmp_path, icon):
    with pytest.raises(ValueError, match="icon"):
        catalog.manifest(_package(tmp_path, icon=icon))


def test_examples_name_their_glyphs():
    root = Path(__file__).resolve().parents[3] / "examples" / "applications"
    icons = {path.parent.name: json.loads(path.read_text()).get("icon") for path in root.glob("*/application.json")}
    assert icons == {"calculator": "calculator", "pdf_editor": "file-pdf"}
