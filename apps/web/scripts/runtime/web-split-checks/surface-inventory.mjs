// surface inventory: original sequential assertions and shared fixtures.
export async function run(testContext) {


// ---------------------------------------------------------------- Task 5:
// desktop-bridge renderer staging / recovery / cleanup around the main
// transfer transaction. The bridge module shares the PLAIN (unqueried)
// center-tabs/session-store instances, so drive those here.

globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };

(testContext.navigateModule = await import("../../../lib/navigate.ts"));

(testContext.popupNavigations = []);

testContext.navigateModule.setNavigate((path) => testContext.popupNavigations.push(path));

globalThis.window.location.pathname = "/s/popup-chat";

(testContext.bridgeModule = await import("../../../lib/desktop/desktop-bridge.ts"));

(testContext.plainTabsModule = await import("../../../lib/tabs/center-tabs-store.ts"));

(testContext.plainSessionModule = await import("../../../lib/session-store/index.ts"));

(testContext.plainTabs = testContext.plainTabsModule.useCenterTabs);


(testContext.popupOpenerGroup = {
  id: "g:popup-opener",
  memberIds: ["s:popup-chat", "w:opener"],
  visibleIds: ["s:popup-chat", "w:opener"],
  focusedId: "w:opener",
});

testContext.plainTabs.setState({
  tabs: [
    { id: "s:popup-chat", kind: "session", title: "Chat", sessionId: "popup-chat" },
    { id: "w:opener", kind: "web", title: "Opener", url: "https://opener.test/" },
  ],
  activeId: "w:opener",
  groups: [testContext.popupOpenerGroup],
  splitWebTabId: "w:opener",
  splitRatio: 0.45,
});

(testContext.popupCallback = null);

(testContext.popupBridge = {
  webTab: {
    onPopup(callback) {
      testContext.popupCallback = callback;
      return () => { testContext.popupCallback = null; };
    },
  },
});

(testContext.unsubscribePopup = testContext.bridgeModule.subscribeWebTabPopups(testContext.popupBridge));

(testContext.beforeRejectedPopup = testContext.plainTabsModule.snapshotCenterTabsPayload());

testContext.popupCallback({ openerId: "missing", url: "https://popup.test/" });

testContext.popupCallback({ openerId: "s:popup-chat", url: "https://popup.test/" });

testContext.assert.deepEqual(
  testContext.plainTabsModule.snapshotCenterTabsPayload(),
  testContext.beforeRejectedPopup,
  "missing and non-web openers must not create popup tabs",
);


testContext.popupCallback({ openerId: "w:opener", url: "https://popup.test/" });

(testContext.popupOne = testContext.plainTabs.getState().activeId);

testContext.popupCallback({ openerId: "w:opener", url: "https://popup.test/" });

(testContext.popupTwo = testContext.plainTabs.getState().activeId);

testContext.assert.notEqual(testContext.popupOne, testContext.popupTwo, "two popup requests must create distinct tabs");

testContext.assert.equal(
  testContext.plainTabs.getState().tabs.find((tab) => tab.id === testContext.popupOne)?.openerTabId,
  "w:opener",
  "popup tabs must retain their opener Page identity",
);

testContext.assert.equal(
  testContext.plainTabs.getState().tabs.find((tab) => tab.id === testContext.popupTwo)?.openerTabId,
  "w:opener",
);

testContext.assert.ok(testContext.plainTabs.getState().tabs.some((tab) => tab.id === "w:opener"));

testContext.assert.equal(testContext.plainTabs.getState().activeId, testContext.popupTwo);

testContext.assert.deepEqual(testContext.plainTabs.getState().groups, [testContext.popupOpenerGroup]);

testContext.assert.equal(testContext.plainTabs.getState().splitWebTabId, "w:opener");

testContext.assert.deepEqual(
  testContext.popupNavigations,
  [],
  "a popup opened from an existing /s route must not navigate through /chat and reactivate the session",
);

testContext.assert.equal(
  testContext.plainTabs.getState().tabs.filter((tab) => tab.url === "https://popup.test/").length,
  2,
  "popup URLs must not reuse a normal deterministic web tab",
);

