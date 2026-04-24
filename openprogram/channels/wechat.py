"""WeChat bot channel via Tencent's iLink bot API.

The same backend weclaw (https://github.com/fastclaw-ai/weclaw) uses.
No weixin-official-account / Enterprise WeChat registration required
— just a personal WeChat on your phone to scan the login QR once.

Protocol summary (host: ``https://ilinkai.weixin.qq.com``):

    GET /ilink/bot/get_bot_qrcode?bot_type=3
        → {qrcode, qrcode_img_content}
           qrcode            — session token to pass to get_qrcode_status
           qrcode_img_content — URL string to encode into a QR the user
                                scans with WeChat. NOT an image, despite
                                the name.
    GET /ilink/bot/get_qrcode_status?qrcode=<token>
        → wait | scaned | confirmed | expired
           (confirmed includes {bot_token, ilink_bot_id, baseurl, ilink_user_id})

    POST /ilink/bot/getupdates   long-poll inbound
    POST /ilink/bot/sendmessage  send text reply

Credentials persist under
    ~/.agentic/wechat/<bot_id>.json    (mode 0600)
    ~/.agentic/wechat/<bot_id>.sync.json

--profile-aware — routes through openprogram.paths.get_state_dir().

Legal note: iLink is Tencent's own bot backend. weclaw's README
says personal-use only, not commercial. Ship accordingly.
"""
from __future__ import annotations

import base64
import json
import os
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from openprogram.channels.base import Channel


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
LONG_POLL_TIMEOUT = 40
SEND_TIMEOUT = 15
MAX_MSG_CHARS = 1800


