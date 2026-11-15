"""The self_update tool's output says what is happening in plain words."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openprogram.operations.self_update.control import progress


def test_every_phase_reads_as_a_sentence():
    for phase in ("preparing", "building", "trial", "ready", "quiescing", "activating", "verifying",
                  "succeeded", "aborted", "rolled_back", "needs_manual_recovery", "queued"):
        sentence = progress.describe(phase, platform="darwin")["progress"]
        assert sentence.endswith((".", "…")), phase
        assert "_" not in sentence, phase


def test_an_unknown_phase_adds_nothing():
    assert progress.describe("something_new", platform="darwin") == {}


def test_windows_says_the_trial_has_no_system_level_isolation():
    assert progress.describe("trial", platform="darwin").get("notes") is None
    assert progress.WINDOWS_TRIAL_NOTE in progress.describe("trial", platform="win32")["notes"]
    assert progress.WINDOWS_RESULT_NOTE in progress.describe("succeeded", platform="win32")["notes"]
    # An update that never reached the trial has no isolation to report.
    assert progress.describe("building", platform="win32").get("notes") is None
    assert progress.describe("aborted", platform="win32").get("notes") is None
    assert progress.WINDOWS_RESULT_NOTE in progress.describe("aborted", platform="win32", reached_trial=True)["notes"]


def test_held_messages_and_the_queue_are_reported(tmp_path):
    held = tmp_path / "held"
    held.mkdir()
    (held / "a.json").write_text(json.dumps({"request": {"user_text": "Also add the chart\nto the report"}}))
    (held / "b.json").write_text(json.dumps({"schedule": "weekly"}))
    (tmp_path / "queue.json").write_text(json.dumps({"entries": [{"task": "x"}, {"task": "y"}]}))
    assert progress.held_messages(tmp_path) == ["Also add the chart to the report", ""]
    assert progress.queued_count(tmp_path) == 2
    notes = progress.describe("activating", held=progress.held_messages(tmp_path), platform="darwin")["notes"]
    assert notes == ["2 messages held. Sends when the switch finishes.", "Held: Also add the chart to the report"]
    assert "2 ahead" in progress.describe("queued", ahead=2, platform="darwin")["progress"]


def test_missing_or_damaged_files_read_as_nothing(tmp_path):
    assert progress.queued_count(tmp_path) == 0
    assert progress.held_messages(tmp_path) == []
    (tmp_path / "queue.json").write_text("{")
    assert progress.queued_count(tmp_path) == 0


def test_annotate_puts_progress_first_and_never_breaks_the_result(tmp_path):
    def missing(_update_id):
        raise OSError("gone")

    store = SimpleNamespace(root=Path(tmp_path), load=missing)
    assert progress.annotate({"phase": "trial", "update_id": "su_1"}, store) == {"phase": "trial", "update_id": "su_1"}
    annotated = progress.annotate({"phase": "trial"}, store, platform="darwin")
    assert list(annotated) == ["progress", "phase"]
    assert progress.annotate("text", store) == "text"
    assert progress.annotate({"no": "phase"}, store) == {"no": "phase"}


def test_a_queued_result_counts_its_own_position(tmp_path):
    def missing(_update_id):
        raise AssertionError("a queued request has no directory")

    store = SimpleNamespace(root=Path(tmp_path), load=missing)
    annotated = progress.annotate({"status": "queued", "update_id": "su_2", "position": 2}, store, platform="darwin")
    assert "2 ahead" in annotated["progress"]


def _record(controller_version):
    return SimpleNamespace(
        request=SimpleNamespace(controller_version=controller_version, candidate_version=None),
        state=SimpleNamespace(detail={}),
    )


def test_a_cancelled_waiting_update_does_not_read_as_still_waiting(tmp_path):
    def missing(_update_id):
        raise AssertionError("it has no update directory")

    store = SimpleNamespace(root=Path(tmp_path), load=missing)
    result = progress.annotate({"update_id": "su_1", "status": "stopped", "phase": "aborted"}, store)
    assert "Starts by itself" not in result["progress"] and "stopped" in result["progress"]


@pytest.mark.parametrize("phase", ["succeeded", "needs_manual_recovery"])
def test_no_settings_page_is_named_where_versions_are_not_kept(tmp_path, phase):
    (Path(tmp_path) / "su_1").mkdir()
    store = SimpleNamespace(root=Path(tmp_path), load=lambda _: _record(""))
    result = progress.annotate({"update_id": "su_1", "phase": phase}, store)
    assert "Settings" not in result["progress"] and "openprogram versions" not in result["progress"]


def test_the_settings_page_is_named_where_versions_are_kept(tmp_path):
    (Path(tmp_path) / "su_1").mkdir()
    store = SimpleNamespace(root=Path(tmp_path), load=lambda _: _record("v1"))
    assert "Settings" in progress.annotate({"update_id": "su_1", "phase": "succeeded"}, store)["progress"]
