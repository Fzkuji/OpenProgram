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
    POST /ilink/bot/getuploadurl upload slot for an outbound file

Media (image / voice / file / video) lives encrypted on Tencent's CDN;
see :mod:`openprogram.interfaces.channels._wechat_media`.

Credentials persist under
    ~/.openprogram/wechat/<bot_id>.json    (mode 0600)
    ~/.openprogram/wechat/<bot_id>.sync.json

--profile-aware — routes through openprogram.state.paths.get_state_dir().

Legal note: iLink is Tencent's own bot backend. weclaw's README
says personal-use only, not commercial. Ship accordingly.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import threading
import time
from typing import Optional

from openprogram.interfaces.channels.base import Channel
from openprogram.host.security.safe_http import configured_safe_client
from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

_log = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
LONG_POLL_TIMEOUT = 40
SEND_TIMEOUT = 15


def _wechat_client(base_url: str):
    origin = normalize_origin(base_url)
    return configured_safe_client(
        "channel.wechat.api",
        origin,
        owner_exception=OwnerURLException(
            consumer="channel.wechat.api", origin=origin
        ),
    )


class WechatChannel(Channel):
    platform_id = "wechat"
    # iLink 不支持 edit — progress streaming 的占位消息没法收尾, 会留下
    # 孤儿"⏳". 关掉, dispatch 直接返回完整 reply 走 send_text.
    progress_stream = False

    def __init__(self, account_id: str = "default") -> None:
        self.account_id = account_id
        self._wechat_uin = _make_wechat_uin()

    def run(self, stop: threading.Event) -> None:
        from openprogram.interfaces.channels import accounts as _accounts
        creds = _accounts.load_credentials("wechat", self.account_id)
        if not creds.get("bot_token") or not creds.get("ilink_bot_id"):
            _log.error("[wechat:%s] no saved credentials — run `openprogram channels "
                       "accounts login wechat --id %s` to scan a QR.",
                       self.account_id, self.account_id)
            return
        base = creds.get("baseurl") or DEFAULT_BASE_URL
        print(f"[wechat:{self.account_id}] online as "  # noqa: T201 - terminal status
              f"{creds.get('ilink_user_id','?')} — ctrl+c to stop")

        cursor = _load_cursor_for_account(self.account_id)
        consecutive_errors = 0
        backoff = 3

        while not stop.is_set():
            try:
                with _wechat_client(base) as client:
                    resp = client.post(
                        f"{base}/ilink/bot/getupdates",
                        headers=self._auth_headers(creds["bot_token"]),
                        json={
                            "get_updates_buf": cursor,
                            "base_info": {"channel_version": "1.0.0"},
                        },
                        timeout=LONG_POLL_TIMEOUT,
                    )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                consecutive_errors += 1
                wait = min(60, backoff * (2 ** min(consecutive_errors - 1, 4)))
                _log.info("[wechat:%s] poll failed (%s); retry in %ss",
                          self.account_id, type(e).__name__, wait)
                time.sleep(wait)
                continue

            consecutive_errors = 0
            errcode = data.get("errcode", 0)
            if errcode == -14:
                if cursor:
                    _log.info("[wechat:%s] session expired; resetting cursor", self.account_id)
                    cursor = ""
                    _save_cursor_for_account(self.account_id, cursor)
                    time.sleep(5)
                    continue
                _log.error("[wechat:%s] bot token invalid — relogin required.", self.account_id)
                return
            if data.get("ret", 0) != 0 and errcode != 0:
                _log.warning("[wechat:%s] poll error ret=%s errcode=%s",
                             self.account_id, data.get("ret"), errcode)
                time.sleep(3)
                continue

            new_cursor = data.get("get_updates_buf") or ""
            if new_cursor:
                cursor = new_cursor
                _save_cursor_for_account(self.account_id, cursor)

            for msg in data.get("msgs", []) or []:
                # parse 在 loop 里 (纯 dict 访问); dispatch 的
                # per-message 线程派发在 base.handle_inbound.
                self._handle_message(msg)

    # -------------------------------------------------------------------

    def _handle_message(self, msg: dict) -> None:
        if msg.get("message_type") != 1:
            return
        # 文本、图片、语音、文件、视频条目; 语音优先用微信自己的转写.
        from openprogram.interfaces.channels._wechat_media import parse_items
        text, attachments = parse_items(msg.get("item_list") or [])
        if not text and not attachments:
            return
        from_id = msg.get("from_user_id")
        if not from_id:
            return
        # iLink 取"正在输入"的 ticket 要带这个用户最近的 context_token.
        from openprogram.interfaces.channels._transport import remember_wechat_context
        remember_wechat_context(
            self.account_id, str(from_id), str(msg.get("context_token") or ""),
        )

        # Parse wechat iLink msg dict → ChannelMessage (audit 缺陷 4).
        from openprogram.interfaces.channels._message import ChannelMessage
        ch_msg = ChannelMessage(
            text=text,
            chat_id=str(from_id),
            user_id=str(from_id),
            user_display=str(from_id),
            message_id=str(
                msg.get("message_id") or msg.get("msg_id") or ""
            ),
            chat_type="direct",
            ts=float(msg.get("ts") or 0),
            attachments=attachments,
        )

        self.handle_inbound(ch_msg)

    def _auth_headers(self, bot_token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {bot_token}",
            "X-WECHAT-UIN": self._wechat_uin,
        }


