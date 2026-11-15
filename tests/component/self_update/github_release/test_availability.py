"""The worker remembers the latest release; the version route reads the memory."""
import time

import pytest

from openprogram.operations.self_update.github_release import availability, detect, github


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(availability, "_record_path", lambda: tmp_path / "latest-release.json")
    return tmp_path


def _release(version):
    return {"tag_name": f"v{version}"}


def test_nothing_is_claimed_before_a_check_has_passed(state):
    assert availability.view("1.0.0") == {"latestVersion": None, "updateAvailable": False, "latestCheckedAt": None}


def test_a_newer_release_is_reported(state, monkeypatch):
    monkeypatch.setattr(github, "latest_release", lambda: _release("1.2.0"))
    assert availability.refresh()
    view = availability.view("1.0.0")
    assert view["latestVersion"] == "1.2.0" and view["updateAvailable"] and view["latestCheckedAt"] > 0
    assert not availability.view("1.2.0")["updateAvailable"]


def test_a_failed_check_keeps_the_last_answer(state, monkeypatch):
    monkeypatch.setattr(github, "latest_release", lambda: _release("1.2.0"))
    availability.refresh()
    monkeypatch.setattr(github, "latest_release", lambda: None)
    assert not availability.refresh()
    assert availability.view("1.0.0")["latestVersion"] == "1.2.0"


def test_a_recent_check_is_not_repeated(state, monkeypatch):
    monkeypatch.setattr(github, "latest_release", lambda: _release("1.2.0"))
    assert availability._seconds_until_due() == 0
    availability.refresh()
    assert availability._seconds_until_due() > availability.CHECK_INTERVAL_SECONDS - 60
    monkeypatch.setattr(availability, "_read", lambda: {"checked_at": time.time() - availability.CHECK_INTERVAL_SECONDS - 1})
    assert availability._seconds_until_due() == 0


def test_a_source_checkout_starts_no_check(monkeypatch):
    monkeypatch.setattr(availability, "detect_install_method", lambda: detect.InstallMethod.SOURCE_CHECKOUT)
    monkeypatch.setattr(availability.threading, "Thread", lambda **_: pytest.fail("no thread for a checkout"))
    availability.start()


def test_harness_dependencies_stay_out_of_a_managed_install(monkeypatch, tmp_path):
    from openprogram.interfaces.cli.commands import programs
    from openprogram.programs._host import sources

    (tmp_path / "requirements.txt").write_text("example\n")
    target = tmp_path / "state" / "program-dependencies" / "python3.12"
    commands = []
    monkeypatch.setattr(detect, "detect_install_method", lambda: detect.InstallMethod.MANAGED_RELEASE)
    monkeypatch.setattr(sources, "dependency_directory", lambda: target)
    monkeypatch.setattr("subprocess.call", lambda command: commands.append(command) or 0)
    programs._install_dependencies(str(tmp_path), "harness")

    assert commands[0][:4] == [programs.sys.executable, "-m", "pip", "install"]
    assert commands[0][4:7] == ["--target", str(target), "--upgrade"]
    assert target.is_dir()


def test_a_source_checkout_installs_dependencies_as_before(monkeypatch, tmp_path):
    from openprogram.interfaces.cli.commands import programs

    (tmp_path / "requirements.txt").write_text("example\n")
    commands = []
    monkeypatch.setattr(detect, "detect_install_method", lambda: detect.InstallMethod.SOURCE_CHECKOUT)
    monkeypatch.setattr("subprocess.call", lambda command: commands.append(command) or 0)
    programs._install_dependencies(str(tmp_path), "harness")
    assert "--target" not in commands[0]


def test_installed_dependencies_come_after_the_products_own(monkeypatch, tmp_path):
    from openprogram.programs._host import sources

    monkeypatch.setattr(sources, "dependency_directory", lambda: tmp_path)
    monkeypatch.setattr(sources.sys, "path", ["first"])
    sources.use_dependency_directory()
    sources.use_dependency_directory()
    assert sources.sys.path == ["first", str(tmp_path)]
