"""Actual FileViewer media dispatch and immutable source playback."""
import base64
import io
import struct
import subprocess
import wave
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def media_bundle(tmp_path_factory):
    target = tmp_path_factory.mktemp("media-preview") / "bundle.js"
    subprocess.run(["node", "apps/web/tests/files/build-document-window-browser.mjs", str(target),
                    "./document-preview-browser-entry.tsx"], check=True)
    return target.read_text()


@pytest.fixture
def media_page(media_bundle):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        requests = []
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request):
            requests.append(request.request.url)
            path = urlsplit(request.request.url).path
            if path == "/":
                request.fulfill(content_type="text/html", body='<div id="root"></div><script src="/bundle.js"></script>')
            elif path == "/bundle.js":
                request.fulfill(content_type="text/javascript", body=media_bundle)
            else:
                request.fulfill(status=404)

        page.route("**/*", route)
        page.goto("https://document.test/")
        try:
            yield page, requests, errors
        finally:
            browser.close()


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(struct.pack("<h", 1000) * 8000)
    return output.getvalue()


def test_retained_audio_plays_without_reading_current_file(media_page):
    page, requests, errors = media_page
    page.evaluate("body => showFile('recording.wav', body)", base64.b64encode(wav_bytes()).decode())
    page.wait_for_function("document.querySelector('audio')?.readyState >= 2")
    page.locator("audio").evaluate("async element => { window.oldMedia = element; await element.play(); }")
    page.wait_for_function("document.querySelector('audio').currentTime > 0.05")
    assert page.locator("audio").evaluate("element => element.controls && element.duration === 1")
    page.evaluate("hideFile()")
    page.wait_for_function("oldMedia.paused && !oldMedia.getAttribute('src')")
    assert not any("/api/" in url for url in requests)
    assert errors == []


def test_corrupt_media_explains_codec_error_and_offers_original(media_page):
    from playwright.sync_api import expect

    page, _, errors = media_page
    page.evaluate("body => showFile('broken.webm', body)", base64.b64encode(b"not video").decode())
    expect(page.get_by_role("alert")).to_contain_text("could not be played")
    expect(page.get_by_role("link", name="Download original")).to_have_attribute("download", "broken.webm")
    page.evaluate("body => showFile('valid.wav', body)", base64.b64encode(wav_bytes()).decode())
    page.wait_for_function("document.querySelector('audio')?.readyState >= 2")
    expect(page.get_by_role("alert")).to_have_count(0)
    assert errors == []


def test_corrupt_image_keeps_original_download_and_recovers_on_source_change(media_page):
    from playwright.sync_api import expect

    page, _, errors = media_page
    page.evaluate("body => showFile('broken.png', body)", base64.b64encode(b"not image").decode())
    expect(page.get_by_role("alert")).to_contain_text("could not be displayed")
    expect(page.get_by_role("link", name="Download original")).to_have_attribute("download", "broken.png")
    # A valid self-contained SVG stays inside an img, without executing markup.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8"><rect width="12" height="8" fill="red"/></svg>'
    page.evaluate("body => showFile('valid.svg', body)", base64.b64encode(svg).decode())
    page.wait_for_function("document.querySelector('img')?.naturalWidth === 12")
    expect(page.get_by_role("alert")).to_have_count(0)
    assert errors == []


def test_retained_video_actually_plays_and_stops_on_close(media_page):
    from pathlib import Path

    page, requests, errors = media_page
    # Generated with FFmpeg: VP9, 320x180, 10fps, two seconds, no audio.
    body = (Path(__file__).parent / "fixtures" / "preview.webm").read_bytes()
    page.evaluate("body => showFile('clip.webm', body)", base64.b64encode(body).decode())
    page.wait_for_function("document.querySelector('video')?.readyState >= 2")
    page.locator("video").evaluate("async element => { window.oldMedia = element; await element.play(); }")
    page.wait_for_function("document.querySelector('video').currentTime > 0.05")
    assert page.locator("video").evaluate("element => element.videoWidth === 320 && element.videoHeight === 180")
    page.evaluate("hideFile()")
    page.wait_for_function("oldMedia.paused && !oldMedia.getAttribute('src')")
    assert not any("/api/" in url for url in requests)
    assert errors == []


