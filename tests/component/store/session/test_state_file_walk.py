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
