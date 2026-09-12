"""Real components and IndexedDB on a private routed origin, without a worker."""
import hashlib
import json
import subprocess
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    root = tmp_path_factory.mktemp("document-browser")
    result = {}
    for name, entry in (("window", "./document-window-browser-entry.tsx"),
                        ("controller", "./document-controller-browser-entry.ts")):
        path = root / f"{name}.js"
        subprocess.run(["node", "apps/web/tests/build-document-window-browser.mjs", str(path), entry], check=True)
        result[name] = path.read_text()
    return result


@pytest.fixture
def browser_page(bundles):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        state = {"body": b"old", "revision": "a" * 64, "writes": [], "requests": []}

        def route(request):
            path = urlsplit(request.request.url).path
            state["requests"].append(path)
            if path in ("/", "/controller"):
                script = "controller" if path == "/controller" else "window"
                request.fulfill(content_type="text/html", body=f'<div id="root"></div><script src="/{script}.js"></script>')
            elif path in ("/window.js", "/controller.js"):
                request.fulfill(content_type="text/javascript", body=bundles[path[1:-3]])
            elif path == "/api/documents/content":
                if request.request.method == "GET":
                    request.fulfill(body=state["body"], headers={"x-document-revision": state["revision"]})
                else:
                    raw = request.request.post_data_buffer
                    state["writes"].append(raw)
                    state["body"] = raw
                    state["revision"] = hashlib.sha256(raw).hexdigest()
                    request.fulfill(json={"ok": True, "status": "committed", "revision": state["revision"]})
            elif path == "/api/documents/history":
                request.fulfill(json={"entries": [{"version_id": "version1", "actor": "user"}], "next_cursor": None})
            elif path == "/api/documents/history/content":
                request.fulfill(body=b"history")
            else:
                request.fulfill(status=404)

        page.route("https://document.test/**", route)
        try:
            yield page, state, errors
        finally:
            browser.close()


def test_document_window_preview_edit_autosave_history(browser_page):
    page, state, errors = browser_page
    page.goto("https://document.test/")
    expect(page.get_by_role("button", name="Preview", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("button", name="Edit", exact=True)).to_be_enabled()
    assert page.locator("textarea:visible").count() == 0
    assert page.get_by_role("button", name="Save", exact=True).count() == 0
    page.get_by_role("button", name="Edit", exact=True).click()
    editor = page.locator("textarea:visible")
    expect(editor).to_have_value("old")
    with page.expect_response(lambda response: response.request.method == "PUT"):
        editor.fill("new")
    assert state["writes"] == [b"new"]
    editor.evaluate("element => window.originalEditor = element")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("p").filter(has_text="new")).to_be_visible()
    page.get_by_role("button", name="Edit", exact=True).click()
    assert editor.evaluate("element => element === window.originalEditor")
    with page.expect_response(lambda response: response.request.method == "PUT"):
        editor.press("End")
        editor.press_sequentially("X")
    page.get_by_role("button", name="Preview", exact=True).click()
    page.get_by_role("button", name="Edit", exact=True).click()
    editor.press("ControlOrMeta+z")
    expect(editor).to_have_value("new")
    page.get_by_role("button", name="History", exact=True).click()
    page.get_by_role("button", name="After", exact=True).click()
    expect(page.get_by_text("history", exact=True)).to_be_visible()
    page.get_by_role("button", name="Back to current file", exact=True).click()
    assert errors == []


def test_document_pending_retry_after_browser_reload_preserves_newer_draft(browser_page):
    page, _state, errors = browser_page
    page.goto("https://document.test/controller")
    page.evaluate("""async () => {
      const a = 'a'.repeat(64);
      let release;
      window.firstRequest = false;
      window.releaseRequest = () => release();
      const hold = new Promise(resolve => release = resolve);
      const c = new DocumentController({projectId:'recovery',path:'notes.txt',debounceMs:60000,maxDebounceMs:60000,
        fetchImpl: async () => { window.firstRequest = true; await hold; throw new Error('reply lost'); }});
      await c.hydrate({bytes:'old',revision:a});
      window.controller = c;
      c.update('A');
      window.firstFlush = c.flush().catch(error => error.message);
    }""")
    page.wait_for_function("window.firstRequest")
    page.evaluate("controller.update('A+B'); releaseRequest()")
    assert page.evaluate("firstFlush") == "reply lost"
    record = page.evaluate("""async () => {
      const r = await new IndexedDbDocumentDraftStore().get('project:recovery:notes.txt');
      return {latest:await r.latestDraft.text(), pending:await r.pending.bytes.text(), key:r.pending.key};
    }""")
    assert record["latest"] == "A+B"
    assert record["pending"] == "A"
    page.reload()
    result = page.evaluate("""async expectedKey => {
      const calls=[];
      const c=new DocumentController({projectId:'recovery',path:'notes.txt',fetchImpl:async(_url, init)=>{
        calls.push({key:init.headers['idempotency-key'],baseline:init.headers['x-baseline-revision'],body:await init.body.text()});
        return new Response(JSON.stringify({ok:true,status:'committed',revision:(calls.length===1?'b':'c').repeat(64)}));
      }});
      await c.hydrate({bytes:'A',revision:'b'.repeat(64)});
      const recovered=await c.currentDraft().text();
      await c.flush();
      const remaining=await new IndexedDbDocumentDraftStore().get('project:recovery:notes.txt');
      return {recovered,calls,remaining,same: calls[0].key===expectedKey};
    }""", record["key"])
    assert result["recovered"] == "A+B"
    assert result["same"]
    assert [call["body"] for call in result["calls"]] == ["A", "A+B"]
    assert [call["baseline"] for call in result["calls"]] == ["a" * 64, "b" * 64]
    assert result["remaining"] is None
    assert errors == []


