// pip ownership: original sequential assertions and shared fixtures.
export async function run(testContext) {

testContext.useWebTabPip.getState().show(testContext.pipOnlyId, "s:chat");

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOnlyId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), "s:chat");

testContext.registerVisibleWebTabBounds(
  { webTab: { syncVisible() {} } },
  testContext.pipOnlyId,
  { x: 40, y: 40, width: 360, height: 188 },
);

testContext.setWebTabReady(testContext.pipOnlyId, true);

testContext.assert.equal(testContext.visibleWebTab()?.id, testContext.pipOnlyId);

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()),
  true,
);

testContext.useCenterTabs.setState((state) => ({
  tabs: [
    ...state.tabs,
    { id: "s:pip-other", kind: "session", title: "Other", sessionId: "pip-other" },
  ],
}));

testContext.useCenterTabs.getState().setActive("s:pip-other");

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()),
  false,
  "PiP must not follow a different chat",
);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOnlyId, "chat switch keeps the owner preview restorable");

testContext.assert.equal(
  testContext.visibleWebTab(),
  null,
  "a hidden owner-scoped PiP must leave the synchronous agent surface inventory",
);

testContext.useCenterTabs.getState().setActive("s:chat");

testContext.useCenterTabs.setState((state) => ({
  tabs: state.tabs.filter((tab) => tab.id !== "s:pip-other"),
}));

testContext.assert.equal(testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()), true);

testContext.assert.equal(testContext.visibleWebTab()?.id, testContext.pipOnlyId);

testContext.useCenterTabs.getState().setActive(testContext.pipOnlyId);

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, testContext.pipOnlyId);

testContext.assert.equal(testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()), false);

testContext.assert.equal(testContext.peekLiveWebTabPipId(), null);

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOnlyId), true);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOnlyId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), "s:chat");

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:chat");

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.find((tab) => tab.id === testContext.pipOnlyId)?.url,
  "https://pip.example/",
);

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, null);

testContext.assert.equal(testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()), true);

testContext.useCenterTabs.getState().setSplitWebTab(testContext.pipOnlyId);

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, testContext.pipOnlyId);

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()),
  true,
  "owner chat keeps the preview when the same Page is also visible in split",
);

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOnlyId), true);

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, null);

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:chat");

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOnlyId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), "s:chat");

testContext.assert.equal(testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()), true);

testContext.useWebTabPip.getState().hide();

testContext.useCenterTabs.setState((state) => ({
  tabs: [
    { id: "s:first", kind: "session", title: "First", sessionId: "first" },
    ...state.tabs,
  ],
  activeId: testContext.pipOnlyId,
}));

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOnlyId), true);

testContext.assert.equal(
  testContext.useCenterTabs.getState().activeId,
  "s:chat",
  "collapse must restore the remembered owner instead of the first session tab",
);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), "s:chat");

testContext.useCenterTabs.getState().setActive("s:first");

testContext.assert.equal(testContext.pipCoversCenter(testContext.pipOnlyId, "s:chat", testContext.useCenterTabs.getState()), false);

testContext.useCenterTabs.getState().setActive("s:chat");

testContext.useWebTabPip.getState().hide();

testContext.useCenterTabs.setState((state) => ({
  tabs: state.tabs.filter((tab) => tab.id !== "s:first"),
}));

testContext.removeVisibleWebTabBounds({ webTab: { syncVisible() {} } }, testContext.pipOnlyId);

testContext.setWebTabReady(testContext.pipOnlyId, false);

testContext.assert.equal(testContext.peekWebTabPipId(), null);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), null);

testContext.assert.equal(testContext.peekWebTabPipBackgroundId(), testContext.pipOnlyId);

testContext.assert.equal(testContext.peekWebTabPipBackgroundOwnerId(), "s:chat");

testContext.assert.deepEqual(
  testContext.clampPipRect(
    { x: -20, y: -10, width: 100, height: 80 },
    { x: 0, y: 0, width: 400, height: 300 },
  ),
  { x: 0, y: 0, width: 260, height: 160 },
);

