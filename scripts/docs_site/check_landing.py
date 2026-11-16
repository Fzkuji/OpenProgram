"""Validate the published landing page's stable structure and metadata.

Run:  python -m scripts.docs_site.check_landing
"""

from __future__ import annotations

import json
import re
import struct
import tomllib
from html.parser import HTMLParser
from pathlib import Path

from openprogram.operations.project_site import DOCS_URL, INSTALL_SCRIPT_URL, SITE_URL

from . import nav as navmod
from .build import DEPLOY_BASE
from .template import render_page


ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "website" / "index.html"
README = ROOT / "README.md"
DOCS_DIR = ROOT / "docs"
DOCS_README = next((p for p in (DOCS_DIR / "README.md", DOCS_DIR / "README.html") if p.is_file()),
                   DOCS_DIR / "README.md")
DOCS_README_ZH = next((p for p in (DOCS_DIR / "README.zh.md", DOCS_DIR / "README.zh.html") if p.is_file()),
                      DOCS_DIR / "README.zh.md")
PYPROJECT = ROOT / "pyproject.toml"
BUILT_SITE = ROOT / "docs" / "_site"
SITE_TITLE = "OpenProgram: Self-Programming AI Agent Framework"
README_HERO = (
    "An open-source agent harness where agents are Python programs that improve themselves."
)
SITE_DESCRIPTION = (
    "Build self-programming AI agents that create and refine their own "
    "workflows with an open-source runtime for models, tools, memory, "
    "context, and multi-agent collaboration."
)
SOCIAL_IMAGE = DOCS_URL + "images/openprogram-social-card.png"
RELEASE_FEED = "https://github.com/Fzkuji/OpenProgram/releases.atom"
HERO_ALT = "OpenProgram workspace showing an agent session that ran two sub-agents"
HERO_AVIFS = ("chat_hero-home.avif", "chat_hero-home-light.avif")
HERO_AVIF_SIZE = (1600, 1000)
THEME_COLORS = {
    "(prefers-color-scheme: dark)": "#0e0f17",
    "(prefers-color-scheme: light)": "#f8f9fc",
}
# The landing's indigo palettes, taken from the logo.
PALETTE_BACKGROUNDS = {
    "dark": ("#0e0f17", "#141622", "#1a1c2b"),
    "light": ("#f8f9fc", "#f1f3f9", "#ffffff"),
}


class LandingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.images: set[str] = set()
        self.image_attrs: list[dict[str, str]] = []
        self.source_attrs: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.pictures: list[dict[str, list[dict[str, str]]]] = []
        self.anchors: set[str] = set()
        self.links: list[dict[str, str]] = []
        self.meta: list[dict[str, str]] = []
        self.text: list[str] = []
        self.styles: list[str] = []
        self.structured_data: list[dict] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._picture: dict[str, list[dict[str, str]]] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "picture":
            self._picture = {"images": [], "sources": []}
        if element_id := values.get("id"):
            self.ids.add(element_id)
        if tag == "img" and (src := values.get("src")):
            self.images.add(src)
            self.image_attrs.append(values)
            if self._picture is not None:
                self._picture["images"].append(values)
        if tag == "source":
            self.source_attrs.append(values)
            if self._picture is not None:
                self._picture["sources"].append(values)
        if tag == "button":
            self.buttons.append(values)
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
        if tag == "picture" and self._picture is not None:
            self.pictures.append(self._picture)
            self._picture = None
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


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(value: str) -> float:
        channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _isobmff_boxes(data: bytes, start: int, end: int):
    position = start
    while position + 8 <= end:
        size, box_type = struct.unpack(">I4s", data[position : position + 8])
        header_size = 8
        if size == 1:
            if position + 16 > end:
                return
            size = struct.unpack(">Q", data[position + 8 : position + 16])[0]
            header_size = 16
        elif size == 0:
            size = end - position
        box_end = position + size
        if size < header_size or box_end > end:
            return
        yield box_type, position + header_size, box_end
        position = box_end


