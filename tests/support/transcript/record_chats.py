"""Write every chat recording under a temporary home.

    python -m tests.support.transcript.record_chats [--out DIR] [--only NAME ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

RECORDINGS = Path(__file__).resolve().parents[3] / "apps/web/tests/chat/transcript/recordings"


def record(name: str, home: Path) -> dict:
    """One scenario on its own home directory and stores."""
    from tests.support.transcript.chat_scenarios import SCENARIOS
    from tests.support.transcript.live_server import live_chat

    with live_chat(home) as chat:
        # A browser reads the chat when it opens, before anyone types.
        chat.client_read("open")
        summary = SCENARIOS[name](chat)
        return chat.recording(name, summary)


def _scrub(text: str) -> str:
    """Drop this machine's paths, which reach a recording through stack traces."""
    return text.replace(str(Path(__file__).resolve().parents[3]), "<repository>").replace(sys.base_prefix, "<python>")


def dump(data: dict) -> str:
    """One timeline step per line, so a re-recording diffs by step."""
    head = {key: value for key, value in data.items() if key not in {"timeline", "cold"}}
    lines = [f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False, default=str)}" for key, value in head.items()]
    steps = ",\n".join("    " + json.dumps(step, ensure_ascii=False, default=str) for step in data["timeline"])
    lines.append(f'  "timeline": [\n{steps}\n  ]')
    lines.append(f'  "cold": {json.dumps(data["cold"], ensure_ascii=False, default=str)}')
    return _scrub("{\n" + ",\n".join(lines) + "\n}\n")


def main() -> None:  # pragma: no cover - a command for people
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RECORDINGS)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="openprogram-recording-") as home:
        os.environ["HOME"] = home
        from tests.support.transcript.chat_scenarios import SCENARIOS

        args.out.mkdir(parents=True, exist_ok=True)
        for name in args.only or SCENARIOS:
            root = Path(home) / name
            root.mkdir()
            data = record(name, root)
            path = args.out / f"{name}.json"
            path.write_text(dump(data), encoding="utf-8")
            print(f"{name}: {path.stat().st_size} bytes")  # noqa: T201 - command output


if __name__ == "__main__":
    main()
