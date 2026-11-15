"""Check the built docs site for broken internal links and fragments.

Run:  python -m scripts.docs_site.checklinks
Scans every href/src in docs/_site/*.html and reports targets that do not
exist on disk, and #fragments with no matching id in the target page.
External URLs are skipped.
"""
from __future__ import annotations

import html as html_lib
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from .build import DEPLOY_BASE

SITE = Path(__file__).resolve().parents[2] / "docs" / "_site"
_LINK = re.compile(r'(?:href|src)="([^"?#]*)(?:\?[^"#]*)?(?:#([^"]*))?"')
_ID = re.compile(r'\b(?:id|name)=["\']([^"\']+)["\']')
_SKIP = ("http://", "https://", "mailto:", "data:", "javascript:")


def _ids(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        text = path.read_text(encoding="utf-8", errors="replace")
        cache[path] = {html_lib.unescape(found) for found in _ID.findall(text)}
    return cache[path]


def main() -> int:
    if not SITE.is_dir():
        print(f"{SITE} does not exist: run python -m scripts.docs_site.build first", file=sys.stderr)
        return 2
    broken: list[tuple[str, str]] = []
    cache: dict[Path, set[str]] = {}
    for page in SITE.rglob("*.html"):
        html = page.read_text(encoding="utf-8", errors="replace")
        for m in _LINK.finditer(html):
            url, fragment = m.group(1), m.group(2)
            if url.startswith(_SKIP):
                continue
            if not url:
                target = page
            elif url.startswith(DEPLOY_BASE):
                target = SITE / url[len(DEPLOY_BASE):]
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
            elif fragment and target.suffix == ".html":
                if unquote(html_lib.unescape(fragment)) not in _ids(target, cache):
                    broken.append((str(page.relative_to(SITE)), f"{url}#{fragment}"))
    for pg, url in sorted(set(broken)):
        print(f"{pg}: {url}")
    print(f"{len(set(broken))} broken link(s)")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
