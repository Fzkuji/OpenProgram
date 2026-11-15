from __future__ import annotations

from pathlib import Path

from scripts.testing import affected_tests

from scripts.testing.affected_tests import (
    Selection,
    build_python_graph,
    commands,
    select,
)


def _write(root: Path, files: dict[str, str]) -> list[str]:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


def _repository(root: Path, extra: dict[str, str] | None = None) -> list[str]:
    files = {
        "openprogram/__init__.py": "",
        "openprogram/runtime/__init__.py": "from .engine import run\n",
        "openprogram/runtime/engine.py": "from openprogram.runtime import planner\nrun = 1\n",
        "openprogram/runtime/planner.py": "VALUE = 1\n",
        "openprogram/runtime/catalog.json": "{}\n",
        "openprogram/other.py": "X = 1\n",
        "tests/contracts/repository/test_layout.py": "def test_x(): pass\n",
        "tests/support/fixtures.py": "from openprogram.runtime.engine import run\n",
        "tests/unit/runtime/test_engine.py": "from tests.support.fixtures import run\n",
        "tests/unit/runtime/test_package.py": "from openprogram.runtime import run\n",
        "tests/unit/runtime/test_patch.py": (
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr('openprogram.other.X', 2)\n"
        ),
        "tests/unit/runtime/test_source_text.py": (
            "from pathlib import Path\n"
            "TEXT = (Path('.') / 'openprogram' / 'runtime' / 'catalog.json')\n"
        ),
        "tests/unit/runtime/test_planner.py": "def test_x(): pass\n",
        "tests/unit/config/test_docs.py": "from scripts.docs_site import nav\n",
        "scripts/__init__.py": "",
        "scripts/docs_site/__init__.py": "",
        "scripts/docs_site/nav.py": "",
    }
    files.update(extra or {})
    return _write(root, files)


def test_graph_resolves_relative_submodule_and_string_imports(tmp_path: Path) -> None:
    files = _repository(tmp_path)
    graph = build_python_graph(tmp_path, files)

    assert graph.imports["openprogram/runtime/__init__.py"] == {"openprogram/runtime/engine.py"}
    # "from openprogram.runtime import planner" names the submodule.
    assert "openprogram/runtime/planner.py" in graph.imports["openprogram/runtime/engine.py"]
    assert "openprogram/other.py" in graph.imports["tests/unit/runtime/test_patch.py"]
    assert "openprogram/runtime/catalog.json" in graph.references[
        "tests/unit/runtime/test_source_text.py"
    ]


def test_selects_tests_through_imports_names_and_contracts(tmp_path: Path) -> None:
    files = _repository(tmp_path)
    selection = select(["openprogram/runtime/planner.py"], tmp_path, files)

    assert set(selection.python_tests) == {
        "tests/contracts/repository/test_layout.py",
        "tests/unit/runtime/test_engine.py",  # via engine <- support module
        "tests/unit/runtime/test_package.py",  # via the package re-export
        "tests/unit/runtime/test_planner.py",  # named after the module
    }


def test_monkeypatch_string_targets_count_as_imports(tmp_path: Path) -> None:
    files = _repository(tmp_path)
    selection = select(["openprogram/other.py"], tmp_path, files, include_contracts=False)

    assert set(selection.python_tests) == {"tests/unit/runtime/test_patch.py"}


def test_walk_stops_at_hub_modules(tmp_path: Path) -> None:
    extra = {f"openprogram/user_{index}.py": "import openprogram.runtime.engine\n" for index in range(4)}
    extra["tests/unit/runtime/test_user.py"] = "import openprogram.user_0\n"
    files = _repository(tmp_path, extra)

    through = select(["openprogram/runtime/planner.py"], tmp_path, files, hub_fan_in=10)
    stopped = select(["openprogram/runtime/planner.py"], tmp_path, files, hub_fan_in=3)

    assert "tests/unit/runtime/test_user.py" in through.python_tests
    assert "tests/unit/runtime/test_user.py" not in stopped.python_tests
    # A changed hub still selects the tests that import it directly.
    direct = select(["openprogram/runtime/engine.py"], tmp_path, files, hub_fan_in=3)
    assert "tests/unit/runtime/test_engine.py" in direct.python_tests


def test_data_files_select_tests_that_name_them(tmp_path: Path) -> None:
    files = _repository(tmp_path)
    selection = select(["openprogram/runtime/catalog.json"], tmp_path, files)

    assert "tests/unit/runtime/test_source_text.py" in selection.python_tests


def test_changed_tests_always_run_and_docs_changes_run_docs_checks(tmp_path: Path) -> None:
    files = _repository(tmp_path)

    changed_test = select(["tests/unit/runtime/test_planner.py"], tmp_path, files)
    assert "tests/unit/runtime/test_planner.py" in changed_test.python_tests

    docs = select(["docs/start/index.md"], tmp_path, files)
    assert set(docs.python_tests) == {
        "tests/unit/config/test_docs.py",
        "tests/contracts/repository/test_layout.py",
    }
    assert docs.docs_check == ["docs/start/index.md"]