# --- Credential store -------------------------------------------------------
# Moved to openprogram.interfaces.channels.accounts — the per-account store now
# owns credentials.json for every platform. These helpers operate on
# the long-poll cursor which is iLink-specific scratch and lives in
# the account dir too.


def _load_cursor_for_account(account_id: str) -> str:
    from openprogram.interfaces.channels.accounts import account_dir
    path = account_dir("wechat", account_id) / "cursor.json"
    try:
        return (json.loads(path.read_text(encoding="utf-8")) or {}).get("get_updates_buf", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def _save_cursor_for_account(account_id: str, cursor: str) -> None:
    from openprogram.interfaces.channels.accounts import account_dir
    from openprogram.models.auth.credentials import (
        PrivateAtomicWriteError,
        _private_atomic_write,
    )
    from openprogram.state.paths import get_state_dir

    payload = json.dumps({"get_updates_buf": cursor}).encode("utf-8")
    try:
        _private_atomic_write(
            account_dir("wechat", account_id) / "cursor.json",
            lambda handle: handle.write(payload),
            root=get_state_dir(),
        )
    except (OSError, PrivateAtomicWriteError):
        # The next poll carries the cursor in memory and saves it again.
        _log.warning("[wechat:%s] could not save the poll cursor", account_id)


# --- QR login flow — operates on one account ------------------------------

def login_account(account_id: str) -> dict[str, str] | None:
    """Drive the QR-scan flow and persist credentials under
    ``<state>/channels/wechat/accounts/<account_id>/credentials.json``.

    Returns the credential dict on success, or None if the user
    cancelled / the QR expired / anything failed. Idempotent: if the
    account already has working credentials we just return them.
    """
    from openprogram.interfaces.channels import accounts as _accounts
    existing = _accounts.load_credentials("wechat", account_id)
    if existing.get("bot_token") and existing.get("ilink_bot_id"):
        print(f"[wechat:{account_id}] already logged in "  # noqa: T201 - terminal prompt
              f"(ilink_bot_id={existing['ilink_bot_id']})")
        return existing
    creds = _qr_login()
    if creds is not None:
        _save_login(account_id, creds)
        print(f"[wechat:{account_id}] saved credentials to "  # noqa: T201 - terminal prompt
              f"{_accounts.account_credentials_path('wechat', account_id)}")
    return creds


def _save_login(account_id: str, creds: dict[str, str]) -> None:
    """Save a confirmed QR login and let the person who scanned it in.

    A WeChat bot is talked to by the WeChat user who scanned its QR code
    (``ilink_user_id``, the ``from_user_id`` of their messages). They
    already proved they own the account, so asking them for a pairing
    code on their first message only drops what they wrote. Anyone else
    still needs one.
    """
    from openprogram.interfaces.channels import _access
    from openprogram.interfaces.channels import accounts as _accounts
    _accounts.save_credentials("wechat", account_id, creds)
    owner = str(creds.get("ilink_user_id") or "").strip()
    if owner:
        _access.approve_user("wechat", account_id, owner)


def _qr_login() -> dict[str, str] | None:
    try:
        with _wechat_client(DEFAULT_BASE_URL) as client:
            resp = client.get(
                f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3",
                timeout=15,
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"[wechat] failed to fetch QR ({type(e).__name__})")  # noqa: T201 - terminal prompt
        return None

    token = data.get("qrcode")
    # iLink returns qrcode_img_content as the URL string to encode into
    # a QR, NOT a base64-encoded image — the client renders it locally.
    # (weclaw/cmd/start.go:294 uses qrterminal.GenerateWithConfig on
    # this field for the same reason.)
    qr_url = data.get("qrcode_img_content")
    if not token or not qr_url:
        print("[wechat] QR response missing fields; aborting login")  # noqa: T201 - terminal prompt
        return None

    print()  # noqa: T201 - terminal prompt
    rendered = _print_qr_terminal(qr_url)
    if not rendered:
        print(f"[wechat] QR payload URL: {qr_url}")  # noqa: T201 - terminal prompt
        print("[wechat] QR rendering is unavailable in this installation; "  # noqa: T201 - terminal prompt
              "reinstall the complete OpenProgram release, or paste the URL "
              "above into a QR generator and scan with WeChat.")
    print()  # noqa: T201 - terminal prompt
    print("[wechat] waiting for scan + confirm (up to a few minutes)...")  # noqa: T201 - terminal prompt
    while True:
        try:
            with _wechat_client(DEFAULT_BASE_URL) as client:
                resp = client.get(
                    f"{DEFAULT_BASE_URL}/ilink/bot/get_qrcode_status?qrcode={token}",
                    timeout=40,
                )
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            print(f"[wechat] status poll failed ({type(e).__name__}); retrying...")  # noqa: T201 - terminal prompt
            time.sleep(3)
            continue

        status = data.get("status")
        if status == "wait":
            continue
        if status == "scaned":
            print("[wechat] scanned on phone — now tap 'confirm' there.")  # noqa: T201 - terminal prompt
            continue
        if status == "expired":
            print("[wechat] QR expired; rerun to fetch a new one.")  # noqa: T201 - terminal prompt
            return None
        if status == "confirmed":
            creds = {
                "bot_token": data.get("bot_token") or "",
                "ilink_bot_id": data.get("ilink_bot_id") or "",
                "baseurl": data.get("baseurl") or "",
                "ilink_user_id": data.get("ilink_user_id") or "",
            }
            if not creds["bot_token"] or not creds["ilink_bot_id"]:
                print("[wechat] confirm response missing token/bot id; abort")  # noqa: T201 - terminal prompt
                return None
            print(f"[wechat] logged in (ilink_bot_id="  # noqa: T201 - terminal prompt
                  f"{creds['ilink_bot_id']})")
            return creds
        print(f"[wechat] unexpected status {status!r}; retrying...")  # noqa: T201 - terminal prompt


def _qr_to_ascii(payload: str) -> Optional[str]:
    """Render ``payload`` as an ASCII QR code returned as a string.
    Returns None when the ``qrcode`` library isn't installed.

    Used by the TUI / web frontend QR-login flows so they can paste
    the rendered code into the transcript instead of relying on the
    server's stdout. Same parameters as ``_print_qr_terminal``.
    """
    try:
        import qrcode
        from io import StringIO
    except ImportError:
        return None
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    buf = StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def login_account_event_driven(
    account_id: str,
    on_event,        # callable(dict) — receives qr_login envelopes
) -> Optional[dict]:
    """QR-login that pushes status envelopes through ``on_event``
    instead of printing. Same flow as ``login_account`` / ``_qr_login``
    but consumable by a server / TUI / GUI without scraping stdout.

    Envelope shapes pushed to ``on_event``:
      {"phase": "qr_ready", "url": <encoded payload>, "ascii": <str|None>}
      {"phase": "scanned"}
      {"phase": "confirmed"}
      {"phase": "expired"}
      {"phase": "error", "message": <str>}
      {"phase": "done", "credentials": {...}}

    Returns the credentials dict on success (also persisted to disk),
    or None on cancel / expire / error. Idempotent: when the account
    already has working credentials we emit a single ``done`` envelope
    and return them.
    """
    from openprogram.interfaces.channels import accounts as _acct
    existing = _acct.load_credentials("wechat", account_id)
    if existing.get("bot_token") and existing.get("ilink_bot_id"):
        on_event({"phase": "done", "credentials": existing,
                  "already_configured": True})
        return existing

    try:
        with _wechat_client(DEFAULT_BASE_URL) as client:
            resp = client.get(
                f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3",
                timeout=15,
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        on_event({"phase": "error",
                  "message": f"failed to fetch QR ({type(e).__name__})"})
        return None

    token = data.get("qrcode")
    qr_url = data.get("qrcode_img_content")
    if not token or not qr_url:
        on_event({"phase": "error",
                  "message": "QR response missing fields"})
        return None

    on_event({
        "phase": "qr_ready",
        "url": qr_url,
        "ascii": _qr_to_ascii(qr_url),
    })

    while True:
        try:
            with _wechat_client(DEFAULT_BASE_URL) as client:
                resp = client.get(
                    f"{DEFAULT_BASE_URL}/ilink/bot/get_qrcode_status?qrcode={token}",
                    timeout=40,
                )
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            on_event({"phase": "error",
                      "message": f"status poll failed ({type(e).__name__})"})
            time.sleep(3)
            continue

        status = data.get("status")
        if status == "wait":
            continue
        if status == "scaned":
            on_event({"phase": "scanned"})
            continue
        if status == "expired":
            on_event({"phase": "expired"})
            return None
        if status == "confirmed":
            creds = {
                "bot_token": data.get("bot_token") or "",
                "ilink_bot_id": data.get("ilink_bot_id") or "",
                "baseurl": data.get("baseurl") or "",
                "ilink_user_id": data.get("ilink_user_id") or "",
            }
            if not creds["bot_token"] or not creds["ilink_bot_id"]:
                on_event({"phase": "error",
                          "message": "confirm response missing fields"})
                return None
            on_event({"phase": "confirmed"})
            _acct.create("wechat", account_id) if not _acct.get(
                "wechat", account_id) else None
            _save_login(account_id, creds)
            on_event({"phase": "done", "credentials": creds,
                      "already_configured": False})
            return creds
        on_event({"phase": "error",
                  "message": f"unexpected status {status!r}"})
        time.sleep(2)


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

def _make_wechat_uin() -> str:
    """Stable-per-process X-WECHAT-UIN the iLink server expects."""
    uin = random.getrandbits(32)
    decimal = str(uin)
    return base64.b64encode(decimal.encode("ascii")).decode("ascii")
