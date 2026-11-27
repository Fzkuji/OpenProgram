"""Building sealed versions from a real Git repository with stubbed build steps."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

import pytest

from openprogram.operations.self_update.versions import build as builder
from openprogram.host._platform import runtime_layout as paths
from openprogram.operations.self_update.versions import pointer, store
from openprogram.host._platform.runtime_layout import Layout
from scripts.docs_site import generate_reference
from tests.support.sealed_versions import REPOSITORY

pytestmark = pytest.mark.skipif(os.name == "nt", reason="build stubs are POSIX shell")
MINOR = f"{sys.version_info.major}.{sys.version_info.minor}"
ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"]}


def git(repo, *arguments):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *arguments],
        cwd=repo, env=ENV, capture_output=True, text=True, check=True,
    ).stdout.strip()


FAKE_BASE = f"""#!/usr/bin/env bash
set -eu
echo "base $*" >> "$OPENPROGRAM_TEST_LOG"
echo "secret=${{OPENPROGRAM_TEST_SECRET:-absent}}" >> "$OPENPROGRAM_TEST_LOG"
[ -z "${{FAIL_BASE:-}}" ] || exit 3
dest="$2"
mkdir -p "$dest/bin"
ln -s "{sys.base_prefix}" "$dest/python"
ln -s ../python/bin/python3 "$dest/bin/python"
printf '#!/bin/sh\\necho "npm $*" >> "$OPENPROGRAM_TEST_LOG"\\nmkdir -p node_modules apps/web/node_modules\\necho m > node_modules/m\\n' > "$dest/bin/npm"
chmod +x "$dest/bin/npm"
cat > "$dest/bin/verify-product-runtime.py" <<'PY'
import os, pathlib, sys
pathlib.Path(os.environ["OPENPROGRAM_TEST_LOG"]).open("a").write("verify " + " ".join(sys.argv[1:]) + " prefix=" + sys.prefix + "\\n")
sys.exit(int(pathlib.Path(sys.argv[1], "fail-verify").exists() or os.environ.get("FAIL_VERIFY", "0")))
PY
"""
FAKE_STAGE = """#!/usr/bin/env bash
set -eu
echo "stage $*" >> "$OPENPROGRAM_TEST_LOG"
echo "docs-python=$OPENPROGRAM_DOCS_PYTHON" >> "$OPENPROGRAM_TEST_LOG"
root="$(cd "$(dirname "$0")/../.." && pwd)"
target="$root/openprogram/interfaces/server/_frontend"
mkdir -p "$target/tui"
echo "built from $(git -C "$root" rev-parse HEAD)" > "$target/index.html"
echo "tui" > "$target/tui/index.cjs"
"""


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "build.log"
    path.write_text("")
    monkeypatch.setenv("OPENPROGRAM_TEST_LOG", str(path))
    original = builder._PASSED_THROUGH
    monkeypatch.setattr(builder, "_PASSED_THROUGH", (*original, "OPENPROGRAM_TEST_LOG", "FAIL_BASE", "FAIL_VERIFY"))
    return path


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    (root / "openprogram" / "interfaces" / "cli").mkdir(parents=True)
    (root / "scripts/release").mkdir(parents=True)
    (root / "apps/desktop/main").mkdir(parents=True)
    (root / "apps/web").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "tests").mkdir()
    files = {
        "openprogram/__init__.py": "VALUE = 'one'\n",
        "openprogram/module.py": "number = 1\n",
        "openprogram/interfaces/__init__.py": "",
        "openprogram/interfaces/cli/__init__.py": "",
        "openprogram/interfaces/cli/parser.py": "HELP = 'first'\n",
        "pyproject.toml": '[project]\nname = "openprogram"\nversion = "9.9.9"\n'
                          'dependencies = ["a"]\n[project.scripts]\nopenprogram = "openprogram:main"\n',
        "uv.lock": "lock one\n",
        "package-lock.json": "{}\n",
        "scripts/release/product-runtime.json": json.dumps({"python": f"{MINOR}.1", "uv": "0"}) + "\n",
        "scripts/release/build-base.sh": FAKE_BASE,
        "scripts/release/stage-release-assets.sh": FAKE_STAGE,
        "apps/desktop/main.js": "exports.start = () => {};\n",
        "apps/desktop/main/instance.js": "// main process\n",
        "apps/desktop/renderer.js": "// not part of a version\n",
        "apps/web/page.tsx": "export default 1\n",
        "docs/index.md": "# docs\n",
        "tests/test_x.py": "",
    }
    for name, content in files.items():
        (root / name).write_text(content)
    (root / ".gitignore").write_text("node_modules/\nopenprogram/interfaces/server/_frontend/\n")
    git(root, "init", "-q", "--initial-branch=local")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "first commit")
    return root


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "state" / "runtime"
    root.mkdir(parents=True)
    return Layout(root)


def commit_change(repo, path, content, message="change"):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def build(layout, repo, update="u1", parent=None, **options):
    head = git(repo, "rev-parse", "HEAD")
    return builder.build_version(layout, builder.BuildRequest(update, head, worktree=repo, parent=parent, **options))


def venv_run(layout, version, code):
    return subprocess.run(
        [str(layout.version(version) / "venv/bin/python"), "-I", "-B", "-c", code],
        capture_output=True, text=True, timeout=60,
    )


def test_a_first_build_seals_a_working_version(layout, repo, log):
    head = git(repo, "rev-parse", "HEAD")
    result = build(layout, repo, built_by="controller-x")
    assert result.new_base and result.frontend_rebuilt
    assert result.version.endswith("-" + head[:12])
    manifest = store.read_manifest(layout, result.version)
    assert manifest["commit"] == head and manifest["base"] == result.base
    assert manifest["built_by"] == "controller-x" and manifest["parent"] is None
    assert manifest["summary"] == "first commit" and manifest["destructive_migration"] is None
    assert manifest["frontend_sha"]
    assert store.changed_files(result.path, result.base) == []
    assert (layout.frontends / f"{manifest['frontend_sha']}.zip").is_file()

    done = venv_run(layout, result.version, (
        "import sys, importlib.metadata as m, openprogram, openprogram.module as x;"
        "print(sys.prefix); print(openprogram.VALUE, x.number); print(m.version('openprogram'));"
        "print(x.__cached__); print(x.__spec__.origin)"
    ))
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[1] == "one 1" and lines[2] == "9.9.9"
    assert Path(lines[4]).is_relative_to(result.path), "tracebacks must name the final path"


def test_venv_names_the_final_base_and_nothing_is_left_in_staging(layout, repo, log):
    result = build(layout, repo)
    config = (result.path / "venv/pyvenv.cfg").read_text()
    assert ".staging" not in config
    assert str(layout.base(result.base)) in config
    assert "include-system-site-packages = true" in config
    assert ".staging" not in os.readlink(result.path / "venv/bin/python")
    assert not [p.name for p in layout.versions.iterdir() if p.name.startswith(".")]
    assert not [p.name for p in layout.bases.iterdir() if p.name.startswith(".")]


def test_version_contents_match_the_commit(layout, repo, log):
    result = build(layout, repo)
    site = paths.site_packages(result.path, result.base)
    assert (site / "openprogram/_build_revision.txt").read_text().strip() == git(repo, "rev-parse", "HEAD")
    assert (site / "pyproject.toml").is_file()
    assert sorted(p.name for p in (result.path / "desktop").iterdir()) == ["main", "main.js"]
    assert (result.path / "desktop/main/instance.js").is_file()
    assert (site / "openprogram/__pycache__").is_dir()
    pyc = next((site / "openprogram/__pycache__").glob("module.*.pyc")).read_bytes()
    assert int.from_bytes(pyc[4:8], "little") == 0b01, "unchecked-hash bytecode"
    entry_points = next(site.glob("openprogram-*.dist-info")) / "entry_points.txt"
    assert "openprogram = openprogram:main" in entry_points.read_text()


def test_the_seal_is_what_the_launcher_will_check(layout, repo, log):
    result = build(layout, repo)
    recorded = store.read_files_tsv(result.path)
    assert any(name.endswith("_build_revision.txt") for name in recorded)
    assert "venv/pyvenv.cfg" in recorded


def test_build_scripts_never_see_the_owners_secrets(layout, repo, log, monkeypatch):
    monkeypatch.setenv("OPENPROGRAM_TEST_SECRET", "sk-live-value")
    build(layout, repo)
    text = log.read_text()
    assert "secret=absent" in text
    assert "sk-live-value" not in text


def test_a_python_only_change_reuses_base_and_bundle(layout, repo, log):
    first = build(layout, repo)
    log.write_text("")
    commit_change(repo, "openprogram/module.py", "number = 2\n")
    second = build(layout, repo, update="u2", parent=first.version)
    assert not second.new_base and not second.frontend_rebuilt
    assert log.read_text() == ""
    assert second.base == first.base and second.components == ("python",)
    assert store.read_manifest(layout, second.version)["parent"] == first.version
    assert venv_run(layout, second.version, "import openprogram.module as x; print(x.number)").stdout.strip() == "2"
    assert store.read_manifest(layout, second.version)["frontend_sha"] == store.read_manifest(layout, first.version)["frontend_sha"]
    assert len(list(layout.frontends.iterdir())) == 1
    assert venv_run(layout, first.version, "import openprogram.module as x; print(x.number)").stdout.strip() == "1"


def test_a_desktop_only_change_clones_the_bundle(layout, repo, log):
    first = build(layout, repo)
    commit_change(repo, "apps/desktop/main.js", "// new\n")
    second = build(layout, repo, update="u2", parent=first.version)
    assert not second.frontend_rebuilt and second.components == ("desktop",)
    assert (second.path / "desktop/main.js").read_text() == "// new\n"


@pytest.mark.parametrize("path", ["apps/web/page.tsx", "docs/index.md", "apps/cli/index.ts", "openprogram/interfaces/cli/parser.py"])
def test_a_frontend_input_rebuilds_the_bundle_from_the_toolchain(layout, repo, log, path):
    first = build(layout, repo)
    log.write_text("")
    commit_change(repo, path, "// changed\n" if not path.endswith(".py") else "HELP = 'second'\n")
    second = build(layout, repo, update="u2", parent=first.version)
    assert second.frontend_rebuilt and not second.new_base
    text = log.read_text()
    toolchain = next(p for p in layout.toolchain.iterdir() if p.name not in ("build-home", "cache"))
    assert f"stage --node-modules {toolchain}" in text
    assert f"docs-python={layout.staging('u2')}/venv/bin/python" in text


def test_the_toolchain_id_ignores_only_our_own_version_numbers():
    def lock(own="1.0.1", dependency="2.0.0", workspace="1.0.1"):
        return json.dumps({
            "name": "workspace", "version": own, "lockfileVersion": 3, "packages": {
                "": {"name": "workspace", "version": own},
                "apps/desktop": {"name": "openprogram-desktop", "version": workspace},
                "node_modules/left-pad": {"version": dependency},
                "apps/web/node_modules/left-pad": {"version": dependency},
            },
        }).encode()

    assert builder.toolchain_id(lock()) == builder.toolchain_id(lock(own="1.0.2", workspace="1.0.2"))
    assert builder.toolchain_id(lock()) != builder.toolchain_id(lock(dependency="2.0.1"))
    assert builder.toolchain_id(b"not json") == builder.toolchain_id(b"not json")


def test_the_toolchain_is_fetched_once_per_lock_and_moved_out_of_the_worktree(layout, repo, log):
    first = build(layout, repo)
    text = log.read_text()
    assert text.count("npm ci --ignore-scripts") == 1
    toolchain = next(p for p in layout.toolchain.iterdir() if p.name not in ("build-home", "cache"))
    assert (toolchain / "node_modules/m").is_file()
    assert (toolchain / "apps/web/node_modules").is_dir()
    assert not (repo / "node_modules").exists()
    log.write_text("")
    commit_change(repo, "apps/web/page.tsx", "// 2\n")
    build(layout, repo, update="u2", parent=first.version)
    assert "npm ci" not in log.read_text()
    log.write_text("")
    commit_change(repo, "package-lock.json", '{"changed": true}\n')
    build(layout, repo, update="u3", parent=first.version)
    assert log.read_text().count("npm ci") == 1


def test_a_changed_lock_builds_a_new_base_cloned_from_the_current(layout, repo, log):
    first = build(layout, repo)
    log.write_text("")
    commit_change(repo, "uv.lock", "lock two\n")
    second = build(layout, repo, update="u2", parent=first.version)
    assert second.new_base and second.base != first.base
    assert f"--clone-from {layout.base(first.base)}" in log.read_text()
    assert (layout.base(first.base)).is_dir() and (layout.base(second.base)).is_dir()
    assert "verify" in log.read_text()


def test_verification_runs_only_for_a_new_base_and_through_the_venv(layout, repo, log):
    first = build(layout, repo)
    text = log.read_text()
    assert "--probe-only --openprogram-version 9.9.9 --tui" in text
    assert "/.staging-u1/venv" in text
    log.write_text("")
    commit_change(repo, "openprogram/module.py", "number = 3\n")
    build(layout, repo, update="u2", parent=first.version)
    assert "verify" not in log.read_text()


def test_a_failed_base_build_leaves_nothing(layout, repo, log, monkeypatch):
    monkeypatch.setenv("FAIL_BASE", "1")
    with pytest.raises(builder.BuildError, match="building the base failed"):
        build(layout, repo)
    assert list(layout.versions.iterdir()) == [] and list(layout.bases.iterdir()) == []


def test_a_failed_verification_does_not_install_the_base(layout, repo, log, monkeypatch):
    monkeypatch.setenv("FAIL_VERIFY", "1")
    with pytest.raises(builder.BuildError, match="verifying the base failed"):
        build(layout, repo)
    assert list(layout.versions.iterdir()) == [] and list(layout.bases.iterdir()) == []


def test_a_syntax_error_stops_the_build_and_keeps_the_worktree(layout, repo, log):
    commit_change(repo, "openprogram/module.py", "def broken(:\n")
    with pytest.raises(builder.BuildError, match="compiling the package failed"):
        build(layout, repo)
    assert (repo / "openprogram/module.py").is_file()
    assert [p for p in layout.versions.iterdir()] == []


def test_not_enough_disk_says_both_sizes(layout, repo, log, monkeypatch):
    monkeypatch.setattr(builder.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1024 ** 3})())
    with pytest.raises(builder.BuildError, match=r"needs [\d.]+ GB and 1\.0 GB is free, [\d.]+ GB short"):
        build(layout, repo)


def test_a_disk_just_under_the_need_names_the_difference(layout, repo, log, monkeypatch):
    required = builder.required_space(layout, new_base=True)
    free = required - 38 * 1024 ** 2
    monkeypatch.setattr(builder.shutil, "disk_usage", lambda _p: type("U", (), {"free": free})())
    with pytest.raises(builder.BuildError, match=r"is free, 38 MB short"):
        builder.check_space(layout, new_base=True)


def test_the_shortfall_is_never_printed_as_nothing():
    from openprogram.host._platform.runtime_layout import disk_shortfall

    assert disk_shortfall(5 * 1024 ** 3, 5 * 1024 ** 3 - 1) == "less than 1 MB"
    assert disk_shortfall(5 * 1024 ** 3, 5 * 1024 ** 3 - 300 * 1024 ** 2) == "300 MB"
    assert disk_shortfall(5 * 1024 ** 3, 2 * 1024 ** 3) == "3.0 GB"


def test_a_reused_base_needs_less_space(layout):
    assert builder.required_space(layout, new_base=False) + builder.NEW_BASE_BYTES == builder.required_space(layout, new_base=True)


def test_the_trial_copy_is_part_of_the_space_estimate(layout):
    (layout.root.parent / "usage.db").write_bytes(b"x" * 1024)
    (layout.root / "skip.db").write_bytes(b"x" * 4096)
    assert builder._trial_copy_bytes(layout.root.parent) == 1024


def test_the_space_estimate_counts_the_conversations_the_trial_copies(layout):
    chat = layout.root.parent / "projects" / "default" / "chats" / "chat"
    chat.mkdir(parents=True)
    (chat / "main.jsonl").write_bytes(b"x" * 2048)
    assert builder._trial_copy_bytes(layout.root.parent) == 2048


def test_a_python_minor_the_launcher_cannot_run_is_refused(layout, repo, log):
    (layout.root / "identity").mkdir()
    (layout.root / "identity/python-minor").write_text("3.9\n")
    with pytest.raises(builder.BuildError, match="new signed release"):
        build(layout, repo)


def test_a_commit_outside_the_shells_range_is_refused(layout, repo, log):
    (layout.root / "identity").mkdir()
    (layout.root / "identity/shell-compat.json").write_text('{"min": 1, "max": 2}')
    commit_change(repo, "apps/desktop/shell/compat.json", '{"shell_compat": 3}\n')
    with pytest.raises(builder.BuildError, match="new signed shell release"):
        build(layout, repo)


def test_a_commit_inside_the_range_records_its_contract(layout, repo, log):
    (layout.root / "identity").mkdir()
    (layout.root / "identity/shell-compat.json").write_text('{"min": 1, "max": 2}')
    commit_change(repo, "apps/desktop/shell/compat.json", '{"shell_compat": 2}\n')
    result = build(layout, repo)
    assert store.read_manifest(layout, result.version)["shell_compat"] == 2


def test_a_worktree_at_another_commit_is_refused(layout, repo, log):
    head = git(repo, "rev-parse", "HEAD")
    commit_change(repo, "openprogram/module.py", "number = 2\n")
    with pytest.raises(builder.BuildError, match="not at"):
        builder.build_version(layout, builder.BuildRequest("u1", head, worktree=repo))


def test_uncommitted_changes_are_refused(layout, repo, log):
    (repo / "openprogram/module.py").write_text("dirty = 1\n")
    with pytest.raises(builder.BuildError, match="uncommitted"):
        build(layout, repo)


def test_a_commit_with_no_worktree_builds_in_a_temporary_one(layout, repo, log):
    head = git(repo, "rev-parse", "HEAD")
    result = builder.build_version(layout, builder.BuildRequest("u9", head, repository=repo))
    assert store.read_manifest(layout, result.version)["commit"] == head
    assert not list(layout.work.iterdir())
    assert "u9" not in git(repo, "worktree", "list")


def test_the_destructive_trailer_is_recorded(layout, repo, log):
    first = build(layout, repo)
    commit_change(repo, "openprogram/module.py", "number = 2\n", "drop\n\nDestructive-Migration: drop-old-table")
    second = build(layout, repo, update="u2", parent=first.version)
    assert store.read_manifest(layout, second.version)["destructive_migration"] == "drop-old-table"


def test_a_rerun_after_a_crash_replaces_old_staging(layout, repo, log):
    layout.versions.mkdir(parents=True)
    leftover = layout.staging("u1")
    leftover.mkdir()
    (leftover / "junk").write_text("x")
    result = build(layout, repo)
    assert not leftover.exists() and result.path.is_dir()


def test_a_built_version_is_retained_by_the_pointer_protocol(layout, repo, log):
    result = build(layout, repo)
    created = pointer.create(layout, pointer.Pointer(generation=1, version=result.version, base=result.base, writer="installer"))
    assert pointer.read(layout) == created


def test_component_limited_build_matches_the_generator_inputs():
    source = Path(generate_reference.__file__).read_text(encoding="utf-8")
    assert "openprogram.interfaces.cli" in source
    assert "openprogram.state.settings" in source
    assert "openprogram/models/providers/" in source
    assert builder.frontend_changes(["openprogram/runtime/agent/x.py"]) is False
    assert builder.frontend_changes(["openprogram/state/settings.py"]) is True
    assert builder.frontend_changes(["uv.lock"]) is True
    assert builder.frontend_changes(None) is True
    assert builder.frontend_changes(["tests/x.py", "README.md"]) is False


# ---- the real shell recipes, with stand-ins for uv, node and npm -------------


# A stand-in Python still answers the build's wheel-target question with the real script.
_RUN_TARGET_SCRIPT = f'case "$*" in *runtime-wheels.py*) exec "{sys.executable}" "$@" ;; esac\n'
_RUN_TARGET_PRINTF = _RUN_TARGET_SCRIPT.replace("\n", "\\n")


def _fake_tools(tmp_path, log):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv").write_text(f"""#!/usr/bin/env bash
echo "uv $*" >> "{log}"
if [ "$1" = --version ]; then echo "uv 0.11.16"; exit 0; fi
if [ "$1 $2" = "python install" ]; then
  dir="$UV_PYTHON_INSTALL_DIR/cpython-x"
  mkdir -p "$dir/bin"
  printf '#!/bin/sh\\n{_RUN_TARGET_PRINTF}echo "python $*" >> "{log}"\\nmkdir -p "$PLAYWRIGHT_BROWSERS_PATH/chromium" 2>/dev/null || true\\n' > "$dir/bin/python3"
  chmod +x "$dir/bin/python3"
  ln -s "$dir" "$UV_PYTHON_INSTALL_DIR/cpython-alias"
  exit 0
