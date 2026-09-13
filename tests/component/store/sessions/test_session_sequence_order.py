"""Public cached reads agree with a reopened store for explicit sequences."""
from contextlib import closing

import pytest

from openprogram.context.nodes import Call
from openprogram.store import SessionNodeWriter, SessionStore


@pytest.mark.parametrize("read", ["nodes", "latest_user", "deepest_leaf", "descendants"])
def test_explicit_sequence_reads_match_reopened_store(tmp_path, read):
    root = tmp_path / "sessions"
    with closing(SessionStore(root)) as store:
        store.create_session("sequence", "main")
        writer = SessionNodeWriter(store, "sequence")
        writer.append(Call(id="root", role="user", seq=0, output="root"))
        for identifier, seq in [("higher", 7), ("lower", 2)]:
            writer.append(Call(
                id=identifier, role="user", seq=seq, output=identifier,
                predecessor="root", caller="root" if read == "descendants" else "",
            ))

        def result(reader):
            if read == "nodes":
                return [(node.id, node.seq) for node in reader.get_nodes("sequence")]
            if read == "latest_user":
                return reader.latest_user_text("sequence")
            if read == "deepest_leaf":
                return reader.get_deepest_leaf("sequence", "root")
            return [node["id"] for node in reader.get_descendants("sequence", "root")]

        with closing(SessionStore(root)) as fresh:
            assert result(store) == result(fresh)
        assert [(node.id, node.seq) for node in store.get_nodes("sequence")] == [
            ("root", 0), ("lower", 2), ("higher", 7),
        ]
        automatic = Call(id="automatic", role="user", predecessor="higher")
        writer.append(automatic)
        assert automatic.seq == 8
