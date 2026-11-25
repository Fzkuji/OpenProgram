from pathlib import Path

import pytest

from openprogram.operations.project_site import SITE_HOST, SITE_URL
from scripts.docs_site.indexnow import (
    ENDPOINT,
    HOST,
    KEY,
    KEY_LOCATION,
    MAX_URLS,
    payloads,
    submit_payloads,
    write_payloads,
)


def _sitemap(urls: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>"
    )


def test_site_publish_notifies_indexnow_after_pages_deploy() -> None:
    workflow = Path(".github/workflows/docs-pages.yml").read_text(encoding="utf-8")

    publish = workflow.index("- name: Publish to GitHub Pages")
    notify = workflow.index("- name: Submit sitemap to IndexNow")

    assert publish < notify
    assert "continue-on-error: true" in workflow[notify:]
    assert "_publish/sitemap.xml" in workflow[notify:]
    assert "python -m scripts.docs_site.indexnow" in workflow[notify:]
    assert "--submit" in workflow[notify:]


def test_indexnow_payload_is_deduplicated_and_protocol_complete(tmp_path: Path) -> None:
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(_sitemap([
        SITE_URL,
        f"{SITE_URL}docs/",
        SITE_URL,
    ]))

    assert payloads(sitemap) == [{
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": [SITE_URL, f"{SITE_URL}docs/"],
    }]


@pytest.mark.parametrize("url", [
    SITE_URL.replace("https://", "http://", 1),
    "https://example.com/",
    # The right host but outside the site, such as another project's pages.
    f"https://{SITE_HOST}/elsewhere/",
])
def test_indexnow_rejects_noncanonical_urls(tmp_path: Path, url: str) -> None:
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(_sitemap([url]))

    with pytest.raises(ValueError, match="non-canonical"):
        payloads(sitemap)


def test_indexnow_splits_more_than_one_batch(tmp_path: Path) -> None:
    urls = [f"{SITE_URL}docs/{index}" for index in range(MAX_URLS + 1)]
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(_sitemap(urls))

    batches = payloads(sitemap)

    assert [len(batch["urlList"]) for batch in batches] == [MAX_URLS, 1]


def test_indexnow_ignores_non_sitemap_loc_tags(tmp_path: Path) -> None:
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'<evil-loc>{SITE_URL}injected</evil-loc>'
        "</urlset>"
    )

    with pytest.raises(ValueError, match="no URLs"):
        payloads(sitemap)


def test_indexnow_removes_stale_batches_after_validating(tmp_path: Path) -> None:
    sitemap = tmp_path / "sitemap.xml"
    output = tmp_path / "batches"
    sitemap.write_text(_sitemap([
        f"{SITE_URL}docs/{index}" for index in range(MAX_URLS + 1)
    ]))
    assert len(write_payloads(sitemap, output)) == 2

    sitemap.write_text(_sitemap([f"{SITE_URL}current"]))
    paths = write_payloads(sitemap, output)

    assert [path.name for path in paths] == ["batch-1.json"]
    assert sorted(path.name for path in output.iterdir()) == ["batch-1.json"]


def test_indexnow_submits_later_batches_after_an_earlier_failure(tmp_path: Path) -> None:
    paths = [tmp_path / "batch-1.json", tmp_path / "batch-2.json"]
    for path in paths:
        path.write_text("{}")
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def request(req, *, timeout):
        calls.append((req.full_url, timeout, req.data))
        if req.data == b"{}" and len(calls) <= 3:
            raise OSError("first batch unavailable")
        return Response()

    with pytest.raises(RuntimeError, match="batch-1.json"):
        submit_payloads(paths, request=request)

    assert calls == [
        (ENDPOINT, 30, b"{}"),
        (ENDPOINT, 30, b"{}"),
        (ENDPOINT, 30, b"{}"),
        (ENDPOINT, 30, b"{}"),
    ]