fi
if [ "$1 $2" = "python find" ]; then echo "$UV_PYTHON_INSTALL_DIR/cpython-alias/bin/python3"; exit 0; fi
if [ "$1" = export ]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --output-file ]; then out="$2"; fi
    shift
  done
  printf 'first==1 \\\\\\n    --hash=sha256:aa\\nmdit-py-plugins==0.6 \\\\\\n    --hash=sha256:bb \\\\\\n    --hash=sha256:cc\\nother==2\\n' > "$out"
  exit 0
fi
exit 0
""")
    (tools / "node").write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo v22.0.0; fi\n')
    (tools / "npm").write_text(f"""#!/bin/sh
if [ "$1" = --version ]; then echo 10.0.0; exit 0; fi
if [ "$1 $2" = "root -g" ]; then echo "{tmp_path}/global"; exit 0; fi
echo "npm $*" >> "{log}"
""")
    # The pinned Git, served from a small real archive instead of the network.
    git_tree = tmp_path / "git-archive-tree"
    (git_tree / "bin").mkdir(parents=True)
    (git_tree / "bin/git").write_text("#!/bin/sh\n")
    (tmp_path / "global/git.tar.gz").parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tmp_path / "global/git.tar.gz", "w:gz") as archive:
        archive.add(git_tree / "bin/git", arcname="bin/git")
    (tools / "curl").write_text(f"""#!/bin/sh
