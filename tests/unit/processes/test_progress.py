"""A background command's progress, last line and redrawn lines, read from its output."""
from __future__ import annotations

import pytest

from openprogram.host.processes import progress
from openprogram.host.processes.presentation import command_display
from openprogram.host.processes.store import ProcessStore, public_record


@pytest.fixture
def store(tmp_path):
    return ProcessStore(tmp_path / "processes.db")


def _record(store, command="python eval.py"):
    return store.create(session_id="conversation", execution_id=None, tool_call_id=None,
                        command=command, cwd="/work", backend_id="local")


def test_tqdm_bar_gives_count_speed_and_time_left():
    found = progress.parse("eval:  45%|████▌     | 45/100 [00:13<00:16,  3.41it/s]")
    assert found["source"] == "bar"
    assert (found["current"], found["total"], found["label"]) == (45, 100, "eval")
    assert found["rate"] == pytest.approx(3.41)
    assert found["eta"] == 16


def test_seconds_per_item_becomes_items_per_second():
    found = progress.parse("3/10 [00:06<00:14,  2.00s/it]")
    assert found["rate"] == pytest.approx(0.5)


def test_counters_and_markers():
    assert progress.parse("Epoch 3/10 loss=0.2")["label"] == "epochs"
    assert progress.parse("processing [7/20]")["current"] == 7
    marked = progress.parse("noise\n##progress 45/100 samples\n")
    assert (marked["source"], marked["current"], marked["label"]) == ("marker", 45, "samples")
    assert progress.parse("##progress 30% indexing")["total"] == 100


def test_dates_and_plain_fractions_are_not_progress():
    assert progress.parse("built on 10/27 at 3/4 capacity") is None


def test_osc_progress_sequence():
    found = progress.parse("\x1b]9;4;1;62\x07")
    assert (found["source"], found["current"], found["total"]) == ("osc", 62, 100)
    assert progress.parse("\x1b]9;4;3;\x07")["state"] == "indeterminate"


def test_less_trusted_sources_do_not_overwrite_a_marker():
    marked = progress.merge(None, progress.parse("##progress 4/10 files"), now=0)
    after = progress.merge(marked, progress.parse("step 900/1000"), now=1)
    assert after["current"] == 4 and after["label"] == "files"


def test_speed_is_measured_when_the_output_has_none():
    first = progress.merge(None, progress.parse("##progress 10/100"), now=100)
    later = progress.merge(first, progress.parse("##progress 30/100"), now=110)
    assert later["rate"] == pytest.approx(2)
    assert later["eta"] == pytest.approx(35)


def test_redrawn_frames_are_stored_as_one_line(store):
    record = _record(store)
    store.append_output(record["id"], b"loading\n 10/100 [00:01<00:09, 10it/s]\r")
    store.append_output(record["id"], b" 20/100 [00:02<00:08, 10it/s]\r 30/100 [00:03<00:07, 10it/s]\r")
    store.append_output(record["id"], b"100/100 [00:10<00:00, 10it/s]\ndone\n")
    assert store.output(record["id"]) == "loading\n100/100 [00:10<00:00, 10it/s]\ndone\n"
    shown = public_record(store.get(record["id"]))
    assert shown["progress"]["current"] == 100
    assert shown["last_line"] == "done"


def test_agent_reads_each_frame_once_and_resumes_after_a_redraw(store):
    record = _record(store)
    store.append_output(record["id"], b"start\n 10/100 [00:01<00:09, 10it/s]\r")
    first, dropped = store.take(record["id"])
    assert first.endswith(b"10it/s]\r") and dropped == 0
    store.append_output(record["id"], b" 50/100 [00:05<00:05, 10it/s]\r")
    again, _ = store.take(record["id"])
    # The redrawn line is handed out again, never glued to the old frame.
    assert again == b" 50/100 [00:05<00:05, 10it/s]\r"


def test_line_cap_counts_newlines_not_redraws(store, monkeypatch):
    monkeypatch.setattr("openprogram.host.processes.store.MAX_OUTPUT_LINES", 3)
    record = _record(store)
    store.append_output(record["id"], b"a\nb\n" + b"".join(b"%d/50 [00:00<00:00, 1it/s]\r" % i for i in range(50)))
    assert store.output(record["id"]).startswith("a\nb\n")


@pytest.mark.parametrize(("command", "name"), [
    ("cd /work && mkdir -p out && python3 -u eval.py --split test | tee log", "eval.py"),
    ("FOO=1 timeout 600 uv run python -m pkg.main", "pkg.main"),
    ("nohup python train.py > log 2>&1 &", "train.py"),
    ("cd a; ls -la", "ls"),
    ("sleep 45; echo done", "sleep"),
])
def test_names_the_command_a_chain_runs(command, name):
    assert command_display(command)["name"] == name


def test_heredoc_is_a_snippet():
    assert command_display("python3 - <<'EOF'\nimport os; print(1)\nEOF") == {"name": "python3", "kind": "snippet"}
