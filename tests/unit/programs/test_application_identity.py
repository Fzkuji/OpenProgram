"""One title, one description, one icon per application or package."""
import json
from pathlib import Path

import pytest

from openprogram.programs._host import identity
from openprogram.programs._host.ui import catalog

REPO = Path(__file__).resolve().parents[3]


def _package(tmp_path, **extra):
    (tmp_path / "index.html").write_text("<!doctype html><title>App</title>")
    (tmp_path / "application.json").write_text(json.dumps({"id": "test.app", "title": "App", "version": "1", **extra}))
    return tmp_path


def test_description_is_optional_and_kept(tmp_path):
    assert "description" not in catalog.manifest(_package(tmp_path))
    assert catalog.manifest(_package(tmp_path, description="Adds numbers."))["description"] == "Adds numbers."


@pytest.mark.parametrize("description", ["", "   ", "two\nlines", "a" * 301, 3, None])
def test_description_must_be_one_short_line(tmp_path, description):
    with pytest.raises(ValueError, match="description"):
        catalog.manifest(_package(tmp_path, description=description))


def test_manifest_identity(tmp_path):
    _package(tmp_path, description="Adds numbers.", icon="calculator")
    assert identity.identity(tmp_path) == {"title": "App", "description": "Adds numbers.", "icon": "calculator"}


def test_readme_identity_takes_the_heading_and_first_sentence(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Text rewriting\n\n"
        "Summarise or polish a text with `one` model call. The model only rewrites.\n"
        "More detail.\n\n## Contract\n\nIgnored.\n",
    )
    assert identity.identity(tmp_path) == {
        "title": "Text rewriting",
        "description": "Summarise or polish a text with one model call.",
        "icon": "",
    }


def test_python_application_constants_and_fallback(tmp_path):
    (tmp_path / "__init__.py").write_text('TITLE = "Shop"\nDESCRIPTION = "Sells things."\nICON = "browser"\n')
    assert identity.identity(tmp_path) == {"title": "Shop", "description": "Sells things.", "icon": "browser"}
    bare = tmp_path / "bare_pkg"
    bare.mkdir()
    assert identity.identity(bare) == {"title": "bare_pkg", "description": "", "icon": ""}


def test_shipped_applications_describe_themselves():
    for directory in (
        REPO / "examples" / "applications" / "calculator",
        REPO / "examples" / "applications" / "pdf_editor",
        REPO / "openprogram" / "programs" / "applications" / "browser",
    ):
        found = identity.identity(directory)
        assert found["description"].endswith("."), directory
        assert found["icon"], directory
    assert identity.identity(REPO / "openprogram" / "programs" / "applications" / "browser")["title"] == "Browser"
