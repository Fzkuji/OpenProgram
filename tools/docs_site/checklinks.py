"""Check the built docs site for broken internal links.

Run:  python -m tools.docs_site.checklinks
Scans every href/src in docs/_site/*.html and reports targets that do not
exist on disk. External URLs and pure-anchor links are skipped.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[2] / "docs" / "_site"
_LINK = re.compile(r'(?:href|src)="([^"#?]+)')
_SKIP = ("http://", "https://", "mailto:", "data:", "javascript:")


def main() -> int:
    broken: list[tuple[str, str]] = []
    for page in SITE.rglob("*.html"):
        html = page.read_text(encoding="utf-8", errors="replace")
        for m in _LINK.finditer(html):
            url = m.group(1)
            if not url or url.startswith(_SKIP):
                continue
            if url.startswith("/docs/"):
                target = SITE / url[len("/docs/"):]
            elif url.startswith("/"):
                continue  # outside the docs mount (web UI routes etc.)
            else:
                target = page.parent / url
            if url.endswith("/"):
                target = target / "index.html"
            try:
                target = target.resolve()
            except OSError:
                pass
            if not target.exists():
                broken.append((str(page.relative_to(SITE)), url))
    for pg, url in sorted(set(broken)):
        print(f"{pg}: {url}")
    print(f"{len(set(broken))} broken link(s)")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