class WechatChannel(Channel):
    platform_id = "wechat"

    def __init__(self) -> None:
        # weclaw's iLink client is pure HTTP; we only need requests.
        try:
            import requests  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "WeChat channel requires `requests`. "
                "It ships with Python in most distributions; install with "
                "`pip install requests`."
            ) from e
        self._wechat_uin = _make_wechat_uin()
        self._creds: dict[str, str] | None = None  # populated on run()

    def run(self, stop: threading.Event) -> None:
        creds = _load_or_login()
        if creds is None:
            print("[wechat] login aborted / failed; channel not starting.")
            return
        self._creds = creds
        base = creds.get("baseurl") or DEFAULT_BASE_URL

        rt = _get_chat_runtime_or_die()
        print(f"[wechat] logged in as {creds['ilink_user_id']} "
              f"(model={getattr(rt, 'model', '?')}) — ctrl+c to stop")

        cursor = _load_cursor(creds["ilink_bot_id"])
        consecutive_errors = 0
        backoff = 3

        import requests

        while not stop.is_set():
            try:
                resp = requests.post(
                    f"{base}/ilink/bot/getupdates",
                    headers=self._auth_headers(creds["bot_token"]),
                    json={
                        "get_updates_buf": cursor,
                        "base_info": {"channel_version": "1.0.0"},
                    },
                    timeout=LONG_POLL_TIMEOUT,
                )
                data = resp.json() if resp.ok else {}
            except Exception as e:  # noqa: BLE001
                consecutive_errors += 1
                wait = min(60, backoff * (2 ** min(consecutive_errors - 1, 4)))
                print(f"[wechat] poll failed ({type(e).__name__}: {e}); "
                      f"retry in {wait}s")
                time.sleep(wait)
                continue

            consecutive_errors = 0
            errcode = data.get("errcode", 0)
            if errcode == -14:
                if cursor:
                    print("[wechat] session expired; resetting cursor")
                    cursor = ""
                    _save_cursor(creds["ilink_bot_id"], cursor)
                    time.sleep(5)
                    continue
                print("[wechat] bot token invalid — rerun "
                      "`openprogram config channels` and scan QR again")
                return
            if data.get("ret", 0) != 0 and errcode != 0:
                print(f"[wechat] poll error ret={data.get('ret')} "
                      f"errcode={errcode} errmsg={data.get('errmsg','')[:120]}")
                time.sleep(3)
                continue

            new_cursor = data.get("get_updates_buf") or ""
            if new_cursor:
                cursor = new_cursor
                _save_cursor(creds["ilink_bot_id"], cursor)

            for msg in data.get("msgs", []) or []:
                self._handle_message(msg, rt, base, creds)

    # -------------------------------------------------------------------

    def _handle_message(self, msg: dict, rt, base: str,
                        creds: dict[str, str]) -> None:
        if msg.get("message_type") != 1:  # 1 = User
            return
        items = msg.get("item_list") or []
        text_item = next(
            (it for it in items if it.get("type") == 1), None
        )
        if not text_item:
            return
        text = (text_item.get("text_item", {}) or {}).get("text", "").strip()
        if not text:
            return
        from_id = msg.get("from_user_id")
        if not from_id:
            return
        context_token = msg.get("context_token") or ""

        snippet = text[:60] + ("..." if len(text) > 60 else "")
        print(f"[wechat] <{from_id}> {snippet}")

        from openprogram.channels._conversation import turn_with_history
        reply_text = turn_with_history(
            platform="wechat",
            user_id=from_id,
            user_text=text,
            rt=rt,
        )

        for chunk in _chunk(reply_text, MAX_MSG_CHARS):
            self._send_text(base, creds, from_id, context_token, chunk)

    def _send_text(self, base: str, creds: dict[str, str],
                   to_user_id: str, context_token: str, text: str) -> None:
        import requests
        try:
            resp = requests.post(
                f"{base}/ilink/bot/sendmessage",
                headers=self._auth_headers(creds["bot_token"]),
                json={
                    "msg": {
                        "from_user_id": creds["ilink_bot_id"],
                        "to_user_id": to_user_id,
                        "client_id": uuid.uuid4().hex,
                        "message_type": 2,     # Bot
                        "message_state": 2,    # Finish
                        "item_list": [
                            {"type": 1, "text_item": {"text": text}}
                        ],
                        "context_token": context_token,
                    },
                    "base_info": {},
                },
                timeout=SEND_TIMEOUT,
            )
            data = resp.json() if resp.ok else {}
            if data.get("ret", 0) != 0:
                print(f"[wechat] send failed: {data.get('errmsg', '?')[:160]}")
        except Exception as e:  # noqa: BLE001
            print(f"[wechat] send error: {type(e).__name__}: {e}")

    def _auth_headers(self, bot_token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {bot_token}",
            "X-WECHAT-UIN": self._wechat_uin,
        }


# --- Credential store -------------------------------------------------------

def _wechat_dir() -> Path:
    from openprogram.paths import get_state_dir
    d = get_state_dir() / "wechat"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _normalize_id(bot_id: str) -> str:
    for c in "@.:":
        bot_id = bot_id.replace(c, "-")
    return bot_id


def _creds_path(bot_id: str) -> Path:
    return _wechat_dir() / f"{_normalize_id(bot_id)}.json"


def _sync_path(bot_id: str) -> Path:
    return _wechat_dir() / f"{_normalize_id(bot_id)}.sync.json"


