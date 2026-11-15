"""The compiled launcher, driven by subprocess against a temporary root."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time

import pytest

from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import pointer, store
from tests.support.sealed_versions import BASE, REPOSITORY, make_layout, make_version, point_at

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("cc") is None, reason="needs a POSIX C compiler")
SOURCE = REPOSITORY / "scripts/release/runtime-launcher.c"


@pytest.fixture(scope="session")
def launcher(tmp_path_factory):
    output = tmp_path_factory.mktemp("launcher") / "launcher"
    subprocess.run(
        ["cc", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
         "-DOPENPROGRAM_LAUNCHER_EXEC", str(SOURCE), "-o", str(output)],
        check=True, capture_output=True, text=True,
    )
    return output


@pytest.fixture
def layout(tmp_path):
    root = make_layout(tmp_path / "state" / "runtime", tools=True)
    make_version(root, "v1")
    make_version(root, "v2")
    make_version(root, "v3")
    return root


def run(launcher, layout, *arguments):
    return subprocess.run(
        [str(launcher), "--root", str(layout.root), *arguments],
        capture_output=True, text=True, timeout=60,
    )


def attempts(layout, version):
    try:
        return layout.attempts(version).read_text().splitlines()
    except FileNotFoundError:
        return []


def seed_attempts(layout, version, count, age=10):
    layout.boot.mkdir(exist_ok=True)
    moment = int(time.time()) - age
    layout.attempts(version).write_text("".join(f"{moment}\n" for _ in range(count)))


def test_it_starts_the_current_version_with_isolated_python(launcher, layout):
    point_at(layout, "v2")
    done = run(launcher, layout, "-m", "openprogram", "worker", "run")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["ran v2", "-I", "-B", "-m", "openprogram", "worker", "run"]
    assert len(attempts(layout, "v2")) == 1
    assert pointer.read(layout).generation == 5


def test_three_recent_starts_return_to_last_good(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE, previous_good="v1", previous_good_base=BASE)
    seed_attempts(layout, "v3", 3)
    done = run(launcher, layout)
    assert done.stdout.startswith("ran v2")
    after = pointer.read(layout)
    assert (after.version, after.writer, after.generation) == ("v2", "launcher", 6)
    assert after.last_good == "v2" and after.previous_good == "v1"
    assert pointer.parse(layout.previous_pointer.read_text()).version == "v3"
    assert "moved from v3 to v2" in layout.recovery_log.read_text()
    assert not layout.lock.exists()


def test_two_starts_are_not_enough_outside_probation(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    seed_attempts(layout, "v3", 2)
    assert run(launcher, layout).stdout.startswith("ran v3")


def test_two_starts_return_while_on_probation(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE, probation="u1", probation_deadline=int(time.time()) + 600)
    seed_attempts(layout, "v3", 2)
    done = run(launcher, layout)
    assert done.stdout.startswith("ran v2")
    after = pointer.read(layout)
    assert after.probation == "" and after.probation_deadline is None


def test_old_starts_do_not_count(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    seed_attempts(layout, "v3", 9, age=700)
    assert run(launcher, layout).stdout.startswith("ran v3")


def test_the_last_good_version_falls_back_to_previous_good(launcher, layout):
    point_at(layout, "v2", last_good="v2", last_good_base=BASE, previous_good="v1", previous_good_base=BASE)
    seed_attempts(layout, "v2", 3)
    assert run(launcher, layout).stdout.startswith("ran v1")
    assert pointer.read(layout).version == "v1"


def test_an_expired_probation_returns(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE, probation="u1", probation_deadline=int(time.time()) - 5)
    assert run(launcher, layout).stdout.startswith("ran v2")
    assert "probation deadline passed" in layout.recovery_log.read_text()


def test_probation_inside_its_deadline_runs_the_candidate(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE, probation="u1", probation_deadline=int(time.time()) + 600)
    assert run(launcher, layout).stdout.startswith("ran v3")


def test_a_missing_version_uses_the_fallback(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    shutil.rmtree(layout.version("v3"))
    assert run(launcher, layout).stdout.startswith("ran v2")


def test_no_fallback_and_no_version_exits_with_a_reason(launcher, layout):
    point_at(layout, "v3")
    shutil.rmtree(layout.version("v3"))
    done = run(launcher, layout)
    assert done.returncode == 127 and "no fallback" in done.stderr


def test_a_failing_version_with_no_fallback_still_starts(launcher, layout):
    point_at(layout, "v3")
    seed_attempts(layout, "v3", 9)
    assert run(launcher, layout).stdout.startswith("ran v3")


def test_a_damaged_pointer_is_restored_from_the_previous(launcher, layout):
    point_at(layout, "v3")
    pointer.compare_and_set(layout, 5, writer="t", version="v2")
    layout.pointer.write_text("garbage\n")
    assert run(launcher, layout).stdout.startswith("ran v3")
    assert pointer.read(layout).version == "v3"


def test_no_pointer_at_all_is_rebuilt_from_the_versions(launcher, layout):
    # No repo here, so `git merge-base` fails for every version; give it one.
    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": os.environ["PATH"]}
    subprocess.run(["git", "init", "-q", "--initial-branch=local", str(layout.repo)], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(layout.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "--allow-empty", "-qm", "m"], check=True, env=env)
    head = subprocess.run(["git", "-C", str(layout.repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True, env=env).stdout.strip()
    make_version(layout, "20270101T000000Z-aaaaaaaaaaaa", commit=head)
    done = run(launcher, layout)
    assert done.stdout.startswith("ran 20270101T000000Z-aaaaaaaaaaaa"), done.stderr
    assert pointer.read(layout).writer == "bootstrap"


def test_a_pending_rollback_is_applied(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    pointer.write_pending_rollback(layout, "v3")
    assert run(launcher, layout).stdout.startswith("ran v2")
    assert not layout.pending_rollback.exists()


def test_a_stale_pending_rollback_is_dropped(launcher, layout):
    point_at(layout, "v2", last_good="v1", last_good_base=BASE)
    pointer.write_pending_rollback(layout, "v3")
    assert run(launcher, layout).stdout.startswith("ran v2")
    assert not layout.pending_rollback.exists()


def test_developer_mode_follows_the_checkout_and_ignores_the_pointer(launcher, layout, tmp_path):
    point_at(layout, "v2")
    python = tmp_path / "dev python"
    python.write_text('#!/bin/sh\necho "dev $PYTHONPATH"\necho "$@"\n')
    python.chmod(0o755)
    layout.developer.write_text('{"checkout": "/src/my checkout", "python": "' + str(python) + '"}')
    done = run(launcher, layout, "-m", "openprogram")
    assert done.stdout.splitlines() == ["dev /src/my checkout", "-m openprogram"]
    assert attempts(layout, "v2") == []


def test_unfinished_seeding_refuses(launcher, layout):
    point_at(layout, "v2")
    layout.seeding.write_text("")
    assert run(launcher, layout).returncode == 65


def test_a_direct_edit_is_captured_and_the_version_starts_clean(launcher, layout):
    point_at(layout, "v2")
    site = paths.site_packages(layout.version("v2"), BASE) / "openprogram"
    (site / "module.py").write_text("value = 'edited'\n")
    done = run(launcher, layout)
    assert done.stdout.startswith("ran v2"), done.stderr
    assert (site / "module.py").read_text() == "value = 1\n"
    saved = next((layout.root / "unapplied").iterdir())
    assert (saved / "files/openprogram/module.py").read_text() == "value = 'edited'\n"
    assert store.changed_files(layout.version("v2"), BASE) == []


def test_an_added_file_in_the_package_is_noticed(launcher, layout):
    point_at(layout, "v2")
    site = paths.site_packages(layout.version("v2"), BASE) / "openprogram"
    (site / "planted.py").write_text("x = 1\n")
    assert run(launcher, layout).stdout.startswith("ran v2")
    assert not (site / "planted.py").exists()


def test_unlisted_pycache_is_ignored(launcher, layout):
    point_at(layout, "v2")
    cache = paths.site_packages(layout.version("v2"), BASE) / "openprogram" / "__pycache__"
    (cache / "fresh.cpython-312.pyc").write_bytes(b"x")
    done = run(launcher, layout)
    assert done.stdout.startswith("ran v2")
    assert "capturing" not in done.stderr
    assert (cache / "fresh.cpython-312.pyc").exists()


def test_a_clean_version_runs_no_capture(launcher, layout):
    point_at(layout, "v2")
    assert "capturing" not in run(launcher, layout).stderr
    assert not (layout.root / "unapplied").exists()


def test_a_failed_capture_starts_the_fallback_and_counts_a_failed_start(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    version = layout.version("v3")
    site = paths.site_packages(version, BASE) / "openprogram"
    (site / "module.py").write_text("value = 'edited'\n")
    (version / "pristine.zip").unlink()
    done = run(launcher, layout)
    assert done.stdout.startswith("ran v2"), done.stderr
    assert pointer.read(layout).version == "v2"
    assert len(attempts(layout, "v3")) == 1
    assert "direct-edit capture failed" in layout.recovery_log.read_text()


def test_it_reads_a_pointer_python_wrote_and_python_reads_what_it_wrote(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    text = layout.pointer.read_text()
    layout.pointer.write_text(text + "future_field=kept\n")
    seed_attempts(layout, "v3", 3)
    run(launcher, layout)
    after = pointer.read(layout)
    assert after.version == "v2" and ("future_field", "kept") in after.extras


def test_a_stale_lock_does_not_block_a_guard_move(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    seed_attempts(layout, "v3", 3)
    layout.lock.write_text("")
    old = time.time() - 30
    os.utime(layout.lock, (old, old))
    assert run(launcher, layout).stdout.startswith("ran v2")
    assert not layout.lock.exists()


def test_root_is_found_from_the_identity_directory(launcher, layout):
    point_at(layout, "v2")
    identity = layout.root / "identity"
    identity.mkdir()
    shutil.copy2(launcher, identity / "openprogram-launcher")
    done = subprocess.run([str(identity / "openprogram-launcher")], capture_output=True, text=True, timeout=60)
    assert done.stdout.startswith("ran v2"), done.stderr


def test_outside_an_identity_directory_it_asks_for_a_root(launcher):
    done = subprocess.run([str(launcher)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 64 and "--root" in done.stderr


def test_a_version_with_an_unknown_base_is_treated_as_changed(launcher, layout):
    point_at(layout, "v3", last_good="v2", last_good_base=BASE)
    pointer.compare_and_set(layout, 5, writer="t", base="nonsense")
    done = run(launcher, layout)
    assert done.stdout.startswith("ran v2"), done.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="the in-process path is macOS only")
def test_macos_runs_the_real_interpreter_in_process(tmp_path):
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    library = Path(sysconfig.get_config_var("LIBDIR")) / f"libpython{minor}.dylib"
    if not library.is_file():
        pytest.skip("this Python has no shared libpython")
    output = tmp_path / "embedded"
    subprocess.run(
        ["cc", "-O1", f"-I{sysconfig.get_paths()['include']}", str(SOURCE), "-o", str(output)],
        check=True, capture_output=True, text=True,
    )
    base_id = f"py{minor}-fedcba9876543210"
    root = make_layout(tmp_path / "state" / "runtime", tools=True)
    python_dir = root.base(base_id) / "python"
    python_dir.parent.mkdir(parents=True)
    python_dir.symlink_to(sys.base_prefix)
    staging = root.versions / ".staging-real"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(staging / "venv")],
        check=True, capture_output=True, text=True,
    )
    site = paths.site_packages(staging, base_id)
    (site / "openprogram").mkdir(parents=True)
    (site / "openprogram" / "__init__.py").write_text("LOADED = 'from the sealed version'\n")
    paths.frontend_bundle(site).mkdir(parents=True)
    (paths.frontend_bundle(site) / "index.html").write_text("x")
    (staging / "desktop").mkdir()
    (staging / "desktop" / "main.js").write_text("")
    store.seal(root, staging, {
        "id": "real", "commit": "a" * 40, "base": base_id, "shell_compat": 1,
        "built_by": "test", "built_at": "now", "components": [], "parent": None,
    })
    pointer.create(root, pointer.Pointer(generation=1, version="real", base=base_id, writer="test"))
    code = (
        "import sys, openprogram;"
        "print(sys.prefix); print(sys.executable); print(sys.flags.isolated, sys.flags.dont_write_bytecode);"
        "print(openprogram.LOADED)"
    )
    done = subprocess.run([str(output), "--root", str(root.root), "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert Path(lines[0]).name == "venv" and Path(lines[0]).parent.name == "real"
    assert lines[1].endswith("venv/bin/python")
    assert lines[2] == "1 1"
    assert lines[3] == "from the sealed version"


def _install_bin(layout):
    (layout.root / "bin").mkdir(exist_ok=True)
    wrapper = layout.root / "bin" / "openprogram"
    shutil.copy2(REPOSITORY / "scripts/release/bootstrap/bin-openprogram.sh", wrapper)
    return wrapper


def test_the_bin_wrapper_sends_versions_to_the_out_of_version_tool(layout):
    point_at(layout, "v2")
    wrapper = _install_bin(layout)
    done = subprocess.run([str(wrapper), "versions", "list"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "v2" in done.stdout and "[current]" in done.stdout


def test_the_bin_wrapper_runs_everything_else_on_the_current_version_without_a_guard(layout):
    point_at(layout, "v2")
    wrapper = _install_bin(layout)
    done = subprocess.run([str(wrapper), "status"], capture_output=True, text=True, timeout=60)
    assert done.stdout.splitlines() == ["ran v2", "-I", "-B", "-m", "openprogram", "status"]
    assert attempts(layout, "v2") == []


def test_the_bin_wrapper_finds_a_base_when_the_pointer_is_gone(layout):
    make_version(layout, "20270101T000000Z-aaaaaaaaaaaa")
    wrapper = _install_bin(layout)
    done = subprocess.run([str(wrapper), "versions", "rebuild-pointer"], capture_output=True, text=True, timeout=60)
    assert "no sealed version" in done.stderr or "rebuilt" in done.stderr