globalThis.window.location.pathname = "/settings/general";

testContext.popupCallback({ openerId: "w:opener", url: "https://off-route-popup.test/" });

testContext.assert.deepEqual(
  testContext.popupNavigations,
  ["/chat"],
  "a popup received off the center surface must navigate to /chat exactly once",
);

testContext.assert.equal(
  testContext.plainTabs.getState().tabs.find((tab) => tab.id === testContext.plainTabs.getState().activeId)?.url,
  "https://off-route-popup.test/",
);

testContext.unsubscribePopup();

testContext.assert.equal(testContext.popupCallback, null);

testContext.navigateModule.setNavigate(null);

globalThis.window.location.pathname = "/chat";


(testContext.scopedPipTabs = [
  { id: "s:pip-owner", kind: "session", title: "Owner", sessionId: "pip-owner" },
  { id: "s:pip-other", kind: "session", title: "Other", sessionId: "pip-other" },
  { id: "w:pip-owner-page", kind: "web", title: "Owner page", url: "https://pip-owner.test/" },
]);

testContext.plainTabs.setState({
  tabs: testContext.scopedPipTabs,
  activeId: "s:pip-owner",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.useWebTabPip.getState().show("w:pip-owner-page", "s:pip-owner");

(testContext.scopedPipBridge = {
  windowId: "pip-scope-window",
  webTab: {
    inspect: async () => ({
      target_id: "target:pip-owner-page",
      url: "https://pip-owner.test/",
      title: "Owner page",
    }),
    syncVisible() {},
  },
});

testContext.bridgeModule.registerVisibleWebTabBounds(
  testContext.scopedPipBridge,
  "w:pip-owner-page",
  { x: 40, y: 40, width: 360, height: 188 },
);

testContext.bridgeModule.setWebTabReady("w:pip-owner-page", true);

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("pip-owner", true)?.tab_id,
  "w:pip-owner-page",
);

testContext.plainTabs.getState().setActive("s:pip-other");

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("pip-other", true),
  null,
  "the next chat must reject stale owner PiP bounds before React cleanup",
);

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("pip-owner", true),
  null,
  "an inactive owner chat must not retain a visible turn surface",
);

(testContext.hiddenPipInventory = await testContext.bridgeModule.browserPageInventory(testContext.scopedPipBridge));

testContext.assert.deepEqual(
  testContext.hiddenPipInventory.pages.map((page) => [page.tab_id, page.visible, page.region]),
  [["w:pip-owner-page", false, "background"]],
);

testContext.assert.equal(
  testContext.bridgeModule.finalizeWebTabPreview(
    "w:pip-owner-page",
    0,
    { preview: { title: "stale" }, target_id: "target:pip-owner-page" },
  ).reason_code,
  "page_context_stale",
);

testContext.assert.equal(
  testContext.bridgeModule.finalizeBoundWebTabActivation(
    "w:pip-owner-page",
    0,
    "target:pip-owner-page",
  ).reason_code,
  "page_context_stale",
);

testContext.useWebTabPip.getState().hide();

testContext.bridgeModule.removeVisibleWebTabBounds(testContext.scopedPipBridge, "w:pip-owner-page");

testContext.bridgeModule.setWebTabReady("w:pip-owner-page", false);


(testContext.surfaceTabs = [
  { id: "s:surface-chat", kind: "session", title: "Chat", sessionId: "surface-chat" },
  { id: "w:surface-page", kind: "web", title: "Page", url: "https://surface.test/" },
]);

testContext.plainTabs.setState({
  tabs: testContext.surfaceTabs,
  activeId: "s:surface-chat",
  groups: [{
    id: "g:surface-geometry",
    memberIds: ["s:surface-chat", "w:surface-page"],
    visibleIds: ["s:surface-chat", "w:surface-page"],
    focusedId: "s:surface-chat",
  }],
  splitWebTabId: null,
  splitRatio: 0.5,
});

(testContext.geometryBridge = {
  webTab: {
    syncVisible() {},
  },
});

