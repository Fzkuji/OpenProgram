"""Real PDF.js preview through the public FileViewer entry."""
import base64
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.browser

PDF_FIXTURE = (
    "JVBERi0xLjcKJYGBgYEKCjYgMCBvYmoKPDwKL0ZpbHRlciAvRmxhdGVEZWNvZGUKL0xlbmd0aCAyMzEKPj4Kc3RyZWFtCnicbVDLSgRBDLznK3IWHNO9laQHRFB2Rg9e1L6JB1nXF7uHFdHfNz0u65PQlSep6mzopJJ0iaXL8ZRfHujgbLl6W74+LW73XfqCIl56zuB6Tw3PKcbDEntmF+G6pkOMLqY2qHi20Tww2TyLCgDL4q2ubtYH+jYfw+yI6zPVPRoqXdBm0jOt/6mkd2QrWa1wKv8qMXwqUViK3R48CB6EL47Z8ZcWzKER9UitphljmE39BIfGxIgB+kuZdGV7psSXpzTjd7q+Cfa77yo07rFY7yoN/6bNNSzCK0J8aRc0/0hXwfcBIEFVqwplbmRzdHJlYW0KZW5kb2JqCgo4IDAgb2JqCjw8Ci9GaWx0ZXIgL0ZsYXRlRGVjb2RlCi9MZW5ndGggMjE5Cj4+CnN0cmVhbQp4nG2QTU8CQQyG7/0VPZuwdrr9mE0IiUZWD1zQuRkOBgExcMAY/ft2NgT8SjNvO51pn84c4LoANQmp4ViKbxu4vFvtPlbv2+XTiIkok3bZkAXLGqrOIK6HJXRGJ8Kyh7G2ptZab1MTJidL5qYTLK9QLmBaYA6HgTWU/qR0riTWRRmm/C/F5EgRS861s1eKhM8u7RVTdBAxJrkRjaiTVHPK0ofZcJ7ERSOSKO5/TUZNPn5BwvtbaPETHhdBf/4+hcZbl/tTpurfbXVVM+EOxPI5qP4FHoL3BSJLTdwKZW5kc3RyZWFtCmVuZG9iagoKOSAwIG9iago8PAovRmlsdGVyIC9GbGF0ZURlY29kZQovVHlwZSAvT2JqU3RtCi9OIDYKL0ZpcnN0IDMyCi9MZW5ndGggNDA2Cj4+CnN0cmVhbQp4nNVTTWvcMBC961fMsT0UjWXrqywLm127hRIakkBKQw6OLRaXIBVbW9J/3xl7kyXQkkPpoZixNDNvZvTspwIQFGiEElwFFejSgQajECw4ZWG1EvL65/cA8qLdh0nIT0M/wS1hEC4Jw+87IbfpEDMosV6LU8W2ze1D2oulFAoGPyEuxtQfujDCqqmbBtEioqnIDKLa0bol82SKfMopR3syWx2NYrZELDeUaxYzdqnh/IzVx/qaVsIaxuwWbOUW/3kuz6qXHuq18/i1kOep37U5wJvde4XKoC8KVWqvi69v6XOMoc3p/yU3n39I8Y8MX/znJsUs5NXhPs8uBwshz9opcAbkx/DwI+Sha4WsY5f6Ie5B3gxxE6fhKfCyIwuGZTMGql90Iy/DlA5jR0Ji3NyZN8/N31n0jphb50nHc8kp522ljFPauGOOxskvn++/hW5uw279mD9cZWa8BDh2HvqhPUuPpHikxxQKrFes+E2MKfNNmNUfM52UPXO8EX9Ph4XhUHtnfkdHY2W8QvuP6bgTnV+adQZsCmVuZHN0cmVhbQplbmRvYmoKCjEwIDAgb2JqCjw8Ci9TaXplIDExCi9Sb290IDIgMCBSCi9JbmZvIDMgMCBSCi9GaWx0ZXIgL0ZsYXRlRGVjb2RlCi9UeXBlIC9YUmVmCi9MZW5ndGggNDcKL1cgWyAxIDIgMiBdCi9JbmRleCBbIDAgMTEgXQo+PgpzdHJlYW0KeJxjYGD4/5+JgZOBAUQwgggmEMEMIlgYGQQgEqyMjA4MQOkUIMGSwMAAAHq1A28KZW5kc3RyZWFtCmVuZG9iagoKc3RhcnR4cmVmCjExMjAKJSVFT0Y="
)


@pytest.fixture(scope="module")
def pdf_bundle(tmp_path_factory):
    subprocess.run(["node", "apps/web/scripts/runtime/prepare-document-assets.mjs"], check=True)
    target = tmp_path_factory.mktemp("pdf-preview") / "bundle.js"
    subprocess.run(["node", "apps/web/tests/files/build-document-window-browser.mjs", str(target),
                    "./document-preview-browser-entry.tsx"], check=True)
    return target.read_text()


# Chromium bundled with Electron can lag behind the browser used by CI.
# Apply the missing APIs in both realms: main-page shims do not affect workers.
DESKTOP_MISSING_APIS = "delete Uint8Array.prototype.toHex; delete Math.sumPrecise;"


