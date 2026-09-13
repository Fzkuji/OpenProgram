from __future__ import annotations


from tests.support.desktop_source import read_desktop_bridge_source, read_desktop_source, read_server_source


import asyncio


import json


import threading


from pathlib import Path


import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]


class _WS:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

