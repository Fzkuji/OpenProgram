from __future__ import annotations






import json




from pathlib import Path




REPO_ROOT = Path(__file__).resolve().parents[5]


class _WS:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

