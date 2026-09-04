"""Approved candidate scripts retain source provenance across native checks."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.component.programs.test_self_update_tools import _isolated_owner, _git, _Manager  # noqa: F401
from tests.component.self_update.test_native_checks import _app, _cli_plan, installed_cli, native_verifier  # noqa: F401
from tests.component.self_update.test_verification_plan import _public_prepare, _candidate
from tests.component.self_update.test_system_probe import live as http_live  # noqa: F401
from tests.component.self_update.test_verification_channel import consume, store_fixture, verifier  # noqa: F401
from openprogram.self_update import native_checks


def _test_plan():
    plan = _cli_plan("test:python")
    plan["checks"][0]["argv"] = ["verify.py", "expected"]
    return plan


@pytest.fixture
def live(http_live, tmp_path, monkeypatch, installed_cli):
    from openprogram.programs.tools.system import self_update as tool
    from openprogram.webui.routes import misc
    original, flags, state = http_live
    worktree, base, _ = _candidate(tmp_path)
    worktree.parent_session = "p1"
    candidate = Path(worktree.worktree_path)
    (candidate / "feature.py").write_text("VALUE = 'expected'\n")
    (candidate / "verify.py").write_text(
        "import os, sys, tempfile\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path.cwd()))\nimport feature\n"
        "assert feature.VALUE == sys.argv[1]\n"
        "assert Path('feature.py').is_file()\n"
        "with tempfile.TemporaryFile() as f: f.write(b'private test data')\n"
        "print('candidate verified')\n")
    _git(candidate, "add", "feature.py", "verify.py")
    _git(candidate, "commit", "-m", "add candidate acceptance")
    sha = _git(candidate, "rev-parse", "HEAD")
    monkeypatch.setattr(tool, "get_manager", lambda: _Manager(worktree))
    monkeypatch.setattr(misc, "_HEAD_SHA", sha)
    installed_cli["identity"]["revision"] = sha
    flags["candidate"] = candidate
    yield replace(original, request=replace(original.request, repo=worktree.source_repo,
                  worktree_id=worktree.id, base_sha=base, candidate_sha=sha)), flags, state


def test_public_prepare_freezes_candidate_test_arguments(tmp_path, monkeypatch):
    from tests.component.self_update import test_verification_plan as helpers
    worktree, base, _ = _candidate(tmp_path)
    candidate = Path(worktree.worktree_path)
    (candidate / "verify.py").write_text("import sys;assert sys.argv[1] == 'expected';print('verified')")
    _git(candidate, "add", "verify.py")
    _git(candidate, "commit", "-m", "add acceptance script")
    sha = _git(candidate, "rev-parse", "HEAD")
    monkeypatch.setattr(helpers, "_candidate", lambda _: (worktree, base, sha))
    app = _app(tmp_path)
    actual = native_checks.runtime_identity
    monkeypatch.setattr(native_checks, "runtime_identity", lambda _app, **kw: actual(app, **kw))
    result, store = _public_prepare(tmp_path, monkeypatch, _test_plan())
    assert not result.is_error, result.content
    from openprogram.self_update.verifier_config import load_verifier_config
    record = store.load(json.loads(result.content[0].text)["update_id"])
    assert load_verifier_config(store, record)["verification_plan"] == _test_plan()


@pytest.mark.parametrize("verifier", [_test_plan()], indirect=True)
def test_actual_candidate_script_has_source_bound_receipt(native_verifier):
    v = native_verifier
    v.run()
    assert not v.control["tool_result"].is_error, v.control["tool_result"]
    assert consume(v)["verdict"] == "pass"
    assert "not installed-App behavior" in v.control["prompt"]
    observed = v.control["observed"]
    assert observed["body"].strip() == "candidate verified"
    execution = observed["execution"]
    assert execution["origin"] == "candidate_test"
    assert execution["cwd"] == str(v.flags["candidate"])
    assert execution["candidate"]["revision"] == v.request.candidate_sha
    assert execution["argv"][-2:] == [str(v.flags["candidate"] / "verify.py"), "expected"]
    assert execution["cleanup_complete"] is True
    assert not _git(v.flags["candidate"], "status", "--porcelain", "--untracked-files=all")
    assert not list((v.store.root / v.request.update_id).glob("native-check-*"))


@pytest.mark.parametrize("verifier", [_test_plan()], indirect=True)
@pytest.mark.parametrize("failure", ["before", "after", "nonzero", "raw_args"])
def test_candidate_failure_cannot_pass(native_verifier, monkeypatch, failure):
    from openprogram.self_update import repair_candidate
    v = native_verifier
    if failure == "before":
        (v.flags["candidate"] / "feature.py").write_text("VALUE = 'changed'\n")
    elif failure == "after":
        actual = repair_candidate._test
        def change_after(*args, **kwargs):
            result = actual(*args, **kwargs)
            (v.flags["candidate"] / "feature.py").write_text("VALUE = 'changed'\n")
            return result
        monkeypatch.setattr(repair_candidate, "_test", change_after)
    elif failure == "nonzero":
        v.native["nonzero"] = True
    else:
        v.control["args"] = {"check_id": "diagnostics", "argv": ["different.py"]}
    v.run()
    assert consume(v)["verdict"] == "inconclusive"
    assert not list((v.store.root / v.request.update_id).glob("native-check-*"))


@pytest.mark.parametrize("argv", ["verify.py", [], ["../verify.py"], ["/tmp/verify.py"],
                                      ["-c", "print(1)"], ["verify.py", None], ["verify.py", "\0"],
                                      ["x//verify.py"], ["verify.py", "x" * 4097]])
def test_public_prepare_rejects_unapproved_test_arguments(tmp_path, monkeypatch, argv):
    plan = _test_plan()
    plan["checks"][0]["argv"] = argv
    result, store = _public_prepare(tmp_path, monkeypatch, plan)
    assert result.is_error
    assert not store.root.exists()


@pytest.mark.parametrize("case", ["missing", "untracked", "ignored", "symlink"])
def test_public_prepare_rejects_uncommitted_or_indirect_script(tmp_path, monkeypatch, case):
    from tests.component.self_update import test_verification_plan as helpers
    worktree, base, sha = _candidate(tmp_path)
    candidate = Path(worktree.worktree_path)
    if case in {"untracked", "ignored"}:
        (candidate / "verify.py").write_text("print('unapproved')")
    if case == "ignored":
        (candidate / ".gitignore").write_text("verify.py\n")
        _git(candidate, "add", ".gitignore")
        _git(candidate, "commit", "-m", "ignore unapproved test")
        sha = _git(candidate, "rev-parse", "HEAD")
    elif case == "symlink":
        (candidate / "verify.py").symlink_to("feature.txt")
        _git(candidate, "add", "verify.py")
        _git(candidate, "commit", "-m", "add indirect test")
        sha = _git(candidate, "rev-parse", "HEAD")
    monkeypatch.setattr(helpers, "_candidate", lambda _: (worktree, base, sha))
    app = _app(tmp_path)
    actual = native_checks.runtime_identity
    monkeypatch.setattr(native_checks, "runtime_identity", lambda _app, **kw: actual(app, **kw))
    result, store = _public_prepare(tmp_path, monkeypatch, _test_plan())
    assert result.is_error
    assert not store.root.exists()