testContext.assert.deepEqual(
  testContext.clampPipRect(
    { x: 500, y: 400, width: 360, height: 220 },
    { x: 10, y: 20, width: 400, height: 300 },
  ),
  { x: 50, y: 100, width: 360, height: 220 },
);


(testContext.pipOwnerA = "s:pip-a");

(testContext.pipOwnerB = "s:pip-b");

(testContext.pipOwnedId = "w:https://pip-owned.example/");

testContext.useCenterTabs.setState({
  tabs: [
    { id: testContext.pipOwnerA, kind: "session", title: "Alpha", sessionId: "pip-a" },
    { id: testContext.pipOwnerB, kind: "session", title: "Beta", sessionId: "pip-b" },
    { id: "f:pip-files", kind: "file", title: "notes", projectId: "p", path: "notes.md" },
    { id: testContext.pipOwnedId, kind: "web", title: "Owned", url: "https://pip-owned.example/" },
  ],
  activeId: testContext.pipOwnerA,
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.useWebTabPip.getState().show(testContext.pipOwnedId, testContext.pipOwnerA);

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()),
  true,
);

testContext.assert.equal(testContext.pipBoundTabId(), testContext.pipOwnedId);

testContext.useCenterTabs.getState().setActive(testContext.pipOwnerB);

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()),
  false,
);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerA);

testContext.assert.equal(testContext.peekLiveWebTabPipId(), null);

testContext.assert.equal(testContext.pipBoundTabId(), testContext.pipOwnedId);

testContext.useCenterTabs.getState().setActive(testContext.pipOwnerA);

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()),
  true,
);

testContext.useCenterTabs.getState().setActive("f:pip-files");

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()),
  false,
);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerA);

testContext.useCenterTabs.getState().setActive(testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerA);

testContext.assert.equal(testContext.pipBoundTabId(), testContext.pipOwnedId);

testContext.assert.equal(
  testContext.pipCoversCenter(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()),
  false,
);

testContext.assert.equal(
  testContext.pipHostMode(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()),
  null,
  "opening the Page hides the chat preview; the native Page keeps the full content area",
);

testContext.assert.equal(testContext.peekLiveWebTabPipId(), null);

testContext.useCenterTabs.getState().setActive(testContext.pipOwnerA);

testContext.assert.equal(testContext.pipHostMode(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()), "chat");

testContext.useCenterTabs.getState().setActive(testContext.pipOwnedId);

testContext.assert.equal(testContext.pipHostMode(testContext.pipOwnedId, testContext.pipOwnerA, testContext.useCenterTabs.getState()), null);

testContext.assert.deepEqual(
  testContext.pipPresentationSize({ x: 40, y: 90, width: 400, height: 250 }, false),
  { width: 400, height: 250 },
);

testContext.assert.deepEqual(
  testContext.pipPresentationSize({ x: 40, y: 90, width: 400, height: 250 }, true),
  { width: testContext.PIP_EXPANDED_WIDTH, height: testContext.PIP_EXPANDED_HEIGHT },
  "expand uses the expanded presentation size even after a prior drag/resize",
);

testContext.assert.deepEqual(
  testContext.pipPresentationSize({ x: 40, y: 90, width: 400, height: 250 }, true, { width: 500, height: 300 }),
  { width: 500, height: 300 },
);

testContext.assert.deepEqual(
  testContext.pipChatRect({ x: 40, y: 90, width: 400, height: 250 }, false, { x: 0, y: 0, width: 900, height: 700 }),
  { x: 40, y: 90, width: 400, height: 250 },
);

testContext.assert.equal(testContext.PIP_DEFAULT_WIDTH, 300);

testContext.assert.equal(testContext.PIP_DEFAULT_HEIGHT, 198.75);

testContext.assert.equal(testContext.PIP_MIN_WIDTH, 240);

testContext.assert.equal(testContext.PIP_MIN_HEIGHT, 160);


globalThis.window.openprogramDesktop = { isDesktop: true, windowId: "main" };

testContext.useCenterTabs.getState().setActive(testContext.pipOwnerA);

testContext.registerVisibleWebTabBounds(
  { webTab: { syncVisible() {} } },
  testContext.pipOwnedId,
  { x: 40, y: 40, width: 360, height: 188 },
);

