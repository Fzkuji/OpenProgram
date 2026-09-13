// transfer validation checks, sharing the same native harness and call order.
module.exports = function createChecks(testContext) {
function assertTransferApiRegistered() {
  testContext.assert.equal(
    typeof testContext.hooks.makeTransferCoordinator,
    "function",
    "main must expose an executable transfer coordinator to the harness",
  );
  testContext.assert.equal(typeof testContext.hooks.registerTabTransferIpc, "function");
  testContext.assert.equal(typeof testContext.hooks.validateTransferPayload, "function");
  testContext.assert.strictEqual(testContext.hooks.validateTransferPayload, testContext.productionValidateTransferPayload);
  testContext.assert.equal(typeof testContext.hooks.reparentRecords, "function");
  testContext.assert.equal(typeof testContext.hooks.restoreRecords, "function");
  testContext.assert.ok(testContext.hooks.tabTransfers);

  testContext.hooks.registerTabTransferIpc();
  testContext.assert.equal(typeof testContext.ipcListeners.get("tab-transfer:prepare"), "function");
  for (const channel of [
    "tab-transfer:inspect",
    "tab-transfer:accept",
    "tab-transfer:reject",
    "tab-transfer:status",
    "tab-transfer:journal-opened",
    "tab-transfer:journal-finalized",
    "tab-transfer:destination-ready",
    "tab-transfer:source-removed",
    "tab-transfer:destination-undone",
    "tab-transfer:cancel",
    "tab-transfer:detach",
    "tab-transfer:claim-pending",
    "tab-transfer:pending-terminal",
  ]) {
    testContext.assert.equal(typeof testContext.ipcHandlers.get(channel), "function", `missing ${channel}`);
  }
}

async function checkTransferPreparationValidationAndAuthorization() {
  const sourceWin = testContext.fakeWindow(70);
  const destinationWin = testContext.fakeWindow(71);
  const unrelatedWin = testContext.fakeWindow(72);
  const sourceCtx = testContext.registerContext("transfer-validation-source", sourceWin);
  const destinationCtx = testContext.registerContext("transfer-validation-destination", destinationWin);
  testContext.registerContext("transfer-validation-unrelated", unrelatedWin);
  const native = testContext.controlledRecord("validation-web");
  testContext.attachControlledRecord(
    sourceCtx,
    native,
    { x: 10, y: 20, width: 400, height: 300 },
  );

  const token = testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["validation-web"]));
  testContext.assert.equal(typeof token, "string");
  const inspected = await testContext.ipcHandlers.get("tab-transfer:inspect")(
    testContext.eventFor(destinationWin),
    token,
  );
  testContext.assert.equal(inspected.sourceId, sourceCtx.id);
  testContext.assert.equal(inspected.payload.source.windowId, sourceCtx.id);
  testContext.assert.equal(
    await testContext.ipcHandlers.get("tab-transfer:status")(testContext.eventFor(unrelatedWin), token),
    null,
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("tab-transfer:reject")(
      testContext.eventFor(unrelatedWin),
      token,
      "duplicate",
      "elsewhere",
    ),
    null,
  );
  testContext.assert.equal(
    await testContext.ipcHandlers.get("tab-transfer:cancel")(testContext.eventFor(sourceWin), token),
    true,
  );
  testContext.assert.equal(testContext.hooks.tabTransfers.status(sourceCtx, token), null);
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, token), false);
  testContext.assert.strictEqual(sourceCtx.views.get("validation-web"), native.record);
  testContext.assert.equal(native.closeCallCount(), 0);

  const invalidPayloads = [
    { ...testContext.webTransferPayload(["a"]), tabs: [] },
    testContext.webTransferPayload(["a", "b", "c", "d"]),
    {
      ...testContext.webTransferPayload(["a", "b"]),
      tabs: [
        testContext.webTransferPayload(["a"]).tabs[0],
        testContext.webTransferPayload(["a"]).tabs[0],
      ],
    },
    {
      ...testContext.webTransferPayload(["a"]),
      tabs: [{ id: "a", kind: "unknown", title: "A" }],
    },
    {
      ...testContext.webTransferPayload(["a"]),
      tabs: [{ id: "x".repeat(4097), kind: "web", title: "A", url: "https://a.test" }],
    },
    {
      ...testContext.webTransferPayload(["a"]),
      tabs: [{ id: "a", kind: "web", title: "x".repeat(4097), url: "https://a.test" }],
    },
    {
      ...testContext.webTransferPayload(["a"]),
      tabs: [{ id: "a", kind: "web", title: "A", url: `https://a.test/${"x".repeat(16385)}` }],
    },
    {
      ...testContext.webTransferPayload(["a"]),
      fileDrafts: [{ key: "draft:a", value: "x".repeat(2 * 1024 * 1024 + 1) }],
    },
    {
      ...testContext.webTransferPayload(["a"]),
      chats: [{
        chatKey: "chat:a",
        pendingProjectId: "x".repeat(16 * 1024 + 1),
      }],
    },
    {
      ...testContext.webTransferPayload(["a", "b"]),
      source: {
        windowId: "spoofed",
        kind: "group",
        groupId: "bad-group",
        memberIds: ["a", "missing"],
        visibleIds: ["a"],
        focusedId: "a",
      },
    },
  ];
  for (const payload of invalidPayloads) {
    testContext.assert.equal(testContext.prepareThroughIpc(sourceWin, payload), null);
  }

  const boundaryPayload = testContext.webTransferPayload(["draft-boundary"]);
  boundaryPayload.fileDrafts = [{
    key: "draft:boundary",
    value: "x".repeat(2 * 1024 * 1024),
  }];
  boundaryPayload.chats = [{
    chatKey: "chat:boundary",
    composerDraft: "x".repeat(2 * 1024 * 1024),
  }];
  const boundaryToken = testContext.prepareThroughIpc(sourceWin, boundaryPayload);
  testContext.assert.equal(typeof boundaryToken, "string");
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, boundaryToken), true);

  const manyUnknownFields = Object.fromEntries(
    Array.from({ length: 128 }, (_, index) => [`extra-${index}`, `value-${index}`]),
  );
  const whitelistedPayload = testContext.webTransferPayload(["whitelist-payload"]);
  whitelistedPayload.extraRoot = manyUnknownFields;
  whitelistedPayload.tabs[0] = {
    ...whitelistedPayload.tabs[0],
    draft: true,
    dirty: true,
    extraTab: manyUnknownFields,
  };
  whitelistedPayload.source.extraSource = manyUnknownFields;
  whitelistedPayload.fileDrafts = [{
    key: "project:file.txt",
    extraEntry: manyUnknownFields,
    value: {
      draft: "edited",
      baselineContent: "base",
      baselineMtime: 4,
      extraFileDraft: manyUnknownFields,
    },
  }];
  whitelistedPayload.chats = [{
    chatKey: "local_whitelist",
    composerDraft: "draft",
    wasActive: true,
    composerSettings: {
      thinking: "high",
      tools: true,
      webSearch: false,
      fast: false,
      permission_mode: "ask",
      unattended: false,
      extraSettings: manyUnknownFields,
    },
    draftChannelChoice: {
      channel: "chat",
      account_id: "account",
      extraChoice: manyUnknownFields,
    },
    extraChat: manyUnknownFields,
  }];
  const whitelistedToken = testContext.prepareThroughIpc(sourceWin, whitelistedPayload);
  testContext.assert.equal(typeof whitelistedToken, "string");
  const whitelistedInspect = testContext.hooks.tabTransfers.inspect(
    destinationCtx,
    whitelistedToken,
  );
  testContext.assert.deepEqual(testContext.plain(whitelistedInspect.payload), {
    tabs: [{
      id: "whitelist-payload",
      kind: "web",
      title: "Title whitelist-payload",
      url: "https://example.com/whitelist-payload",
      draft: true,
      dirty: true,
    }],
    source: {
      windowId: sourceCtx.id,
      kind: "tab",
    },
    fileDrafts: [{
      key: "project:file.txt",
      value: {
        draft: "edited",
        baselineContent: "base",
        baselineMtime: 4,
      },
    }],
    chats: [{
      chatKey: "local_whitelist",
      composerDraft: "draft",
      composerSettings: {
        thinking: "high",
        tools: true,
        webSearch: false,
        fast: false,
        permission_mode: "ask",
        unattended: false,
      },
      draftChannelChoice: {
        channel: "chat",
        account_id: "account",
      },
      wasActive: true,
    }],
  });
  testContext.assert.equal(testContext.hooks.tabTransfers.cancel(sourceCtx, whitelistedToken), true);

  const tooManyFileDrafts = testContext.webTransferPayload(["too-many-file-drafts"]);
  tooManyFileDrafts.fileDrafts = Array.from({ length: 4 }, (_, index) => ({
    key: `draft:${index}`,
    value: "value",
  }));
  testContext.assert.equal(testContext.prepareThroughIpc(sourceWin, tooManyFileDrafts), null);

  const excessiveFileDraftBytes = testContext.webTransferPayload(["file-draft-total"]);
  excessiveFileDraftBytes.fileDrafts = Array.from({ length: 3 }, (_, index) => ({
    key: `draft:${index}`,
    value: "x".repeat(2 * 1024 * 1024),
  }));
  testContext.assert.equal(testContext.prepareThroughIpc(sourceWin, excessiveFileDraftBytes), null);

  const excessiveRawPayload = testContext.webTransferPayload(["raw-payload-limit"]);
  excessiveRawPayload.extraRoot = "x".repeat(20 * 1024 * 1024 + 1);
  testContext.assert.equal(testContext.prepareThroughIpc(sourceWin, excessiveRawPayload), null);

  const foreign = testContext.controlledRecord("foreign-native");
  foreign.record.ownerId = destinationCtx.id;
  sourceCtx.views.set("foreign-native", foreign.record);
  testContext.assert.equal(
    testContext.prepareThroughIpc(sourceWin, testContext.webTransferPayload(["foreign-native"])),
    null,
  );
  sourceCtx.views.delete("foreign-native");
}
return { assertTransferApiRegistered, checkTransferPreparationValidationAndAuthorization };
};
