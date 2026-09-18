// preload checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
function checkPreloadWindowIdentity() {
  const sent = [];
  const invoked = [];
  const exposedByName = new Map();
  const argv = [
    "electron",
    "--openprogram-window-id=window-from-main",
    "--openprogram-window-id=ignored-duplicate",
  ];
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return testContext.require(id);
      return {
        contextBridge: {
          exposeInMainWorld(name, value) {
            testContext.assert.equal(exposedByName.has(name), false, "preload bridge names must be unique");
            exposedByName.set(name, value);
          },
        },
        ipcRenderer: {
          send(...args) { sent.push(args); },
          invoke(...args) { invoked.push(args); return Promise.resolve(null); },
          on() {},
          removeListener() {},
        },
      };
    },
  };
  testContext.vm.createContext(preloadSandbox);
  testContext.vm.runInContext(testContext.preloadSource, preloadSandbox, { filename: "apps/desktop/preload.js" });
  const exposed = exposedByName.get("openprogramDesktop");
  const terminals = exposedByName.get("openprogramTerminals");
  testContext.assert.ok(exposed, "the existing Desktop bridge remains available");
  testContext.assert.ok(terminals, "the shared Terminal bridge is independently named");
  testContext.assert.equal(terminals.windowId, "window-from-main");
  testContext.assert.equal(exposed.windowId, "window-from-main");
  argv[1] = "--openprogram-window-id=changed-after-preload";
  testContext.assert.equal(
    exposed.windowId,
    "window-from-main",
    "preload must parse the window id once",
  );
  const items = [{
    id: "pane-a",
    bounds: { x: 1, y: 2, width: 300, height: 200 },
  }];
  exposed.webTab.syncVisible(items);
  testContext.assert.deepEqual(sent.at(-1), ["webtab:sync-visible", items]);
  testContext.assert.deepEqual(invoked, []);
  exposed.webTab.find("pane-a", "needle", { forward: false, findNext: true });
  exposed.webTab.stopFind("pane-a", "clearSelection");
  exposed.webTab.zoom("pane-a", "in");
  exposed.webTab.print("pane-a");
  exposed.webTab.capture("pane-a");
  exposed.webTab.preview("pane-a");
  exposed.webTab.preview("pane-a", true);
  exposed.webTab.setPipZoom("pane-a", 640);
  const marker = {
    x: 12,
    y: 24,
    width: 800,
    height: 600,
    sequence: 4,
    generation: 5,
    resourceId: "page:incarnation-a:1",
  };
  exposed.webTab.showAction("pane-a", marker);
  exposed.webTab.showAction("pane-a", null);
  exposed.webTab.setControlOverlay("pane-a", { resourceId: "page-a", generation: 1 });
  testContext.assert.deepEqual(sent.slice(-4), [
    ["webtab:find", "pane-a", "needle", { forward: false, findNext: true }],
    ["webtab:stop-find", "pane-a", "clearSelection"],
    ["webtab:set-pip-zoom", "pane-a", 640],
    ["webtab:control-overlay", "pane-a", { resourceId: "page-a", generation: 1 }],
  ]);
  testContext.assert.deepEqual(invoked, [
    ["webtab:zoom", "pane-a", "in"],
    ["webtab:print", "pane-a"],
    ["webtab:capture", "pane-a"],
    ["webtab:preview", "pane-a", undefined],
    ["webtab:preview", "pane-a", true],
    ["webtab:show-action", "pane-a", marker],
    ["webtab:show-action", "pane-a", null],
  ]);
  const request = { terminal_id: "terminal:window-from-main:shell", generation: "instance-a", cursor: 0 };
  terminals.resource("read", request);
  terminals.agentTicket("a".repeat(64));
  testContext.assert.deepEqual(invoked.slice(-2), [
    ["terminal:resource", "read", request],
    ["terminal:agent-ticket", "a".repeat(64)],
  ]);
}