testContext.setWebTabReady(testContext.pipOwnedId, true);

testContext.assert.equal(testContext.visibleWebTab()?.id, testContext.pipOwnedId);

testContext.assert.equal(testContext.peekLiveWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.surfaceRefForChat("pip-a", true)?.tab_id, testContext.pipOwnedId);

testContext.useCenterTabs.getState().setActive(testContext.pipOwnerB);

testContext.assert.equal(
  testContext.visibleWebTab()?.id,
  undefined,
  "a parked PiP must not enter the agent-visible web set",
);

testContext.assert.equal(testContext.peekLiveWebTabPipId(), null);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerA);

testContext.assert.equal(
  testContext.surfaceRefForChat("pip-b", true),
  null,
  "a non-owner session must not bind the floating page as its turn surface",
);

delete globalThis.window.openprogramDesktop;

testContext.removeVisibleWebTabBounds({ webTab: { syncVisible() {} } }, testContext.pipOwnedId);

testContext.setWebTabReady(testContext.pipOwnedId, false);


testContext.useCenterTabs.getState().setActive(testContext.pipOwnerA);

testContext.useCenterTabs.getState().closeTab(testContext.pipOwnerA);

testContext.assert.equal(testContext.useWebTabPip.getState().tabId, null);

testContext.assert.equal(testContext.useWebTabPip.getState().ownerTabId, null);

testContext.assert.equal(testContext.useWebTabPip.getState().backgroundTabId, null);

testContext.assert.equal(testContext.useWebTabPip.getState().backgroundOwnerTabId, null);

testContext.assert.ok(
  testContext.useCenterTabs.getState().tabs.some((tab) => tab.id === testContext.pipOwnedId),
  "ending the float must keep the web leaf",
);


testContext.useCenterTabs.setState({
  tabs: [
    { id: testContext.pipOwnerA, kind: "session", title: "Alpha", sessionId: "pip-a" },
    { id: testContext.pipOwnedId, kind: "web", title: "Owned", url: "https://pip-owned.example/" },
  ],
  activeId: testContext.pipOwnerA,
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.useWebTabPip.getState().show(testContext.pipOwnedId, testContext.pipOwnerA);

testContext.assert.deepEqual(
  testContext.closeAgentWebTabResult(testContext.pipOwnedId, testContext.useCenterTabs.getState().tabs, testContext.useCenterTabs.getState().groups),
  { ok: true },
);

testContext.useCenterTabs.getState().closeTab(testContext.pipOwnedId);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.some((tab) => tab.id === testContext.pipOwnedId),
  false,
);

testContext.assert.equal(testContext.useWebTabPip.getState().tabId, null);

testContext.assert.equal(testContext.useWebTabPip.getState().ownerTabId, null);

testContext.assert.equal(testContext.useWebTabPip.getState().backgroundTabId, null);

testContext.assert.equal(testContext.useWebTabPip.getState().backgroundOwnerTabId, null);

testContext.assert.deepEqual(
  testContext.closeAgentWebTabResult("w:missing", testContext.useCenterTabs.getState().tabs, testContext.useCenterTabs.getState().groups),
  { ok: false, reason: "tab_not_found" },
);

testContext.assert.deepEqual(
  testContext.closeAgentWebTabResult(testContext.pipOwnedId, [
    { id: testContext.pipOwnerA, kind: "session" },
    { id: testContext.pipOwnedId, kind: "web" },
  ], [{ memberIds: [testContext.pipOwnerA, testContext.pipOwnedId] }]),
  { ok: false, reason: "tab_in_user_layout" },
);


testContext.useCenterTabs.setState({
  tabs: [
    { id: testContext.pipOwnerA, kind: "session", title: "Alpha", sessionId: "pip-a" },
    { id: testContext.pipOwnerB, kind: "session", title: "Beta", sessionId: "pip-b" },
    { id: testContext.pipOwnedId, kind: "web", title: "Owned", url: "https://pip-owned.example/" },
  ],
  activeId: testContext.pipOwnedId,
  groups: [{
    id: "g:pip-owned",
    memberIds: [testContext.pipOwnerB, testContext.pipOwnedId],
    visibleIds: [testContext.pipOwnerB, testContext.pipOwnedId],
    focusedId: testContext.pipOwnedId,
  }],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.useWebTabPip.getState().end();

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOwnedId), true);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerB);

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, testContext.pipOwnerB);

