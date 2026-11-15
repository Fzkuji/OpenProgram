/**
 * Several unsent chats at once: each New chat is its own provisional draft
 * (local_* key) with its own text, project intent, channel choice and
 * attachments, and acknowledgements route by their own session.
 *
 * Drives the real center-tabs store, session store, send-chat-message and
 * chat handlers over a fake socket; the attachment and paste hooks are
 * rendered with React. IndexedDB and clipboard image decoding are stubs.
 * The tests share one store and run in order.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/components/ui/confirm-dialog": "export const confirmAction = async () => true;"
      + " export const ConfirmDialog = () => null; export const ConfirmDialogHost = () => null;",
    // The attachment hook's IndexedDB: nothing persisted.
    "./attach-idb": [
      "export const markAttachmentOwnerClosed = () => {};",
      "export const attachmentOwnerIsClosed = () => false;",
      "export const onAttachmentOwnerClosed = () => () => {};",
      "export const loadAttachments = async () => ({ images: [], docs: [] });",
      "export const saveAttachments = async () => {};",
    ].join("\n"),
    // Clipboard image decoding, resolved by the test (see the paste test).
    "../attach/image/image-attach": "export const collectImagesFromTransfer = () => new Promise((resolve) => { globalThis.__pasteDecode = resolve; });",
  },
});
installDom({ pathname: "/chat" });
globalThis.WebSocket = { OPEN: 1 };

const tabsModule = await import("../../../lib/tabs/center-tabs-store.ts");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { sendChatMessage } = await import("../../../components/chat/composer/submit/send-chat-message.ts");
const { runtimeState, setSocket } = await import("../../../lib/runtime-bridge/state.ts");
const pendingUserText = await import("../../../lib/chat/send/pending-user-text.ts");
const responseRouting = await import("../../../lib/runtime-bridge/session/chat-response-routing.ts");
const channelDrafts = await import("../../../lib/runtime-bridge/actions/draft-channel-choice.ts");
const chatHandlers = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");
const attachmentCache = await import("../../../components/chat/composer/attach/attachment-session-cache.ts");
const { useCenterTabs, sessionTabId, sessionAckIsActive, webTabId, builtinTabId, insertTransferredTabs } = tabsModule;

const sent = [];
setSocket({ readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) });
runtimeState.currentSessionId = null;

const store = () => useSessionStore.getState();
// The focused chat's draft is composerDrafts[activeChatKey].
const focusedDraft = () => {
  const s = store();
  return s.composerDrafts[s.activeChatKey ?? s.currentSessionId ?? "__new__"] ?? "";
};

let first;
let second;

describe("drafts", () => {
  test("each New chat claims a distinct provisional draft tab", () => {
    useCenterTabs.setState({ tabs: [{ id: "ntp:first", kind: "ntp", title: "" }], activeId: "ntp:first" });
    first = useCenterTabs.getState().claimDraftSessionTab();
    assert.match(first, /^local_[a-f0-9-]+$/);
    useCenterTabs.getState().openNewTabPage();
    second = useCenterTabs.getState().claimDraftSessionTab();
    assert.match(second, /^local_[a-f0-9-]+$/);
    assert.notEqual(first, second);
    assert.deepEqual(
      useCenterTabs.getState().tabs.filter((tab) => tab.kind === "session" && tab.draft).map((tab) => tab.sessionId),
      [first, second],
    );
  });

  test("each draft keeps its own text across switches", () => {
    store().setCurrentDraft(first);
    store().setComposerInput("draft one");
    store().setCurrentDraft(second);
    assert.equal(focusedDraft(), "", "switching to a fresh draft shows nothing");
    store().setComposerInput("draft two");
    store().setCurrentDraft(first);
    assert.equal(focusedDraft(), "draft one", "switching back restores the draft");
  });

  test("a late completion for draft A clears A, never the visible draft B", () => {
    store().setCurrentDraft(second);
    store().setComposerInputFor(first, "");
    assert.equal(focusedDraft(), "draft two");
    assert.equal(store().composerDrafts[first], "");
    store().setCurrentDraft(first);
  });

  test("project intent is per draft: the ACK takes only its own, closing a draft drops it", () => {
    store().setPendingProject(first, "project-a");
    store().setCurrentDraft(second);
    store().setPendingProject(second, "project-b");
    assert.deepEqual(store().pendingProjectsByChat, { [first]: "project-a", [second]: "project-b" });
    assert.equal(store().takePendingProject(first), "project-a");
    assert.deepEqual(store().pendingProjectsByChat, { [second]: "project-b" });
    store().setCurrentDraft(first);
    store().dropChatDraft(second);
    assert.deepEqual(store().pendingProjectsByChat, {});
  });
});

describe("acknowledgements", () => {
  test("a background acknowledgement does not steal focus, and never reopens a closed draft", () => {
    useCenterTabs.getState().setActive(sessionTabId(second));
    useCenterTabs.getState().markSessionReady(first);
    assert.equal(useCenterTabs.getState().activeId, sessionTabId(second));
    assert.equal(useCenterTabs.getState().tabs.find((tab) => tab.sessionId === first)?.draft, false);
    useCenterTabs.getState().closeTab(sessionTabId(first));
    assert.equal(sessionAckIsActive(first), false);
    assert.equal(useCenterTabs.getState().activeId, sessionTabId(second));
  });

  test("a late ACK never reactivates an explicitly closed session; reopening clears that", () => {
    const existing = "existing-server-session";
    useCenterTabs.setState({
      tabs: [
        { id: sessionTabId(existing), kind: "session", title: "Existing session", sessionId: existing },
        { id: sessionTabId(second), kind: "session", title: "Second draft", sessionId: second, draft: true },
      ],
      activeId: sessionTabId(existing),
    });
    useCenterTabs.getState().closeTab(sessionTabId(existing));
    assert.equal(sessionAckIsActive(existing), false);
    assert.equal(sessionAckIsActive("legacy-session-without-a-tab"), true, "a no-tab caller may still activate");
    useCenterTabs.getState().openSessionTab(existing, "Existing session");
    assert.equal(sessionAckIsActive(existing), true);
  });

  test("two drafts send concurrently, each paired with its own text", () => {
    for (const [sessionId, text] of [[first, "one"], [second, "two"]]) {
      assert.equal(sendChatMessage({ text, sessionId, thinking: "medium", webSearchEnabled: false }), true);
    }
    assert.deepEqual(sent.map((payload) => payload.session_id), [first, second]);
    assert.deepEqual([pendingUserText.getPendingUserText(first), pendingUserText.getPendingUserText(second)], ["one", "two"]);
  });

  test("the first ACK releases the draft's first-ACK reservation and attaches its pending project", () => {
    store().setPendingProject(second, "project-b");
    const before = sent.length;
    chatHandlers.wsHandleChatAck({ session_id: second, msg_id: "second-user" });
    assert.equal(pendingUserText.hasPendingFirstAck(second), false, "a later turn on the same key is not swallowed");
    assert.deepEqual(sent.slice(before).filter((frame) => frame.action === "set_session_project"),
      [{ action: "set_session_project", session_id: second, project_id: "project-b" }]);
    assert.equal(store().pendingProjectsByChat[second], undefined);
    pendingUserText.clearPendingFirstAck(first);
    store().setRunningTaskFor(second, null, "always");
  });

  test("responses route by their envelope session, not the visible tab", () => {
    assert.equal(responseRouting.responseSessionId({ session_id: first }, second), first);
    assert.equal(responseRouting.responseTargetsActiveChat({ session_id: first }, second), false);
    assert.equal(responseRouting.responseTargetsActiveChat({}, second), true, "unscoped responses target the active chat");
  });

  test("each draft keeps its own channel choice, and its send carries it", () => {
    const host = {};
    channelDrafts.setDraftChannelChoice(host, first, { channel: "wechat", account_id: "work" });
    channelDrafts.switchDraftChannelChoice(host, first, second);
    assert.equal(host._pendingChannelChoice, null);
    channelDrafts.setDraftChannelChoice(host, second, { channel: "slack", account_id: "team" });
    channelDrafts.switchDraftChannelChoice(host, second, first);
    assert.deepEqual(host._pendingChannelChoice, { channel: "wechat", account_id: "work" });
    channelDrafts.draftChannelChoiceHost.__pendingChannelChoices = host.__pendingChannelChoices;
    channelDrafts.draftChannelChoiceHost._pendingChannelChoice = host._pendingChannelChoice;
    assert.equal(sendChatMessage({ text: "channel draft", sessionId: first, thinking: "medium", webSearchEnabled: false }), true);
    assert.equal(sent.at(-1).channel, "wechat");
    assert.equal(sent.at(-1).account_id, "work");
    channelDrafts.dropDraftChannelChoice(host, first);
    assert.equal(channelDrafts.draftChannelChoiceFor(host, first), null);
  });
});

describe("attachments per draft", () => {
  const draftA = "local_attachment-a";
  const draftB = "local_attachment-b";
  const image = (id) => ({
    id, previewUrl: null, sizeBytes: 1, loading: true,
    attachment: { type: "image", data: "", media_type: "image/png" },
  });
  const doc = { id: "doc-a", filename: "a.pdf", ext: "pdf", content: null, dataB64: null, sizeBytes: 1, loading: true };

  test("a late read completion finishes its own draft's placeholder, never the other draft's", () => {
    const byChat = new Map();
    attachmentCache.addImagesForChat(byChat, draftA, [image("image-a")]);
    attachmentCache.addDocsForChat(byChat, draftA, [doc]);
    attachmentCache.addImagesForChat(byChat, draftB, [image("image-b")]);
    attachmentCache.updateImageForChat(byChat, draftA, "image-a", {
      previewUrl: "blob:a", attachment: { type: "image", data: "a-data", media_type: "image/png" }, loading: false,
    });
    attachmentCache.updateDocForChat(byChat, draftA, "doc-a", { dataB64: "doc-a-data", loading: false });
    assert.equal(byChat.get(draftA).images[0].attachment.data, "a-data");
    assert.equal(byChat.get(draftA).images[0].loading, false);
    assert.equal(byChat.get(draftA).docs[0].loading, false);
    assert.equal(byChat.get(draftB).images[0].id, "image-b");
    assert.equal(byChat.get(draftB).images[0].attachment.data, "");
  });

  test("a clear or remove during the IndexedDB load is not undone by the load", () => {
    const persisted = { images: [{ ...image("persisted-image") }], docs: [{ ...doc, id: "persisted-doc" }] };
    assert.deepEqual(attachmentCache.mergeAttachments(persisted, { images: [], docs: [] }, { cleared: true }), { images: [], docs: [] });
    assert.deepEqual(
      attachmentCache.mergeAttachments(persisted, { images: [], docs: [] }, {
        removedImageIds: ["persisted-image"], removedDocIds: ["persisted-doc"],
      }),
      { images: [], docs: [] },
    );
  });

  test("the composer's attachments are kept per chat and come back on switching", async () => {
    const { createElement } = await import("react");
    const { useComposerAttachments } = await import("../../../components/chat/composer/attach/use-composer-attachments.ts");
    let hook;
    function Probe() { hook = useComposerAttachments(); return null; }
    store().setCurrentDraft(draftA);
    const view = await render(createElement(Probe));
    try {
      await view.act(() => hook.addImagesForOwner(draftA, [image("kept-a")]));
      assert.deepEqual(hook.pendingImages.map((item) => item.id), ["kept-a"]);
      await view.act(() => store().setCurrentDraft(draftB));
      assert.deepEqual(hook.pendingImages, [], "draft B shows none of A's files");
      // A file finishing for A while B is shown lands in A only.
      await view.act(() => hook.addImagesForOwner(draftA, [image("late-a")]));
      assert.deepEqual(hook.pendingImages, []);
      await view.act(() => store().setCurrentDraft(draftA));
      assert.deepEqual(hook.pendingImages.map((item) => item.id), ["kept-a", "late-a"]);
    } finally {
      await view.unmount();
    }
  });

  test("a pasted image decoded after a switch lands in the draft it was pasted into", async () => {
    const { createElement } = await import("react");
    const { usePasteTokens } = await import("../../../components/chat/composer/paste/use-paste-tokens.ts");
    const added = [];
    const errors = [];
    let hook;
    store().setCurrentDraft(draftA);
    function Probe() {
      hook = usePasteTokens({
        input: "", setInput() {}, activeChatKey: draftA,
        addImagesForOwner: (owner, images) => added.push([owner, images.map((item) => item.id)]),
        setImageError: (message) => errors.push(message),
      });
      return null;
    }
    const view = await render(createElement(Probe));
    try {
      const imageItem = { kind: "file", type: "image/png", getAsFile: () => null };
      let prevented = false;
      hook.onPaste({ clipboardData: { items: [imageItem], getData: () => "" }, preventDefault() { prevented = true; } });
      assert.equal(prevented, true);
      store().setCurrentDraft(draftB);
      globalThis.__pasteDecode({ images: [image("pasted")], errors: ["too large"] });
      await view.act(() => new Promise((resolve) => setTimeout(resolve, 0)));
      assert.deepEqual(added, [[draftA, ["pasted"]]]);
      assert.deepEqual(errors, [], "a read error for A is not shown over draft B");
    } finally {
      await view.unmount();
    }
  });
});

describe("browser and builtin tabs", () => {
  test("opening a bookmark whose tab navigated away opens a new tab and keeps the other", () => {
    const bookmarkedUrl = "https://example.com/";
    const driftedUrl = "https://example.org/after-navigation";
    const bookmarkedTabId = webTabId(bookmarkedUrl);
    useCenterTabs.setState({
      tabs: [
        { id: bookmarkedTabId, kind: "web", title: "Example Domain B", url: driftedUrl },
        { id: "ntp:bookmark-test", kind: "ntp", title: "" },
      ],
      activeId: "ntp:bookmark-test",
    });
    useCenterTabs.getState().openWebTab(bookmarkedUrl);
    const state = useCenterTabs.getState();
    assert.notEqual(state.activeId, bookmarkedTabId);
    assert.equal(state.tabs.length, 2);
    assert.equal(state.tabs[0].url, driftedUrl);
    assert.equal(state.tabs[1].url, bookmarkedUrl);
    assert.equal(state.tabs[1].title, "example.com");
    useCenterTabs.getState().navigateHistory(-1);
    assert.equal(useCenterTabs.getState().activeId, "ntp:bookmark-test");
    assert.equal(useCenterTabs.getState().tabs[0].url, driftedUrl);
  });

  test("builtin pages are singletons: opening one again focuses it", () => {
    useCenterTabs.setState({ tabs: [{ id: "ntp:builtin-test", kind: "ntp", title: "" }], activeId: "ntp:builtin-test" });
    useCenterTabs.getState().openBuiltinTab("bookmarks");
    assert.equal(useCenterTabs.getState().activeId, builtinTabId("bookmarks"));
    assert.equal(useCenterTabs.getState().tabs.length, 1, "the active new-tab page is consumed");
    useCenterTabs.getState().openBuiltinTab("history");
    assert.equal(useCenterTabs.getState().activeId, builtinTabId("history"));
    assert.equal(useCenterTabs.getState().tabs.length, 2);
    useCenterTabs.getState().openBuiltinTab("bookmarks");
    assert.equal(useCenterTabs.getState().activeId, builtinTabId("bookmarks"));
    assert.equal(useCenterTabs.getState().tabs.filter((tab) => tab.kind === "builtin").length, 2);
  });
});

describe("tabs delivered to another window", () => {
  const deliver = (id) => ({
    tabs: [{ id: sessionTabId(id), kind: "session", title: "Torn", sessionId: id, draft: false }],
    source: { windowId: "w1", kind: "tab" },
    fileDrafts: [],
    chats: [{ chatKey: id, wasActive: true }],
  });

  test("a fresh window's lone empty placeholder is consumed by the delivered tab", () => {
    for (const placeholder of [
      { id: "s:local_x", kind: "session", title: "", sessionId: "local_x", draft: true },
      { id: "ntp:x", kind: "ntp", title: "" },
    ]) {
      useCenterTabs.setState({ tabs: [placeholder], activeId: placeholder.id, groups: [] });
      const result = insertTransferredTabs(deliver("torn1"), { kind: "strip-end", consumePlaceholder: true }, { persist: false });
      assert.ok(result.ok);
      assert.deepEqual(useCenterTabs.getState().tabs.map((tab) => tab.id), [sessionTabId("torn1")], placeholder.kind);
    }
  });

  test("a window with a real tab keeps it and appends", () => {
    for (const id of ["real", "realsolo"]) {
      useCenterTabs.setState({
        tabs: [{ id: sessionTabId(id), kind: "session", title: "Real", sessionId: id, draft: false }],
        activeId: sessionTabId(id),
        groups: [],
      });
      assert.ok(insertTransferredTabs(deliver("torn2"), { kind: "strip-end" }, { persist: false }).ok);
      assert.deepEqual(useCenterTabs.getState().tabs.map((tab) => tab.id), [sessionTabId(id), sessionTabId("torn2")]);
    }
  });

  test("merging onto a lone draft keeps the draft as the merge target and groups both", () => {
    useCenterTabs.setState({
      tabs: [{ id: "s:local_merge", kind: "session", title: "", sessionId: "local_merge", draft: true }],
      activeId: "s:local_merge",
      groups: [],
    });
    const merged = insertTransferredTabs(deliver("torn3"), { kind: "merge", targetTabId: "s:local_merge" }, { persist: false });
    assert.ok(merged.ok);
    assert.deepEqual(useCenterTabs.getState().tabs.map((tab) => tab.id), ["s:local_merge", sessionTabId("torn3")]);
    assert.equal(useCenterTabs.getState().groups.length, 1);
    assert.deepEqual(useCenterTabs.getState().groups[0].memberIds, ["s:local_merge", sessionTabId("torn3")]);
  });
});
