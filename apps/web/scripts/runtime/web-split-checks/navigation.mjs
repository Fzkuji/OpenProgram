// navigation: original sequential assertions and shared fixtures.
export async function run(testContext) {

testContext.assert.match(
  await testContext.readFile(
    new URL("../../lib/browser/web-tab-pip-store.ts", testContext.sourceUrl),
    "utf8",
  ),
  /export function setSnapshot\(tabId: string, dataUrl: string\)/,
);

testContext.assert.match(
  await testContext.readFile(
    new URL("../../lib/browser/web-tab-pip-store.ts", testContext.sourceUrl),
    "utf8",
  ),
  /export function pipOpenMustFork\(/,
);

testContext.assert.match(
  await testContext.readFile(
    new URL("../../components/center-tabs/browser-controls.tsx", testContext.sourceUrl),
    "utf8",
  ),
  /case "collapse-to-pip":/,
);

(testContext.pipCss = await testContext.readFile(
  new URL("../../components/center-tabs/center-tabs.module.css", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(testContext.pipCss, /\.webPipResize/);

testContext.assert.match(testContext.pipCss, /z-index: 6/);

testContext.assert.match(testContext.pipCss, /z-index: 7/);

testContext.assert.doesNotMatch(testContext.pipCss, /linear-gradient\(135deg/);

testContext.assert.match(testContext.pipCss, /\.webPipDragging/);

testContext.assert.match(testContext.pipCss, /\.webPipShot/);

testContext.assert.match(testContext.pipCss, /object-fit:\s*contain/);

testContext.assert.match(testContext.pipCss, /\.webPip\[data-state="active"\]/);

testContext.assert.match(testContext.pipCss, /\.webPane\[data-state="yielding"\]/);

testContext.assert.doesNotMatch(testContext.pipCss, /webPipParked/);

(testContext.previewChipSource = await testContext.readFile(
  new URL("../../components/chat/composer/environment-row/chips/web-preview-chip.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(testContext.previewChipSource, /Show page preview/);

testContext.assert.match(testContext.previewChipSource, /backgroundTabId/);

testContext.assert.match(testContext.previewChipSource, /backgroundOwnerTabId/);

testContext.assert.match(testContext.previewChipSource, /activeId === backgroundOwnerTabId/);

testContext.assert.match(testContext.previewChipSource, /show\(restoreId, backgroundOwnerTabId\)/);

testContext.assert.match(
  await testContext.readFile(
    new URL("../../components/chat/composer/environment-row/environment-row.tsx", testContext.sourceUrl),
    "utf8",
  ),
  /<WebPreviewChip \/>/,
);

testContext.assert.match(
  await testContext.readFile(
    new URL("../../lib/tabs/center-tabs-store.ts", testContext.sourceUrl),
    "utf8",
  ),
  /if \(active\?\.kind === "session" && group && !group\.memberIds.includes\(tabId\)\)/,
);


(testContext.id = testContext.useCenterTabs.getState().openWebTabInSplit("https://example.com/"));

testContext.assert.equal(testContext.id, "w:https://example.com/");

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:chat");

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, testContext.id);

testContext.assert.deepEqual(testContext.useCenterTabs.getState().groups[0].memberIds, ["s:chat", testContext.id]);

testContext.assert.equal(JSON.parse(testContext.values.get("centerTabs")).splitWebTabId, testContext.id);

testContext.assert.equal(JSON.parse(testContext.values.get("centerTabs")).splitRatio, 0.45);


// A split group is one top-level tab. Activating another top-level tab must
// hide the whole split without moving its web member into the new tab.
testContext.useCenterTabs.setState((state) => ({
  tabs: [
    ...state.tabs,
    { id: "s:other", kind: "session", title: "Other", sessionId: "other" },
  ],
}));

(testContext.splitMembersBeforeSwitch = testContext.useCenterTabs.getState().groups[0].memberIds);

(testContext.tabOrderBeforeSwitch = testContext.useCenterTabs.getState().tabs.map((tab) => tab.id));

testContext.useCenterTabs.getState().setActive("s:other");

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:other");

testContext.assert.deepEqual(
  testContext.useCenterTabs.getState().groups[0].memberIds,
  testContext.splitMembersBeforeSwitch,
  "switching top-level tabs must not re-parent the split web view",
);

testContext.assert.deepEqual(
  testContext.useCenterTabs.getState().tabs.map((tab) => tab.id),
  testContext.tabOrderBeforeSwitch,
  "switching top-level tabs must not reorder split members",
);

testContext.assert.equal(
  testContext.useCenterTabs.getState().groups.some((group) =>
    group.memberIds.includes("s:other") && group.memberIds.includes(testContext.id)),
  false,
  "the browser must remain owned by its original composite tab",
);


// Explicitly opening a URL already owned by another composite selects that
// whole entry. It must not claim success while leaving its native view hidden.
(testContext.ownedId = testContext.useCenterTabs.getState().openWebTabInSplit(
  "https://example.com/",
));

testContext.assert.equal(testContext.ownedId, testContext.id);

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:chat");

testContext.assert.deepEqual(testContext.useCenterTabs.getState().groups[0].memberIds, ["s:chat", testContext.id]);


// Repeated agent opens reuse the browser pane owned by the active composite.
// A two-view composite must not append an invisible third member or orphan tab.
testContext.useCenterTabs.getState().setActive("s:chat");

(testContext.tabCountBeforeRepeatedOpen = testContext.useCenterTabs.getState().tabs.length);

(testContext.repeatedId = testContext.useCenterTabs.getState().openWebTabInSplit(
  "https://second.example/",
));

testContext.assert.equal(testContext.repeatedId, testContext.id, "the existing composite browser owns the new navigation");

testContext.assert.equal(testContext.useCenterTabs.getState().tabs.length, testContext.tabCountBeforeRepeatedOpen);

testContext.assert.deepEqual(testContext.useCenterTabs.getState().groups[0].memberIds, ["s:chat", testContext.id]);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.find((tab) => tab.id === testContext.id)?.url,
  "https://second.example/",
);

testContext.useCenterTabs.setState({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id: testContext.id, kind: "web", title: "other.example", url: "https://other.example/" },
  ],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: testContext.id,
  splitRatio: 0.45,
});

testContext.useCenterTabs.getState().openWebTabInSplit("https://example.com/");

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:chat");

testContext.assert.deepEqual(testContext.useCenterTabs.getState().tabs.find((tab) => tab.id === testContext.id), {
  id: testContext.id,
  kind: "web",
  title: "example.com",
  url: "https://example.com/",
});

testContext.useCenterTabs.getState().setSplitRatio(5);

testContext.assert.equal(testContext.useCenterTabs.getState().splitRatio, 0.70);

testContext.assert.equal(JSON.parse(testContext.values.get("centerTabs")).splitWebTabId, testContext.id);

testContext.assert.equal(JSON.parse(testContext.values.get("centerTabs")).splitRatio, 0.70);

testContext.useCenterTabs.getState().setSplitWebTab(null);

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, null);

testContext.useCenterTabs.getState().setSplitWebTab(testContext.id);

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, testContext.id);

testContext.useCenterTabs.getState().closeTab(testContext.id);

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, null);

testContext.assert.equal(JSON.parse(testContext.values.get("centerTabs")).splitWebTabId, null);

testContext.assert.equal(JSON.parse(testContext.values.get("centerTabs")).splitRatio, 0.70);


// Browser Home replaces the current web tab with the Browser-only home page.
// The position and compound-tab membership stay intact, while
// the native web-view id disappears so the desktop bridge can destroy it.
(testContext.homeWebId = "w:https://home-target.test/");

testContext.useCenterTabs.setState({
  tabs: [
    { id: "s:home-chat", kind: "session", title: "Chat", sessionId: "home-chat" },
    { id: testContext.homeWebId, kind: "web", title: "Target", url: "https://home-target.test/" },
    { id: "f:after", kind: "file", title: "after.txt", projectId: "p", path: "after.txt" },
  ],
  activeId: testContext.homeWebId,
  groups: [{
    id: "g:home",
    memberIds: ["s:home-chat", testContext.homeWebId],
    visibleIds: ["s:home-chat", testContext.homeWebId],
    focusedId: testContext.homeWebId,
  }],
  splitWebTabId: testContext.homeWebId,
  splitRatio: 0.45,
});

testContext.useCenterTabs.getState().replaceWebTabWithNewTabPage(testContext.homeWebId);

(testContext.homeState = testContext.useCenterTabs.getState());

(testContext.homeTab = testContext.homeState.tabs[1]);

testContext.assert.equal(testContext.homeTab.kind, "builtin", "Home must show the Browser home page");

testContext.assert.equal(testContext.homeTab.page, "browser");

testContext.assert.match(testContext.homeTab.id, /^browser:/, "Home must allocate a pane-local Browser home id");

testContext.assert.equal(testContext.homeState.activeId, testContext.homeTab.id, "the replacement remains active");

testContext.assert.equal(testContext.homeState.tabs[2].id, "f:after", "Home must preserve tab order");

testContext.assert.deepEqual(testContext.homeState.groups[0].memberIds, ["s:home-chat", testContext.homeTab.id]);

testContext.assert.deepEqual(testContext.homeState.groups[0].visibleIds, ["s:home-chat", testContext.homeTab.id]);

testContext.assert.equal(testContext.homeState.groups[0].focusedId, testContext.homeTab.id);

testContext.assert.equal(testContext.homeState.splitWebTabId, null, "the replaced web view must leave legacy split state");

testContext.assert.equal(testContext.homeState.tabs.some((tab) => tab.id === testContext.homeWebId), false);


// A Browser home in another tab must not steal the current pane or create a
// duplicate deterministic id.
testContext.useCenterTabs.setState({
  tabs: [
    { id: "browser:existing", kind: "builtin", title: "", page: "browser" },
    { id: "ntp:browser-choice", kind: "ntp", title: "" },
  ],
  activeId: "ntp:browser-choice",
  groups: [],
  splitWebTabId: null,
});

testContext.useCenterTabs.getState().openBuiltinTab("browser");

(testContext.browserChoiceState = testContext.useCenterTabs.getState());

testContext.assert.equal(testContext.browserChoiceState.tabs.length, 2);

testContext.assert.equal(testContext.browserChoiceState.tabs[0].id, "browser:existing");

testContext.assert.equal(testContext.browserChoiceState.tabs[1].kind, "builtin");

testContext.assert.equal(testContext.browserChoiceState.tabs[1].page, "browser");

testContext.assert.match(testContext.browserChoiceState.tabs[1].id, /^browser:/);

testContext.assert.notEqual(testContext.browserChoiceState.tabs[1].id, "browser:existing");

testContext.assert.equal(testContext.browserChoiceState.activeId, testContext.browserChoiceState.tabs[1].id);


testContext.useCenterTabs.setState({
  tabs: [
    { id: "browser:existing", kind: "builtin", title: "", page: "browser" },
    { id: "w:https://home-second.test/", kind: "web", title: "Second", url: "https://home-second.test/" },
  ],
  activeId: "w:https://home-second.test/",
  groups: [],
  splitWebTabId: null,
});

testContext.useCenterTabs.getState().replaceWebTabWithNewTabPage("w:https://home-second.test/");

(testContext.secondHomeState = testContext.useCenterTabs.getState());

testContext.assert.equal(testContext.secondHomeState.tabs.length, 2);

testContext.assert.equal(testContext.secondHomeState.tabs[0].id, "browser:existing");

testContext.assert.equal(testContext.secondHomeState.tabs[1].page, "browser");

testContext.assert.notEqual(testContext.secondHomeState.tabs[1].id, "browser:existing");

testContext.assert.equal(testContext.secondHomeState.activeId, testContext.secondHomeState.tabs[1].id);


testContext.useCenterTabs.setState(testContext.homeState);

(testContext.beforeWrongKind = JSON.stringify({
  tabs: testContext.homeState.tabs,
  activeId: testContext.homeState.activeId,
  groups: testContext.homeState.groups,
  splitWebTabId: testContext.homeState.splitWebTabId,
  splitRatio: testContext.homeState.splitRatio,
}));

testContext.useCenterTabs.getState().replaceWebTabWithNewTabPage("s:home-chat");

testContext.assert.equal(
  JSON.stringify({
    tabs: testContext.useCenterTabs.getState().tabs,
    activeId: testContext.useCenterTabs.getState().activeId,
    groups: testContext.useCenterTabs.getState().groups,
    splitWebTabId: testContext.useCenterTabs.getState().splitWebTabId,
    splitRatio: testContext.useCenterTabs.getState().splitRatio,
  }),
  testContext.beforeWrongKind,
  "Home must ignore ids that do not belong to a web tab",
);


testContext.values.set("centerTabs", JSON.stringify({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id: testContext.id, kind: "web", title: "example.com", url: "https://example.com/" },
  ],
  activeId: "s:chat",
}));