testContext.useWebTabPip.getState().end();

testContext.useCenterTabs.setState({
  tabs: [{
    id: testContext.pipOwnedId, kind: "web", title: "Owned", url: "https://pip-owned.example/",
  }],
  activeId: testContext.pipOwnedId,
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOwnedId), false);

testContext.assert.equal(testContext.peekWebTabPipId(), null);


// pipCollapseTargetFor: group partner session wins, else remembered
// pairing, else none. Independent pages do not float.
testContext.useCenterTabs.setState({
  tabs: [
    { id: testContext.pipOwnerA, kind: "session", title: "Alpha", sessionId: "pip-a" },
    { id: testContext.pipOwnerB, kind: "session", title: "Beta", sessionId: "pip-b" },
    { id: testContext.pipOwnedId, kind: "web", title: "Owned", url: "https://pip-owned.example/" },
  ],
  activeId: testContext.pipOwnedId,
  groups: [{
    id: "g:pip-owned",
    memberIds: [testContext.pipOwnerB, testContext.pipOwnedId],
    visibleIds: [testContext.pipOwnerB, testContext.pipOwnedId],
    focusedId: testContext.pipOwnedId,
  }],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.assert.equal(testContext.pipCollapseTargetFor(testContext.pipOwnedId), testContext.pipOwnerB);

testContext.useCenterTabs.setState({ groups: [] });

testContext.assert.equal(testContext.pipCollapseTargetFor(testContext.pipOwnedId), null);

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOwnedId), false);

testContext.assert.equal(testContext.pipCollapseTargetFor(testContext.pipOwnerA), null);


testContext.useWebTabPip.getState().show(testContext.pipOwnedId, testContext.pipOwnerA);

(testContext.pipReboundId = "w:https://pip-rebind.example/");

testContext.useCenterTabs.setState({
  tabs: [
    ...testContext.useCenterTabs.getState().tabs,
    { id: testContext.pipReboundId, kind: "web", title: "Rebound", url: "https://pip-rebind.example/" },
  ],
});

testContext.useWebTabPip.getState().show(testContext.pipReboundId, testContext.pipOwnerB);

testContext.assert.equal(testContext.pipPairedOwnerFor(testContext.pipOwnedId), testContext.pipOwnerA);

testContext.assert.equal(testContext.pipCollapseTargetFor(testContext.pipOwnedId), testContext.pipOwnerA);


testContext.useWebTabPip.getState().end();

testContext.useCenterTabs.setState({ activeId: testContext.pipOwnerA });

testContext.assert.equal(testContext.revealAgentWebTab(testContext.pipOwnedId), true);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipOwnedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerA);

testContext.useWebTabPip.getState().end();

testContext.useCenterTabs.setState({ activeId: testContext.pipOwnerB });

testContext.assert.equal(testContext.revealAgentWebTab(testContext.pipOwnedId), false);

testContext.useCenterTabs.setState({ activeId: testContext.pipOwnerA });

testContext.assert.equal(testContext.revealAgentWebTab(testContext.pipReboundId), false);


(testContext.pipUnpairedId = "w:https://pip-unpaired.example/");

testContext.useCenterTabs.setState({
  tabs: [
    ...testContext.useCenterTabs.getState().tabs,
    { id: testContext.pipUnpairedId, kind: "web", title: "Unpaired", url: "https://pip-unpaired.example/" },
  ],
  activeId: testContext.pipOwnerA,
});

testContext.assert.equal(testContext.pipPairedOwnerFor(testContext.pipUnpairedId), null);

testContext.assert.equal(testContext.revealAgentWebTab(testContext.pipUnpairedId), true);

testContext.assert.equal(testContext.pipPairedOwnerFor(testContext.pipUnpairedId), testContext.pipOwnerA);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.pipUnpairedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerA);


