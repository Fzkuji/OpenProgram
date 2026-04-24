"""Telegram bot channel via the public Bot API (long-polling).

Intentionally dep-free beyond ``requests`` (already transitive) — no
``python-telegram-bot`` library. The Bot API is simple enough that
raw HTTP is easier to reason about and keeps the dependency surface
small.

Protocol summary:
    getUpdates  long-poll incoming messages (offset = last_seen + 1)
    sendMessage reply to a chat
    getMe       used on start to confirm the token
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from openprogram.channels.base import Channel


TELEGRAM_API = "https://api.telegram.org"
MAX_MSG_CHARS = 4000   # Telegram caps at 4096; leave headroom


class TelegramChannel(Channel):
    platform_id = "telegram"

    def __init__(self) -> None:
        from openprogram.setup_wizard import _read_config
        cfg = _read_config()
        ch = (cfg.get("channels", {}) or {}).get("telegram", {}) or {}
        env_name = ch.get("api_key_env") or "TELEGRAM_BOT_TOKEN"
        token = (
            os.environ.get(env_name)
            or (cfg.get("api_keys", {}) or {}).get(env_name)
        )
        if not token:
            raise RuntimeError(
                f"Telegram channel: missing token. Set ${env_name} or re-run "
                f"`openprogram config channels`."
            )
        self.token = token
        self.base = f"{TELEGRAM_API}/bot{token}"
        self.offset = 0

    # --- public API -------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        import requests

        rt = _get_chat_runtime_or_die()
        me = self._get_me()
        if me:
            print(f"[telegram] @{me.get('username','?')} online "
                  f"(model={getattr(rt, 'model', '?')}) — ctrl+c to stop")
        else:
            print("[telegram] online (identity check failed); "
                  "continuing anyway")

        while not stop.is_set():
            try:
                r = requests.get(
                    f"{self.base}/getUpdates",
                    params={"offset": self.offset, "timeout": 25},
                    timeout=40,
                )
                data = r.json() if r.ok else {}
                if not data.get("ok"):
                    print(f"[telegram] API error {r.status_code}: "
                          f"{(data.get('description') or r.text)[:200]}")
                    time.sleep(5)
                    continue
                for upd in data.get("result", []):
                    self.offset = upd["update_id"] + 1
                    self._handle_update(upd, rt)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[telegram] poll failed: {type(e).__name__}: {e}")
                # brief back-off so we don't hammer on persistent errors
                time.sleep(3)

    # --- internals --------------------------------------------------------

    def _get_me(self) -> dict[str, Any] | None:
        import requests
        try:
            r = requests.get(f"{self.base}/getMe", timeout=10)
            if r.ok and r.json().get("ok"):
                return r.json().get("result")
        except Exception:
            pass
        return None

    def _handle_update(self, upd: dict, rt) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        text = msg.get("text")
        if not text:
            return  # skip non-text for now (images, commands, ...)
        chat = msg.get("chat", {}) or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return

        who = chat.get("username") or chat.get("title") or str(chat_id)
        snippet = text[:60] + ("..." if len(text) > 60 else "")
        print(f"[telegram] <{who}> {snippet}")

        from openprogram.channels._conversation import turn_with_history
        reply_text = turn_with_history(
            platform="telegram",
            user_id=str(chat_id),
            user_text=text,
            rt=rt,
            user_display=who,
        )

        self._send(chat_id, reply_text)

    def _send(self, chat_id: int, text: str) -> None:
        import requests
        # Chunk for Telegram's per-message char cap.
        parts = (
            [text[i:i + MAX_MSG_CHARS] for i in range(0, len(text), MAX_MSG_CHARS)]
            or [""]
        )
        for p in parts:
            try:
                requests.post(
                    f"{self.base}/sendMessage",
                    json={"chat_id": chat_id, "text": p},
                    timeout=10,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[telegram] send failed: {e}")
                return


def _get_chat_runtime_or_die():
    from openprogram.webui import _runtime_management as rm
    rm._init_providers()
    rt = rm._chat_runtime
    if rt is None:
        raise RuntimeError(
            "No chat runtime configured. Run `openprogram setup` first."
        )
    # Apply user's default thinking effort so bot replies match the REPL.
    try:
        from openprogram.setup_wizard import read_agent_prefs
        eff = read_agent_prefs().get("thinking_effort")
        if eff:
            rt.thinking_level = eff
    except Exception:
        pass
    return rt