testContext.values.set("openprogram.webSplit", JSON.stringify({ tabId: testContext.id, ratio: 5 }));

({ useCenterTabs: testContext.restoredSplit } = await import(
  "../../../lib/tabs/center-tabs-store.ts?restore-valid-split",
));

testContext.assert.equal(testContext.restoredSplit.getState().splitWebTabId, testContext.id);

testContext.assert.equal(testContext.restoredSplit.getState().splitRatio, 0.70);

testContext.assert.deepEqual(testContext.restoredSplit.getState().groups[0].memberIds, ["s:chat", testContext.id]);


testContext.values.set("centerTabs", JSON.stringify({
  tabs: [
    { id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" },
    { id: testContext.id, kind: "web", title: "example.com", url: "https://example.com/" },
  ],
  activeId: "s:chat",
}));

testContext.values.set("openprogram.webSplit", JSON.stringify({ tabId: "s:chat", ratio: 0 }));

({ useCenterTabs: testContext.restoredInvalidSplit } = await import(
  "../../../lib/tabs/center-tabs-store.ts?restore-invalid-split",
));

testContext.assert.equal(testContext.restoredInvalidSplit.getState().splitWebTabId, null);

testContext.assert.equal(testContext.restoredInvalidSplit.getState().splitRatio, 0.30);
}