testContext.bridgeModule.registerVisibleWebTabBounds(
  testContext.geometryBridge,
  "w:surface-page",
  { x: 600, y: 120, width: 500, height: 700 },
);

testContext.bridgeModule.setWebTabReady("w:surface-page", true);

(testContext.firstGeometryRevision = testContext.bridgeModule.surfaceRefForChat(
  "surface-chat",
  true,
)?.geometry_revision);

testContext.assert.ok(testContext.firstGeometryRevision > 0);

testContext.bridgeModule.registerVisibleWebTabBounds(
  testContext.geometryBridge,
  "w:surface-page",
  { x: 600, y: 120, width: 500, height: 700 },
);

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("surface-chat", true)?.geometry_revision,
  testContext.firstGeometryRevision,
  "identical bounds must preserve geometry revision",
);

testContext.bridgeModule.registerVisibleWebTabBounds(
  testContext.geometryBridge,
  "w:surface-page",
  { x: 0, y: 120, width: 500, height: 700 },
);

testContext.assert.ok(
  testContext.bridgeModule.surfaceRefForChat("surface-chat", true)?.geometry_revision
    > testContext.firstGeometryRevision,
  "pane movement must advance geometry revision",
);

(testContext.previewStartRevision = testContext.bridgeModule.surfaceRefForChat(
  "surface-chat",
  true,
)?.geometry_revision);

(testContext.resolveDeferredPreview = undefined);

(testContext.deferredPreview = new Promise((resolve) => {
  testContext.resolveDeferredPreview = resolve;
}));

testContext.bridgeModule.registerVisibleWebTabBounds(
  testContext.geometryBridge,
  "w:surface-page",
  { x: 0, y: 120, width: 520, height: 700 },
);

testContext.resolveDeferredPreview({ preview: { title: "stale" }, target_id: "target-1" });

(testContext.stalePreview = await testContext.deferredPreview.then((result) =>
  testContext.bridgeModule.finalizeWebTabPreview(
    "w:surface-page",
    testContext.previewStartRevision,
    result,
  ),
));

testContext.assert.equal(testContext.stalePreview.reason_code, "page_context_stale");

testContext.assert.equal("preview" in testContext.stalePreview, false);

testContext.assert.equal("target_id" in testContext.stalePreview, false);

(testContext.legacyPreview = testContext.bridgeModule.finalizeWebTabPreview(
  "w:surface-page",
  0,
  { preview: { title: "legacy" }, target_id: "target-legacy" },
));

testContext.assert.equal(testContext.legacyPreview.ok, true);

testContext.assert.equal(testContext.legacyPreview.preview.title, "legacy");

testContext.assert.equal(testContext.legacyPreview.target_id, "target-legacy");

(testContext.resolveDeferredActivation = undefined);

(testContext.deferredActivation = new Promise((resolve) => {
  testContext.resolveDeferredActivation = resolve;
}));

testContext.bridgeModule.removeVisibleWebTabBounds(testContext.geometryBridge, "w:surface-page");

testContext.bridgeModule.setWebTabReady("w:surface-page", false);

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("surface-chat", true),
  null,
  "the production hide lifecycle must remove the page from turn surfaces",
);

(testContext.occludedPreview = testContext.bridgeModule.finalizeWebTabPreview(
  "w:surface-page",
  0,
  { preview: { title: "occluded" }, target_id: "target-occluded" },
));

testContext.assert.equal(testContext.occludedPreview.reason_code, "page_context_stale");

testContext.assert.equal("preview" in testContext.occludedPreview, false);

testContext.assert.equal("target_id" in testContext.occludedPreview, false);

testContext.resolveDeferredActivation("target-occluded");

(testContext.occludedActivation = await testContext.deferredActivation.then((targetId) =>
  testContext.bridgeModule.finalizeBoundWebTabActivation(
    "w:surface-page",
    0,
    targetId,
  ),
));

testContext.assert.equal(testContext.occludedActivation.reason_code, "page_context_stale");