@pytest.fixture(params=["native", "desktop-missing-apis"])
def pdf_page(pdf_bundle, request):
    from playwright.sync_api import sync_playwright
    from openprogram.webui.owner_auth import _shell_content_security_policy

    asset_root = Path("apps/web/public/document-assets/pdfjs")
    if not (asset_root / "pdf.mjs").is_file():
        pytest.fail("prepare local PDF.js assets before running the browser acceptance")
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(8000)
        compatibility = request.param == "desktop-missing-apis"
        if compatibility:
            page.add_init_script(DESKTOP_MISSING_APIS)
        errors = []
        page.on("pageerror", lambda error: errors.append(error.stack or str(error)))

        def route(request):
            path = urlsplit(request.request.url).path
            if path == "/":
                request.fulfill(content_type="text/html", headers={"Content-Security-Policy": _shell_content_security_policy().decode()}, body='<div id="root" style="height:720px"></div><script src="/bundle.js"></script>')
            elif path == "/bundle.js":
                request.fulfill(content_type="text/javascript", body=pdf_bundle)
            elif path.startswith("/document-assets/pdfjs/"):
                relative = path.removeprefix("/document-assets/pdfjs/")
                candidate = asset_root / relative
                if candidate.is_file():
                    if compatibility and relative == "pdf.worker.mjs":
                        request.fulfill(content_type="text/javascript",
                                        body=DESKTOP_MISSING_APIS + "\n" + candidate.read_text())
                    else:
                        request.fulfill(path=str(candidate))
                else:
                    request.fulfill(status=404)
            else:
                request.fulfill(status=404)

        page.route("**/*", route)
        page.goto("https://document.test/")
        if compatibility:
            assert page.evaluate("typeof Uint8Array.prototype.toHex") == "undefined"
            assert page.evaluate("typeof Math.sumPrecise") == "undefined"
        try:
            yield page, errors
        finally:
            browser.close()


def test_pdf_pages_pixels_zoom_and_text_search(pdf_page):
    from playwright.sync_api import expect

    page, errors = pdf_page
    page.evaluate("body => showFile('fixture.pdf', body)", PDF_FIXTURE)
    expect(page.get_by_text("1 / 2", exact=True)).to_be_visible()
    page.wait_for_function("""() => {
      const c = document.querySelector('canvas'); if (!c?.width) return false;
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (let i = 0; i < d.length; i += 4) if (d[i+3] && d[i] < 200 && d[i+1] < 200 && d[i+2] < 200) return true;
      return false;
    }""")
    first_size = page.locator(".pdfViewer canvas").first.evaluate("element => ({width: element.width, height: element.height})")
    assert first_size["width"] > 0 and first_size["height"] > 0
    page.get_by_role("button", name="Next page").click()
    expect(page.get_by_text("2 / 2", exact=True)).to_be_visible()
    page.get_by_role("button", name="Zoom in").click()
    page.wait_for_function("width => document.querySelector('canvas')?.width > width", arg=first_size["width"])
    second_size = page.locator(".pdfViewer canvas").first.evaluate("element => ({width: element.width, height: element.height})")
    assert second_size["width"] > first_size["width"]
    search = page.get_by_role("textbox", name="Search PDF")
    search.fill("target")
    page.get_by_role("button", name="Find").click()
    expect(page.get_by_label("Search results")).to_contain_text("2 / 2")
    page.evaluate("hideFile()")
    expect(page.locator("canvas")).to_have_count(0)
    page.evaluate("body => showFile('reopened.pdf', body)", PDF_FIXTURE)
    expect(page.get_by_text("1 / 2", exact=True)).to_be_visible()
    expect(page.get_by_role("alert")).to_have_count(0)
    assert errors == []


def test_pdf_source_change_discards_obsolete_error(pdf_page):
    from playwright.sync_api import expect

    page, errors = pdf_page
    page.evaluate("body => showFile('broken.pdf', body)", base64.b64encode(b"not a pdf").decode())
    page.evaluate("body => showFile('fixture.pdf', body)", PDF_FIXTURE)
    expect(page.get_by_text("1 / 2", exact=True)).to_be_visible()
    expect(page.get_by_role("alert")).to_have_count(0)
    assert errors == []


def test_malformed_pdf_offers_original_download(pdf_page):
    from playwright.sync_api import expect

    page, errors = pdf_page
    page.evaluate("body => showFile('broken.pdf', body)", base64.b64encode(b"not a pdf").decode())
    expect(page.get_by_role("alert")).to_contain_text("PDF")
    expect(page.get_by_role("link", name="Download original", exact=True)).to_have_attribute("download", "broken.pdf")
    assert errors == []


def test_pdf_shell_policy_rejects_external_fetch(pdf_page):
    page, errors = pdf_page
    # The catch-all route would return 404 (a resolved fetch) if CSP allowed it.
    assert page.evaluate("fetch('https://outside.invalid/data').then(() => false, () => true)")
    assert errors == []


