"""Real components and IndexedDB on a private routed origin, without a worker."""
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

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
    from playwright.sync_api import sync_playwright

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
                    if state.get("put_status"):
                        request.fulfill(status=state["put_status"], json={"error":"injected publication failure"})
                        return
                    raw = request.request.post_data_buffer
                    if request.request.headers.get("x-baseline-revision") == "absent":
                        state.setdefault("copies", []).append(raw)
                        request.fulfill(json={"ok":True,"status":"committed","revision":hashlib.sha256(raw).hexdigest()})
                        return
                    state["writes"].append(raw)
                    state["body"] = raw
                    state["revision"] = hashlib.sha256(raw).hexdigest()
                    request.fulfill(json={"ok": True, "status": "committed", "revision": state["revision"]})
            elif path == "/api/documents/history":
                request.fulfill(json={"entries": state.get("history_entries", [{"version_id": "version1", "actor": "user"}]), "next_cursor": None, "model_index": state.get("model_index", {"state":"complete"})})
            elif path == "/api/documents/history/content":
                request.fulfill(body=state.get("history_body", b"history"))
            else:
                request.fulfill(status=404)

        page.route("https://document.test/**", route)
        try:
            yield page, state, errors
        finally:
            browser.close()


def test_document_window_preview_edit_autosave_history(browser_page):
    from playwright.sync_api import expect

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
    from playwright.sync_api import expect

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
        editor.evaluate("element => { element.focus(); element.setSelectionRange(element.value.length, element.value.length); }")
        editor.press_sequentially("12345678")
    assert page.evaluate("window.blobTextReads") == 0
    assert state["writes"][-1] == b"a" * (256 * 1024) + b"12345678"
    assert errors == []


def test_rename_temporarily_disables_editor_and_failure_reenables_it(browser_page):
    from playwright.sync_api import expect

    page, state, errors = browser_page
    page.goto("https://document.test/")
    page.get_by_role("button", name="Edit", exact=True).click()
    editor = page.locator("textarea:visible")
    expect(editor).to_have_value("old")
    page.evaluate("""() => {
      window.pendingRename=documentDraftLifecycle.runServerRenameWithDrafts('p','notes.md','renamed.md',
        () => new Promise(resolve => {window.finishRename=()=>resolve({status:'error',error:'denied'})}),
        async()=>({status:'ready'}));
    }""")
    expect(editor).to_be_disabled()
    page.wait_for_function("typeof window.finishRename === 'function'")
    page.evaluate("() => window.finishRename()")
    assert page.evaluate("async () => (await window.pendingRename).ok") is False
    expect(editor).to_be_enabled()
    expect(editor).to_have_value("old")
    assert state["writes"] == []
    assert errors == []

def test_real_raster_edit_loads_rotates_and_persists_pixels(browser_page):
    from playwright.sync_api import expect
    page, state, errors = browser_page
    state["body"] = (Path(__file__).parent / "fixtures" / "raster" / "quadrants.png").read_bytes()
    page.goto("https://document.test/?file=quadrants.png")
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-raster-editor="true"]')).to_be_visible(timeout=15000)
    with page.expect_response(lambda response: response.request.method == "PUT", timeout=15000):
        page.get_by_role("button", name="Rotate", exact=True).click()
    assert state["writes"]
    pixels = page.evaluate("""async bytes => { const blob = new Blob([new Uint8Array(bytes)], {type:'image/png'}); const image = await createImageBitmap(blob); const c=document.createElement('canvas'); c.width=image.width;c.height=image.height; const x=c.getContext('2d');x.drawImage(image,0,0); const p=x.getImageData(0,0,image.width,image.height).data; return {width:image.width,height:image.height,tl:[p[0],p[1],p[2]]}; }""", list(state["writes"][-1]))
    assert pixels["width"] == 240 and pixels["height"] == 320
    assert pixels["tl"][2] > pixels["tl"][0]
    assert errors == []


def test_raster_history_keeps_native_undo_after_autosave_and_visibility(browser_page):
    from playwright.sync_api import expect
    page, state, errors = browser_page
    original = (Path(__file__).parent / "fixtures/raster/quadrants.png").read_bytes()
    state["body"] = state["history_body"] = original
    page.goto("https://document.test/?file=quadrants.png")
    page.get_by_role("button", name="Edit", exact=True).click()
    host = page.locator('[data-raster-editor]')
    expect(host).to_be_visible(timeout=15000)
    canvas = host.locator("canvas").first.element_handle()
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Rotate", exact=True).click()
    page.get_by_role("button", name="Other tab", exact=True).click()
    page.get_by_role("button", name="Return to file", exact=True).click()
    page.get_by_role("button", name="Open settings route", exact=True).click()
    page.get_by_role("button", name="Back to file route", exact=True).click()
    page.get_by_role("button", name="History", exact=True).click()
    page.get_by_role("button", name="After", exact=True).click()
    expect(host.locator("canvas")).to_have_count(2)
    page.get_by_role("button", name="Back to current file", exact=True).click()
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(host).to_be_visible()
    assert host.locator("canvas").first.evaluate("(current,previous)=>current===previous",canvas)
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Undo", exact=True).click()
    result = page.evaluate("""async bytes=>{const b=await createImageBitmap(new Blob([new Uint8Array(bytes)]));const c=document.createElement('canvas');c.width=b.width;c.height=b.height;const x=c.getContext('2d');x.drawImage(b,0,0);const p=Array.from(x.getImageData(0,0,1,1).data);b.close();return {w:c.width,h:c.height,p};}""",list(state["writes"][-1]))
    assert result == {"w":320,"h":240,"p":[255,0,0,255]}
    assert errors == []


