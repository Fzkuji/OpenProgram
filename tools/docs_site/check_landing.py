"""Validate the published landing page's stable structure and metadata.

Run:  python -m tools.docs_site.check_landing
"""

from __future__ import annotations

import json
import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "site" / "index.html"
README = ROOT / "README.md"
DOCS_README = ROOT / "docs" / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
BUILT_SITE = ROOT / "docs" / "_site"
SITE_TITLE = "OpenProgram: Self-Programming AI Agent Framework"
SITE_DESCRIPTION = (
    "Build self-programming AI agents that create and refine their own "
    "workflows with an open-source runtime for models, tools, memory, "
    "context, and multi-agent collaboration."
)
SOCIAL_IMAGE = "https://openprogram.io/docs/images/openprogram-social-card.png"


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
    readme = README.read_text(encoding="utf-8")
    docs_readme = DOCS_README.read_text(encoding="utf-8")
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    require(("canonical", "https://openprogram.io/") in links,
            "missing canonical URL", failures)
    require(f"<title>{SITE_TITLE}</title>" in source,
            "landing title differs from the product title", failures)
    require(("icon", "/favicon.ico") in links,
            "missing root ICO favicon", failures)
    require(("icon", "/docs/assets/mark.svg") in links,
            "missing SVG favicon", failures)
    require(property_meta.get("og:title") == SITE_TITLE,
            "Open Graph title differs from the landing title", failures)
    require(named_meta.get("twitter:title") == SITE_TITLE,
            "Twitter title differs from the landing title", failures)
    require(named_meta.get("description") == SITE_DESCRIPTION,
            "landing description differs from the product description", failures)
    require(property_meta.get("og:description") == SITE_DESCRIPTION,
            "Open Graph description differs from the product description", failures)
    require(named_meta.get("twitter:description") == SITE_DESCRIPTION,
            "Twitter description differs from the product description", failures)
    require(property_meta.get("og:image") == SOCIAL_IMAGE,
            "Open Graph image is not the canonical social card", failures)
    require(property_meta.get("og:image:secure_url") == SOCIAL_IMAGE,
            "missing secure Open Graph image URL", failures)
    require(property_meta.get("og:image:width") == "1200"
            and property_meta.get("og:image:height") == "630",
            "Open Graph image dimensions differ from the social card", failures)
    require(named_meta.get("twitter:image") == SOCIAL_IMAGE,
            "Twitter image is not the canonical social card", failures)
    require(named_meta.get("twitter:card") == "summary_large_image",
            "missing large Twitter card", failures)
    require(named_meta.get("theme-color") == "#07080a",
            "missing dark browser theme color", failures)
    require(f"<b>{SITE_TITLE}</b>" in readme,
            "README hero differs from the product title", failures)
    require(f"<b>{SITE_TITLE}</b>" in docs_readme,
            "docs hero differs from the product title", failures)
    require(project.get("description") == SITE_TITLE,
            "package description differs from the product title", failures)
    project_urls = project.get("urls", {})
    require(project_urls.get("Homepage") == "https://openprogram.io/",
            "package homepage is not the canonical website", failures)
    require(project_urls.get("Documentation") == "https://openprogram.io/docs/",
            "package documentation URL is not canonical", failures)
    require(project_urls.get("Repository") == "https://github.com/Fzkuji/OpenProgram",
            "package repository URL is not canonical", failures)
    software = next((item for item in page.structured_data
                     if item.get("@type") == "SoftwareSourceCode"), None)
    require(software is not None,
            "missing SoftwareSourceCode structured data", failures)
    require(software is not None and software.get("description") == SITE_DESCRIPTION,
            "structured data differs from the product description", failures)

    for section_id in (
        "use-cases", "quick-start", "capabilities", "interfaces",
        "mechanisms", "papers", "start",
    ):
        require(section_id in page.ids, f"missing #{section_id} section", failures)

    for phrase in (
        "Build agents that program their own workflows.",
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

    sitemap_path = BUILT_SITE / "sitemap.xml"
    require(sitemap_path.is_file(), "docs build did not produce sitemap.xml", failures)
    if sitemap_path.is_file():
        sitemap = sitemap_path.read_text(encoding="utf-8")
        for url in (
            "https://openprogram.io/docs/capabilities/agentic-programming/self-programming-ai-agents.html",
            "https://openprogram.io/docs/comparisons/ai-agent-frameworks.html",
        ):
            require(f"<loc>{url}</loc>" in sitemap,
                    f"sitemap is missing {url}", failures)
        require("https://openprogram.io/docs/README.html" not in sitemap,
                "sitemap includes the duplicate docs README URL", failures)
    built_favicon = BUILT_SITE / "favicon.ico"
    require(built_favicon.is_file(), "docs build did not produce favicon.ico", failures)
    if built_favicon.is_file():
        require(built_favicon.read_bytes() == (ROOT / "web/app/favicon.ico").read_bytes(),
                "built favicon differs from the application favicon", failures)
    built_social_card = BUILT_SITE / "images" / "openprogram-social-card.png"
    require(built_social_card.is_file(),
            "docs build did not produce the social card", failures)
    if built_social_card.is_file():
        require(built_social_card.read_bytes()
                == (ROOT / "docs/images/openprogram-social-card.png").read_bytes(),
                "built social card differs from the source asset", failures)
    llms = BUILT_SITE / "llms.txt"
    require(llms.is_file(), "docs build did not produce llms.txt", failures)
    if llms.is_file():
        llms_text = llms.read_text(encoding="utf-8")
        for url in (
            "https://openprogram.io/",
            "https://openprogram.io/docs/",
            "https://github.com/Fzkuji/OpenProgram",
            "https://openprogram.io/docs/capabilities/agentic-programming/self-programming-ai-agents.html",
            "https://openprogram.io/docs/comparisons/ai-agent-frameworks.html",
        ):
            require(url in llms_text, f"llms.txt is missing {url}", failures)

    if failures:
        for failure in failures:
            print(f"landing: {failure}")
        return 1
    print("check-landing: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
