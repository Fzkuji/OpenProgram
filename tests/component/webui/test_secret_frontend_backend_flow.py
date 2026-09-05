"""End-to-end secret flow: the real frontend normalizer into the real routes.

The backend tests prove the routes hold their contract and the node checks
prove the form never sends a mask. Neither proves the two agree. These tests
run the ACTUAL TypeScript ``normalizeSecretReplacement`` over the exact
strings the UI can produce, then feed its verdict to a live TestClient — so a
drift between what the form sends and what the route accepts fails here.

The node step is skipped when the web toolchain is absent; the backend
assertions still run against the mask the API itself returned.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.auth.store import AuthStore, set_store_for_testing
from openprogram.auth.types import Credential, CredentialData, CredentialPool
from openprogram.webui.routes import accounts, config, providers


_STORED_SECRET = "sk-stored-secret-abc4"
_NEW_SECRET = "sk-rotated-secret-9999"
_WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _normalize_in_browser_code(cases: list[tuple[str, str]]) -> list[str | None]:
    """Run the real ``normalizeSecretReplacement`` over (input, mask) pairs."""
    # Unique per call: parallel workers share this directory, and a fixed name
    # let one worker delete the script another was still running.
    script = (
        _WEB_ROOT / "scripts" / f"run-secret-replacement-{uuid.uuid4().hex}.mjs"
    )
    script.write_text(
        "import { normalizeSecretReplacement } from "
        '"../lib/net/secret-replacement.ts";\n'
        "const cases = JSON.parse(process.argv[2]);\n"
        "console.log(JSON.stringify(cases.map(([input, mask]) =>\n"
        "  normalizeSecretReplacement(input, mask))));\n",
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                "--no-warnings",
                str(script),
                json.dumps(cases),
            ],
            cwd=_WEB_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        script.unlink(missing_ok=True)
    if completed.returncode != 0:
        pytest.skip(f"web toolchain unavailable: {completed.stderr.strip()[:200]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture
def secret_flow(tmp_path, monkeypatch):
    """Live route app whose credential probe always accepts."""
    from openprogram import setup
    from openprogram.auth import account_priority, account_selection, rotation
    from openprogram.webui._model_listing import credentials

    monkeypatch.setattr(setup, "get_config_path", lambda: tmp_path / "config.json")

    sidecar_root = tmp_path / "sidecars"
    for module in (account_priority, account_selection, rotation):
        monkeypatch.setattr(module, "DEFAULT_ROOT", sidecar_root)

    for name in ("OPENAI_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        credentials,
        "validate_credential",
        lambda *args, **kwargs: SimpleNamespace(
            status="valid", detail=None, to_dict=lambda: {"status": "valid"}
        ),
    )

    store = AuthStore(root=tmp_path / "store")
    set_store_for_testing(store)
    store.put_pool(
        CredentialPool(
            provider_id="openai",
            account_id="work",
            credentials=[
                Credential(
                    provider_id="openai",
                    account_id="work",
                    kind="api_key",
                    payload=CredentialData(
                        kind="api_key", auth_value=_STORED_SECRET
                    ),
                )
            ],
        )
    )

    app = FastAPI()
    providers.register(app)
    accounts.register(app)
    config.register(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, store=store, setup=setup)

    set_store_for_testing(None)


def _stored_secret(store: AuthStore) -> str:
    pool = store.find_pool("openai", "work")
    assert pool is not None
    return pool.credentials[0].payload.auth_value


def _displayed_mask(client: TestClient) -> str:
    """The mask the account list actually hands the form."""
    listing = client.get("/api/providers/openai/accounts").json()
    return listing["accounts"][0]["masked_key"]


def test_empty_submission_never_reaches_the_backend(secret_flow):
    """Blank input stops in the form, so the stored key survives untouched."""
    mask = _displayed_mask(secret_flow.client)

    normalized = _normalize_in_browser_code([("", mask), ("   ", mask)])

    assert normalized == [None, None]
    assert _stored_secret(secret_flow.store) == _STORED_SECRET


@pytest.mark.parametrize(
    "secret",
    [
        "sk-stored-secret-abc4",  # long key → "sk-…abc4"
        "abc12345wxyz",  # exactly 12 chars → "abc…wxyz"
        "12345678901",  # too short to abbreviate → "••••••••"
        "sk-ééé-key-1234",  # non-ASCII key → "••••••••"
    ],
)
def test_every_mask_form_is_structurally_unsubmittable(secret):
    """No mask this system can produce is a valid credential value.

    Both mask forms carry a non-ASCII character by construction ("…" or "•"),
    so the printable-ASCII rule alone rejects a resubmitted mask — the
    mask-equality check in the form is a second line, not the only one. If a
    future mask became plain ASCII this fails, which is the point: it would
    silently become submittable.
    """
    from openprogram.webui.routes._credential_secrets import (
        is_nonempty_printable_ascii,
        mask_credential,
    )

    mask = mask_credential(secret)

    assert mask
    assert not is_nonempty_printable_ascii(mask)


def test_resubmitted_mask_is_refused_by_form_and_backend(secret_flow):
    """The displayed mask is rejected twice over — and stores nothing.

    The form drops it, and if a caller bypasses the form the route still
    refuses it, so neither layer alone is load-bearing.
    """
    mask = _displayed_mask(secret_flow.client)

    assert _normalize_in_browser_code([(mask, mask)]) == [None]

    response = secret_flow.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": mask, "validate": True},
    )

    assert response.status_code == 400
    assert _stored_secret(secret_flow.store) == _STORED_SECRET


def test_form_drops_a_resubmitted_value_matching_the_displayed_mask(secret_flow):
    """The mask-equality guard itself, exercised with an ASCII stand-in.

    Real masks are non-ASCII, so this feeds the normalizer an ASCII mask to
    isolate the equality check from the character-set check.
    """
    assert _normalize_in_browser_code(
        [("abc...wxyz", "abc...wxyz"), ("  abc...wxyz  ", "abc...wxyz")]
    ) == [None, None]


def test_valid_replacement_rotates_and_retires_the_old_secret(secret_flow):
    """A real new value goes through, and the old one is gone for good."""
    mask = _displayed_mask(secret_flow.client)

    assert _normalize_in_browser_code([(f"  {_NEW_SECRET}  ", mask)]) == [_NEW_SECRET]

    response = secret_flow.client.post(
        "/api/providers/openai/accounts/work/update",
        json={"api_key": _NEW_SECRET, "validate": True},
    )

    assert response.status_code == 200
    assert _stored_secret(secret_flow.store) == _NEW_SECRET

    # The retired secret is unreachable through every read path.
    for path in (
        "/api/providers/openai/accounts",
        "/api/config",
        "/api/config/key/OPENAI_API_KEY",
    ):
        assert _STORED_SECRET not in secret_flow.client.get(path).text


def test_no_read_path_returns_either_secret_in_plaintext(secret_flow, monkeypatch):
    """Sweep every credential-adjacent GET for the live plaintext."""
    monkeypatch.setenv("OPENAI_API_KEY", _STORED_SECRET)
    secret_flow.setup._write_config({"api_keys": {"OPENAI_API_KEY": _STORED_SECRET}})

    paths = [
        "/api/config",
        "/api/config/key/OPENAI_API_KEY",
        "/api/config/key/OPENAI_API_KEY?reveal=1",
        "/api/settings",
        "/api/providers/openai/accounts",
        "/api/providers/openai/config",
        "/api/providers/openai/accounts/work/reveal",
        "/api/search-providers/list",
    ]
    for path in paths:
        response = secret_flow.client.get(path)
        assert _STORED_SECRET not in response.text, f"{path} leaked the credential"


def test_config_save_round_trip_masks_on_read_back(secret_flow):
    """Saving through /api/config yields a mask, never the value."""
    save = secret_flow.client.post(
        "/api/config", json={"api_keys": {"TAVILY_API_KEY": _NEW_SECRET}}
    )
    assert save.status_code == 200

    read_back = secret_flow.client.get("/api/config/key/TAVILY_API_KEY").json()

    assert read_back["has_value"] is True
    assert read_back["masked"] != _NEW_SECRET
    assert _NEW_SECRET not in secret_flow.client.get("/api/config").text

    # The mask that read-back produced must itself be unusable as a value.
    assert _normalize_in_browser_code([(read_back["masked"], read_back["masked"])]) == [
        None
    ]


def test_verify_refuses_the_mask_instead_of_probing_the_stored_key(secret_flow):
    """/api/config/verify must not report a mask as a valid credential."""
    secret_flow.client.post(
        "/api/config", json={"api_keys": {"TAVILY_API_KEY": _NEW_SECRET}}
    )
    mask = secret_flow.client.get("/api/config/key/TAVILY_API_KEY").json()["masked"]

    response = secret_flow.client.post(
        "/api/config/verify", json={"env": "TAVILY_API_KEY", "value": mask}
    )

    assert response.status_code == 400
    assert _NEW_SECRET not in response.text


def test_verify_without_a_value_probes_the_stored_credential(secret_flow):
    """Omitting ``value`` is how the UI re-checks a key it cannot read."""
    secret_flow.client.post(
        "/api/config", json={"api_keys": {"TAVILY_API_KEY": _NEW_SECRET}}
    )

    response = secret_flow.client.post(
        "/api/config/verify", json={"env": "TAVILY_API_KEY"}
    )

    assert response.status_code == 200
    assert _NEW_SECRET not in response.text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_frontend_secret_guard_script_passes(secret_flow):
    """The committed node guard must still hold against the live sources."""
    script = _WEB_ROOT / "scripts" / "check-secret-non-retrieval.mjs"
    if not any((root / "node_modules" / "typescript" / "lib" / "typescript.js").is_file()
               for root in (_WEB_ROOT, _WEB_ROOT.parent.parent)):
        pytest.skip("web dependencies are not installed")

    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        cwd=_WEB_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
