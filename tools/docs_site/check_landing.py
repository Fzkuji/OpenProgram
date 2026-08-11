"""Validate the published landing page's stable structure and metadata.

Run:  python -m tools.docs_site.check_landing
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "site" / "index.html"


class LandingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.images: set[str] = set()
        self.anchors: set[str] = set()
        self.links: list[dict[str, str]] = []
        self.meta: list[dict[str, str]] = []
        self.text: list[str] = []
        self.styles: list[str] = []
        self.structured_data: list[dict] = []
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if element_id := values.get("id"):
            self.ids.add(element_id)
        if tag == "img" and (src := values.get("src")):
            self.images.add(src)
        if tag == "a" and (href := values.get("href")):
            self.anchors.add(href)
        if tag == "link":
            self.links.append(values)
        if tag == "meta":
            self.meta.append(values)
        if tag == "style":
            self._capture = "style"
            self._buffer = []
        if tag == "script" and values.get("type") == "application/ld+json":
            self._capture = "json"
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._capture == "style":
            self.styles.append("".join(self._buffer))
            self._capture = None
        if tag == "script" and self._capture == "json":
            self.structured_data.append(json.loads("".join(self._buffer)))
            self._capture = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._capture:
            self._buffer.append(data)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    source = LANDING.read_text(encoding="utf-8")
    page = LandingParser()
    page.feed(source)
    failures: list[str] = []

    links = {(item.get("rel"), item.get("href")) for item in page.links}
    named_meta = {item.get("name"): item.get("content") for item in page.meta}
    property_meta = {
        item.get("property"): item.get("content") for item in page.meta
    }
    visible_text = " ".join(" ".join(page.text).split())
    css = "\n".join(page.styles)

    require(("canonical", "https://openprogram.io/") in links,
            "missing canonical URL", failures)
    require(any(item.get("rel") == "icon" for item in page.links),
            "missing favicon", failures)
    require(property_meta.get("og:image", "").startswith("https://openprogram.io/"),
            "missing absolute Open Graph image", failures)
    require(named_meta.get("twitter:card") == "summary_large_image",
            "missing large Twitter card", failures)
    require(named_meta.get("theme-color") == "#07080a",
            "missing dark browser theme color", failures)
    require(any(item.get("@type") == "SoftwareSourceCode"
                for item in page.structured_data),
            "missing SoftwareSourceCode structured data", failures)

    for section_id in (
        "use-cases", "quick-start", "capabilities", "interfaces",
        "mechanisms", "papers", "start",
    ):
        require(section_id in page.ids, f"missing #{section_id} section", failures)

    for phrase in (
        "Build agents that run in Python.",
        "Use an agent or build your own.",
        "Everything you need to run agents.",
        "GUI Agent", "Research Agent", "Wiki Agent",
    ):
        require(phrase in visible_text, f"missing product copy: {phrase}", failures)

    for harness_url in (
        "https://github.com/Fzkuji/GUI-Agent-Harness",
        "https://github.com/Fzkuji/Research-Agent-Harness",
        "https://github.com/Fzkuji/Wiki-Agent-Harness",
    ):
        require(harness_url in page.anchors,
                f"missing harness link {harness_url}", failures)

    require("Agents are just Python functions." not in visible_text,
            "landing page still leads with the concept message", failures)

    for arxiv_id in ("2606.15874", "2608.03270"):
        require(f"https://arxiv.org/abs/{arxiv_id}" in source,
                f"missing related paper arXiv:{arxiv_id}", failures)
    require("KDD 2026 AgenticSE Workshop" not in visible_text,
            "landing page still emphasizes the workshop venue", failures)

    install = "curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/scripts/install.sh | bash"
    require(install in visible_text, "missing documented installer", failures)
    require('data-copy="openprogram"' in source,
            "missing copyable openprogram run command", failures)
    require("pip install openprogram" not in visible_text,
            "landing page still advertises unsupported pip install", failures)

    for image in (
        "/docs/images/logo-lockup-dark.svg",
        "/docs/images/code_hero.png",
        "/docs/images/chat_hero.png",
        "/docs/images/tui_hero.png",
    ):
        require(image in page.images, f"missing product image {image}", failures)

    require(re.search(r"\.reveal\s*\{[^}]*opacity\s*:\s*1", css) is not None,
            "reveal content is not visible by default", failures)
    require(re.search(r"\.reveal[^}]*\{[^}]*opacity\s*:\s*0", css) is None,
            "reveal CSS can hide landing-page content", failures)
    require(re.search(
        r"\.command-body\s*\{[^}]*white-space\s*:\s*nowrap[^}]*"
        r"overflow-x\s*:\s*auto", css,
    ) is not None, "command text can wrap inside the terminal card", failures)

    if failures:
        for failure in failures:
            print(f"landing: {failure}")
        return 1
    print("check-landing: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