testContext.useWebTabPip.getState().end();

(testContext.splitPairUrl = "https://pip-split-pair.example/");

(testContext.splitPairId = testContext.useCenterTabs.getState().openWebTabInSplit(testContext.splitPairUrl));

testContext.registerPipPair(testContext.splitPairId, testContext.pipOwnerA);

testContext.useCenterTabs.setState({ groups: [], splitWebTabId: null });

testContext.assert.equal(testContext.pipPairedOwnerFor(testContext.splitPairId), testContext.pipOwnerA);

testContext.assert.equal(testContext.pipCollapseTargetFor(testContext.splitPairId), testContext.pipOwnerA);


// A tab already floating returns to its owner instead of re-binding.
testContext.useWebTabPip.getState().end();

testContext.useWebTabPip.getState().show(testContext.pipOwnedId, testContext.pipOwnerB);

testContext.useCenterTabs.setState({ activeId: testContext.pipOwnerA });

testContext.assert.equal(testContext.collapseWebTabToPip(testContext.pipOwnedId), true);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerB);

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, testContext.pipOwnerB);

testContext.useWebTabPip.getState().end();


(testContext.forkUrl = "https://pip-fork.example/");

(testContext.forkCanonical = "w:https://pip-fork.example/");

testContext.useCenterTabs.setState({
  tabs: [
    { id: testContext.pipOwnerA, kind: "session", title: "Alpha", sessionId: "pip-a" },
    { id: testContext.pipOwnerB, kind: "session", title: "Beta", sessionId: "pip-b" },
    { id: testContext.forkCanonical, kind: "web", title: "Fork", url: testContext.forkUrl },
  ],
  activeId: testContext.pipOwnerA,
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.useWebTabPip.getState().show(testContext.forkCanonical, testContext.pipOwnerA);

testContext.assert.equal(testContext.pipOpenMustFork(testContext.forkUrl, testContext.pipOwnerA), false);

testContext.assert.equal(
  testContext.useCenterTabs.getState().ensureWebTab(testContext.forkUrl),
  testContext.forkCanonical,
);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.filter((tab) => tab.url === testContext.forkUrl).length,
  1,
);

testContext.assert.equal(testContext.pipOpenMustFork(testContext.forkUrl, testContext.pipOwnerB), true);

testContext.useCenterTabs.getState().setActive(testContext.pipOwnerB);

(testContext.forkedId = testContext.pipOpenMustFork(testContext.forkUrl, testContext.pipOwnerB)
  ? testContext.useCenterTabs.getState().ensureExclusiveWebTab(testContext.forkUrl)
  : testContext.useCenterTabs.getState().ensureWebTab(testContext.forkUrl));

testContext.useWebTabPip.getState().show(testContext.forkedId, testContext.pipOwnerB);

testContext.assert.notEqual(testContext.forkedId, testContext.forkCanonical);

testContext.assert.match(testContext.forkedId, /:popup:/);

testContext.assert.equal(testContext.peekWebTabPipId(), testContext.forkedId);

testContext.assert.equal(testContext.peekWebTabPipOwnerId(), testContext.pipOwnerB);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.find((tab) => tab.id === testContext.forkCanonical)?.url,
  testContext.forkUrl,
);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.filter((tab) => tab.url === testContext.forkUrl).length,
  2,
);


testContext.useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id: testContext.pipOnlyId, kind: "web", title: "pip.example", url: "https://pip.example/" },
  ],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

testContext.useWebTabPip.getState().end();

testContext.useWebTabPip.getState().show(testContext.pipOnlyId, "s:chat");

testContext.useWebTabPip.getState().hide();


