import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from openprogram._ports import backend_is_ours
from openprogram.backend_endpoint import create_owner_challenge_proof
from openprogram.webui.owner_auth import OwnerAuthState


RAW_TOKEN = bytes(range(32))
OWNER_PRINCIPAL_ID = "owner/install/0123456789abcdef"


def test_backend_identity_uses_worker_ownership_without_network_credentials(
    monkeypatch,
    tmp_path: Path,
):
    port_file = tmp_path / "worker.port"
    port_file.write_text("18100\n", encoding="utf-8")
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid",
        lambda: 12345,
    )
    monkeypatch.setattr(
        "openprogram.worker.paths.port_path",
        lambda: port_file,
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)

    auth_state = OwnerAuthState.start(
        state_dir=tmp_path,
        bind_host="127.0.0.1",
        port=18100,
        allowed_origins=(),
        raw_token=RAW_TOKEN,
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )

    class ChallengeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return self.body

    class ChallengeOpener:
        def open(self, request, timeout: float):
            assert timeout == 1.0
            assert "authorization" not in {
                name.lower() for name, _value in request.header_items()
            }
            assert auth_state.token not in request.full_url
            nonce = parse_qs(urlsplit(request.full_url).query)["nonce"][0]
            proof = create_owner_challenge_proof(
                token=auth_state.token,
                nonce=nonce,
            )
            return ChallengeResponse(json.dumps({"proof": proof}).encode())

    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *_handlers: ChallengeOpener(),
    )

    try:
        assert backend_is_ours(18100) is True
        assert backend_is_ours(18101) is False
    finally:
        auth_state.close()


def test_backend_identity_is_inconclusive_without_a_managed_worker(monkeypatch):
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid",
        lambda: None,
    )

    assert backend_is_ours(18100) is None