def test_pdf_reader_exposes_selectable_text_and_navigation(pdf_page):
    from playwright.sync_api import expect

    page, errors = pdf_page
    page.evaluate("body => showFile('fixture.pdf', body)", PDF_FIXTURE)
    expect(page.locator('.textLayer').first).to_be_visible()
    expect(page.get_by_role('button', name='Toggle sidebar')).to_be_visible()
    expect(page.get_by_role('checkbox', name='Remove line breaks when copying')).to_be_checked()
    assert errors == []


def test_pdf_copy_cleans_only_selected_document_text(pdf_page):
    from playwright.sync_api import expect

    page, errors = pdf_page
    page.evaluate("body => showFile('fixture.pdf', body)", PDF_FIXTURE)
    expect(page.locator('.textLayer span').first).to_be_visible()
    result = page.evaluate("""() => {
      const range = document.createRange(); range.selectNodeContents(document.querySelector('.textLayer'));
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
      const raw = selection.toString();
      const data = new DataTransfer();
      document.dispatchEvent(new ClipboardEvent('copy', {bubbles:true, cancelable:true, clipboardData:data}));
      return {raw, copied:data.getData('text/plain')};
    }""")
    assert result['raw']
    assert result['copied'] == ' '.join(result['raw'].splitlines())
    page.get_by_role('textbox', name='Search PDF').fill('search input')
    assert page.get_by_role('textbox', name='Search PDF').evaluate("""element => {
      const data = new DataTransfer();
      element.dispatchEvent(new ClipboardEvent('copy', {bubbles:true, cancelable:true, clipboardData:data}));
      return data.getData('text/plain');
    }""") == ''
    assert errors == []


@pytest.mark.parametrize("fail_first", [False, True])
def test_pdf_text_annotation_persists_and_reopens(pdf_page, fail_first):
    import hashlib
    from playwright.sync_api import expect

    page, errors = pdf_page
    state = {'body': base64.b64decode(PDF_FIXTURE), 'writes': [], 'fail': fail_first}

    def content(route):
        if route.request.method == 'GET':
            route.fulfill(body=state['body'], headers={'x-document-revision': hashlib.sha256(state['body']).hexdigest()})
        else:
            if state['fail']:
                route.fulfill(status=503, json={'error': 'injected save failure'})
                return
            state['body'] = route.request.post_data_buffer
            state['writes'].append(state['body'])
            route.fulfill(json={'ok': True, 'status': 'committed', 'revision': hashlib.sha256(state['body']).hexdigest()})

    page.route('**/api/documents/content?*', content)
    page.evaluate("showDocument('annotation.pdf')")
    expect(page.get_by_role('button', name='Text note', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Text note', exact=True).click()
    layer = page.locator('.annotationEditorLayer').first
    expect(layer).to_be_visible()
    layer.click(position={'x': 100, 'y': 100})
    note = page.locator('.freeTextEditor [contenteditable=true]').first
    expect(note).to_be_visible()
    note.fill('Reading note survives reopening')
    with page.expect_response(lambda response: response.request.method == 'PUT'):
        page.get_by_role('button', name='Save', exact=True).click()
    if fail_first:
        expect(page.get_by_role('alert').first).to_contain_text('503')
        expect(page.locator('.freeTextEditor').first).to_contain_text('Reading note survives reopening')
        state['fail'] = False
        with page.expect_response(lambda response: response.request.method == 'PUT' and response.status == 200):
            page.get_by_role('button', name='Retry', exact=True).click()
    expect(page.get_by_text('Saved', exact=True)).to_be_visible()
    assert state['writes']
    page.get_by_role('button', name='Highlight', exact=True).click()
    expect(page.get_by_role('button', name='Highlight', exact=True)).to_have_attribute('aria-pressed', 'true')
    page.locator('.textLayer span').first.evaluate("""element => {
      const range = document.createRange(); range.selectNodeContents(element);
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")
    expect(page.locator('.highlightEditor').first).to_be_visible()
    page.get_by_role('button', name='Undo', exact=True).click()
    expect(page.locator('.highlightEditor')).to_have_count(0)
    page.get_by_role('button', name='Redo', exact=True).click()
    expect(page.locator('.highlightEditor').first).to_be_visible()
    with page.expect_response(lambda response: response.request.method == 'PUT'):
        page.get_by_role('button', name='Save', exact=True).click()
    # Decode retained bytes independently of the editor instance.
    annotations = page.evaluate("""async encoded => {
      const core = await import('/document-assets/pdfjs/pdf.mjs');
      const task = core.getDocument({data:Uint8Array.from(atob(encoded), c => c.charCodeAt(0))});
      const pdf = await task.promise;
      const annotations = await (await pdf.getPage(1)).getAnnotations();
      await task.destroy(); return annotations;
    }""", base64.b64encode(state['body']).decode())
    assert any(a.get('subtype') == 'Highlight' for a in annotations)
    assert any(a.get('contentsObj', {}).get('str') == 'Reading note survives reopening' for a in annotations)
    page.evaluate('hideFile()')
    page.evaluate("body => showFile('saved.pdf', body)", base64.b64encode(state['body']).decode())
    expect(page.get_by_text('1 / 2', exact=True)).to_be_visible()
    assert errors == []