function checkPreloadPopupSubscription() {
  const listeners = new Map();
  const exposedByName = new Map();
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv: ["electron"] },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return testContext.require(id);
      return {
        contextBridge: {
          exposeInMainWorld(name, value) { exposedByName.set(name, value); },
        },
        ipcRenderer: {
          send() {},
          invoke() { return Promise.resolve(null); },
          on(channel, listener) { listeners.set(channel, listener); },
          removeListener(channel, listener) {
            if (listeners.get(channel) === listener) listeners.delete(channel);
          },
        },
      };
    },
  };
  testContext.vm.createContext(preloadSandbox);
  testContext.vm.runInContext(testContext.preloadSource, preloadSandbox, { filename: "apps/desktop/preload.js" });
  const exposed = exposedByName.get("openprogramDesktop");

  const received = [];
  const unsubscribe = exposed.webTab.onPopup((popup) => received.push(popup));
  listeners.get("webtab:popup")?.({}, {
    openerId: "opener",
    url: "https://popup.example/",
  });
  testContext.assert.deepEqual(received, [{
    openerId: "opener",
    url: "https://popup.example/",
  }]);
  unsubscribe();
  testContext.assert.equal(listeners.has("webtab:popup"), false);
  const terminalReceived = [];
  const unsubscribeTerminal = exposedByName.get("openprogramTerminals").onResource(value => terminalReceived.push(value));
  const resource = { terminal_id: "terminal:main:shell", generation: "instance-a", status: "running" };
  listeners.get("terminal:resource")?.({}, resource);
  testContext.assert.deepEqual(terminalReceived, [resource]);
  unsubscribeTerminal();
  testContext.assert.equal(listeners.has("terminal:resource"), false);
}

function checkPreloadHumanInputSubscription() {
  const listeners = new Map();
  const exposedByName = new Map();
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv: ["electron"] },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return testContext.require(id);
      return {
        contextBridge: {
          exposeInMainWorld(name, value) { exposedByName.set(name, value); },
        },
        ipcRenderer: {
          send() {},
          invoke() { return Promise.resolve(null); },
          on(channel, listener) { listeners.set(channel, listener); },
          removeListener(channel, listener) {
            if (listeners.get(channel) === listener) listeners.delete(channel);
          },
        },
      };
    },
  };
  testContext.vm.createContext(preloadSandbox);
  testContext.vm.runInContext(testContext.preloadSource, preloadSandbox, { filename: "apps/desktop/preload.js" });
  const exposed = exposedByName.get("openprogramDesktop");

  const received = [];
  const unsubscribe = exposed.webTab.onHumanInput((payload) => received.push(payload));
  const payload = {
    id: "pane-a",
    windowId: "window-from-main",
    sequence: 3,
    kind: "pointer",
  };
  listeners.get("webtab:human-input")?.({}, payload);
  testContext.assert.deepEqual(received, [payload]);
  testContext.assert.equal(
    Object.keys(received[0]).sort().join(","),
    "id,kind,sequence,windowId",
    "human-input payload must not include typed text, key values, coordinates, or URLs",
  );
  unsubscribe();
  testContext.assert.equal(listeners.has("webtab:human-input"), false);
}

function checkPreloadLocalFilePath() {
  const exposedByName = new Map();
  const nativeFile = { name: "Project Folder" };
  const expectedPath = "/Users/test/Project Folder";
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv: ["electron"] },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return testContext.require(id);
      return {
        contextBridge: {
          exposeInMainWorld(name, value) { exposedByName.set(name, value); },
        },
        ipcRenderer: {
          send() {},
          invoke() { return Promise.resolve(null); },
          on() {},
          removeListener() {},
        },
        webUtils: {
          getPathForFile(file) {
            testContext.assert.equal(file, nativeFile, "preload must pass the original File object");
            return expectedPath;
          },
        },
      };
    },
  };
  testContext.vm.createContext(preloadSandbox);
  testContext.vm.runInContext(testContext.preloadSource, preloadSandbox, { filename: "apps/desktop/preload.js" });
  const exposed = exposedByName.get("openprogramDesktop");
  testContext.assert.equal(
    exposed.getPathForFile(nativeFile),
    expectedPath,
    "preload must return Electron's exact native file path",
  );
}