def test_web_tests_follow_imports_and_source_paths(tmp_path: Path) -> None:
    files = _write(tmp_path, {
        "apps/web/lib/a.ts": "export const a = 1;\n",
        "apps/web/lib/b.ts": "import { a } from '@/lib/a';\nexport const b = a;\n",
        "apps/web/components/c.tsx": "export const c = 1;\n",
        "apps/web/components/d.tsx": "export const d = 1;\n",
        "apps/web/tests/lib/b.test.mjs": "import { b } from '../../lib/b.ts';\n",
        "apps/web/tests/components/c.test.mjs": (
            "const source = await readFile(join(web, 'components/c.tsx'), 'utf8');\n"
        ),
    })

    selection = select(["apps/web/lib/a.ts", "apps/web/components/c.tsx"], tmp_path, files)

    assert set(selection.web_tests) == {
        "apps/web/tests/lib/b.test.mjs",
        "apps/web/tests/components/c.test.mjs",
    }
    assert selection.typecheck == ["apps/web/components/c.tsx", "apps/web/lib/a.ts"]

    untested = select(["apps/web/components/d.tsx"], tmp_path, files)
    assert untested.web_tests == {}
    assert untested.typecheck == ["apps/web/components/d.tsx"]


def test_commands_cover_each_selected_kind(tmp_path: Path) -> None:
    selection = Selection(
        python_tests={"tests/unit/a/test_a.py": "x"},
        web_tests={"apps/web/tests/a.test.mjs": "x"},
        typecheck=["apps/web/a.ts"],
        docs_check=["docs/a.html"],
    )

    planned = commands(selection, tmp_path)

    assert planned[0][1][1:] == [
        "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "auto", "--timeout=300",
        "tests/unit/a/test_a.py",
    ]
    assert planned[1][0] == "apps/web" and planned[1][1][-1] == "tests/a.test.mjs"
    assert planned[2] == ("apps/web", ["npx", "tsc", "--noEmit", "-p", "."])
    assert planned[3][1][1:] == ["-m", "scripts.docs_site.check", "docs/a.html"]


def test_every_kind_of_change_runs_the_contracts_and_the_docs_check(tmp_path: Path) -> None:
    files = _repository(tmp_path, {
        "apps/web/lib/a.ts": "export const a = 1;\n",
    })
    for change in ("apps/web/lib/a.ts", "docs/start/index.html", "AGENTS.md", "openprogram/other.py"):
        selection = select([change], tmp_path, files)
        assert "tests/contracts/repository/test_layout.py" in selection.python_tests, change
        assert selection.docs_check == [change]

    nothing = select([], tmp_path, files)
    assert not nothing.python_tests and not nothing.docs_check


def test_docs_check_keeps_the_scope_it_was_asked_for(tmp_path: Path) -> None:
    named = Selection(docs_check=["a.py"])
    range_ = Selection(docs_check=["a.py"], docs_scope=["--base", "dev~3"])
    worktree = Selection(docs_check=["a.py"], docs_scope=[])

    assert commands(named, tmp_path)[0][1][2:] == ["scripts.docs_site.check", "a.py"]
    assert commands(range_, tmp_path)[0][1][2:] == ["scripts.docs_site.check", "--base", "dev~3"]
    assert commands(worktree, tmp_path)[0][1][2:] == ["scripts.docs_site.check"]


def test_python_tests_that_run_node_are_recognised(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text("subprocess.run(['node', '-e', ''])\n")
    (tmp_path / "tests/test_b.py").write_text("import _asar_helper\n")
    (tmp_path / "tests/_asar_helper.py").write_text("x = '@electron/asar'\n")
    (tmp_path / "tests/test_c.py").write_text("assert True\n")
    assert affected_tests.needs_node(tmp_path, "tests/test_a.py")
    assert affected_tests.needs_node(tmp_path, "tests/test_b.py")
    assert not affected_tests.needs_node(tmp_path, "tests/test_c.py")


def test_node_tests_are_left_out_and_counted_when_node_is_unavailable(tmp_path, monkeypatch, capsys):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text("x = 'node_modules'\n")
    (tmp_path / "tests/test_c.py").write_text("assert True\n")
    monkeypatch.setattr(affected_tests, "prepare_web", lambda root: "node is not on PATH")
    ran = []
    monkeypatch.setattr(affected_tests, "_stream", lambda command, directory: (ran.append(command), (0, []))[1])
    planned = [(".", ["python", "-m", "pytest", "tests/test_a.py", "tests/test_c.py"])]
    assert affected_tests.run_planned(planned, tmp_path) == affected_tests.EXIT_UNAVAILABLE
    assert ran == [["python", "-m", "pytest", "tests/test_c.py"]]
    assert "COULD NOT RUN 1 Python test files" in capsys.readouterr().err