def _avif_dimensions(path: Path) -> tuple[int, int] | None:
    """Read the primary AVIF item's associated spatial extents using stdlib."""
    data = path.read_bytes()
    top_level = list(_isobmff_boxes(data, 0, len(data)))
    ftyp = next((box for box in top_level if box[0] == b"ftyp"), None)
    if ftyp is None or b"avif" not in data[ftyp[1] : ftyp[2]]:
        return None
    for box_type, meta_start, meta_end in top_level:
        if box_type != b"meta" or meta_start + 4 > meta_end:
            continue
        children = list(_isobmff_boxes(data, meta_start + 4, meta_end))
        primary_item = None
        for child_type, child_start, child_end in children:
            if child_type != b"pitm" or child_start + 6 > child_end:
                continue
            version = data[child_start]
            item_size = 2 if version == 0 else 4
            if child_start + 4 + item_size <= child_end:
                primary_item = int.from_bytes(
                    data[child_start + 4 : child_start + 4 + item_size], "big"
                )
        for child_type, child_start, child_end in children:
            if child_type != b"iprp" or primary_item is None:
                continue
            properties: list[tuple[int, int] | None] = [None]
            associations: dict[int, list[int]] = {}
            for property_type, property_start, property_end in _isobmff_boxes(
                data, child_start, child_end
            ):
                if property_type == b"ipco":
                    for item_type, item_start, item_end in _isobmff_boxes(
                        data, property_start, property_end
                    ):
                        dimensions = None
                        if item_type == b"ispe" and item_start + 12 <= item_end:
                            dimensions = struct.unpack(
                                ">II", data[item_start + 4 : item_start + 12]
                            )
                        properties.append(dimensions)
                elif property_type == b"ipma" and property_start + 8 <= property_end:
                    version = data[property_start]
                    wide_index = int.from_bytes(
                        data[property_start + 1 : property_start + 4], "big"
                    ) & 1
                    position = property_start + 4
                    entry_count = int.from_bytes(data[position : position + 4], "big")
                    position += 4
                    for _ in range(entry_count):
                        item_size = 2 if version == 0 else 4
                        if position + item_size + 1 > property_end:
                            return None
                        item_id = int.from_bytes(
                            data[position : position + item_size], "big"
                        )
                        position += item_size
                        association_count = data[position]
                        position += 1
                        indexes = associations.setdefault(item_id, [])
                        association_size = 2 if wide_index else 1
                        index_mask = 0x7FFF if wide_index else 0x7F
                        for _ in range(association_count):
                            if position + association_size > property_end:
                                return None
                            association = int.from_bytes(
                                data[position : position + association_size], "big"
                            )
                            position += association_size
                            indexes.append(association & index_mask)
            for property_index in associations.get(primary_item, []):
                if property_index < len(properties) and properties[property_index]:
                    return properties[property_index]
    return None


