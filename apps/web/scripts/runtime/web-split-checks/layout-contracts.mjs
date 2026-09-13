// layout contracts: original sequential assertions and shared fixtures.
export async function run(testContext) {

// The strip is split across center-tab-strip.tsx and its submodules;
// readCenterTabStripSource concatenates them in source order.
(testContext.tabStripSource = testContext.readCenterTabStripSource());

(testContext.tabsCssSource = await testContext.readFile(
  new URL("../../components/center-tabs/center-tabs.module.css", testContext.sourceUrl),
  "utf8",
));

(testContext.baseCssSource = await testContext.readFile(
  new URL("../../app/styles/base.css", testContext.sourceUrl),
  "utf8",
));

(testContext.desktopBridgeSource = testContext.readDesktopBridgeSource());


testContext.assert.match(testContext.webTabPaneSource, /\bHouse\b/, "browser toolbar Home icon missing");

testContext.assert.match(
  testContext.webTabPaneSource,
  /replaceWebTabWithNewTabPage\(tabId\)/,
  "browser toolbar must navigate the current web tab to the OpenProgram home page",
);

testContext.assert.match(testContext.webTabPaneSource, /text\("Home",\s*"主页"\)/);

testContext.assert.equal(
  (testContext.webTabPaneSource.match(/<HomeButton tabId=\{tabId\} \/>/g) ?? []).length,
  2,
  "desktop and iframe browser toolbars must both expose Home",
);

testContext.assert.equal(
  (testContext.webTabPaneSource.match(/<CollapseToPipButton tabId=\{tabId\} \/>/g) ?? []).length,
  2,
  "desktop and iframe browser toolbars must both collapse back to PiP",
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /function CollapseToPipButton[\s\S]*?if \(!targetId\) return null/,
  "CollapseToPipButton must hide itself when there is no session target",
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /collapseToPip: collapseTarget\s*\n\s*\? \(\) => \{ collapseWebTabToPip\(tabId\); \}\s*\n\s*: undefined/,
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /Open in browser[\s\S]*?<CollapseToPipButton tabId=\{tabId\} \/>\s*<BrowserMenu/,
  "collapse-to-PiP belongs on the trailing toolbar cluster",
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /ensureWebView\(bridge, tabId, viewUrlRef\.current\);[\s\S]*?bridge\.webTab\.setPipZoom\?\.\(tabId, null\);/,
  "an ordinary desktop WebTab must clear persisted PiP layout zoom on mount",
);


testContext.assert.match(testContext.desktopBridgeSource, /function visibleWebTab\(\)/);

testContext.assert.match(
  testContext.desktopBridgeSource,
  /resolveCenterTabPanes\(group, state\.tabs, state\.activeId\)/,
);

testContext.assert.match(
  testContext.desktopBridgeSource,
  /group\.focusedId/,
);

testContext.assert.match(testContext.desktopBridgeSource, /const visibleWebBounds = new Map/);

testContext.assert.match(testContext.desktopBridgeSource, /targetBridge\.webTab\.syncVisible/);

testContext.assert.match(testContext.desktopBridgeSource, /d\.op === "close"/);

testContext.assert.match(testContext.desktopBridgeSource, /closeAgentWebTabResult\(d\.tab_id, state\.tabs, state\.groups\)/);

testContext.assert.match(testContext.desktopBridgeSource, /if \(closed\.ok\) state\.closeTab\(d\.tab_id!\)/);

testContext.assert.match(testContext.desktopBridgeSource, /state\.openWebTabInSplit\(d\.url\)/);

testContext.assert.match(testContext.desktopBridgeSource, /registerPipPair\(id, active\.id\)/);

testContext.assert.match(testContext.desktopBridgeSource, /state\.ensureWebTab\(d\.url\)/);

testContext.assert.match(testContext.desktopBridgeSource, /pipOpenMustFork\(d\.url, active\.id\)/);

testContext.assert.match(testContext.desktopBridgeSource, /ensureExclusiveWebTab\(d\.url\)/);

testContext.assert.match(
  testContext.desktopBridgeSource,
  /if \(d\.background \|\| d\.session_id\)[\s\S]*?ensureExclusiveWebTab\(d\.url\)[\s\S]*?ensureWebView\(bridge, id, d\.url\)[\s\S]*?bridge\.webTab\.resolve\?\.\(id\)/,
  "a background agent open must create and resolve a hidden exclusive Page without activating it",
);

testContext.assert.match(
  testContext.desktopBridgeSource,
  /d\.op === "preview" && d\.background === true[\s\S]*?selectedMirrorTabById/,
  "selected PiP preview stays a background mirror and does not reveal the native Page",
);

testContext.assert.match(
  testContext.desktopBridgeSource,
  /if \(d\.op === "close"\)[\s\S]*?d\.window_id !== bridge\.windowId[\s\S]*?closeAgentWebTabResult/,
  "an exact background Page close must reject another desktop window",
);

testContext.assert.match(testContext.desktopBridgeSource, /useWebTabPip\.getState\(\)\.show\(id, active\.id\)/);

testContext.assert.match(testContext.desktopBridgeSource, /waitForWebTabReady\(id, 2000\)/);

testContext.assert.match(testContext.desktopBridgeSource, /subscribeWebTabPopups\(bridge\)/);

testContext.assert.match(testContext.desktopBridgeSource, /state\.openPopupWebTab\(popup\.url, popup\.openerId\)/);

testContext.assert.match(
  testContext.desktopBridgeSource,
  /if \(!split && !routeVisible\) \{\s*const routed = showCenterSurface\(\);\s*if \(!routed\)/,
  "split opens and existing /s or /chat fallbacks must preserve their route",
);

testContext.assert.doesNotMatch(
  testContext.desktopBridgeSource,
  /openWebTab\(d\.url\);\s*const routed = showCenterSurface\(\);/,
  "webtab.command must not unconditionally navigate a session route to /chat",
);


testContext.assert.match(testContext.appShellSource, /splitRatio/);

testContext.assert.match(
  testContext.appShellSource,
  /findCenterTabGroup,[\s\S]*?resolveCenterTabPanes/,
  "AppShell must use the shared group and pane resolver",
);

testContext.assert.match(testContext.appShellSource, /<WebTabPip \/>/);

testContext.assert.doesNotMatch(
  testContext.appShellSource,
  /useWebTabPip\.getState\(\)\.hide\(\)/,
  "leaving the chat route must keep the PiP binding",
);

testContext.assert.match(testContext.appShellSource, /const tabs = useCenterTabs\(\(s\) => s\.tabs\);/);

testContext.assert.match(testContext.appShellSource, /const groups = useCenterTabs\(\(s\) => s\.groups\);/);

testContext.assert.match(testContext.appShellSource, /const activeId = useCenterTabs\(\(s\) => s\.activeId\);/);

testContext.assert.match(testContext.appShellSource, /const activeGroup = activeId[\s\S]*?findCenterTabGroup\(groups, activeId\)/);

testContext.assert.match(
  testContext.appShellSource,
  /const splitAvailable = isSplitLayoutAvailable\(centerBodyWidth\);/,
  "split availability must use the measured center-body width",
);

testContext.assert.match(
  testContext.appShellSource,
  /const compoundPanes = resolveCenterTabPanes\(activeGroup, tabs, activeId\);/,
);

testContext.assert.match(
  testContext.appShellSource,
  /const focusedPanes = resolveCenterTabPanes\([\s\S]*?undefined,[\s\S]*?tabs,[\s\S]*?activeGroup\?\.focusedId \?\? activeId/,
  "narrow layout must resolve only the focused member",
);

testContext.assert.match(
  testContext.appShellSource,
  // No isDesktop gate: split is purely a measured-width decision now.
  /const panes = topLevelTabs\(tabs, groups\)\.length === 0\s*\? \[\]\s*: activeGroup && splitAvailable \? compoundPanes : focusedPanes;/,
);

testContext.assert.match(
  testContext.appShellSource,
  /const showDivider = panes\.length === 2;/,
  "divider follows rendered panes, not visible member count",
);

testContext.assert.doesNotMatch(testContext.appShellSource, /visibleTabs\.length === 2/);

testContext.assert.equal(
  testContext.appShellSource.match(/<PageShell page="chat" \/>/g)?.length,
  1,
  "the chat shell must remain a mounted singleton",
);

testContext.assert.match(
  testContext.appShellSource,
  /const sessionPaneIndex = panes\.findIndex\(\(pane\) => pane\.kind === "session"\);/,
);

testContext.assert.match(testContext.appShellSource, /panes\.map\(\(pane, index\) =>/);

testContext.assert.match(testContext.appShellSource, /if \(pane\.kind === "session"\) return null;/);

testContext.assert.match(testContext.appShellSource, /tab\.kind === "web"[\s\S]*?<WebTabPane/);

testContext.assert.match(testContext.appShellSource, /"center-split-primary"/);

testContext.assert.match(testContext.appShellSource, /className="center-split-divider"/);

testContext.assert.match(testContext.appShellSource, /"center-split-secondary"/);

testContext.assert.match(testContext.appShellSource, /setDesktopSplitLayoutAvailable/);

testContext.assert.match(testContext.appShellSource, /clampSplitRatioForWidth/);

testContext.assert.match(testContext.appShellSource, /const effectiveSplitRatio = clampSplitRatioForWidth/);

testContext.assert.doesNotMatch(
  testContext.appShellSource,
  /if \(effectiveSplitRatio !== splitRatio\) setSplitRatio\(effectiveSplitRatio\);/,
  "container constraints must not overwrite the preferred split ratio",
);

(testContext.splitMeasureSource = testContext.appShellSource.slice(
  testContext.appShellSource.indexOf("const centerBodyRef"),
  testContext.appShellSource.indexOf("const splitAvailable"),
));

testContext.assert.match(
  testContext.splitMeasureSource,
  /createSplitLayoutMeasureScheduler/,
  "AppShell must use the behavior-tested measurement scheduler",
);

testContext.assert.match(
  testContext.splitMeasureSource,
  /new ResizeObserver\(measureScheduler\.schedule\)/,
);

testContext.assert.match(
  testContext.splitMeasureSource,
  /window\.addEventListener\("resize", measureScheduler\.schedule\)/,
);

testContext.assert.match(
  testContext.splitMeasureSource,
  /window\.removeEventListener\("resize", measureScheduler\.schedule\)/,
);

testContext.assert.match(testContext.splitMeasureSource, /node\.closest\("\.app"\)/);

testContext.assert.match(
  testContext.splitMeasureSource,
  /layoutRoot\?\.addEventListener\(\s*"transitionend",\s*measureScheduler\.schedule,?\s*\)/,
);

testContext.assert.match(
  testContext.splitMeasureSource,
  /layoutRoot\?\.removeEventListener\(\s*"transitionend",\s*measureScheduler\.schedule,?\s*\)/,
);

testContext.assert.match(testContext.splitMeasureSource, /measureScheduler\.cancel\(\)/);

testContext.assert.doesNotMatch(testContext.splitMeasureSource, /setInterval/);

testContext.assert.match(testContext.appShellSource, /width: `\$\{effectiveSplitRatio \* 100\}%`/);

testContext.assert.match(testContext.appShellSource, /aria-valuenow=\{Math\.round\(effectiveSplitRatio \* 100\)\}/);

testContext.assert.match(testContext.appShellSource, /onPointerDown=/);

testContext.assert.match(testContext.appShellSource, /onPointerMove=/);

testContext.assert.match(testContext.appShellSource, /role="separator"/);

testContext.assert.match(testContext.appShellSource, /aria-orientation="vertical"/);

testContext.assert.match(testContext.appShellSource, /"ArrowLeft"/);

testContext.assert.match(testContext.appShellSource, /"ArrowRight"/);

testContext.assert.match(testContext.appShellSource, /0\.02/);

testContext.assert.match(testContext.appShellSource, /sessionStore\.setCurrentConv\(sid\);/);

testContext.assert.match(testContext.appShellSource, /sessionStore\.setCurrentDraft\(active\.sessionId\);/);


(testContext.activeFocusEffect = testContext.tabStripSource.slice(
  testContext.tabStripSource.indexOf("// Active center-tab focus"),
  testContext.tabStripSource.indexOf("function onTabClick"),
));

testContext.assert.match(
  testContext.tabStripSource,
  /const \[sessionActivationRequest, setSessionActivationRequest\] = useState\(0\);/,
);

testContext.assert.match(testContext.activeFocusEffect, /useEffect\(\(\) =>/);

testContext.assert.match(testContext.activeFocusEffect, /activateSession\(tab\);/);

testContext.assert.match(testContext.activeFocusEffect, /\[activeId, activeSessionId, activeSessionDraft, sessionActivationRequest\]/);

(testContext.onTabClickSource = testContext.tabStripSource.slice(
  testContext.tabStripSource.indexOf("function onTabClick"),
  testContext.tabStripSource.indexOf("function onOpenNewTab"),
));

testContext.assert.match(testContext.onTabClickSource, /tab\.kind === "session" && tab\.id === activeId/);

testContext.assert.match(
  testContext.onTabClickSource,
  /setSessionActivationRequest\(\(request\) => request \+ 1\);/,
);

testContext.assert.doesNotMatch(testContext.onTabClickSource, /activateSession/);

(testContext.finishCloseSource = testContext.tabStripSource.slice(
  testContext.tabStripSource.indexOf("function finishClose"),
  testContext.tabStripSource.indexOf("function labelOf"),
));

testContext.assert.doesNotMatch(testContext.finishCloseSource, /activateSession/);


testContext.assert.doesNotMatch(
  testContext.webTabPaneSource,
  /Open split view|Exit split view|SplitViewPicker|Columns2/,
  "split controls belong to the tab and pane layer, not the browser toolbar",
);

testContext.assert.doesNotMatch(testContext.webTabPaneSource, /setSplitWebTab\(/);

testContext.assert.doesNotMatch(testContext.webTabPaneSource, /openDraftSessionTab\(|newSession\(/);

testContext.assert.match(
  testContext.splitViewPickerSource,
  /const latestState = useCenterTabs\.getState\(\);[\s\S]*?splitCandidates\(\s*latestState\.tabs,\s*latestState\.groups,\s*subjectId,?\s*\)/,
  "a stale picker row must be revalidated against the latest store before grouping",
);

testContext.assert.match(testContext.splitViewPickerSource, /onClose\("escape"\)/);

testContext.assert.match(testContext.splitViewPickerSource, /onClose\("outside"\)/);

testContext.assert.match(testContext.splitViewPickerSource, /onClose\("close-button"\)/);

testContext.assert.match(
  testContext.splitViewPickerSource,
  /querySelector<HTMLButtonElement>\("\[data-split-option\]"\)[\s\S]*?\?\?[\s\S]*?querySelector<HTMLButtonElement>\("\[data-split-close\]"\)/,
  "a picker must focus its first option, falling back to Close only when empty",
);

testContext.assert.match(
  testContext.tabStripSource,
  /if \(reason !== "outside"\)[\s\S]*?returnFocusToMenuInvoker\(subject\)/,
  "outside dismissal must preserve the newly clicked element's focus",
);


testContext.assert.doesNotMatch(testContext.tabStripSource, /splitPinned|data-split-pinned/);

testContext.assert.match(testContext.tabStripSource, /active=\{tab\.id === activeId\}/);

testContext.assert.doesNotMatch(testContext.tabsCssSource, /\[data-split-pinned="true"\]/);

testContext.assert.match(testContext.baseCssSource, /\.center-split-divider\s*\{[^}]*width:\s*6px;/s);

testContext.assert.match(testContext.baseCssSource, /\.center-split-primary\s*\{[^}]*min-width:\s*360px;/s);

testContext.assert.match(
  testContext.baseCssSource,
  /\.center-split-primary\.center-pane-chat\s*\{[^}]*flex:\s*0\s+0\s+auto;/s,
  "a chat pane in the primary split slot must keep the ratio-controlled width",
);

testContext.assert.match(testContext.baseCssSource, /\.center-split-secondary\s*\{[^}]*min-width:\s*480px;/s);

testContext.assert.match(testContext.webTabPaneSource, /setWebTabReady/);

testContext.assert.doesNotMatch(testContext.webTabPaneSource, /bridge\.webTab\.(?:show|hide|setBounds)\(/);

testContext.assert.doesNotMatch(
  testContext.webTabPaneSource,
  /bridge\.webTab\.navigate\(tabId, viewUrlRef\.current\);/,
);

testContext.assert.match(testContext.webTabPaneSource, /const occluded = isWebTabOccluded\(/);

testContext.assert.match(
  testContext.webTabPaneSource,
  /document\.querySelectorAll\([\s\S]*?\[role="dialog"\], \[role="menu"\], \[role="listbox"\], \.branches-merge-modal-backdrop, \[data-native-view-occluder="true"\]/,
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /const bounds = measureWebTabBounds\(el\);/,
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /if \(occluded \|\| bounds\.width <= 0 \|\| bounds\.height <= 0\) \{\s*removeVisibleWebTabBounds\(bridge, tabId\);\s*setWebTabReady\(tabId, false\);/s,
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /registerVisibleWebTabBounds\(bridge, tabId, bounds\);\s*setWebTabReady\(tabId, true\);/s,
);

testContext.assert.match(
  testContext.webTabPaneSource,
  /return \(\) => \{[\s\S]*?removeVisibleWebTabBounds\(bridge, tabId\);/s,
);

testContext.assert.match(testContext.webTabPaneSource, /new MutationObserver\(report\)/);

testContext.assert.match(
  testContext.webTabPaneSource,
  /mo\.observe\(document\.body, \{ subtree: true, childList: true, attributes: true \}\);/,
);

testContext.assert.match(testContext.webTabPaneSource, /mo\.disconnect\(\);/);


(testContext.fileTilesSource = await testContext.readFile(
  new URL("../../components/chat/composer/attach/file-tiles.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(testContext.fileTilesSource, /data-native-view-occluder="true"/);

(testContext.dialogSource = await testContext.readFile(
  new URL("../../components/ui/dialog.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(
  testContext.dialogSource,
  /<DialogPrimitive\.Overlay[\s\S]*?data-native-view-occluder="true"/,
  "the full-window dialog backdrop must participate in native view occlusion",
);

(testContext.permissionMenuSource = await testContext.readFile(
  new URL("../../components/chat/top-bar/permission-menu.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(
  testContext.permissionMenuSource,
  /bypassConfirm[\s\S]*?data-native-view-occluder="true"/,
  "the custom full-window permission confirmation must occlude native views",
);

(testContext.contextBadgeSource = await testContext.readFile(
  new URL("../../components/chat/context-badge.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(
  testContext.contextBadgeSource,
  /透明全屏遮罩[\s\S]*?data-native-view-occluder="true"/,
  "the context panel click-catcher must not sit behind a native view",
);

(testContext.scopedDropOverlaySource = await testContext.readFile(
  new URL("../../components/chat/composer/attach/scoped-drop-overlay.tsx", testContext.sourceUrl),
  "utf8",
));

testContext.assert.match(
  testContext.scopedDropOverlaySource,
  /<div[\s\S]*?data-native-view-occluder="true"[\s\S]*?\.\.\.style/,
  "the measured drop overlay must occlude only Web bodies it intersects",
);


testContext.useCenterTabs.setState({
  tabs: [{ id: "s:chat", kind: "session", title: "Chat", sessionId: "chat" }],
  activeId: "s:chat",
  groups: [],
  splitWebTabId: null,
  splitRatio: 0.45,
});

(testContext.pipOnlyId = testContext.useCenterTabs.getState().ensureWebTab("https://pip.example/"));

testContext.assert.equal(testContext.pipOnlyId, "w:https://pip.example/");

testContext.assert.equal(testContext.useCenterTabs.getState().activeId, "s:chat");

testContext.assert.equal(testContext.useCenterTabs.getState().splitWebTabId, null);

testContext.assert.equal(testContext.useCenterTabs.getState().groups.length, 0);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.find((tab) => tab.id === testContext.pipOnlyId)?.url,
  "https://pip.example/",
);

testContext.assert.equal(
  testContext.useCenterTabs.getState().ensureWebTab("https://pip.example/"),
  testContext.pipOnlyId,
);

testContext.assert.equal(
  testContext.useCenterTabs.getState().tabs.filter((tab) => tab.kind === "web").length,
  1,
);

({
  peekWebTabPipId: testContext.peekWebTabPipId,
  peekWebTabPipOwnerId: testContext.peekWebTabPipOwnerId,
  peekWebTabPipBackgroundId: testContext.peekWebTabPipBackgroundId,
  peekLiveWebTabPipId: testContext.peekLiveWebTabPipId,
  peekWebTabPipBackgroundOwnerId: testContext.peekWebTabPipBackgroundOwnerId,
  pipBoundTabId: testContext.pipBoundTabId,
  pipOpenMustFork: testContext.pipOpenMustFork,
  useWebTabPip: testContext.useWebTabPip,
  clampPipRect: testContext.clampPipRect,
  collapseWebTabToPip: testContext.collapseWebTabToPip,
  pipCollapseTargetFor: testContext.pipCollapseTargetFor,
  pipCoversCenter: testContext.pipCoversCenter,
  pipHostMode: testContext.pipHostMode,
  pipChatRect: testContext.pipChatRect,
  pipPresentationSize: testContext.pipPresentationSize,
  PIP_DEFAULT_HEIGHT: testContext.PIP_DEFAULT_HEIGHT,
  PIP_DEFAULT_WIDTH: testContext.PIP_DEFAULT_WIDTH,
  PIP_EXPANDED_HEIGHT: testContext.PIP_EXPANDED_HEIGHT,
  PIP_EXPANDED_WIDTH: testContext.PIP_EXPANDED_WIDTH,
  PIP_MIN_HEIGHT: testContext.PIP_MIN_HEIGHT,
  PIP_MIN_WIDTH: testContext.PIP_MIN_WIDTH,
  pipPairedOwnerFor: testContext.pipPairedOwnerFor,
  registerPipPair: testContext.registerPipPair,
  revealAgentWebTab: testContext.revealAgentWebTab,
} = await import("../../../lib/browser/web-tab-pip-store.ts"));
}
