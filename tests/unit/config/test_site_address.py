"""The website address is one setting: ``openprogram.operations.project_site``.

Generated output (canonical links, sitemap, Markdown copies, IndexNow) derives
from it in code. Files that cannot import it (READMEs, release notes, install
scripts, the landing page, the desktop App) write the address out, and this
test fails when one of them names any other host for the site, so a change of
address cannot leave a stale link behind. Links between documentation pages
are relative and need no address at all.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from openprogram.operations import project_site
from scripts import retired_accounts

ROOT = Path(__file__).resolve().parents[3]

# Hosts and paths the site has been served from. A URL on one of them that is
# not under the configured address is a stale site link.
_FORMER_SITE_HOSTS = {"openprogram.io", "www.openprogram.io"}
_SITE_REPOSITORY_PATH = "/openprogram-site"
_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?:/[^\s\"'<>()`\]\\]*)?")
# The former custom domain written without a scheme, in prose or in an e-mail
# address: the project does not control it.
_BARE_FORMER_HOST = re.compile(r"(?<![\w.-])(?:www\.)?openprogram\.io\b(?!\.)")

# Files that may name another site host, each for a stated reason.
ALLOWED = {
    # The decision record says which address the site moved from.
    "docs/reference/design/distribution/github-publishing.html",
    "docs/reference/design/distribution/github-publishing.zh.html",
    # Drawn text inside the social card picture; the PNG beside it is the
    # published image and changes with it.
    "docs/images/openprogram-social-card.svg",
    # A dotted name in a code sample about module names, not a link.
    "tests/component/config/test_docs_governance_claims.py",
    # This test.
    "tests/unit/config/test_site_address.py",
}


def stale_site_links(text: str) -> list[str]:
    """Site links in ``text`` that do not use the configured address."""
    stale = []
    for match in _URL.finditer(text):
        url = match.group(0)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        on_site = (
            host in _FORMER_SITE_HOSTS
            or host == project_site.SITE_HOST
            and parsed.path.startswith(_SITE_REPOSITORY_PATH)
            or host.endswith(".github.io")
            and parsed.path.startswith(_SITE_REPOSITORY_PATH)
        )
        if on_site and not (url + "/").startswith(project_site.SITE_URL):
            stale.append(url)
    stale.extend(match.group(0) for match in _BARE_FORMER_HOST.finditer(_URL.sub("", text)))
    return stale


def _tracked_texts():
    for path, data in retired_accounts.checkout_files(ROOT):
        if data is None or path in ALLOWED:
            continue
        try:
            yield path, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def test_no_tracked_file_names_another_site_address() -> None:
    hits = [
        f"{path}: {url}"
        for path, text in _tracked_texts()
        for url in stale_site_links(text)
    ]
    assert not hits, (
        "these name a site address other than project_site.SITE_URL "
        f"({project_site.SITE_URL}); use the configured address, or a relative "
        "link between documentation pages:\n" + "\n".join(hits)
    )


def test_links_between_documentation_pages_are_relative() -> None:
    absolute = re.compile(r'href="' + re.escape(project_site.DOCS_URL))
    hits = [
        path
        for path, text in _tracked_texts()
        if path.startswith("docs/") and path.endswith(".html") and absolute.search(text)
    ]
    assert not hits, "link to other documentation pages with relative links:\n" + "\n".join(hits)


def test_published_root_files_take_the_address_from_the_build() -> None:
    static_root = ROOT / "docs" / "_static_root"
    for path in static_root.iterdir():
        if path.suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8")
            assert project_site.SITE_URL not in text, f"{path.name}: write {{{{SITE_URL}}}}"
    assert not (static_root / "CNAME").exists(), "the build writes CNAME from the address"


def test_the_app_reads_the_update_source_from_the_configured_address() -> None:
    service = (ROOT / "apps/desktop/main/self-update/update-service.js").read_text(encoding="utf-8")
    url = re.search(r'const UPDATE_SOURCE_URL = "([^"]+)";', service)
    hosts = re.search(r"const UPDATE_SOURCE_HOSTS = new Set\(\[([^\]]*)\]\);", service)
    assert url and url.group(1) == project_site.UPDATE_SOURCE_URL
    assert hosts and re.findall(r'"([^"]+)"', hosts.group(1)) == [project_site.SITE_HOST]


@pytest.mark.parametrize(
    "text",
    [
        "curl -fsSL https://openprogram.io/install | sh",
        "see openprogram.io for more",
        "maintainer: OpenProgram <noreply@openprogram.io>",
        "https://someone-else.github.io/openprogram-site/docs/",
        f"https://{project_site.SITE_HOST}/openprogram-site-old/",
    ],
)
def test_a_stale_address_is_found(text: str) -> None:
    assert stale_site_links(text)


@pytest.mark.parametrize(
    "text",
    [
        f"curl -fsSL {project_site.INSTALL_SCRIPT_URL} | sh",
        f"[docs]({project_site.DOCS_URL}install/install.html)",
        project_site.SITE_URL.rstrip("/"),
        "https://openai.github.io/openai-agents-python/",
    ],
)
def test_the_configured_address_and_other_sites_pass(text: str) -> None:
    assert not stale_site_links(text)
