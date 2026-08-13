"""Build validated IndexNow request batches from the generated sitemap."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

HOST = "openprogram.io"
KEY = "7c5062acbc32b8a88d2e6b627d65cbfa"
KEY_LOCATION = f"https://{HOST}/{KEY}.txt"
MAX_URLS = 10_000
ENDPOINT = "https://api.indexnow.org/indexnow"
LOC_TAG = "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"


def payloads(sitemap: Path) -> list[dict[str, object]]:
    urls = list(dict.fromkeys(
        node.text.strip()
        for node in ET.parse(sitemap).getroot().iter()
        if node.tag == LOC_TAG and node.text
    ))
    if not urls:
        raise ValueError("sitemap contains no URLs")
    if any(
        urlparse(url).scheme != "https" or urlparse(url).netloc != HOST
        for url in urls
    ):
        raise ValueError("sitemap contains a non-canonical URL")

    return [
        {
            "host": HOST,
            "key": KEY,
            "keyLocation": KEY_LOCATION,
            "urlList": urls[start:start + MAX_URLS],
        }
        for start in range(0, len(urls), MAX_URLS)
    ]


def write_payloads(sitemap: Path, output: Path) -> list[Path]:
    batches = payloads(sitemap)
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob("batch-*.json"):
        stale.unlink()
    paths = []
    for index, payload in enumerate(batches, start=1):
        path = output / f"batch-{index}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
        paths.append(path)
    return paths


def submit_payloads(
    paths: list[Path],
    *,
    request=urlopen,
    attempts: int = 3,
) -> None:
    failures = []
    for path in paths:
        error = None
        for _ in range(attempts):
            try:
                req = Request(
                    ENDPOINT,
                    data=path.read_bytes(),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with request(req, timeout=30):
                    error = None
                break
            except Exception as exc:  # endpoint failure must not skip later batches
                error = exc
        if error is not None:
            failures.append(f"{path.name}: {error}")
    if failures:
        raise RuntimeError("IndexNow submission failed: " + "; ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sitemap", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    paths = write_payloads(args.sitemap, args.output)
    if args.submit:
        submit_payloads(paths)


if __name__ == "__main__":
    main()