testContext.assert.equal("target_id" in testContext.occludedActivation, false);

testContext.bridgeModule.registerVisibleWebTabBounds(
  testContext.geometryBridge,
  "w:surface-page",
  { x: 0, y: 120, width: 520, height: 700 },
);

testContext.bridgeModule.setWebTabReady("w:surface-page", true);

for (const [visibleIds, expectedRegion] of [
  [["w:surface-page", "s:surface-chat"], "left"],
  [["s:surface-chat", "w:surface-page"], "right"],
]) {
  testContext.plainTabsModule.replaceCenterTabsPayload({
    version: 2,
    tabs: testContext.surfaceTabs,
    activeId: "s:surface-chat",
    groups: [{
      id: "g:surface",
      memberIds: visibleIds,
      visibleIds,
      focusedId: "s:surface-chat",
    }],
    splitWebTabId: null,
    splitRatio: 0.5,
  }, { persist: false });
  testContext.assert.equal(
    testContext.bridgeModule.surfaceRefForChat("surface-chat", true)?.region,
    expectedRegion,
  );
}

testContext.plainTabs.setState({
  tabs: testContext.surfaceTabs,
  activeId: "s:surface-chat",
  groups: [{
    id: "g:surface",
    memberIds: ["s:surface-chat", "w:surface-page"],
    visibleIds: ["w:surface-page"],
    focusedId: "w:surface-page",
  }],
  splitWebTabId: null,
  splitRatio: 0.5,
});

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("surface-chat", true)?.region,
  "center",
);

testContext.plainTabs.setState({
  tabs: testContext.surfaceTabs,
  activeId: "s:surface-chat",
  groups: [],
  splitWebTabId: "w:surface-page",
  splitRatio: 0.5,
});

testContext.assert.equal(
  testContext.bridgeModule.surfaceRefForChat("surface-chat", true)?.region,
  "right",
);

testContext.plainTabsModule.replaceCenterTabsPayload({
  version: 2,
  tabs: testContext.surfaceTabs,
  activeId: "s:surface-chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.5,
}, { persist: false });

testContext.assert.equal(testContext.bridgeModule.surfaceRefForChat("surface-chat", true), null);

testContext.assert.deepEqual(
  testContext.bridgeModule.surfaceOriginForChat("surface-chat", true),
  { version: 1, window_id: "main", access: "enabled" },
);

(testContext.hiddenLegacyPreview = testContext.bridgeModule.finalizeWebTabPreview(
  "w:surface-page",
  0,
  { preview: { title: "hidden" }, target_id: "target-hidden" },
));

testContext.assert.equal(testContext.hiddenLegacyPreview.reason_code, "page_context_stale");

testContext.assert.equal("preview" in testContext.hiddenLegacyPreview, false);

testContext.assert.equal("target_id" in testContext.hiddenLegacyPreview, false);


// Replacing the id is the cleanup signal used by the desktop bridge. Verify
// the existing reconciler destroys the native view once Home removes that id.
{
  const calls = [];
  const cleanupBridge = {
    webTab: {
      ensure: (...args) => calls.push(["ensure", ...args]),
      destroy: (...args) => calls.push(["destroy", ...args]),
      syncVisible: () => {},
    },
  };
  const cleanupId = "w:https://cleanup-home.test/";
  testContext.bridgeModule.ensureWebView(cleanupBridge, cleanupId, "https://cleanup-home.test/");
  testContext.plainTabs.setState({
    tabs: [{ id: cleanupId, kind: "web", title: "Cleanup", url: "https://cleanup-home.test/" }],
    activeId: cleanupId,
    groups: [],
    splitWebTabId: null,
  });
  testContext.plainTabs.getState().replaceWebTabWithNewTabPage(cleanupId);
  testContext.bridgeModule.destroyStaleWebViews(
    cleanupBridge,
    testContext.plainTabs.getState().tabs.map((tab) => tab.id),
  );
  testContext.assert.deepEqual(calls, [
    ["ensure", cleanupId, "https://cleanup-home.test/"],
    ["destroy", cleanupId],
  ]);
}
}