def test_document_concurrent_flush_and_invalid_response_preserve_draft(browser_page):
    page, _state, errors = browser_page
    page.goto("https://document.test/controller")
    result = page.evaluate("""async () => {
      let calls=0, release;
      const hold=new Promise(resolve=>release=resolve);
      const c=new DocumentController({projectId:'serial',path:'a.txt',fetchImpl:async()=>{
        calls++;await hold;return new Response('{}');
      }});
      await c.hydrate({bytes:'old',revision:'a'.repeat(64)});c.update('new');
      const one=c.flush().catch(()=>{}),two=c.flush().catch(()=>{});
      while(calls===0) await new Promise(resolve=>setTimeout(resolve,0));
      release();await Promise.all([one,two]);
      return {calls,status:c.getState().status,draft:await c.currentDraft().text()};
    }""")
    assert result == {"calls": 1, "status": "error", "draft": "new"}
    assert errors == []


def test_document_blob_store_keeps_binary_bytes_and_enforces_text_limit(browser_page):
    page, _state, errors = browser_page
    page.goto("https://document.test/controller")
    result = page.evaluate("""async () => {
      const store=new IndexedDbDocumentDraftStore();
      const record={key:'project:quota:a.bin',projectId:'quota',path:'a.bin',
        latestDraft:new Blob([new Uint8Array([0,255,128,1])]),baselineRevision:'a'.repeat(64),
        generation:1,editorId:'editor',updatedAt:Date.now(),storageVersion:0};
      const version=await store.put(record);
      const bytes=Array.from(new Uint8Array(await (await store.get(record.key)).latestDraft.arrayBuffer()));
      let rejected=false;
      try {await store.put({...record,storageVersion:version,
        latestDraft:new Blob([new Uint8Array(8*1024*1024+1)],{type:'text/plain'})});}
      catch(error){rejected=error.name==='QuotaExceededError';}
      const retained=Array.from(new Uint8Array(await (await store.get(record.key)).latestDraft.arrayBuffer()));
      await store.delete(record.key,version);
      return {bytes,rejected,retained,remaining:await store.list('quota')};
    }""")
    assert result == {"bytes": [0, 255, 128, 1], "rejected": True,
                      "retained": [0, 255, 128, 1], "remaining": []}
    assert errors == []


def test_unmounted_blob_draft_blocks_rename_and_flushes_before_close(browser_page):
    page, state, errors = browser_page
    page.goto("https://document.test/controller")
    result = page.evaluate("""async () => {
      const store=new IndexedDbDocumentDraftStore();
      const record={key:'project:inactive:a.bin',projectId:'inactive',path:'a.bin',
        latestDraft:new Blob([new Uint8Array([0,255,128,1])]),baselineRevision:'a'.repeat(64),
        generation:1,editorId:'editor',updatedAt:Date.now(),storageVersion:0};
      await store.put(record);
      const dirty=await documentDraftLifecycle.hasDirtyDraftsForPath('inactive','a.bin');
      let calls=0;
      const renamed=await documentDraftLifecycle.runServerRenameWithDrafts('inactive','a.bin','b.bin',
        async()=>{calls++;return {status:'ready'}},async()=>({status:'ready'}));
      const closed=await documentDraftLifecycle.flushFileDocumentsBeforeClose([{projectId:'inactive',path:'a.bin'}]);
      return {dirty,calls,renamed:renamed.ok,closed,remaining:await store.list('inactive')};
    }""")
    assert result == {"dirty": True, "calls": 0, "renamed": False, "closed": True, "remaining": []}
    assert state["writes"] == [bytes([0, 255, 128, 1])]
    assert errors == []


def test_typing_does_not_redecode_the_entire_text_blob(browser_page):
    page, state, errors = browser_page
    state["body"] = b"a" * (256 * 1024)
    page.goto("https://document.test/")
    page.get_by_role("button", name="Edit", exact=True).click()
    editor = page.locator("textarea:visible")
    expect(editor).to_have_value(state["body"].decode())
    page.evaluate("""() => {
      window.blobTextReads=0;
      const original=Blob.prototype.text;
      Blob.prototype.text=function(){window.blobTextReads++;return original.call(this)};
    }""")
    with page.expect_response(lambda response: response.request.method == "PUT"):
        editor.press("ControlOrMeta+End")
        editor.press_sequentially("12345678")
    assert page.evaluate("window.blobTextReads") == 0
    assert state["writes"][-1] == b"a" * (256 * 1024) + b"12345678"
    assert errors == []
