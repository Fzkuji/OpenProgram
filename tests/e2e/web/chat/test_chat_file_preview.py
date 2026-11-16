"""Real reply finalization, output cards, canonical split and file preview."""
import hashlib
import re
import subprocess
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.browser


def open_button(page, name):
    """The Open control on the reply's file card for ``name``."""
    card = page.locator('figure').filter(has=page.locator('strong', has_text=re.compile('^' + re.escape(name) + '$')))
    return card.get_by_role('button', name='Open', exact=True)


@pytest.fixture(scope='module')
def output_bundle(tmp_path_factory):
    target = tmp_path_factory.mktemp('chat-file-preview') / 'output.js'
    subprocess.run(['node', 'apps/web/tests/files/build-document-window-browser.mjs', str(target), './chat-file-preview-browser-entry.tsx'], check=True)
    return target.read_text()


@pytest.mark.parametrize('mode', ['automatic', 'manual', 'dirty'])
def test_live_output_card_preserves_chat_and_manual_layout(output_bundle, mode):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            path = urlsplit(request.request.url).path
            if path == '/':
                request.fulfill(content_type='text/html; charset=utf-8', body='<div id="root"></div><script src="/fixture.js"></script>')
            elif path == '/fixture.js':
                request.fulfill(content_type='text/javascript; charset=utf-8', body=output_bundle)
            elif path == '/api/documents/content':
                if request.request.method == 'PUT':
                    request.fulfill(status=503)
                    return
                body = b'Preview file body'
                request.fulfill(body=body, headers={'x-document-revision': hashlib.sha256(body).hexdigest(), 'x-document-version': 'v1'})
            elif path == '/api/documents/stat':
                request.fulfill(json={'version': 'v1'})
            else:
                request.fulfill(status=404)
        page.route('https://preview.test/**', route)
        page.goto('https://preview.test/' + ('?manual' if mode == 'manual' else ''))
        page.evaluate('complete()')
        expect(open_button(page, 'report.md')).to_be_visible()
        page.wait_for_function("layout().groups.length === 1")
        if mode == 'manual':
            assert page.evaluate('layout().groups[0].id') == 'manual-group'
            assert page.evaluate('layout().tabs.length') == 2
            open_button(page, 'report.md').click()
            assert page.evaluate('layout().groups[0].id') == 'manual-group'
        else:
            expect(page.get_by_text('Preview file body', exact=True)).to_be_visible()
            assert page.evaluate('layout().groups[0].visibleIds[0]') == 's:s'
            assert page.evaluate('layout().tabs.filter(t=>t.kind==="file").length') == 1
            open_button(page, 'report.md').click()
            assert page.evaluate('layout().tabs.filter(t=>t.kind==="file").length') == 1
            if mode == 'dirty':
                page.get_by_role('button', name='Edit', exact=True).click()
                editor = page.locator('textarea:visible')
                editor.fill('retained draft')
                page.evaluate("complete('next.md')")
                expect(open_button(page, 'next.md')).to_be_visible()
                expect(editor).to_have_value('retained draft')
                assert page.evaluate('layout().groups[0].visibleIds[1]') == 'f:preview:s:p:report.md'
        assert errors == []
        browser.close()


def test_two_conversations_reuse_one_real_pdf_engine(tmp_path):
    import base64
    from playwright.sync_api import sync_playwright, expect
    from test_pdf_preview_browser import outlined_pdf
    from pathlib import Path
    bundle = tmp_path / 'shared-pdf.js'
    subprocess.run(['node', 'apps/web/tests/files/build-document-window-browser.mjs', str(bundle), './shared-file-preview-browser-entry.tsx'], check=True)
    raw = base64.b64decode(outlined_pdf())
    assets = Path('apps/web/public/document-assets/pdfjs')
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            path = urlsplit(request.request.url).path
            if path == '/':
                request.fulfill(content_type='text/html; charset=utf-8', body='<div id="root"></div><script src="/fixture.js"></script>')
            elif path == '/fixture.js':
                request.fulfill(content_type='text/javascript; charset=utf-8', body=bundle.read_text())
            elif path == '/api/documents/content':
                request.fulfill(body=raw, headers={'x-document-revision': hashlib.sha256(raw).hexdigest(), 'x-document-version': 'v1'})
            elif path == '/api/documents/stat':
                request.fulfill(json={'version': 'v1'})
            elif path.startswith('/document-assets/pdfjs/'):
                target = assets / path.removeprefix('/document-assets/pdfjs/')
                request.fulfill(path=str(target)) if target.is_file() else request.fulfill(status=404)
            else:
                request.fulfill(status=404)
        page.route('https://preview.test/**', route)
        page.goto('https://preview.test/')
        expect(page.get_by_text('1 / 2', exact=True)).to_be_visible()
        first = page.locator('[data-pdf-reader]')
        first.evaluate('element=>window.originalReader=element')
        assert page.evaluate("showOwner('b')")
        expect(page.locator('[data-file-pane-id="f:preview:b:p:report.pdf"]').get_by_text('1 / 2', exact=True)).to_be_visible()
        assert page.locator('[data-pdf-reader]').count() == 1
        assert first.evaluate('element=>element===window.originalReader')
        page.evaluate("showOwner('a')")
        expect(page.locator('[data-file-pane-id="f:preview:a:p:report.pdf"]').get_by_text('1 / 2', exact=True)).to_be_visible()
        page.evaluate('showBoth()')
        expect(page.get_by_role('button', name='Show here', exact=True)).to_be_visible()
        page.get_by_role('button', name='Show here', exact=True).click()
        expect(page.locator('[data-file-pane-id="f:preview:b:p:report.pdf"]').get_by_text('1 / 2', exact=True)).to_be_visible()
        assert page.locator('[data-pdf-reader]').count() == 1
        assert first.evaluate('element=>element===window.originalReader')
        expect(page.get_by_role('alert')).to_have_count(0)
        assert errors == []
        browser.close()