def main() -> int:
    source = LANDING.read_text(encoding="utf-8")
    page = LandingParser()
    page.feed(source)
    failures: list[str] = []
    escaped_schema = render_page(
        title="</script><script>alert(1)</script>", body_html="", nav_html="",
        toc_html="", base="/docs/", canonical_url=DOCS_URL + "test.html",
        docs_root_url=DOCS_URL,
    )
    require("\\u003c/script>" in escaped_schema and "</script><script>" not in escaped_schema,
            "breadcrumb JSON does not safely encode page titles", failures)

    links = {(item.get("rel"), item.get("href")) for item in page.links}
    named_meta = {item.get("name"): item.get("content") for item in page.meta}
    property_meta = {
        item.get("property"): item.get("content") for item in page.meta
    }
    visible_text = " ".join(" ".join(page.text).split())
    css = "\n".join(page.styles)
    readme = README.read_text(encoding="utf-8")
    docs_readme = DOCS_README.read_text(encoding="utf-8")
    docs_readme_zh = DOCS_README_ZH.read_text(encoding="utf-8")
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    require(("canonical", SITE_URL) in links,
            "missing canonical URL", failures)
    require(("describedby", SITE_URL + "llms.txt") in links,
            "missing llms.txt discovery link", failures)
    feed_links = [
        item
        for item in page.links
        if (
            "alternate" in item.get("rel", "").split()
            and item.get("type", "").casefold() == "application/atom+xml"
        )
    ]
    require(
        len(feed_links) == 1 and feed_links[0].get("href") == RELEASE_FEED,
        "landing must expose exactly one release feed discovery link",
        failures,
    )
    require(f"<title>{SITE_TITLE}</title>" in source,
            "landing title differs from the product title", failures)
    require(("icon", "favicon.ico") in links,
            "missing root ICO favicon", failures)
    require(("icon", "docs/assets/mark.svg") in links,
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
    theme_colors = {
        (item.get("media"), item.get("content"))
        for item in page.meta if item.get("name") == "theme-color"
    }
    for media, color in THEME_COLORS.items():
        require((media, color) in theme_colors,
                f"missing {media} browser theme color", failures)
    # The dark and light screenshots are separate pictures; the dark one comes
    # first and is the page's one high-priority image.
    heroes = [
        image
        for image in page.image_attrs
        if image.get("alt") == HERO_ALT
    ]
    hero = heroes[0] if heroes else None
    require(len(heroes) == len(HERO_AVIFS),
            "landing hero needs one picture per theme", failures)
    for image, avif in zip(heroes, HERO_AVIFS):
        hero_picture = next(
            (picture for picture in page.pictures if image in picture["images"]),
            None,
        )
        require(
            hero_picture is not None
            and any(
                item.get("srcset") == "docs/images/" + avif
                and item.get("type") == "image/avif"
                and item.get("width") == str(HERO_AVIF_SIZE[0])
                and item.get("height") == str(HERO_AVIF_SIZE[1])
                for item in hero_picture["sources"]
            ),
            f"landing hero does not offer the optimized AVIF image {avif}",
            failures,
        )
        require(
            image.get("width") == "3024" and image.get("height") == "1890",
            "landing PNG fallback dimensions differ from its intrinsic dimensions",
            failures,
        )
    require(hero is not None and hero.get("fetchpriority") == "high",
            "landing LCP image is not high priority", failures)
    require(sum(image.get("fetchpriority") == "high"
                for image in page.image_attrs) == 1,
            "landing must have exactly one high-priority image", failures)
    require(all(image.get("width") and image.get("height")
                for image in page.image_attrs),
            "landing image is missing explicit dimensions", failures)
    command_buttons = [
        button
        for button in page.buttons
        if "command-card" in button.get("class", "").split()
    ]
    require(
        command_buttons and all("aria-label" not in button for button in command_buttons),
        "copy button accessible name overrides its visible text",
        failures,
    )
    # The first --faint belongs to the dark base palette, the second to light.
    faint_colors = re.findall(r"--faint\s*:\s*(#[0-9a-fA-F]{6})", css)
    require(len(faint_colors) == len(PALETTE_BACKGROUNDS),
            "landing secondary-text color is missing from a theme", failures)
    for faint, (theme, backgrounds) in zip(faint_colors, PALETTE_BACKGROUNDS.items()):
        for background in backgrounds:
            require(_contrast_ratio(faint, background) >= 4.5,
                    f"{theme} secondary text fails 4.5:1 contrast on {background}", failures)
    require(f"<b>{README_HERO}</b>" in readme,
            "README hero differs from the canonical tagline", failures)
    require(f"<b>{SITE_TITLE}</b>" in docs_readme,
            "docs hero differs from the product title", failures)
    require("<b>OpenProgram：自编程 AI Agent 框架</b>" in docs_readme_zh,
            "Chinese docs hero differs from the product positioning", failures)
    release_url = "https://github.com/Fzkuji/OpenProgram/releases"
    for name, document in (("README", readme), ("docs README", docs_readme),
                           ("Chinese docs README", docs_readme_zh)):
        require(release_url in document,
                f"{name} does not link to the current release", failures)
    require(project.get("description") == SITE_TITLE,
            "package description differs from the product title", failures)
    project_urls = project.get("urls", {})
    require(project_urls.get("Homepage") == SITE_URL,
            "package homepage is not the configured website address", failures)
    require(project_urls.get("Documentation") == DOCS_URL,
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
    ):
        require(phrase in visible_text, f"missing product copy: {phrase}", failures)
    require('<span class="program-word">program</span>' in source,
            "program keyword is not visually emphasized", failures)

    for removed in ("GUI-Agent-Harness", "Research-Agent-Harness"):
        require(removed not in source,
                f"landing page still links the removed {removed}", failures)
    for community_url in (
        "https://github.com/Fzkuji/OpenProgram/discussions",
        "https://github.com/Fzkuji/OpenProgram/blob/main/.github/CONTRIBUTING.md",
    ):
        require(community_url in page.anchors,
                f"missing community link {community_url}", failures)

    require("Agents are just Python functions." not in visible_text,
            "landing page still leads with the concept message", failures)

    for arxiv_id in ("2606.15874", "2608.03270"):
        require(f"https://arxiv.org/abs/{arxiv_id}" in source,
                f"missing related paper arXiv:{arxiv_id}", failures)
    require("KDD 2026 AgenticSE Workshop" not in visible_text,
            "landing page still emphasizes the workshop venue", failures)

    install = f"curl -fsSL {INSTALL_SCRIPT_URL} | sh"
    require(install in visible_text, "missing documented installer", failures)
    require('data-copy="openprogram web"' in source,
            "missing copyable openprogram run command", failures)
    require("pip install openprogram" not in visible_text,
            "landing page still advertises unsupported pip install", failures)

    for image in (
        "docs/images/logo-lockup-dark.svg",
        "docs/images/code_hero.png",
        "docs/images/chat_hero.png",
        "docs/images/browser_hero.png",
        "docs/images/chat_edit.png",
        "docs/images/logo-lockup-light.svg",
        "docs/images/code_hero_light.png",
        "docs/images/chat_hero_light.png",
        "docs/images/browser_hero_light.png",
        "docs/images/chat_edit_light.png",
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
            DOCS_URL + "capabilities/agentic-programming/self-programming-ai-agents.html",
            DOCS_URL + "reference/comparisons/ai-agent-frameworks.html",
        ):
            require(f"<loc>{url}</loc>" in sitemap,
                    f"sitemap is missing {url}", failures)
        require(DOCS_URL + "README.html" not in sitemap,
                "sitemap includes the duplicate docs README URL", failures)
    built_favicon = BUILT_SITE / "favicon.ico"
    require(built_favicon.is_file(), "docs build did not produce favicon.ico", failures)
    if built_favicon.is_file():
        require(built_favicon.read_bytes() == (ROOT / "apps/web/app/favicon.ico").read_bytes(),
                "built favicon differs from the application favicon", failures)
    built_social_card = BUILT_SITE / "images" / "openprogram-social-card.png"
    require(built_social_card.is_file(),
            "docs build did not produce the social card", failures)
    if built_social_card.is_file():
        require(built_social_card.read_bytes()
                == (ROOT / "docs/images/openprogram-social-card.png").read_bytes(),
                "built social card differs from the source asset", failures)
    for avif in HERO_AVIFS:
        optimized_hero = ROOT / "docs/images" / avif
        require(optimized_hero.is_file(), f"optimized landing hero {avif} is missing", failures)
        if not optimized_hero.is_file():
            continue
        require(optimized_hero.stat().st_size < 150_000,
                f"optimized landing hero {avif} exceeds 150 KiB", failures)
        require(_avif_dimensions(optimized_hero) == HERO_AVIF_SIZE,
                f"optimized landing hero {avif} dimensions differ from 1600x1000", failures)
        built_optimized_hero = BUILT_SITE / "images" / avif
        require(built_optimized_hero.is_file(),
                f"docs build did not copy the optimized landing hero {avif}", failures)
        if built_optimized_hero.is_file():
            require(built_optimized_hero.read_bytes() == optimized_hero.read_bytes(),
                    f"built optimized hero {avif} differs from its source", failures)

    redirect_source = BUILT_SITE.parent / "redirects.json"
    redirects = json.loads(redirect_source.read_text(encoding="utf-8")) if redirect_source.exists() else {}
    language_pairs: set[tuple[Path, Path]] = set()
    for html_path in BUILT_SITE.rglob("*.html"):
        if html_path.name.endswith(".raw.html"):
            if html_path.relative_to(BUILT_SITE).as_posix() in redirects:
                continue  # Retired raw URLs are redirects, not visualization pages.
            raw_head = LandingParser()
            raw_head.feed(html_path.read_text(encoding="utf-8").split("</head>", 1)[0])
            require([link.get("href") for link in raw_head.links
                     if "describedby" in link.get("rel", "").split()]
                    == [SITE_URL + "llms.txt"],
                    f"invalid raw-page llms.txt discovery in {html_path}", failures)
            continue
        suffix = ".viz.html" if html_path.name.endswith(".viz.html") else ".html"
        if html_path.name.endswith(".zh" + suffix):
            en_path = html_path.with_name(html_path.name.removesuffix(".zh" + suffix) + suffix)
            zh_path = html_path
        elif html_path == BUILT_SITE / "index.html":
            en_path = html_path
            zh_path = BUILT_SITE / "README.zh.html"
        else:
            en_path = html_path
            zh_path = html_path.with_name(html_path.name.removesuffix(suffix) + ".zh" + suffix)

        head = html_path.read_text(encoding="utf-8").split("</head>", 1)[0]
        head_page = LandingParser()
        head_page.feed(head)
        alternates = {
            (link.get("hreflang"), link.get("href"))
            for link in head_page.links
            if "alternate" in link.get("rel", "").split() and link.get("hreflang")
        }
        canonical_url = next(
            (link.get("href") for link in head_page.links
             if "canonical" in link.get("rel", "").split()),
            "",
        )
        retired_target = redirects.get(html_path.relative_to(BUILT_SITE).as_posix())
        if retired_target is not None:
            require(canonical_url == DEPLOY_BASE + retired_target,
                    f"incorrect redirect canonical URL in {html_path}", failures)
            require(any(meta.get("name") == "robots" and meta.get("content") == "noindex"
                        for meta in head_page.meta),
                    f"redirect must be excluded from indexing in {html_path}", failures)
            # Retired URLs are not articles or translated copies of articles.
            continue
        if canonical_url:
            require([link.get("href") for link in head_page.links
                     if "describedby" in link.get("rel", "").split()]
                    == [SITE_URL + "llms.txt"],
                    f"invalid llms.txt discovery in {html_path}", failures)
        markdown_links = [link.get("href") for link in head_page.links
                          if "alternate" in link.get("rel", "").split()
                          and link.get("type", "").casefold() == "text/markdown"]
        source_copy = html_path.with_suffix(html_path.suffix + ".md")
        expected_markdown = []
        if source_copy.is_file():
            expected_markdown = [canonical_url + "index.html.md"
                                 if canonical_url.endswith("/") else canonical_url + ".md"]
        require(markdown_links == expected_markdown,
                f"invalid Markdown discovery in {html_path}", failures)
        breadcrumbs = [
            item for item in head_page.structured_data
            if item.get("@type") == "BreadcrumbList"
        ]
        is_docs_root = canonical_url.rstrip("/") == DOCS_URL.rstrip("/")
        if canonical_url:
            require(len(breadcrumbs) == (0 if is_docs_root else 1),
                    f"invalid breadcrumb structured data count in {html_path}", failures)
        if canonical_url and breadcrumbs:
            items = breadcrumbs[0].get("itemListElement", [])
            require(
                len(items) == 2
                and items[0].get("position") == 1
                and items[0].get("name") == "OpenProgram Docs"
                and items[0].get("item") == DOCS_URL
                and items[1].get("position") == 2
                and bool(items[1].get("name")),
                f"invalid breadcrumb trail in {html_path}", failures,
            )
        if not (en_path.is_file() and zh_path.is_file()):
            require(not alternates,
                    f"unpaired page has hreflang links in {html_path}", failures)
            continue

        language_pairs.add((en_path, zh_path))
        en_rel = en_path.relative_to(BUILT_SITE).as_posix()
        zh_rel = zh_path.relative_to(BUILT_SITE).as_posix()
        en_url = DOCS_URL + en_rel
        zh_url = DOCS_URL + zh_rel
        if en_path in (BUILT_SITE / "README.html", BUILT_SITE / "index.html"):
            en_url = DOCS_URL
        expected = {
            ("en", en_url),
            ("zh-Hans", zh_url),
            ("x-default", en_url),
        }
        require(alternates == expected,
                f"incomplete reciprocal hreflang cluster in {html_path}", failures)
    require(bool(language_pairs), "docs build contains no bilingual page pairs", failures)
    for doc_page in navmod.discover(ROOT / "docs"):
        for src, out in ((doc_page.src, doc_page.out), (doc_page.zh_src, doc_page.zh_out)):
            if src is None or out is None or src.suffix != ".md":
                continue
            copy = (BUILT_SITE / out).with_suffix(out.suffix + ".md")
            require(copy.is_file() and copy.read_bytes() == src.read_bytes(),
                    f"missing or altered Markdown source: {copy}", failures)
    index_markdown = BUILT_SITE / "index.html.md"
    # An HTML overview publishes a generated copy; only a Markdown one is verbatim.
    require(index_markdown.is_file()
            and (DOCS_README.suffix != ".md" or index_markdown.read_bytes() == DOCS_README.read_bytes()),
            "missing or altered index Markdown source", failures)
    llms = BUILT_SITE / "llms.txt"
    require(llms.is_file(), "docs build did not produce llms.txt", failures)
    if llms.is_file():
        llms_text = llms.read_text(encoding="utf-8")
        for target in re.findall(re.escape(DOCS_URL) + r"([^\s)]+\.html\.md)", llms_text):
            require((BUILT_SITE / target).is_file(),
                    f"llms.txt Markdown target is missing: {target}", failures)

        for url in (
            SITE_URL,
            DOCS_URL,
            "https://github.com/Fzkuji/OpenProgram",
            DOCS_URL + "capabilities/agentic-programming/self-programming-ai-agents.html",
            DOCS_URL + "reference/comparisons/ai-agent-frameworks.html",
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
