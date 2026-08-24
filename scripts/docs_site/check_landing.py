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

from .template import render_page


ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "website" / "index.html"
README = ROOT / "README.md"
DOCS_README = ROOT / "docs" / "README.md"
DOCS_README_ZH = ROOT / "docs" / "README.zh.md"
PYPROJECT = ROOT / "pyproject.toml"
BUILT_SITE = ROOT / "docs" / "_site"
SITE_TITLE = "OpenProgram: Self-Programming AI Agent Framework"
README_HERO = (
    "Self-Programming AI Assistant. Capture, automate, and refine all your workflows."
)
SITE_DESCRIPTION = (
    "Build self-programming AI agents that create and refine their own "
    "workflows with an open-source runtime for models, tools, memory, "
    "context, and multi-agent collaboration."
)
SOCIAL_IMAGE = "https://openprogram.io/docs/images/openprogram-social-card.png"
RELEASE_FEED = "https://github.com/Fzkuji/OpenProgram/releases.atom"


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
        toc_html="", base="/docs/", canonical_url="https://openprogram.io/docs/test.html",
        docs_root_url="https://openprogram.io/docs/",
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

    require(("canonical", "https://openprogram.io/") in links,
            "missing canonical URL", failures)
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
    hero = next(
        (
            image
            for image in page.image_attrs
            if image.get("alt")
            == "OpenProgram Web UI showing an agent session with a conversation summary"
        ),
        None,
    )
    hero_picture = next(
        (picture for picture in page.pictures if hero in picture["images"]),
        None,
    )
    require(
        hero_picture is not None
        and any(
            item.get("srcset") == "/docs/images/chat_hero-home.avif"
            and item.get("type") == "image/avif"
            and item.get("width") == "800"
            and item.get("height") == "500"
            for item in hero_picture["sources"]
        ),
        "landing hero does not offer the optimized AVIF image",
        failures,
    )
    require(hero is not None and hero.get("fetchpriority") == "high",
            "landing LCP image is not high priority", failures)
    require(
        hero is not None
        and hero.get("width") == "3024"
        and hero.get("height") == "1890",
        "landing PNG fallback dimensions differ from its intrinsic dimensions",
        failures,
    )
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
    faint_match = re.search(r"--faint\s*:\s*(#[0-9a-fA-F]{6})", css)
    require(faint_match is not None,
            "landing secondary-text color is missing", failures)
    if faint_match:
        faint = faint_match.group(1)
        for background in ("#07080a", "#0d1014", "#11151b"):
            require(_contrast_ratio(faint, background) >= 4.5,
                    f"secondary text fails 4.5:1 contrast on {background}", failures)
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
    require('<span class="program-word">program</span>' in source,
            "program keyword is not visually emphasized", failures)

    for harness_url in (
        "https://github.com/Fzkuji/GUI-Agent-Harness",
        "https://github.com/Fzkuji/Research-Agent-Harness",
        "https://github.com/Fzkuji/Wiki-Agent-Harness",
    ):
        require(harness_url in page.anchors,
                f"missing harness link {harness_url}", failures)
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

    install = "curl -fsSL https://openprogram.io/install | sh"
    require(install in visible_text, "missing documented installer", failures)
    require('data-copy="openprogram web"' in source,
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
        require(built_favicon.read_bytes() == (ROOT / "apps/web/app/favicon.ico").read_bytes(),
                "built favicon differs from the application favicon", failures)
    built_social_card = BUILT_SITE / "images" / "openprogram-social-card.png"
    require(built_social_card.is_file(),
            "docs build did not produce the social card", failures)
    if built_social_card.is_file():
        require(built_social_card.read_bytes()
                == (ROOT / "docs/images/openprogram-social-card.png").read_bytes(),
                "built social card differs from the source asset", failures)
    optimized_hero = ROOT / "docs/images/chat_hero-home.avif"
    require(optimized_hero.is_file(), "optimized landing hero is missing", failures)
    if optimized_hero.is_file():
        require(optimized_hero.stat().st_size < 150_000,
                "optimized landing hero exceeds 150 KiB", failures)
        require(_avif_dimensions(optimized_hero) == (800, 500),
                "optimized landing hero dimensions differ from 800x500", failures)
        built_optimized_hero = BUILT_SITE / "images/chat_hero-home.avif"
        require(built_optimized_hero.is_file(),
                "docs build did not copy the optimized landing hero", failures)
        if built_optimized_hero.is_file():
            require(built_optimized_hero.read_bytes() == optimized_hero.read_bytes(),
                    "built optimized hero differs from its source", failures)

    language_pairs: set[tuple[Path, Path]] = set()
    for html_path in BUILT_SITE.rglob("*.html"):
        if html_path.name.endswith(".raw.html"):
            continue
        if html_path.name.endswith(".zh.html"):
            en_path = html_path.with_name(html_path.name.replace(".zh.html", ".html"))
            zh_path = html_path
        elif html_path == BUILT_SITE / "index.html":
            en_path = html_path
            zh_path = BUILT_SITE / "README.zh.html"
        else:
            en_path = html_path
            zh_path = html_path.with_name(html_path.stem + ".zh.html")

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
        breadcrumbs = [
            item for item in head_page.structured_data
            if item.get("@type") == "BreadcrumbList"
        ]
        is_docs_root = canonical_url.rstrip("/") == "https://openprogram.io/docs"
        if canonical_url:
            require(len(breadcrumbs) == (0 if is_docs_root else 1),
                    f"invalid breadcrumb structured data count in {html_path}", failures)
        if canonical_url and breadcrumbs:
            items = breadcrumbs[0].get("itemListElement", [])
            require(
                len(items) == 2
                and items[0].get("position") == 1
                and items[0].get("name") == "OpenProgram Docs"
                and items[0].get("item") == "https://openprogram.io/docs/"
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
        en_url = "https://openprogram.io/docs/" + en_rel
        zh_url = "https://openprogram.io/docs/" + zh_rel
        if en_path in (BUILT_SITE / "README.html", BUILT_SITE / "index.html"):
            en_url = "https://openprogram.io/docs/"
        expected = {
            ("en", en_url),
            ("zh-Hans", zh_url),
            ("x-default", en_url),
        }
        require(alternates == expected,
                f"incomplete reciprocal hreflang cluster in {html_path}", failures)
    require(bool(language_pairs), "docs build contains no bilingual page pairs", failures)
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
