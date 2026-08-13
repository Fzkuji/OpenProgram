from pathlib import Path

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
        def __init__(self, proof: str):
            self._proof = proof

        def raise_for_status(self):
            return None

        def json(self):
            return {"proof": self._proof}

    class ChallengeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, *, params, timeout: float):
            assert timeout == 1.0
            assert auth_state.token not in url
            assert auth_state.token not in params.values()
            nonce = params["nonce"]
            proof = create_owner_challenge_proof(
                token=auth_state.token,
                nonce=nonce,
            )
            return ChallengeResponse(proof)

    def configured(consumer, origin, *, owner_exception):
        assert consumer == "runtime.local_probe"
        assert origin == "http://127.0.0.1:18100"
        assert owner_exception.consumer == consumer
        assert owner_exception.origin == origin
        return ChallengeClient()

    monkeypatch.setattr(
        "openprogram.security.safe_http.configured_safe_client", configured
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
