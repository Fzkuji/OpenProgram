"""Web Push from this computer: what each run state sends, to whom, and that only the phone can read it."""
from __future__ import annotations

import base64
import json
import struct

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from openprogram.interfaces.server import web_push


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Phone:
    """A browser's half of a subscription (RFC 8291), able to read what it receives."""

    def __init__(self, endpoint="https://web.push.apple.com/QOk1"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.endpoint = endpoint
        self.public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def subscription(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": _b64(self.public), "auth": _b64(self.auth)}}

    def read(self, body: bytes) -> dict:
        salt, (_rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
        sender = body[21:21 + idlen]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
        ikm = HKDF(hashes.SHA256(), 32, self.auth, b"WebPush: info\x00" + self.public + sender).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    web_push._sent.clear()
    web_push.set_device_check(None)
    yield tmp_path
    web_push.set_device_check(None)


@pytest.fixture
def posts(monkeypatch):
    sent = []
    status = {"code": 201}

    def handler(request: httpx.Request):
        sent.append(request)
        return httpx.Response(status["code"])

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return sent, status


def test_only_the_subscribing_phone_can_read_the_notice_and_it_names_no_conversation_content(posts):
    sent, _ = posts
    phone = Phone()
    web_push.save_subscription(phone.subscription(), device_id=None, lang="zh-CN", categories=None)
    assert web_push.send("attention", session_id="abc") == 1
    request = sent[0]
    assert request.headers["Content-Encoding"] == "aes128gcm" and request.headers["Urgency"] == "high"
    assert request.headers["Authorization"].startswith("vapid t=") and f"k={web_push.public_key()}" in request.headers["Authorization"]
    assert phone.read(request.content) == {"title": "OpenProgram", "body": "有对话在等你", "url": "/s/abc", "tag": "attention:abc"}


def test_the_vapid_token_is_signed_by_this_computers_key_for_the_push_service(posts):
    sent, _ = posts
    phone = Phone("https://fcm.googleapis.com/fcm/send/xyz")
    web_push.save_subscription(phone.subscription(), device_id=None, lang="en", categories=None)
    web_push.send("replies", session_id="s1")
    token = sent[0].headers["Authorization"].split("t=")[1].split(",")[0]
    header, claims, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(claims + "=="))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"].startswith("https://")
    raw = base64.urlsafe_b64decode(signature + "==")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), base64.urlsafe_b64decode(web_push.public_key() + "=="))
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{header}.{token.split('.')[1]}".encode(), ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize("endpoint", [
    "http://web.push.apple.com/x", "https://evil.example/x", "https://web.push.apple.com:8443/x",
    "https://user@fcm.googleapis.com/x", "https://push.apple.com.evil.example/x",
])
def test_a_subscription_to_anything_but_a_push_service_is_refused(endpoint):
    with pytest.raises(ValueError):
        web_push.save_subscription(Phone(endpoint).subscription(), device_id=None, lang="en", categories=None)


def test_categories_choose_what_a_phone_receives_and_a_gone_subscription_is_dropped(posts):
    sent, status = posts
    phone = Phone()
    assert web_push.save_subscription(phone.subscription(), device_id=None, lang="en", categories=["failures"]) == {"categories": ["failures"]}
    # Read back without categories: what it had.
    assert web_push.save_subscription(phone.subscription(), device_id=None, lang="en", categories=None) == {"categories": ["failures"]}
    assert web_push.send("replies", session_id="s") == 0 and not sent
    status["code"] = 410
    assert web_push.send("failures", session_id="s") == 0
    assert web_push._load() == {}


def test_a_revoked_phone_stops_getting_notices(posts):
    sent, _ = posts
    web_push.save_subscription(Phone().subscription(), device_id="dev1", lang="en", categories=None)
    web_push.set_device_check(lambda device_id: False)
    assert web_push.send("attention", session_id="s") == 0 and not sent and web_push._load() == {}


def _frame(status, reason=None, parent=None, version=3):
    return {"type": "execution.updated", "execution": {
        "execution_id": "e1", "session_id": "s1", "status": status, "reason_code": reason,
        "parent_execution_id": parent, "status_version": version}}


@pytest.mark.parametrize("frame,kind", [
    (_frame("paused", "wait_open"), "attention"),
    (_frame("paused", "system_access_required"), "attention"),
    (_frame("completed"), "replies"),
    (_frame("failed", "agent_runner_error"), "failures"),
    (_frame("running"), None),
    (_frame("cancelled"), None),
    (_frame("completed", parent="e0"), None),
    ({"type": "chat_response"}, None),
])
def test_which_run_states_notify(frame, kind):
    found = web_push.kind_of(frame)
    assert (found[0] if found else None) == kind


def test_one_notice_per_run_state(monkeypatch):
    calls = []
    web_push.save_subscription(Phone().subscription(), device_id=None, lang="en", categories=None)

    class Now:
        def __init__(self, target, args, kwargs, **_):
            self.go = lambda: calls.append((args, kwargs))

        def start(self):
            self.go()

    monkeypatch.setattr(web_push.threading, "Thread", Now)
    web_push.on_frame(_frame("completed"))
    web_push.on_frame(_frame("completed"))
    web_push.on_frame(_frame("completed", version=4))
    assert len(calls) == 2