@pytest.mark.parametrize("extension", ["jpg", "webp"])
def test_raster_same_format_export_and_reopen(browser_page, extension):
    from playwright.sync_api import expect
    page, state, errors = browser_page
    state["body"] = (Path(__file__).parent / f"fixtures/raster/quadrants.{extension}").read_bytes()
    page.goto(f"https://document.test/?file=quadrants.{extension}")
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-raster-editor]')).to_be_visible(timeout=15000)
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Rotate", exact=True).click()
    raw = state["writes"][-1]
    assert raw.startswith(b"\xff\xd8\xff") if extension == "jpg" else raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    dimensions = page.evaluate("""async bytes=>{const b=await createImageBitmap(new Blob([new Uint8Array(bytes)]));const result=[b.width,b.height];b.close();return result;}""",list(raw))
    assert dimensions == [240,320]
    page.reload()
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator('[data-raster-editor]')).to_be_visible(timeout=15000)
    assert errors == []


def test_raster_crop_annotations_and_close_persist_real_pixels(browser_page):
    from playwright.sync_api import expect
    page, state, errors = browser_page
    state["body"] = (Path(__file__).parent / "fixtures/raster/quadrants.png").read_bytes()
    page.goto("https://document.test/?file=quadrants.png")
    page.get_by_role("button", name="Edit", exact=True).click()
    host=page.locator('[data-raster-editor]')
    expect(host).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Crop", exact=True).click()
    # A missing crop selection is a recoverable operation error, not an empty file.
    page.get_by_role("button", name="Apply crop", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("crop")
    box=host.locator("canvas").last.bounding_box()
    page.mouse.move(box["x"]+20,box["y"]+20)
    page.mouse.down()
    page.mouse.move(box["x"]+140,box["y"]+100,steps=5)
    page.mouse.up()
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Apply crop", exact=True).click()
    cropped=state["writes"][-1]
    size=page.evaluate("""async bytes=>{const b=await createImageBitmap(new Blob([new Uint8Array(bytes)]));const r=[b.width,b.height];b.close();return r;}""",list(cropped))
    assert size == [120,80]
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Shape", exact=True).click()
    shaped=state["writes"][-1]
    def colors(data):
        return page.evaluate("""async bytes => {
            const b=await createImageBitmap(new Blob([new Uint8Array(bytes)]));
            const c=document.createElement('canvas');c.width=b.width;c.height=b.height;
            const ctx=c.getContext('2d');ctx.drawImage(b,0,0);b.close();
            const p=ctx.getImageData(0,0,c.width,c.height).data;
            let magenta=0,black=0;
            for(let i=0;i<p.length;i+=4){
                if(p[i]>200 && p[i+1]<60 && p[i+2]>200)magenta++;
                if(p[i]<60 && p[i+1]<60 && p[i+2]<60)black++;
            }
            return {magenta,black};
        }""",list(data))
    assert colors(shaped)["magenta"] > colors(cropped)["magenta"]
    page.once("dialog",lambda dialog:dialog.accept("Hello"))
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Text", exact=True).click()
    annotated=state["writes"][-1]
    assert colors(annotated)["black"] > colors(shaped)["black"]
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Undo", exact=True).click()
    assert colors(state["writes"][-1])["black"] == colors(shaped)["black"]
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Redo", exact=True).click()
    assert colors(state["writes"][-1])["black"] == colors(annotated)["black"]
    page.get_by_role("button", name="Draw", exact=True).click()
    box=host.locator("canvas").last.bounding_box()
    before_draw=colors(state["writes"][-1])["magenta"]
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.mouse.move(box["x"]+5,box["y"]+5)
        page.mouse.down()
        page.mouse.move(box["x"]+110,box["y"]+5,steps=8)
        page.mouse.up()
    assert colors(state["writes"][-1])["magenta"] > before_draw
    page.get_by_role("button", name="Close file", exact=True).click()
    expect(page.get_by_text("File closed",exact=True)).to_be_visible()
    assert errors == []


@pytest.mark.parametrize("status", [409,503])
def test_raster_failed_publication_keeps_editor_and_retryable_draft(browser_page,status):
    from playwright.sync_api import expect
    page, state, errors = browser_page
    state["body"] = (Path(__file__).parent / "fixtures/raster/quadrants.png").read_bytes()
    page.goto("https://document.test/?file=quadrants.png")
    page.get_by_role("button", name="Edit", exact=True).click()
    host=page.locator('[data-raster-editor]')
    expect(host).to_be_visible(timeout=15000)
    state["put_status"]=status
    page.get_by_role("button", name="Rotate", exact=True).click()
    expect(page.get_by_role("button",name="Retry",exact=True)).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "PUT" and response.status == status):
        page.get_by_role("button", name="Close file", exact=True).click()
    expect(page.get_by_role("button",name="Retry",exact=True)).to_be_visible()
    expect(host).to_be_visible()
    expect(page.get_by_text("File closed",exact=True)).to_have_count(0)
    assert state["writes"] == []
    state.pop("put_status")
    with page.expect_response(lambda response: response.request.method == "PUT"):
        page.get_by_role("button", name="Retry", exact=True).click()
    page.get_by_role("button", name="Close file", exact=True).click()
    expect(page.get_by_text("File closed",exact=True)).to_be_visible()
    assert errors == []


