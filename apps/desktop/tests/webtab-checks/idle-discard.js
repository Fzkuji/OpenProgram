// An idle Page sleeps (its view closes, its URL stays) and wakes where it
// was; Pages in use are never put to sleep.
module.exports = async function checkIdleDiscard(t) {
  const win = t.fakeWindow(961);
  const ctx = t.registerContext("idle-window", win);
  const bounds = { x: 0, y: 0, width: 800, height: 600 };
  async function page(id, url) {
    const record = t.hooks.ensureView(ctx, id, url);
    t.generatedNativeRecords.at(-1).controls[0].resolve();
    await record.navigation?.promise;
    return record;
  }
  try {
    const shown = await page("shown-page", "https://site.test/shown");
    const idle = await page("idle-page", "https://site.test/idle");
    const held = await page("held-page", "https://site.test/held");
    const loading = await page("loading-page", "https://site.test/loading");
    t.assert.ok(shown.lastUsedAt > 0, "a new Page is stamped as used");
    t.hooks.syncVisibleViews(ctx, [{ id: "shown-page", bounds }]);
    held.controlPayload = { session_id: "agent" };
    loading.view.webContents.loadURL("https://site.test/loading-2");
    for (const record of [shown, idle, held, loading]) record.lastUsedAt = 0;

    t.assert.equal(t.hooks.idleDiscard.sweep(ctx), 1, "only the idle hidden Page sleeps");
    t.assert.ok(ctx.views.has("shown-page"), "a visible Page stays");
    t.assert.ok(ctx.views.has("held-page"), "a Page under Agent control stays");
    t.assert.ok(ctx.views.has("loading-page"), "a loading Page stays");
    t.assert.equal(ctx.views.has("idle-page"), false, "the idle Page's view closed");
    t.assert.ok(shown.lastUsedAt > 0, "a Page in use is stamped by the sweep");
    t.assert.equal(ctx.sleepingViews.get("idle-page").url, "https://site.test/idle");
    t.assert.equal(t.hooks.idleDiscard.sweep(ctx), 0, "a second sweep finds nothing");

    const inspected = await t.hooks.inspectView(ctx, "idle-page");
    t.assert.equal(inspected.url, "https://site.test/idle", "a sleeping Page still reports its URL");
    t.assert.equal(inspected.asleep, true);
    t.assert.equal(inspected.target_id, null);

    // Showing the Page wakes it at the URL it slept at.
    t.assert.equal(t.hooks.syncVisibleViews(ctx, [{ id: "idle-page", bounds }]), true);
    const woken = ctx.views.get("idle-page");
    t.assert.ok(woken && woken !== idle, "showing recreates the view");
    t.assert.equal(woken.navigation?.url, "https://site.test/idle");
    t.assert.equal(ctx.sleepingViews.has("idle-page"), false);
    t.assert.ok(ctx.visibleViewIds.has("idle-page"));
    t.generatedNativeRecords.at(-1).controls[0].resolve();
    await woken.navigation?.promise;

    // Activation without a URL wakes a sleeping Page too; closing the tab
    // of a sleeping Page forgets it.
    t.hooks.syncVisibleViews(ctx, [{ id: "shown-page", bounds }]);
    woken.lastUsedAt = 0;
    t.assert.equal(t.hooks.idleDiscard.sweep(ctx), 1);
    const activated = await t.hooks.activateView(ctx, "idle-page", "");
    t.assert.ok(activated && ctx.views.has("idle-page"), "activation wakes the Page");
    t.generatedNativeRecords.at(-1).controls[0].resolve();
    await ctx.views.get("idle-page").navigation?.promise;
    t.hooks.syncVisibleViews(ctx, [{ id: "shown-page", bounds }]);
    ctx.views.get("idle-page").lastUsedAt = 0;
    t.assert.equal(t.hooks.idleDiscard.sweep(ctx), 1);
    t.assert.equal(t.hooks.destroyView(ctx, "idle-page", { strict: true }), true, "closing a sleeping Page's tab succeeds");
    t.assert.equal(ctx.sleepingViews.has("idle-page"), false);
    t.assert.equal(await t.hooks.inspectView(ctx, "idle-page"), null);
  } finally {
    t.hooks.cleanupWindowContext(ctx);
    t.hooks.windows.delete(ctx.id);
  }
};