(testContext.pipSource = await testContext.readFile(
  new URL("../../components/center-tabs/web-tab-pip.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(testContext.pipSource, /onPointerDown=\{\(event\) => onDragPointerDown\("move"/);

testContext.assert.match(testContext.pipSource, /onPointerDown=\{\(event\) => onDragPointerDown\("resize", event, dir\)\}/);

testContext.assert.match(testContext.pipSource, /data-pip-resize=\{dir\}/);

testContext.assert.match(testContext.pipSource, /PIP_RESIZE_DIRS\.map/);

testContext.assert.match(testContext.pipSource, /setSnapshot\(id, dataUrl\)/);

testContext.assert.match(
  testContext.pipSource,
  /useEffect\(\(\) => \{\s*if \(live\) return;\s*endActiveDragRef\.current\(false\);\s*captureGenRef\.current \+= 1;\s*\}, \[live\]\);/,
  "owner loss during drag must cancel interaction state before PiP can remount",
);

testContext.assert.match(testContext.pipSource, /useEffect\(\(\) => \(\) => \{\s*endActiveDragRef\.current\(false\);\s*\}, \[\]\);/);

testContext.assert.match(
  testContext.pipSource,
  /const finishDrag = \(persist: boolean\) => \{[\s\S]*?dragRef\.current = null;[\s\S]*?cancelAnimationFrame\(rafRef\.current\);[\s\S]*?pendingRectRef\.current = null;[\s\S]*?releasePointerCapture\(drag\.pointerId\)/,
  "drag helper must clear pointer, RAF, and pending rect on discard",
);

testContext.assert.match(testContext.pipSource, /endActiveDragRef\.current = finishDrag/);

testContext.assert.match(testContext.pipSource, /endActiveDragRef\.current\(false\);[\s\S]*?captureGenRef\.current === gen/);

testContext.assert.match(testContext.pipSource, /translate\(\$\{next\.x - drag\.origin\.x\}px/);

testContext.assert.match(testContext.pipSource, /requestAnimationFrame/);

testContext.assert.match(testContext.pipSource, /bridge\?\.webTab\.capture|webTab\.capture/);

testContext.assert.match(testContext.pipSource, /startWebTabCaptureLoop/);

testContext.assert.match(testContext.pipSource, /showShot\(getSnapshot\(tabId\)/);

testContext.assert.match(testContext.pipSource, /className=\{styles\.webPipShot\}/);

testContext.assert.match(testContext.pipSource, /Last frame/);

testContext.assert.doesNotMatch(testContext.pipSource, /<iframe/);

testContext.assert.doesNotMatch(testContext.pipSource, /ensureWebView|registerVisibleWebTabBounds|setPipZoom/);

testContext.assert.doesNotMatch(testContext.pipSource, /webPipParked|parkedShot|Controlled by/);

testContext.assert.doesNotMatch(testContext.webTabPaneSource, /PipBoundMask|Controlled by|webBoundMask|pipBoundTabId/);

testContext.assert.doesNotMatch(testContext.webTabPaneSource, /signalHumanBrowserInput|isHumanYieldEvent/);

testContext.assert.match(
  testContext.webTabPaneSource,
  /ensureWebView\(bridge, tabId, viewUrlRef\.current\);[\s\S]*?bridge\.webTab\.setPipZoom\?\.\(tabId, null\);/,
);

{
  const desktop = testContext.webTabPaneSource.slice(
    testContext.webTabPaneSource.indexOf("function DesktopWebTabPane"),
    testContext.webTabPaneSource.indexOf("function IframeWebTabPane"),
  );
  const iframe = testContext.webTabPaneSource.slice(
    testContext.webTabPaneSource.indexOf("function IframeWebTabPane"),
  );
  testContext.assert.doesNotMatch(desktop, /if \(pipBound\)/);
  testContext.assert.match(desktop, /ensureWebView\(bridge, tabId/);
  testContext.assert.match(iframe, /url\.startsWith\("file:"\)/);
  testContext.assert.doesNotMatch(iframe, /ensureWebView|registerVisibleWebTabBounds|PipBoundMask/);
}

testContext.assert.match(
  await testContext.readFile(
    new URL("../../lib/browser/web-tab-pip-store.ts", testContext.sourceUrl),
    "utf8",
  ),
  /export function collapseWebTabToPip\(tabId: string\): boolean/,
);

testContext.assert.match(
  await testContext.readFile(
    new URL("../../lib/browser/web-tab-pip-store.ts", testContext.sourceUrl),
    "utf8",
  ),
  /export const usePipSnapshots = create/,
);
}
