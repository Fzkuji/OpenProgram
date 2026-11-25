"""Validate the published landing page's stable structure and metadata.

Run:  python -m scripts.docs_site.check_landing
"""

from __future__ import annotations

import json
import re
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
README_HERO = "Agents program anything."
SITE_DESCRIPTION = (
    "Build self-programming AI agents that create and refine their own "
    "workflows with an open-source runtime for models, tools, memory, "
    "context, and multi-agent collaboration."
)
SOCIAL_IMAGE = DOCS_URL + "images/openprogram-social-card.png"
RELEASE_FEED = "https://github.com/Fzkuji/OpenProgram/releases.atom"
# The hero is the product tour video, one rendering per theme; the page picks
# the one that matches data-theme and uses its first frame as the poster.
HERO_VIDEOS = ("openprogram-tour-light", "openprogram-tour-dark")
HERO_VIDEO_LIMIT = 8_000_000
THEME_COLORS = {
    "(prefers-color-scheme: dark)": "#171719",
    "(prefers-color-scheme: light)": "#f5f5f7",
}
# The App's surfaces (apps/web/app/styles/themes), light first as in the page.
PALETTE_BACKGROUNDS = {
    "light": ("#ffffff", "#f5f5f7", "#ebebef"),
    "dark": ("#1e1e20", "#171719", "#2a2a2e"),
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
        self.videos: list[dict[str, str]] = []
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
        if tag == "video":
            self.videos.append(values)
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
    hero_videos = [video for video in page.videos if "hero-video" in video.get("class", "").split()]
    require(len(hero_videos) == 1, "landing hero needs exactly one tour video", failures)
    if hero_videos:
        video = hero_videos[0]
        require(all(name in video for name in ("muted", "loop", "playsinline")),
                "landing tour video must be muted, looping and inline", failures)
        require(video.get("width") == "1920" and video.get("height") == "1080",
                "landing tour video dimensions differ from the rendering", failures)
        require(video.get("aria-label"), "landing tour video has no accessible name", failures)
        require((video.get("data-light"), video.get("data-dark"))
                == tuple("docs/images/" + name for name in HERO_VIDEOS),
                "landing tour video does not name both theme renderings", failures)
        require(video.get("poster") == "docs/images/" + HERO_VIDEOS[0] + ".jpg",
                "landing tour video has no poster", failures)
    require(all(image.get("width") and image.get("height")
                for image in page.image_attrs),
            "landing image is missing explicit dimensions", failures)
    copy_buttons = [
        button
        for button in page.buttons
        if "copy-btn" in button.get("class", "").split()
    ]
    require(
        copy_buttons and all(button.get("aria-label") for button in copy_buttons),
        "icon-only copy button has no accessible name",
        failures,
    )
    # The first --text-secondary belongs to the light base palette, the second to dark.
    secondary_colors = re.findall(r"--text-secondary\s*:\s*(#[0-9a-fA-F]{6})", css)
    require(len(secondary_colors) == len(PALETTE_BACKGROUNDS),
            "landing secondary-text color is missing from a theme", failures)
    for secondary, (theme, backgrounds) in zip(secondary_colors, PALETTE_BACKGROUNDS.items()):
        for background in backgrounds:
            require(_contrast_ratio(secondary, background) >= 4.5,
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

    for section_id in ("how", "features", "research"):
        require(section_id in page.ids, f"missing #{section_id} section", failures)

    for phrase in (
        "Agents program anything.",
        "The model decides.",
        "Everything an agent needs, in one place.",
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
    require(f"irm {INSTALL_SCRIPT_URL}.ps1 | iex" in visible_text,
            "missing Windows installer", failures)
    version = project.get("version", "")
    require(f"v{version}" in visible_text
            and f"https://github.com/Fzkuji/OpenProgram/releases/tag/v{version}" in page.anchors,
            f"landing release note does not name the current version v{version}", failures)
    require("pip install openprogram" not in visible_text,
            "landing page still advertises unsupported pip install", failures)

    for image in (
        "docs/images/openprogram-app-icon.svg",
        "docs/images/code_hero.png",
        "docs/images/code_hero_light.png",
    ):
        require(image in page.images, f"missing product image {image}", failures)

    require(re.search(r"\.reveal\s*\{[^}]*opacity\s*:\s*1", css) is not None,
            "reveal content is not visible by default", failures)
    require(re.search(r"\.reveal[^}]*\{[^}]*opacity\s*:\s*0", css) is None,
            "reveal CSS can hide landing-page content", failures)
    require(re.search(
        r"\.composer \.cmd\s*\{[^}]*white-space\s*:\s*nowrap[^}]*"
        r"overflow-x\s*:\s*auto", css,
    ) is not None, "command text can wrap inside the install box", failures)

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
    for name in HERO_VIDEOS:
        for suffix in (".mp4", ".jpg"):
            asset = ROOT / "docs/images" / (name + suffix)
            require(asset.is_file(), f"landing tour asset {asset.name} is missing", failures)
            if not asset.is_file():
                continue
            if suffix == ".mp4":
                require(asset.stat().st_size < HERO_VIDEO_LIMIT,
                        f"landing tour video {asset.name} exceeds 8 MB", failures)
            built = BUILT_SITE / "images" / asset.name
            require(built.is_file() and built.read_bytes() == asset.read_bytes(),
                    f"docs build did not copy {asset.name}", failures)

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
