from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit(f"staged chat.html is missing: {path}")
    html = path.read_text(encoding="utf-8")
    if 'aria-label="Authenticating"' in html:
        raise SystemExit(f"staged chat.html still ships Authenticating: {path}")
    start = html.lower().find("<body")
    body = html[start:] if start >= 0 else html
    match = re.search(r"<script[\s>]", body, flags=re.I)
    paint = body[: match.start()] if match else body
    if 'id="sidebar"' not in paint:
        raise SystemExit(
            f'staged chat.html first-paint lacks id="sidebar": {path}'
        )


if __name__ == "__main__":
    main()
