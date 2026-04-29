from pathlib import Path

from openprogram.agents import session_aliases as A


def test_lookup_uses_catch_all_after_exact_miss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)

    A.attach(
        channel="wechat",
        account_id="default",
        peer_kind="direct",
        peer_id="*",
        agent_id="main",
        session_id="local_all",
    )

    assert A.lookup(
        "wechat",
        "default",
        {"kind": "direct", "id": "alice"},
    ) == ("main", "local_all")


def test_lookup_prefers_exact_alias_over_catch_all(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)

    A.attach(
        channel="wechat",
        account_id="default",
        peer_kind="direct",
        peer_id="*",
        agent_id="main",
        session_id="local_all",
    )
    A.attach(
        channel="wechat",
        account_id="default",
        peer_kind="direct",
        peer_id="alice",
        agent_id="main",
        session_id="local_alice",
    )

    assert A.lookup(
        "wechat",
        "default",
        {"kind": "direct", "id": "alice"},
    ) == ("main", "local_alice")
    assert A.lookup(
        "wechat",
        "default",
        {"kind": "direct", "id": "bob"},
    ) == ("main", "local_all")
