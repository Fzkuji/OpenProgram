// geometry scheduling: original sequential assertions and shared fixtures.
export async function run(testContext) {

for (const tab of testContext.inventoryTabs) testContext.setWebTabReady(tab.id, true);

testContext.registerVisibleWebTabBounds(
  testContext.inventoryBridge,
  "w:bilibili",
  { x: 0, y: 0, width: 500, height: 700 },
);

testContext.registerVisibleWebTabBounds(
  testContext.inventoryBridge,
  "w:youtube",
  { x: 500, y: 0, width: 500, height: 700 },
);

await Promise.resolve();

(testContext.pageInventory = await testContext.browserPageInventory(testContext.inventoryBridge));

testContext.assert.equal(testContext.pageInventory.window_id, "window-1");

testContext.assert.equal(testContext.pageInventory.active_tab_entry_id, "group:g3");

testContext.assert.equal(testContext.pageInventory.focused_tab_id, "w:youtube");

testContext.assert.deepEqual(testContext.pageInventory.tab_entries, [
  { id: "tab:w:google", mode: "single", tab_ids: ["w:google"] },
  { id: "tab:w:github", mode: "single", tab_ids: ["w:github"] },
  {
    id: "group:g3",
    mode: "split",
    tab_ids: ["w:bilibili", "w:youtube"],
    split: {
      axis: "horizontal",
      ratio: 0.5,
      panes: [
        { pane_id: "pane:g3:0", order: 0, tab_id: "w:bilibili" },
        { pane_id: "pane:g3:1", order: 1, tab_id: "w:youtube" },
      ],
    },
  },
]);

testContext.assert.deepEqual(
  testContext.pageInventory.pages.map((page) => ({
    tab_id: page.tab_id,
    tab_entry_id: page.tab_entry_id,
    placement: page.placement,
    visible: page.visible,
    focused: page.focused,
  })),
  [
    { tab_id: "w:google", tab_entry_id: "tab:w:google", placement: { mode: "single" }, visible: false, focused: false },
    { tab_id: "w:github", tab_entry_id: "tab:w:github", placement: { mode: "single" }, visible: false, focused: false },
    { tab_id: "w:bilibili", tab_entry_id: "group:g3", placement: { mode: "split", pane_id: "pane:g3:0", order: 0 }, visible: true, focused: false },
    { tab_id: "w:youtube", tab_entry_id: "group:g3", placement: { mode: "split", pane_id: "pane:g3:1", order: 1 }, visible: true, focused: true },
  ],
);

(testContext.samePageInventory = await testContext.browserPageInventory(testContext.inventoryBridge));

testContext.assert.equal(
  testContext.samePageInventory.inventory_revision,
  testContext.pageInventory.inventory_revision,
  "an unchanged Page/layout snapshot keeps its inventory revision",
);

testContext.useCenterTabs.setState({ splitRatio: 0.6 });

(testContext.resizedPageInventory = await testContext.browserPageInventory(testContext.inventoryBridge));

testContext.assert.ok(
  testContext.resizedPageInventory.inventory_revision > testContext.pageInventory.inventory_revision,
  "a split ratio change advances the inventory revision",
);

testContext.assert.equal(testContext.resizedPageInventory.tab_entries[2].split.ratio, 0.6);

(testContext.releaseOldInventory = undefined);

(testContext.oldInventoryGate = new Promise((resolve) => { testContext.releaseOldInventory = resolve; }));

(testContext.blockOldInventory = true);

(testContext.concurrentBridge = {
  ...testContext.inventoryBridge,
  webTab: {
    ...testContext.inventoryBridge.webTab,
    inspect: async (id) => {
      if (testContext.blockOldInventory) await testContext.oldInventoryGate;
      return testContext.inventoryBridge.webTab.inspect(id);
    },
  },
});

testContext.useCenterTabs.setState({ splitRatio: 0.55 });

(testContext.oldInventoryPromise = testContext.browserPageInventory(testContext.concurrentBridge));

await Promise.resolve();

testContext.blockOldInventory = false;

testContext.useCenterTabs.setState({ splitRatio: 0.65 });

(testContext.newConcurrentInventory = await testContext.browserPageInventory(testContext.concurrentBridge));

testContext.releaseOldInventory();

(testContext.oldConcurrentInventory = await testContext.oldInventoryPromise);

testContext.assert.equal(testContext.oldConcurrentInventory.tab_entries[2].split.ratio, 0.55);

testContext.assert.equal(testContext.newConcurrentInventory.tab_entries[2].split.ratio, 0.65);

testContext.assert.ok(
  testContext.oldConcurrentInventory.inventory_revision
    < testContext.newConcurrentInventory.inventory_revision,
  "an older blocked snapshot must keep an older inventory revision",
);

testContext.useCenterTabs.getState().moveGroupMember("g3", "w:bilibili", 1);

(testContext.reorderedPageInventory = await testContext.browserPageInventory(testContext.inventoryBridge));

testContext.assert.deepEqual(
  testContext.useCenterTabs.getState().groups[0].memberIds,
  ["w:youtube", "w:bilibili"],
);

testContext.assert.deepEqual(
  testContext.useCenterTabs.getState().groups[0].visibleIds,
  ["w:bilibili", "w:youtube"],
);

testContext.assert.deepEqual(
  testContext.reorderedPageInventory.tab_entries[2].split.panes,
  [
    { pane_id: "pane:g3:0", order: 0, tab_id: "w:bilibili" },
    { pane_id: "pane:g3:1", order: 1, tab_id: "w:youtube" },
  ],
  "Page inventory pane order must match the rendered visibleIds order",
);

testContext.assert.deepEqual(
  Object.fromEntries(testContext.reorderedPageInventory.pages.slice(2).map((page) => [
    page.tab_id,
    page.placement,
  ])),
  {
    "w:bilibili": { mode: "split", pane_id: "pane:g3:0", order: 0 },
    "w:youtube": { mode: "split", pane_id: "pane:g3:1", order: 1 },
  },
);

testContext.removeVisibleWebTabBounds(testContext.inventoryBridge, "w:bilibili");

testContext.removeVisibleWebTabBounds(testContext.inventoryBridge, "w:youtube");

for (const tab of testContext.inventoryTabs) testContext.setWebTabReady(tab.id, false);


(testContext.hiddenThird = testContext.focusCenterTabGroupMember({
  tabIds: testContext.paneTabs.map((tab) => tab.id),
  groups: [{
    id: "g:hidden",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "s:a",
  }],
}, "g:hidden", "w:two").groups[0]);

(testContext.hiddenThirdPanes = testContext.resolveCenterTabPanes(testContext.hiddenThird, testContext.paneTabs, "w:two"));

testContext.assert.deepEqual(testContext.hiddenThird.memberIds, ["s:a", "w:one"]);

testContext.assert.equal(testContext.hiddenThirdPanes.some((pane) => pane.tabId === "w:two"), false);

(testContext.narrowPanes = testContext.resolveCenterTabPanes(
  undefined,
  testContext.paneTabs,
  "w:two",
));

testContext.assert.deepEqual(testContext.narrowPanes, [{ key: "w:two", kind: "tab", tabId: "w:two" }]);


{
  const syncCalls = [];
  const singletonShowCalls = [];
  const bridge = {
    webTab: {
      syncVisible(items) { syncCalls.push(items); },
      show(id) { singletonShowCalls.push(id); },
    },
  };
  const boundsOne = testContext.measureWebTabBounds({
    getBoundingClientRect: () => ({
      left: 812.6333618164062,
      top: 79.99031066894531,
      width: 518.6390380859375,
      height: 696.86279296875,
    }),
  });
  const boundsTwo = { x: 506, y: 40, width: 600, height: 600 };
  testContext.assert.equal(
    testContext.isWebTabOccluded(boundsOne, [{
      getBoundingClientRect: () => ({ left: 560, top: 720, right: 740, bottom: 840, width: 180, height: 120 }),
    }]),
    false,
    "a composer popover outside the native Page must not hide it",
  );
  testContext.assert.equal(
    testContext.isWebTabOccluded(boundsOne, [{
      getBoundingClientRect: () => ({ left: 780, top: 200, right: 900, bottom: 360, width: 120, height: 160 }),
    }]),
    true,
    "an overlay intersecting the native Page must hide it",
  );
  testContext.registerVisibleWebTabBounds(bridge, "w:one", boundsOne);
  testContext.registerVisibleWebTabBounds(bridge, "w:two", boundsTwo);
  await Promise.resolve();
  testContext.assert.equal(syncCalls.length, 1, "one scheduled flush publishes both panes");
  testContext.assert.deepEqual(syncCalls[0], [
    { id: "w:one", bounds: boundsOne },
    { id: "w:two", bounds: boundsTwo },
  ]);
  testContext.assert.deepEqual(syncCalls[0][0].bounds, {
    x: 812.6333618164062,
    y: 79.99031066894531,
    width: 518.6390380859375,
    height: 696.86279296875,
  }, "renderer bridge must preserve fractional DOMRect bounds until main IPC");
  testContext.removeVisibleWebTabBounds(bridge, "w:one");
  await Promise.resolve();
  testContext.assert.deepEqual(syncCalls.at(-1), [{ id: "w:two", bounds: boundsTwo }]);
  testContext.assert.deepEqual(
    singletonShowCalls,
    [],
    "two singleton show calls are not collection synchronization",
  );
  testContext.removeVisibleWebTabBounds(bridge, "w:two");
  await Promise.resolve();
}


{
  const timing = testContext.createTimingHarness();
  const published = [];
  let width = 1200;
  const scheduler = testContext.createSplitLayoutMeasureScheduler(
    () => published.push(width),
    timing.deps,
  );
  scheduler.schedule();
  width = 864;
  testContext.assert.equal(timing.runTimer(), true);
  testContext.assert.deepEqual(published, [864]);
  testContext.assert.equal(
    timing.frames.size,
    0,
    "timer completion cancels the pending frame",
  );
}


{
  const timing = testContext.createTimingHarness();
  const published = [];
  let width = 1200;
  const scheduler = testContext.createSplitLayoutMeasureScheduler(
    () => published.push(width),
    timing.deps,
  );
  scheduler.schedule();
  const staleFrame = timing.frames.keys().next().value;
  const staleTimer = timing.timers.keys().next().value;
  width = 864;
  scheduler.schedule();
  testContext.assert.equal(timing.frames.has(staleFrame), false);
  testContext.assert.equal(timing.timers.has(staleTimer), false);
  testContext.assert.equal(timing.runTimer(), true);
  timing.runFrame();
  testContext.assert.deepEqual(
    published,
    [864],
    "continuous scheduling only publishes the latest width",
  );
}


{
  const timing = testContext.createTimingHarness();
  const published = [];
  const scheduler = testContext.createSplitLayoutMeasureScheduler(
    () => published.push(1200),
    timing.deps,
  );
  scheduler.schedule();
  testContext.assert.equal(timing.runFrame(), true);
  testContext.assert.equal(timing.runTimer(), false);
  testContext.assert.deepEqual(
    published,
    [1200],
    "frame completion cancels the timer fallback",
  );
}


{
  const timing = testContext.createTimingHarness();
  const published = [];
  const scheduler = testContext.createSplitLayoutMeasureScheduler(
    () => published.push(1200),
    timing.deps,
  );
  scheduler.schedule();
  scheduler.cancel();
  timing.runFrame();
  timing.runTimer();
  testContext.assert.deepEqual(published, [], "cleanup prevents pending measurements");
}


testContext.setWebTabReady("already-ready", true);

testContext.assert.equal(testContext.isWebTabReady("already-ready"), true);

testContext.assert.equal(await testContext.waitForWebTabReady("already-ready", 10), true);


(testContext.waiting = testContext.waitForWebTabReady("becomes-ready", 10));

testContext.setWebTabReady("becomes-ready", true);

testContext.assert.equal(await testContext.waiting, true);


testContext.assert.equal(await testContext.waitForWebTabReady("never-ready", 1), false);


(testContext.clearingWaiter = testContext.waitForWebTabReady("clear-while-waiting", 5));

testContext.setWebTabReady("clear-while-waiting", false);

testContext.assert.equal(await testContext.clearingWaiter, false);


testContext.setWebTabReady("clear-ready", true);

testContext.setWebTabReady("clear-ready", false);

testContext.assert.equal(testContext.isWebTabReady("clear-ready"), false);


testContext.setWebTabReady("w:one", true);

testContext.setWebTabReady("w:two", true);

testContext.useCenterTabs.setState({
  tabs: testContext.paneTabs,
  groups: [{
    id: "g:visible-webs",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["w:one", "w:two"],
    focusedId: "w:two",
  }],
  activeId: "w:two",
});

testContext.assert.equal(testContext.visibleWebTab()?.id, "w:two", "focused visible web wins");

testContext.useCenterTabs.setState({
  groups: [{
    id: "g:visible-webs",
    memberIds: ["s:a", "w:one", "w:two"],
    visibleIds: ["s:a", "w:one"],
    focusedId: "s:a",
  }],
  activeId: "s:a",
});

testContext.assert.equal(
  testContext.visibleWebTab()?.id,
  "w:one",
  "session focus selects the first resolved visible web pane",
);

testContext.assert.notEqual(
  testContext.visibleWebTab()?.id,
  "w:two",
  "a hidden group member must not be selected",
);

testContext.useCenterTabs.setState({ groups: [], activeId: "w:two" });

testContext.assert.equal(testContext.visibleWebTab()?.id, "w:two", "ungrouped active web is selected");


testContext.setDesktopSplitLayoutAvailable(true);

testContext.assert.equal(testContext.isDesktopSplitLayoutAvailable(), true);

testContext.setDesktopSplitLayoutAvailable(false);

testContext.assert.equal(testContext.isDesktopSplitLayoutAvailable(), false);


(testContext.priorSessionId = "s:prior");

(testContext.fallbackWebId = "w:https://fallback.example/");

(testContext.userSelectedId = "s:user-selected");

(testContext.rollbackTabs = [
  { id: testContext.priorSessionId, kind: "session", title: "Prior", sessionId: "prior" },
  {
    id: testContext.fallbackWebId,
    kind: "web",
    title: "fallback.example",
    url: "https://fallback.example/",
  },
  {
    id: testContext.userSelectedId,
    kind: "session",
    title: "User selected",
    sessionId: "user-selected",
  },
]);

testContext.useCenterTabs.setState({ tabs: testContext.rollbackTabs, activeId: testContext.fallbackWebId });

testContext.restorePriorActiveTabAfterFailedWebOpen(testContext.priorSessionId, testContext.fallbackWebId);

testContext.assert.equal(
  testContext.useCenterTabs.getState().activeId,
  testContext.priorSessionId,
  "failed fallback restores the prior tab while the opened web tab is active",
);

testContext.useCenterTabs.setState({ tabs: testContext.rollbackTabs, activeId: testContext.userSelectedId });

testContext.restorePriorActiveTabAfterFailedWebOpen(testContext.priorSessionId, testContext.fallbackWebId);

testContext.assert.equal(
  testContext.useCenterTabs.getState().activeId,
  testContext.userSelectedId,
  "failed fallback must preserve a tab selected by the user during activation",
);

testContext.useCenterTabs.setState({ tabs: testContext.rollbackTabs, activeId: null });

testContext.restorePriorActiveTabAfterFailedWebOpen(testContext.priorSessionId, null);

testContext.assert.equal(
  testContext.useCenterTabs.getState().activeId,
  null,
  "a missing fallback web id must not restore the prior tab",
);


(testContext.webTabPaneSource = await testContext.readFile(
  new URL("../../components/center-tabs/web-tab-pane.tsx", testContext.sourceUrl),
  "utf8",
));

(testContext.splitViewPickerSource = await testContext.readFile(
  new URL("../../components/center-tabs/split-view-picker.tsx", testContext.sourceUrl),
  "utf8",
));

(testContext.appShellSource = await testContext.readFile(
  new URL("../../components/app-shell.tsx", testContext.sourceUrl),
  "utf8",
));
}
