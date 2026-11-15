"""Generated reference behavior: bilingual parity and source-change visibility."""
import argparse
import re

import pytest

from scripts.docs_site import generate_reference as reference


def test_generated_reference_pairs_preserve_machine_identifiers(tmp_path):
    reference.generate_all(tmp_path)
    english = [p for p in tmp_path.rglob('*.md') if not p.name.endswith('.zh.md')]
    assert english
    for page in english:
        chinese = page.with_name(page.stem + '.zh.md')
        assert chinese.exists(), page
        en, zh = page.read_text(), chinese.read_text()
        # Table identities are the command flags, configuration keys and values.
        identity = lambda text: re.findall(r'^\| (`[^|]*?`) \|', text, flags=re.M)
        assert identity(en) == identity(zh), page
        assert re.search(r'[一-鿿]', zh), page
    assert reference.generate_all(tmp_path) == 0


def test_recursive_commands_and_stale_language_pairs(tmp_path, monkeypatch):
    from openprogram.interfaces.cli import application as cli

    parser = argparse.ArgumentParser(prog='openprogram')
    child = parser.add_subparsers().add_parser('config', help='List accounts')
    grandchild = child.add_subparsers().add_parser('nested', help='List accounts')
    leaf = grandchild.add_subparsers().add_parser('leaf', help='List accounts')
    leaf.add_argument('--account', help='Account (default: default)')
    leaf.add_argument('--internal', help=argparse.SUPPRESS)
    monkeypatch.setattr(cli, 'build_parser', lambda: parser)
    folder = tmp_path / 'reference/cli'
    folder.mkdir(parents=True)
    for name in ('removed.md', 'removed.zh.md'):
        (folder / name).write_text('old generated page')
    reference.generate_cli(tmp_path)
    for name, description in [('config.md', 'Account (default: default)'),
                              ('config.zh.md', '账号（默认：default）')]:
        text = (folder / name).read_text()
        assert '`config nested leaf`' in text
        assert '`--account`' in text
        assert description in text
        assert '--internal' not in text
    assert not (folder / 'removed.md').exists()
    assert not (folder / 'removed.zh.md').exists()


def test_untranslated_source_aborts_generation(tmp_path, monkeypatch):
    from openprogram.interfaces.cli import application as cli

    parser = argparse.ArgumentParser(prog='openprogram', description='New untranslated help')
    parser.add_subparsers().add_parser('config')
    monkeypatch.setattr(cli, 'build_parser', lambda: parser)
    with pytest.raises(ValueError, match='New untranslated help'):
        reference.generate_all(tmp_path)


def test_generated_help_renders_placeholders_and_legacy_anchor(tmp_path):
    from scripts.docs_site.build import make_md

    reference.generate_cli(tmp_path)
    for suffix in ("", ".zh"):
        folder = tmp_path / "reference/cli"
        root = make_md().render((folder / f"README{suffix}.md").read_text())
        backup = make_md().render((folder / f"backup{suffix}.md").read_text())
        providers = make_md().render((folder / f"providers{suffix}.md").read_text())
        assert 'id="openprogram"' in root
        assert '&lt;name&gt;' in root
        assert '&lt;state&gt;/backups/' in backup
        assert '<state>' not in backup
        assert 'printf %s' in providers
        assert 'printf %%s' not in providers
        setup = make_md().render((folder / f"setup{suffix}.md").read_text())
        assert '<code>[menu | &lt;section&gt;]</code>' in setup
        assert ('完整首次设置向导' if suffix else 'full first-run wizard') in setup