@pytest.mark.parametrize("extension", ["png","webp"])
def test_raster_animation_is_readonly_and_original_download_remains(browser_page,extension):
    from playwright.sync_api import expect
    page, state, errors = browser_page
    original=(Path(__file__).parent / f"fixtures/raster/animated.{extension}").read_bytes()
    state["body"]=original
    page.goto(f"https://document.test/?file=animated.{extension}")
    page.get_by_role("button",name="Edit",exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("animated")
    expect(page.get_by_role("link",name="Download disk file",exact=True)).to_be_visible()
    expect(page.locator('[data-raster-editor] canvas')).to_have_count(0)
    assert state["writes"] == [] and state["body"] == original
    assert errors == []


def test_raster_explicit_png_copy_preserves_source_and_conflict(browser_page):
    from playwright.sync_api import expect
    page,state,errors=browser_page
    original=(Path(__file__).parent / "fixtures/raster/quadrants.webp").read_bytes()
    state["body"]=original
    page.goto("https://document.test/?file=quadrants.webp")
    button=page.get_by_role("button",name="Convert to PNG",exact=True)
    expect(button).to_be_enabled()
    state["put_status"]=409
    page.once("dialog",lambda dialog:dialog.accept("copy.png"))
    with page.expect_response(lambda r:r.request.method=="PUT" and r.status==409):
        button.click()
    expect(page.get_by_role("alert")).to_be_visible()
    assert state["body"]==original and not state.get("copies")
    state.pop("put_status")
    page.once("dialog",lambda dialog:dialog.accept("copy.png"))
    with page.expect_response(lambda r:r.request.method=="PUT" and r.status==200) as response:
        button.click()
    assert response.value.request.headers["x-baseline-revision"]=="absent"
    assert state["copies"][0].startswith(bytes([137,80,78,71,13,10,26,10]))
    assert state["body"]==original and state["writes"]==[]
    assert errors==[]


def test_partial_model_history_is_not_reported_as_no_versions(browser_page):
    from playwright.sync_api import expect
    page,state,errors=browser_page
    state["history_entries"]=[]
    state["model_index"]={"state":"partial"}
    page.goto("https://document.test/")
    page.get_by_role("button",name="History",exact=True).click()
    expect(page.get_by_text("No retained versions.",exact=True)).to_have_count(0)
    button=page.get_by_role("button",name="Continue loading history",exact=True)
    expect(button).to_be_visible()
    state["model_index"]={"state":"unavailable","unavailable_count":2}
    button.click()
    expect(page.get_by_text("Some older model versions are unavailable.",exact=True)).to_be_visible()
    assert errors==[]


def test_incomplete_model_history_cannot_restore_unconfirmed_after_version(browser_page):
    from playwright.sync_api import expect
    page,state,errors=browser_page
    state["history_entries"]=[{"version_id":"m-version","actor":"model","status":"recovery_required","before_revision":"a"*64,"after_revision":None}]
    page.goto("https://document.test/")
    page.get_by_role("button",name="History",exact=True).click()
    expect(page.get_by_text("After version unavailable",exact=True)).to_be_visible()
    expect(page.get_by_role("button",name="After",exact=True)).to_be_disabled()
    expect(page.get_by_role("button",name="Restore",exact=True)).to_be_disabled()
    expect(page.get_by_role("button",name="Before",exact=True)).to_be_enabled()
    assert state["writes"]==[] and errors==[]