function checkPreloadTabTransfer() {
  const sentSync = [];
  const invoked = [];
  const listeners = new Map();
  const exposedByName = new Map();
  const preloadSandbox = {
    CustomEvent: class CustomEvent {},
    process: { argv: ["electron", "--openprogram-window-id=transfer-window"] },
    window: { dispatchEvent() {} },
    require(id) {
      if (id !== "electron") return testContext.require(id);
      return {
        contextBridge: {
          exposeInMainWorld(name, value) { exposedByName.set(name, value); },
        },
        ipcRenderer: {
          send() {},
          sendSync(...args) { sentSync.push(args); return "token-sync"; },
          invoke(...args) { invoked.push(args); return Promise.resolve(true); },
          on(channel, listener) {
            listeners.set(channel, [...(listeners.get(channel) ?? []), listener]);
          },
          removeListener(channel, listener) {
            listeners.set(
              channel,
              (listeners.get(channel) ?? []).filter((item) => item !== listener),
            );
          },
        },
      };
    },
  };
  testContext.vm.createContext(preloadSandbox);
  testContext.vm.runInContext(testContext.preloadSource, preloadSandbox, { filename: "apps/desktop/preload.js" });
  const exposed = exposedByName.get("openprogramDesktop");

  const transfer = exposed.tabTransfer;
  testContext.assert.ok(transfer, "preload must expose tabTransfer");
  const payload = { tabs: [], source: {}, fileDrafts: [], chats: [] };
  testContext.assert.equal(transfer.prepare(payload), "token-sync");
  testContext.assert.deepEqual(sentSync, [["tab-transfer:prepare", payload]]);

  transfer.inspect("tok");
  transfer.accept("tok", { kind: "strip-end" });
  transfer.reject("tok", "duplicate", "w:dup");
  transfer.status("tok");
  transfer.journalOpened("tok", "destination");
  transfer.journalFinalized("tok", "source", "owner-window");
  transfer.destinationReady("tok", true);
  transfer.sourceRemoved("tok", true, false);
  transfer.destinationUndone("tok", true);
  transfer.cancel("tok");
  transfer.detach("tok");
  transfer.claimPending("transfer-window");
  transfer.pendingTerminal("transfer-window");
  // Objects built inside the preload VM have that realm's prototypes;
  // JSON-normalize before the strict deep comparison.
  testContext.assert.deepEqual(JSON.parse(JSON.stringify(invoked)), [
    ["tab-transfer:inspect", "tok"],
    ["tab-transfer:accept", "tok", { kind: "strip-end" }],
    ["tab-transfer:reject", "tok", "duplicate", "w:dup"],
    ["tab-transfer:status", "tok"],
    ["tab-transfer:journal-opened", "tok", "destination"],
    ["tab-transfer:journal-finalized", "tok", "source", "owner-window"],
    ["tab-transfer:destination-ready", "tok", true],
    ["tab-transfer:source-removed", "tok", { ok: true, sourceEmpty: false }],
    ["tab-transfer:destination-undone", "tok", true],
    ["tab-transfer:cancel", "tok"],
    ["tab-transfer:detach", "tok"],
    ["tab-transfer:claim-pending", "transfer-window"],
    ["tab-transfer:pending-terminal", "transfer-window"],
  ]);

  const channelBySubscription = {
    onRemoveSource: "tab-transfer:remove-source",
    onUndoDestination: "tab-transfer:undo-destination",
    onCommitted: "tab-transfer:committed",
    onRejected: "tab-transfer:rejected",
    onRolledBack: "tab-transfer:rolled-back",
    onFinalizeOrphaned: "tab-transfer:finalize-orphaned",
  };
  for (const [method, channel] of Object.entries(channelBySubscription)) {
    const received = [];
    const cleanup = transfer[method]((detail) => received.push(detail));
    const registered = listeners.get(channel) ?? [];
    testContext.assert.equal(registered.length, 1, `${method} must subscribe ${channel}`);
    registered[0](null, { token: "tok", channel });
    testContext.assert.deepEqual(received, [{ token: "tok", channel }]);
    cleanup();
    testContext.assert.deepEqual(
      listeners.get(channel),
      [],
      `${method} cleanup must remove its listener`,
    );
  }
}
return { checkPreloadWindowIdentity, checkPreloadPopupSubscription, checkPreloadHumanInputSubscription, checkPreloadLocalFilePath, checkPreloadTabTransfer };
};
