"""The address the project's public website is served from.

This is the one setting for it. The documentation build writes canonical
links, the sitemap, Markdown copies and IndexNow batches from it; installed
versions read the update source below it; and a repository test keeps the
READMEs, install scripts, release notes and the desktop App (which cannot
import Python) pointing at the same address.

The site is published from the FuzzyKuji/openprogram-site repository with
GitHub Pages. The landing page sits at the root, the documentation one level
down under ``docs/``. Pages serves a project site below the account's
``github.io`` host, so the address has a path, and every link the build
writes has to carry it.
"""
from __future__ import annotations

from urllib.parse import urlparse

SITE_URL = "https://fuzzykuji.github.io/openprogram-site/"

SITE_HOST = urlparse(SITE_URL).hostname or ""
DOCS_URL = SITE_URL + "docs/"
# The path the published documentation is mounted under, which the build
# prefixes to the links it writes when it builds for publication.
DOCS_PATH = urlparse(DOCS_URL).path
INSTALL_SCRIPT_URL = SITE_URL + "install"
WINDOWS_INSTALL_SCRIPT_URL = SITE_URL + "install.ps1"
UPDATE_SOURCE_URL = SITE_URL + "update-source.json"


def site_url(path: str = "") -> str:
    """An absolute URL on the site, for a path relative to its root."""
    return SITE_URL + path.lstrip("/")


def docs_url(path: str = "") -> str:
    """An absolute URL in the documentation, for a path relative to docs/."""
    return DOCS_URL + path.lstrip("/")


if __name__ == "__main__":
    import sys

    # ``python -m openprogram.operations.project_site docs-path`` lets a
    # workflow read the mount path without repeating the address.
    names = {"site-url": SITE_URL, "docs-url": DOCS_URL, "docs-path": DOCS_PATH}
    if len(sys.argv) != 2 or sys.argv[1] not in names:
        raise SystemExit("usage: python -m openprogram.operations.project_site "
                         + "|".join(names))
    print(names[sys.argv[1]])
