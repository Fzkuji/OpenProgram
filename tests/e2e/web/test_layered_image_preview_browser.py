"""Public FileViewer entry checks for bounded layered image previews."""
import base64
import struct
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def layered_bundle(tmp_path_factory):
    target = tmp_path_factory.mktemp("layered-preview") / "bundle.js"
    subprocess.run(["node", "apps/web/tests/files/build-document-window-browser.mjs", str(target),
                    "./document-preview-browser-entry.tsx"], check=True)
    return target.read_text()


@pytest.fixture
def layered_page(layered_bundle):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda request: request.fulfill(
            content_type="text/html", body='<div id="root"></div><script src="/bundle.js"></script>'
        ) if request.request.url.endswith("/") else request.fulfill(
            content_type="text/javascript", body=layered_bundle
        ) if request.request.url.endswith("/bundle.js") else request.fulfill(status=404))
        page.goto("https://document.test/")
        try:
            yield page, errors
        finally:
            browser.close()


def tiff_bytes():
    """A valid uncompressed 4x3 RGB TIFF with four known first pixels."""
    pixels = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0)) * 1
    pixels += bytes((0, 0, 0)) * 8
    entries = [
        (256, 3, 1, 4), (257, 3, 1, 3), (258, 3, 3, 122),
        (259, 3, 1, 1), (262, 3, 1, 2), (273, 4, 1, 128),
        (277, 3, 1, 3), (278, 4, 1, 3), (279, 4, 1, len(pixels)),
    ]
    output = bytearray(b"II") + struct.pack("<H", 42) + struct.pack("<I", 8)
    output += struct.pack("<H", len(entries))
    for tag, kind, count, value in entries:
        output += struct.pack("<HHI", tag, kind, count) + (struct.pack("<H", value) + b"\0\0" if kind == 3 and count == 1 else struct.pack("<I", value))
    output += struct.pack("<I", 0) + struct.pack("<HHH", 8, 8, 8) + pixels
    return bytes(output)


def test_valid_tiff_renders_first_page_and_pixels(layered_page):
    from playwright.sync_api import expect

    page, errors = layered_page
    encoded = base64.b64encode(tiff_bytes()).decode()
    page.evaluate("body => showFile('sample.tiff', body)", encoded)
    expect(page.get_by_text("TIFF first page", exact=False)).to_be_visible()
    expect(page.locator("canvas")).to_be_visible()
    assert page.locator("canvas").evaluate("c => [c.width, c.height, [...c.getContext('2d').getImageData(0,0,4,3).data.slice(0,16)]]") == [4, 3, [255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 0, 255]]
    assert errors == []


def test_valid_psd_lists_real_layers_and_renders_composite(layered_page):
    from playwright.sync_api import expect

    page, errors = layered_page
    encoded = base64.b64encode((FIXTURES / "layered-two-layers.psd").read_bytes()).decode()
    page.evaluate("body => showFile('layers.psd', body)", encoded)
    expect(page.get_by_text("PSD composite", exact=False)).to_be_visible()
    expect(page.get_by_text("Yellow rectangle", exact=True)).to_be_visible()
    expect(page.get_by_text("Pink circle", exact=True)).to_be_visible()
    assert page.locator("canvas").evaluate("c => [c.width, c.height]") == [64, 32]
    assert page.locator("canvas").evaluate("""c => {
      const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
      const colors = new Set();
      for(let i=0; i<d.length; i+=4) if(d[i+3]) colors.add(`${d[i]},${d[i+1]},${d[i+2]}`);
      return colors.size > 1;
    }""")
    assert errors == []


def test_corrupt_layered_image_explains_error_and_offers_original(layered_page):
    from playwright.sync_api import expect

    page, errors = layered_page
    page.evaluate("body => showFile('broken.psd', body)", base64.b64encode(b"not a psd").decode())
    expect(page.get_by_role("alert")).to_contain_text("cannot be previewed")
    expect(page.get_by_role("link", name="Download original")).to_have_attribute("download", "broken.psd")
    assert errors == []


def test_psd_oversized_dimensions_are_rejected_before_decode(layered_page):
    from playwright.sync_api import expect

    page, errors = layered_page
    body = bytearray((FIXTURES / "layered-two-layers.psd").read_bytes())
    struct.pack_into(">I", body, 18, 1_000_000)
    page.evaluate("body => showFile('large.psd', body)", base64.b64encode(body).decode())
    expect(page.get_by_role("alert")).to_contain_text("16 million pixels")
    assert page.locator("canvas").count() == 0
    page.evaluate("body => showFile('new.tiff', body)", base64.b64encode(tiff_bytes()).decode())
    expect(page.get_by_text("TIFF first page", exact=False)).to_be_visible()
    expect(page.get_by_role("alert")).to_have_count(0)
    assert errors == []