for argument in "$@"; do last="$argument"; done
echo "curl $last" >> "{log}"
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then cp "{tmp_path}/global/git.tar.gz" "$2"; fi
  shift
done
""")
    for tool in tools.iterdir():
        tool.chmod(0o755)
    (tmp_path / "global/npm/bin").mkdir(parents=True)
    (tmp_path / "global/npm/bin/npm-cli.js").write_text("// npm\n")
    return tools


@pytest.fixture
def recipe(tmp_path):
    """The real scripts in a small repository with stub tools on PATH."""
    root = tmp_path / "recipe"
    (root / "scripts/release").mkdir(parents=True)
    for name in ("build-base.sh", "runtime-wheels.py", "stage-release-assets.sh", "verify-product-runtime.py",
                 "smoke-ink-tui-pty.py", "share-runtime-node.py"):
        shutil.copy2(REPOSITORY / "scripts/release" / name, root / "scripts/release" / name)
    shutil.copy2(REPOSITORY / "scripts/release/product-runtime.json", root / "scripts/release/product-runtime.json")
    (root / "uv.lock").write_text('[[package]]\nname = "playwright"\nversion = "1.50.0"\n')
    log = tmp_path / "recipe.log"
    log.write_text("")
    tools = _fake_tools(tmp_path, log)
    digest = hashlib.sha256((tmp_path / "global/git.tar.gz").read_bytes()).hexdigest()
    product = root / "scripts/release/product-runtime.json"
    product.write_text(re.sub(r'(^    "(?:macOS|ubuntu|windows)-\w+": ")\w+', rf"\g<1>{digest}", product.read_text(), flags=re.M))
    environment = {
        "PATH": f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin",
        "OPENPROGRAM_UV_BIN": str(tools / "uv"),
        "HOME": str(tmp_path / "home"),
    }
    return root, log, environment


def run_recipe(root, environment, *arguments):
    return subprocess.run(
        ["bash", str(root / "scripts/release/build-base.sh"), *arguments],
        env=environment, capture_output=True, text=True, timeout=60,
    )


def test_recipe_builds_a_complete_base(recipe, tmp_path):
    root, log, environment = recipe
    dest = tmp_path / "base"
    done = run_recipe(root, environment, "--dest", str(dest))
    assert done.returncode == 0, done.stderr
    assert (dest / "python/bin/python3").is_file() and not (dest / ".python-install").exists()
    assert os.readlink(dest / "bin/python") == "../python/bin/python3"
    assert os.readlink(dest / "bin/npm") == "../lib/node_modules/npm/bin/npm-cli.js"
    assert (dest / "lib/node_modules/npm/bin/npm-cli.js").is_file()
    assert (dest / "bin/node").is_file() and (dest / "bin/uv").is_file()
    assert (dest / "product-uv.lock").is_file() and (dest / "bin/verify-product-runtime.py").is_file()
    assert (dest / "assets/playwright").exists() or "playwright install chromium" in log.read_text()
    pins = json.loads((dest / "base.json").read_text())
    assert (pins["playwright"], pins["node"], pins["npm"], pins["uv"]) == ("1.50.0", "v22.0.0", "10.0.0", "0.11.16")
    text = log.read_text()
    assert "pip install" in text and "--require-hashes" in text and "python install" in text
    assert (dest / "docs-requirements.txt").read_text().splitlines() == [
        "mdit-py-plugins==0.6 \\", "    --hash=sha256:bb \\", "    --hash=sha256:cc",
    ]


def test_recipe_clones_unchanged_layers(recipe, tmp_path):
    root, log, environment = recipe
    previous = tmp_path / "previous"
    assert run_recipe(root, environment, "--dest", str(previous)).returncode == 0
    (previous / "python/marker").write_text("from previous")
    (previous / "assets/playwright").mkdir(parents=True, exist_ok=True)
    (previous / "assets/playwright/marker").write_text("browser")
    (previous / "git/bin").mkdir(parents=True, exist_ok=True)
    (previous / "git/bin/marker").write_text("git")
    log.write_text("")
    dest = tmp_path / "next"
    done = run_recipe(root, environment, "--dest", str(dest), "--clone-from", str(previous))
    assert done.returncode == 0, done.stderr
    text = log.read_text()
    assert "python install" not in text and "playwright install" not in text
    assert "pip sync" in text, "stale packages from the clone must go"
    assert (dest / "python/marker").read_text() == "from previous"
    assert (dest / "assets/playwright/marker").read_text() == "browser"
    assert (dest / "git/bin/marker").read_text() == "git"
    assert not (dest / "python/marker").is_symlink()


def test_recipe_reinstalls_a_layer_whose_pin_changed(recipe, tmp_path):
    root, log, environment = recipe
    previous = tmp_path / "previous"
    assert run_recipe(root, environment, "--dest", str(previous)).returncode == 0
    (root / "uv.lock").write_text('[[package]]\nname = "playwright"\nversion = "1.51.0"\n')
    log.write_text("")
    dest = tmp_path / "next"
    assert run_recipe(root, environment, "--dest", str(dest), "--clone-from", str(previous)).returncode == 0
    assert "playwright install chromium" in log.read_text()
    assert "python install" not in log.read_text()


def test_recipe_refuses_the_wrong_uv(recipe, tmp_path):
    root, _log, environment = recipe
    product = root / "scripts/release/product-runtime.json"
    product.write_text(product.read_text().replace("0.11.16", "9.9.9"))
    done = run_recipe(root, environment, "--dest", str(tmp_path / "base"))
    assert done.returncode == 1 and "uv version mismatch" in done.stderr


def test_recipe_refuses_an_existing_destination(recipe, tmp_path):
    root, _log, environment = recipe
    (tmp_path / "base").mkdir()
    assert run_recipe(root, environment, "--dest", str(tmp_path / "base")).returncode == 1


def test_recipe_fills_an_embedded_runtime_with_the_same_commands(recipe, tmp_path):
    root, log, environment = recipe
    runtime = tmp_path / "runtime"
    (runtime / "assets/playwright").mkdir(parents=True)
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\n{_RUN_TARGET_SCRIPT}echo "python $PLAYWRIGHT_BROWSERS_PATH $*" >> "{log}"\n')
    python.chmod(0o755)
    done = run_recipe(root, environment, "--embedded-runtime", str(runtime), "--python", str(python))
    assert done.returncode == 0, done.stderr
    text = log.read_text()
    assert "--frozen --no-dev --extra all --extra search --no-emit-project" in text
    assert f"pip install --python {python} --strict --break-system-packages --require-hashes" in text
    # Wheels only, for the oldest supported system rather than this machine.
    assert "--python-platform" in text and "--only-binary :all:" in text
    assert f"python {runtime}/assets/playwright -m playwright install chromium" in text
    assert "mdit-py-plugins" not in (runtime / "product-requirements.txt").read_text().split("other")[1]
    assert (runtime / "git/bin/git").is_file() and not (tmp_path / ".git-archive.tar.gz").exists()


@pytest.mark.parametrize("machine,target,platform_name", [
    ("x86_64", "linux-x86_64", "ubuntu-x64"), ("aarch64", "linux-arm64", "ubuntu-arm64"),
])
def test_recipe_fetches_the_pinned_git_on_linux(recipe, tmp_path, machine, target, platform_name):
    root, log, environment = recipe
    tools = tmp_path / "tools"
    (tools / "uname").write_text(f'#!/bin/sh\nif [ "$1" = -s ]; then echo Linux; else echo {machine}; fi\n')
    (tools / "uname").chmod(0o755)
    runtime = tmp_path / "runtime"
    python = tmp_path / "python"
    # The Git is the packaged Python's: it answers for itself, whatever the machine is.
    python.write_text(f'#!/bin/sh\ncase "$*" in *"runtime-wheels.py target") echo {target}; exit 0 ;; esac\n'
                      f"{_RUN_TARGET_SCRIPT}")
    python.chmod(0o755)
    (runtime / "assets/playwright").mkdir(parents=True)
    done = run_recipe(root, environment, "--embedded-runtime", str(runtime), "--python", str(python))
    assert done.returncode == 0, done.stderr
    assert f"-{platform_name}.tar.gz" in log.read_text()
    assert (runtime / "git/bin/git").is_file()


def test_pinned_linux_git_has_real_checksums():
    pins = json.loads((REPOSITORY / "scripts/release/product-runtime.json").read_text())["gitSha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", pins["ubuntu-x64"])
    assert re.fullmatch(r"[0-9a-f]{64}", pins["ubuntu-arm64"])
    assert pins["ubuntu-x64"] != pins["ubuntu-arm64"]


def test_recipe_refuses_a_git_archive_with_the_wrong_checksum(recipe, tmp_path):
    root, _log, environment = recipe
    product = root / "scripts/release/product-runtime.json"
    product.write_text(re.sub(r'(^    "(?:macOS|ubuntu)-\w+": ")\w+', r"\g<1>" + "0" * 64, product.read_text(), flags=re.M))
    dest = tmp_path / "base"
    done = run_recipe(root, environment, "--dest", str(dest))
    assert done.returncode == 1 and "Git archive checksum mismatch" in done.stderr


def test_recipe_fetches_git_when_the_previous_base_has_none(recipe, tmp_path):
    """A Linux base from before Git was bundled records the pin but holds no Git."""
    root, log, environment = recipe
    previous = tmp_path / "previous"
    assert run_recipe(root, environment, "--dest", str(previous)).returncode == 0
    shutil.rmtree(previous / "git")
    log.write_text("")
    dest = tmp_path / "next"
    done = run_recipe(root, environment, "--dest", str(dest), "--clone-from", str(previous))
    assert done.returncode == 0, done.stderr
    assert "curl " in log.read_text() and (dest / "git/bin/git").is_file()


@pytest.fixture
def staging_repo(tmp_path):
    root = tmp_path / "stage"
    (root / "scripts/release/office").mkdir(parents=True)
    (root / "apps/web").mkdir(parents=True)
    (root / "apps/cli/dist").mkdir(parents=True)
    (root / "apps/cli/dist/index-standalone.cjs").write_text("// tui\n")
    (root / "docs/_site").mkdir(parents=True)
    (root / "docs/_site/index.html").write_text("docs")
    shutil.copy2(REPOSITORY / "scripts/release/stage-release-assets.sh", root / "scripts/release/stage-release-assets.sh")
    tools = tmp_path / "stage-tools"
    tools.mkdir()
    (tools / "npm").write_text(f"""#!/bin/sh
