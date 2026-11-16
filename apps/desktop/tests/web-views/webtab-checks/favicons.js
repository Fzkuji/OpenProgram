module.exports = function createChecks(t) {
  async function checkFaviconLifecycle() {
    const win = t.fakeWindow(950);
    const ctx = t.registerContext("favicon-window", win);
    const record = t.hooks.ensureView(ctx, "favicon-page", "https://site.test/");
    const controlled = t.generatedNativeRecords.at(-1);
    controlled.controls[0].resolve();
    await record.navigation?.promise;
    const wc = record.view.webContents;
    const calls = [];
    const requests = [];
    wc.session = { fetch(url, options) {
      calls.push({ url, ...options });
      return new Promise(resolve => requests.push(resolve));
    } };
    const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
    const response = (body, headers = {}) => new Response(body, { headers: { "content-type": "image/png", ...headers } });
    controlled.emitWebContents("page-favicon-updated", {}, ["https://site.test/icon.png"]);
    requests.shift()(response(new Uint8Array([1, 2, 3])));
    await tick();
    t.assert.equal(record.faviconUrl, "data:image/png;base64,AQID");
    t.assert.equal(calls[0].credentials, "omit");
    t.hooks.ensureView(ctx, record.id, "https://site.test/");
    t.assert.equal(win.sent.at(-1)[1].faviconUrl, record.faviconUrl);

    const loadedCalls = calls.length;
    controlled.emitWebContents("page-favicon-updated", {}, ["https://site.test/icon.png"]);
    await tick();
    t.assert.equal(calls.length, loadedCalls, "same-page favicon events reuse the current icon");
    t.assert.equal(record.faviconUrl, "data:image/png;base64,AQID");
    controlled.emitWebContents("page-favicon-updated", {}, ["https://site.test/late.png"]);
    const staleRequest = calls.at(-1);
    controlled.emitWebContents("did-navigate");
    t.assert.equal(staleRequest.signal.aborted, true);
    requests.shift()(response(new Uint8Array([4])));
    await tick();
    t.assert.equal(record.faviconUrl, "", "old-page results must not survive navigation");
    t.assert.equal(win.sent.at(-1)[1].faviconUrl, "");

    controlled.emitWebContents("page-favicon-updated", {}, ["https://site.test/large.png"]);
    requests.shift()(response(new Uint8Array(256 * 1024 + 1)));
    await tick();
    t.assert.equal(record.faviconUrl, "", "streaming responses must obey the byte limit");
    const before = calls.length;
    controlled.emitWebContents("page-favicon-updated", {}, ["file:///private/icon.png", "javascript:alert(1)"]);
    await tick();
    t.assert.equal(calls.length, before, "favicon loading must not read local or executable URLs");
    controlled.emitWebContents("page-favicon-updated", {}, ["https://site.test/pending.png"]);
    controlled.emitWebContents("destroyed");
    t.assert.equal(calls.at(-1).signal.aborted, true, "destroyed pages cancel pending favicon reads");
    requests.shift()(response(new Uint8Array([1])));
    await tick();
    t.assert.equal(record.faviconUrl, "");
    controlled.emitWebContents("destroyed");
    t.hooks.windows.delete(ctx.id);
  }
  return { checkFaviconLifecycle };
};
