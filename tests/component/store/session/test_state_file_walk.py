"""Reading every conversation's documents twice reads each log once."""
from __future__ import annotations

from openprogram.state.store.session.files import session_log, session_state_file


def test_a_second_pass_over_a_large_store_reads_no_log_again(tmp_path, monkeypatch):
    directories = []
    for number in range(700):
        directory = tmp_path / f"chat-{number}"
        directory.mkdir()
        (directory / session_log.MAIN_LOG).write_bytes(session_log.encode_record(
            number + 1, "file.put", {"name": "jobs.json", "content": f"jobs of {number}"}))
        directories.append(directory)
    reads = []
    read_appended = session_state_file.read_appended

    def counted(path, *args, **kwargs):
        reads.append(path)
        return read_appended(path, *args, **kwargs)

    monkeypatch.setattr(session_state_file, "read_appended", counted)
    try:
        for _pass in range(2):
            for number, directory in enumerate(directories):
                assert session_state_file.read_files(directory) == {"jobs.json": f"jobs of {number}"}
        assert len(reads) == len(directories)
    finally:
        for directory in directories:
            session_state_file.forget_directory(directory)


def _counted_reads(monkeypatch):
    starts = []
    read_appended = session_state_file.read_appended

    def counted(path, start=0, **kwargs):
        starts.append(start)
        return read_appended(path, start, **kwargs)

    monkeypatch.setattr(session_state_file, "read_appended", counted)
    return starts


def test_a_long_first_read_leaves_a_checkpoint_the_next_process_starts_from(tmp_path, monkeypatch):
    monkeypatch.setattr(session_state_file, "CHECKPOINT_AFTER_BYTES", 2000)
    log = tmp_path / session_log.MAIN_LOG
    lines = [session_log.encode_record(1, "file.put", {"name": "jobs.json", "content": "first"})]
    lines += [session_log.encode_record(2 + n, "node.put", {"id": f"n{n}", "content": "x" * 200})
              for n in range(20)]
    log.write_bytes(b"".join(lines))
    starts = _counted_reads(monkeypatch)

    assert session_state_file.read_files(tmp_path) == {"jobs.json": "first"}
    assert starts == [0]
    assert (tmp_path / session_log.CHECKPOINT_DIRECTORY / session_log.RECORDS_CHECKPOINT).is_file()

    session_state_file.forget_directory(tmp_path)  # what a new process knows
    assert session_state_file.read_files(tmp_path) == {"jobs.json": "first"}
    assert starts == [0], "nothing was appended, so no log is read"

    with open(log, "ab") as handle:
        handle.write(session_log.encode_record(90, "file.put", {"name": "jobs.json", "content": "second"}))
    session_state_file.forget_directory(tmp_path)
    assert session_state_file.read_files(tmp_path) == {"jobs.json": "second"}
    assert starts == [0, sum(len(line) for line in lines)], "only the appended line is read"
    session_state_file.forget_directory(tmp_path)


def test_a_short_first_read_leaves_no_checkpoint(tmp_path):
    (tmp_path / session_log.MAIN_LOG).write_bytes(
        session_log.encode_record(1, "file.put", {"name": "jobs.json", "content": "first"}))
    assert session_state_file.read_files(tmp_path) == {"jobs.json": "first"}
    assert not (tmp_path / session_log.CHECKPOINT_DIRECTORY).exists()
    session_state_file.forget_directory(tmp_path)


def test_a_checkpoint_of_a_rewritten_log_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(session_state_file, "CHECKPOINT_AFTER_BYTES", 100)
    log = tmp_path / session_log.MAIN_LOG
    filler = session_log.encode_record(2, "node.put", {"id": "n", "content": "x" * 300})
    log.write_bytes(session_log.encode_record(1, "file.put", {"name": "jobs.json", "content": "old"}) + filler)
    assert session_state_file.read_files(tmp_path) == {"jobs.json": "old"}
    session_state_file.forget_directory(tmp_path)

    replaced = tmp_path / "replaced"
    replaced.write_bytes(filler + session_log.encode_record(3, "file.put", {"name": "jobs.json", "content": "new"}))
    replaced.replace(log)
    assert session_state_file.read_files(tmp_path) == {"jobs.json": "new"}
    session_state_file.forget_directory(tmp_path)


def test_appended_lines_are_read_one_at_a_time_with_the_same_answer(tmp_path, monkeypatch):
    """A log is never read whole: the largest single read is one line."""
    log = tmp_path / session_log.MAIN_LOG
    wanted = session_log.encode_record(1, "file.put", {"name": "jobs.json", "content": "first"})
    other = session_log.encode_record(2, "node.put", {"id": "n", "content": "x" * 5000})
    damaged = b'{"type":"file.put","broken\n'
    later = session_log.encode_record(3, "file.put", {"name": "jobs.json", "content": "later"})
    unfinished = later[:-1]
    log.write_bytes(wanted + other * 400 + wanted)
    whole = len(wanted) * 2 + len(other) * 400

    records, offset, stopped = session_log.read_appended(log, 0, containing=session_state_file._MARKERS)
    assert [record["data"]["content"] for record in records] == ["first", "first"]
    assert (offset, stopped) == (whole, False)

    with open(log, "ab") as handle:
        handle.write(damaged + later + unfinished)
    records, offset, stopped = session_log.read_appended(log, whole, containing=session_state_file._MARKERS)
    assert records == [] and stopped, "reading stops at a damaged line"
    assert offset == whole + len(damaged) + len(later), "and the line still being written is left for later"

    import tracemalloc
    tracemalloc.start()
    session_log.read_appended(log, 0, containing=session_state_file._MARKERS)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < whole / 4, "the file is not held in memory to find three of its lines"


def test_a_fold_reads_only_the_last_copy_of_a_reply_trace(tmp_path):
    """A patch that sets the trace replaces the one before: a fold's read
    hands the earlier ones back without it, and folds to the same session."""
    log = tmp_path / session_log.MAIN_LOG
    lines = [session_log.encode_record(1, "node.put", {"payload": {"id": "a1", "seq": 1, "role": "assistant"}}),
             session_log.encode_record(2, "node.put", {"payload": {"id": "a2", "seq": 2, "role": "assistant"}})]
    for step in range(3, 9):
        lines.append(session_log.encode_record(step, "node.patch", {
            "id": "a1", "set": {"output": f"text {step}"}, "metadata": {"extra": {"step": step}, "kept": step}}))
    lines.append(session_log.encode_record(9, "node.patch", {"id": "a2", "metadata": {"extra": {"only": True}}}))
    log.write_bytes(b"".join(lines))

    whole = session_log.read_appended(log)[0]
    lean, offset, stopped = session_log.read_appended(log, replaced_traces=False)
    assert (offset, stopped) == (sum(len(line) for line in lines), False)
    traces = [record["data"]["metadata"].get("extra") for record in lean if record["type"] == "node.patch"]
    assert traces == [None] * 5 + [{"step": 8}, {"only": True}]
    assert [record["data"]["metadata"]["kept"] for record in lean[2:8]] == list(range(3, 9)), "only the trace goes"

    def folded(records):
        state = session_log.FoldedSession()
        for record in records:
            session_log.apply_record(state, record)
        return session_log.settle(state).nodes

    assert folded(lean) == folded(whole)
    assert session_log.SessionLog(tmp_path)._fold()[1].nodes == folded(whole)