echo "npm $*" >> "{tmp_path}/stage.log"
[ "$1" = run ] || exit 0
mkdir -p "{root}/apps/web/out"
printf '<body><div id="sidebar"></div></body>' > "{root}/apps/web/out/chat.html"
echo index > "{root}/apps/web/out/index.html"
""")
    (tools / "npm").chmod(0o755)
    return root, tools, tmp_path / "stage.log"


def test_node_modules_mode_clones_and_skips_npm_ci(staging_repo, tmp_path):
    root, tools, log = staging_repo
    toolchain = tmp_path / "toolchain"
    (toolchain / "node_modules/pkg").mkdir(parents=True)
    (toolchain / "node_modules/pkg/index.js").write_text("x")
    (toolchain / "apps/web/node_modules/nested").mkdir(parents=True)
    docs_python = tmp_path / "docs-python"
    docs_python.write_text(f'#!/bin/sh\necho "docs $*" >> "{log}"\n')
    docs_python.chmod(0o755)
    environment = {
        "PATH": f"{tools}:/opt/miniconda3/bin:/usr/bin:/bin",
        "OPENPROGRAM_DOCS_PYTHON": str(docs_python),
    }
    done = subprocess.run(
        ["bash", str(root / "scripts/release/stage-release-assets.sh"), "--node-modules", str(toolchain)],
        env=environment, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    text = log.read_text()
    assert "npm ci" not in text and "docs -m scripts.docs_site.build" in text
    assert (root / "node_modules/pkg/index.js").read_text() == "x"
    assert not (root / "node_modules").is_symlink() and not (root / "node_modules/pkg/index.js").is_symlink()
    assert (root / "apps/web/node_modules/nested").is_dir()
    bundle = root / "openprogram/interfaces/server/_frontend"
    assert (bundle / "tui/index.cjs").read_text() == "// tui\n"
    assert (bundle / "docs/index.html").is_file()
    assert (toolchain / "node_modules/pkg/index.js").is_file(), "the toolchain keeps its copy"


def test_node_modules_mode_needs_a_real_toolchain(staging_repo, tmp_path):
    root, tools, _log = staging_repo
    done = subprocess.run(
        ["bash", str(root / "scripts/release/stage-release-assets.sh"), "--node-modules", str(tmp_path / "missing")],
        env={"PATH": f"{tools}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 1 and "no node_modules" in done.stderr


def test_a_finished_directory_waits_for_windows_to_let_go_of_it(tmp_path, monkeypatch):
    source, target = tmp_path / "staged", tmp_path / "final"
    source.mkdir()
    refusals, replace = [PermissionError(5, "Access is denied")] * 2, os.replace

    def busy_at_first(old, new):
        if refusals:
            raise refusals.pop()
        replace(old, new)

    monkeypatch.setattr(builder.os, "replace", busy_at_first)
    monkeypatch.setattr(builder.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(builder.os, "name", "nt")
    builder._move_directory(source, target)
    assert target.is_dir() and not source.exists() and not refusals


def test_a_refused_rename_is_not_retried_off_windows(tmp_path, monkeypatch):
    calls = []

    def refuse(old, new):
        calls.append(old)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(builder.os, "replace", refuse)
    monkeypatch.setattr(builder.os, "name", "posix")
    with pytest.raises(PermissionError):
        builder._move_directory(tmp_path / "staged", tmp_path / "final")
    assert len(calls) == 1


def test_a_compiled_dependency_finds_the_rust_toolchain_under_the_real_home(layout, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".rustup").mkdir(parents=True)
    monkeypatch.setattr(builder.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("RUSTUP_HOME", raising=False)
    environment = builder._base_environment(layout)
    assert environment["RUSTUP_HOME"] == str(home / ".rustup")
    assert environment["HOME"] != str(home) and "CARGO_HOME" not in environment
    monkeypatch.setenv("RUSTUP_HOME", "/opt/rustup")
    assert builder._base_environment(layout)["RUSTUP_HOME"] == "/opt/rustup"


def test_windows_workspace_junctions_leave_the_toolchain(tmp_path, monkeypatch):
    modules = tmp_path / "node_modules"
    (modules / "@openprogram" / "cli").mkdir(parents=True)
    (modules / "react").mkdir()
    removed = []
    monkeypatch.setattr(builder.os, "name", "nt")
    monkeypatch.setattr(builder.os.path, "isjunction", lambda entry: Path(entry).name == "cli")
    monkeypatch.setattr(builder.os, "rmdir", lambda entry: removed.append(Path(entry).name))
    builder._drop_junctions(tmp_path)
    assert removed == ["cli"]


def test_the_base_takes_a_node_that_runs_on_its_own(tmp_path):
    root = Path(__file__).resolve().parents[4]
    script = (root / "scripts/release/build-base.sh").read_text(encoding="utf-8")
    start, end = script.index("runs_alone() {"), script.index('node_pin="$(')
    shared, official = tmp_path / "shared", tmp_path / "official"
    for directory, body in ((shared, 'test -e "$(dirname "$0")/libnode.dylib" || exit 1\necho v24.0.0\n'),
                            (official, "echo v22.0.0\n")):
        directory.mkdir()
        (directory / "node").write_text("#!/bin/sh\n" + body)
        (directory / "node").chmod(0o755)
    (shared / "libnode.dylib").write_text("")
    done = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + script[start:end] + 'printf "%s" "$node_bin"'],
        env={"PATH": f"{shared}:{official}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == str(official / "node")


def test_the_build_has_its_own_windows_profile_folders(layout, tmp_path, monkeypatch):
    # Without LOCALAPPDATA PowerShell writes its module cache under the working
    # directory, the worktree, and the next build refuses it as uncommitted.
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    environment = builder._base_environment(layout)
    home = Path(environment["HOME"])
    for name in ("LOCALAPPDATA", "APPDATA"):
        folder = Path(environment[name])
        assert folder.is_dir() and folder.is_relative_to(home)