def _save_creds(creds: dict[str, str]) -> None:
    path = _creds_path(creds["ilink_bot_id"])
    path.write_text(json.dumps(creds, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _find_saved_creds() -> dict[str, str] | None:
    """Return the first credential file in the wechat dir, if any."""
    d = _wechat_dir()
    for entry in sorted(d.glob("*.json")):
        if entry.name.endswith(".sync.json"):
            continue
        try:
            return json.loads(entry.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _load_cursor(bot_id: str) -> str:
    path = _sync_path(bot_id)
    try:
        return (json.loads(path.read_text()) or {}).get("get_updates_buf", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _save_cursor(bot_id: str, cursor: str) -> None:
    try:
        _sync_path(bot_id).write_text(
            json.dumps({"get_updates_buf": cursor})
        )
    except OSError:
        pass


# --- QR login flow ----------------------------------------------------------

def _load_or_login() -> dict[str, str] | None:
    """Return existing credentials or drive the QR scan flow."""
    existing = _find_saved_creds()
    if existing:
        return existing
    print()
    print("[wechat] first-time login: you'll scan a QR with your phone.")
    return _qr_login()


def _qr_login() -> dict[str, str] | None:
    import requests
    try:
        resp = requests.get(
            f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"[wechat] failed to fetch QR: {e}")
        return None

    token = data.get("qrcode")
    # iLink returns qrcode_img_content as the URL string to encode into
    # a QR, NOT a base64-encoded image — the client renders it locally.
    # (weclaw/cmd/start.go:294 uses qrterminal.GenerateWithConfig on
    # this field for the same reason.)
    qr_url = data.get("qrcode_img_content")
    if not token or not qr_url:
        print("[wechat] QR response missing fields; aborting login")
        return None

    print()
    rendered = _print_qr_terminal(qr_url)
    if not rendered:
        print(f"[wechat] QR payload URL: {qr_url}")
        print("[wechat] install `qrcode` (`pip install openprogram[channels]`) "
              "to render the QR in-terminal, or paste the URL above into any "
              "QR generator and scan with WeChat.")
    print()
    print("[wechat] waiting for scan + confirm (up to a few minutes)...")
    while True:
        try:
            resp = requests.get(
                f"{DEFAULT_BASE_URL}/ilink/bot/get_qrcode_status?qrcode={token}",
                timeout=40,
            )
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            print(f"[wechat] status poll failed ({e}); retrying...")
            time.sleep(3)
            continue

        status = data.get("status")
        if status == "wait":
            continue
        if status == "scaned":
            print("[wechat] scanned on phone — now tap 'confirm' there.")
            continue
        if status == "expired":
            print("[wechat] QR expired; rerun to fetch a new one.")
            return None
        if status == "confirmed":
            creds = {
                "bot_token": data.get("bot_token") or "",
                "ilink_bot_id": data.get("ilink_bot_id") or "",
                "baseurl": data.get("baseurl") or "",
                "ilink_user_id": data.get("ilink_user_id") or "",
            }
            if not creds["bot_token"] or not creds["ilink_bot_id"]:
                print("[wechat] confirm response missing token/bot id; abort")
                return None
            _save_creds(creds)
            print(f"[wechat] logged in! credentials saved to {_creds_path(creds['ilink_bot_id'])}")
            return creds
        print(f"[wechat] unexpected status {status!r}; retrying...")


def _print_qr_terminal(payload: str) -> bool:
    """Render ``payload`` as an ASCII QR code to stdout. Returns True
    on success, False if the ``qrcode`` library isn't installed.

    Uses ``print_ascii(invert=True)`` so the QR has a white background
    and black squares — same orientation WeChat's phone camera expects.
    Half-block rendering isn't used because many terminal font pairings
    still clip at 1x row heights; the full-block ASCII path is
    reliable across iTerm / Terminal.app / VS Code terminal.
    """
    try:
        import qrcode
    except ImportError:
        return False
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    return True


# --- Misc helpers -----------------------------------------------------------

def _chunk(text: str, limit: int) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _make_wechat_uin() -> str:
    """Stable-per-process X-WECHAT-UIN the iLink server expects."""
    uin = random.getrandbits(32)  # 4 random bytes as a decimal string
    decimal = str(uin)
    return base64.b64encode(decimal.encode("ascii")).decode("ascii")


def _get_chat_runtime_or_die():
    from openprogram.webui import _runtime_management as rm
    rm._init_providers()
    rt = rm._chat_runtime
    if rt is None:
        raise RuntimeError(
            "No chat runtime configured. Run `openprogram setup` first."
        )
    try:
        from openprogram.setup_wizard import read_agent_prefs
        eff = read_agent_prefs().get("thinking_effort")
        if eff:
            rt.thinking_level = eff
    except Exception:
        pass
    return rt
