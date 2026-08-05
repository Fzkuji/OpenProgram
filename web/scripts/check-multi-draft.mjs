import assert from "node:assert/strict";

import { readComposerSource } from "./composer-source.mjs";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../${specifier.slice(2)}`, import.meta.url).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    // Extensionless relative imports between source modules (Node needs the
    // extension; TypeScript and the Next build resolve them on their own).
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      // Append to href, not to pathname: pathname is percent-encoded and
      // re-parsing it against the same base double-encodes any space in the
      // repo path.
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const values = new Map();
globalThis.window = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  location: { pathname: "/chat" },
};
// The composer's `setRunning` import reaches runtime-bridge/ui + the DAG
// module, both of which install document-level listeners at import time.
// Seed the DOM hooks they touch so the module graph loads under Node.
globalThis.document = {
  addEventListener: () => {},
  removeEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => ({
    getContext: () => null,
    style: {},
    classList: { add() {}, remove() {} },
    setAttribute() {},
    appendChild() {},
  }),
  body: null,
};
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};

const tabsModule = await import("../lib/state/center-tabs-store.ts");
const sessionModule = await import("../lib/session-store/index.ts");
const sendModule = await import("../components/chat/composer/legacy-send.ts");
const { useCenterTabs, sessionTabId, sessionAckIsActive, webTabId, builtinTabId } =
  tabsModule;
const { useSessionStore } = sessionModule;

useCenterTabs.setState({
  tabs: [{ id: "ntp:first", kind: "ntp", title: "" }],
  activeId: "ntp:first",
});

const first = useCenterTabs.getState().claimDraftSessionTab();
assert.match(first, /^local_[a-f0-9-]+$/, "first draft gets a provisional id");

useCenterTabs.getState().openNewTabPage();
const second = useCenterTabs.getState().claimDraftSessionTab();
assert.match(second, /^local_[a-f0-9-]+$/, "second draft gets a provisional id");
assert.notEqual(first, second, "each New chat is distinct");
assert.deepEqual(
  useCenterTabs.getState().tabs
    .filter((tab) => tab.kind === "session" && tab.draft)
    .map((tab) => tab.sessionId),
  [first, second],
);

// The focused chat's draft is `composerDrafts[activeChatKey]` — there is no
// separate live-slice field anymore, so read it the way the composer's
// session scope does.
const focusedDraft = () => {
  const s = useSessionStore.getState();
  return s.composerDrafts[s.activeChatKey ?? s.currentSessionId ?? "__new__"] ?? "";
};

const sessions = useSessionStore.getState();
assert.equal(typeof sessions.setCurrentDraft, "function");
sessions.setCurrentDraft(first);
useSessionStore.getState().setComposerInput("draft one");
useSessionStore.getState().setCurrentDraft(second);
assert.equal(focusedDraft(), "", "switching to a fresh draft shows nothing");
useSessionStore.getState().setComposerInput("draft two");
useSessionStore.getState().setCurrentDraft(first);
assert.equal(focusedDraft(), "draft one", "switching back restores the draft");

assert.equal(
  typeof useSessionStore.getState().setComposerInputFor,
  "function",
  "async submit completion needs an owner-keyed draft write",
);
useSessionStore.getState().setCurrentDraft(second);
useSessionStore.getState().setComposerInputFor(first, "");
assert.equal(
  focusedDraft(),
  "draft two",
  "late completion for draft A must not clear visible draft B",
);
assert.equal(useSessionStore.getState().composerDrafts[first], "");
useSessionStore.getState().setCurrentDraft(first);

assert.equal(
  typeof useSessionStore.getState().setPendingProject,
  "function",
  "session store must expose per-draft project intent",
);
useSessionStore.getState().setPendingProject(first, "project-a");
useSessionStore.getState().setCurrentDraft(second);
useSessionStore.getState().setPendingProject(second, "project-b");
assert.deepEqual(useSessionStore.getState().pendingProjectsByChat, {
  [first]: "project-a",
  [second]: "project-b",
});
assert.equal(
  useSessionStore.getState().takePendingProject(first),
  "project-a",
  "ACK consumes only the acknowledged draft's project intent",
);
assert.deepEqual(useSessionStore.getState().pendingProjectsByChat, {
  [second]: "project-b",
});
useSessionStore.getState().setCurrentDraft(first);
useSessionStore.getState().dropChatDraft(second);
assert.deepEqual(
  useSessionStore.getState().pendingProjectsByChat,
  {},
  "closing a draft removes its unconsumed project intent",
);

useCenterTabs.getState().setActive(sessionTabId(second));
useCenterTabs.getState().markSessionReady(first);
assert.equal(
  useCenterTabs.getState().activeId,
  sessionTabId(second),
  "a background acknowledgement does not steal focus",
);
assert.equal(
  useCenterTabs.getState().tabs.find((tab) => tab.sessionId === first)?.draft,
  false,
);
useCenterTabs.getState().closeTab(sessionTabId(first));
assert.equal(
  sessionAckIsActive(first),
  false,
  "an acknowledgement must not reopen a closed provisional tab",
);
assert.equal(useCenterTabs.getState().activeId, sessionTabId(second));

// Existing server sessions also receive chat_ack for each new turn. If the
// user closes that tab while its acknowledgement is in flight, the late ACK
// must not reactivate the explicitly closed session. A session that never had
// a center tab remains the legacy no-tab case and may still activate.
const existingSession = "existing-server-session";
useCenterTabs.setState({
  tabs: [
    {
      id: sessionTabId(existingSession),
      kind: "session",
      title: "Existing session",
      sessionId: existingSession,
    },
    {
      id: sessionTabId(second),
      kind: "session",
      title: "Second draft",
      sessionId: second,
      draft: true,
    },
  ],
  activeId: sessionTabId(existingSession),
});
useCenterTabs.getState().closeTab(sessionTabId(existingSession));
assert.equal(
  sessionAckIsActive(existingSession),
  false,
  "a late ACK must not reactivate an explicitly closed existing session",
);
assert.equal(
  sessionAckIsActive("legacy-session-without-a-tab"),
  true,
  "a legacy no-tab caller may still activate its session",
);
useCenterTabs.getState().openSessionTab(existingSession, "Existing session");
assert.equal(
  sessionAckIsActive(existingSession),
  true,
  "explicitly reopening a session clears its close tombstone",
);

const sent = [];
globalThis.WebSocket = { OPEN: 1 };
// The socket and the active session id live on the shared runtimeState
// module now (lib/runtime-bridge/state), not on `window`.
const { runtimeState, setSocket } = await import(
  "../lib/runtime-bridge/state.ts"
);
// The ack-pairing reservations live in lib/pending-user-text now.
const pendingUserText = await import("../lib/pending-user-text.ts");
setSocket({ readyState: 1, send: (payload) => sent.push(JSON.parse(payload)) });
runtimeState.currentSessionId = null;

for (const [sessionId, text] of [[first, "one"], [second, "two"]]) {
  assert.equal(sendModule.sendChatMessage({
    text,
    sessionId,
    thinking: "medium",
    toolsEnabled: true,
    webSearchEnabled: false,
  }), true);
}
assert.deepEqual(sent.map((payload) => payload.session_id), [first, second]);
assert.deepEqual(
  [pendingUserText.getPendingUserText(first), pendingUserText.getPendingUserText(second)],
  ["one", "two"],
);
// Simulate both first ACKs before reusing `first` for the channel-choice
// assertion below. The production ACK handler clears this reservation.
pendingUserText.clearPendingFirstAck(first);
pendingUserText.clearPendingFirstAck(second);

// Legacy response bookkeeping must route by the envelope's session rather
// than whichever tab happens to be visible when a background run completes.
const responseRouting = await import(
  "../lib/runtime-bridge/chat-response-routing.ts"
);
assert.equal(
  responseRouting.responseSessionId({ session_id: first }, second),
  first,
  "a background result belongs to its envelope session",
);
assert.equal(
  responseRouting.responseTargetsActiveChat({ session_id: first }, second),
  false,
  "a background result must not change active-chat controls",
);
assert.equal(
  responseRouting.responseTargetsActiveChat({}, second),
  true,
  "legacy unscoped responses still target the active chat",
);

const channelDrafts = await import(
  "../lib/runtime-bridge/draft-channel-choice.ts"
);
const channelHost = {};
channelDrafts.setDraftChannelChoice(channelHost, first, {
  channel: "wechat",
  account_id: "work",
});
channelDrafts.switchDraftChannelChoice(channelHost, first, second);
assert.equal(channelHost._pendingChannelChoice, null);
channelDrafts.setDraftChannelChoice(channelHost, second, {
  channel: "slack",
  account_id: "team",
});
channelDrafts.switchDraftChannelChoice(channelHost, second, first);
assert.deepEqual(channelHost._pendingChannelChoice, {
  channel: "wechat",
  account_id: "work",
});
channelDrafts.draftChannelChoiceHost.__pendingChannelChoices =
  channelHost.__pendingChannelChoices;
channelDrafts.draftChannelChoiceHost._pendingChannelChoice =
  channelHost._pendingChannelChoice;
assert.equal(sendModule.sendChatMessage({
  text: "channel draft",
  sessionId: first,
  thinking: "medium",
  toolsEnabled: true,
  webSearchEnabled: false,
}), true);
assert.equal(sent.at(-1).channel, "wechat");
assert.equal(sent.at(-1).account_id, "work");
channelDrafts.dropDraftChannelChoice(channelHost, first);
assert.equal(channelDrafts.draftChannelChoiceFor(channelHost, first), null);

const chatHandlers = readFileSync(
  new URL("../lib/runtime-bridge/chat-handlers.ts", import.meta.url),
  "utf8",
);
assert.match(
  chatHandlers,
  /const sid = responseSessionId\(data, runtimeState\.currentSessionId\);/,
);
assert.match(
  chatHandlers,
  /if \(responseTargetsActiveChat\(data, runtimeState\.currentSessionId\)\) \{\s*setRunActive\(false\);/,
);
// The ACK handler must release the per-session first-ACK reservation so a
// later turn on the same draft key isn't swallowed as a duplicate.
assert.match(chatHandlers, /clearPendingFirstAck\(sid\);/);
assert.match(chatHandlers, /action:\s*"set_session_project"/);
assert.match(chatHandlers, /takePendingProject\(sid\)/);

for (const relativePath of [
  "../components/chat/top-bar/project-menu.tsx",
  "../components/sidebar/sidebar.tsx",
  "../components/sidebar/sessions-list.tsx",
  "../lib/state/files-shared.ts",
]) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  assert.doesNotMatch(
    source,
    /_pendingProjectId/,
    `${relativePath} must not use a process-wide pending project`,
  );
}

const attachmentHook = readFileSync(
  new URL("../components/chat/composer/attach/use-composer-attachments.ts", import.meta.url),
  "utf8",
);
// The composer is split across index.tsx and its submodules;
// readComposerSource concatenates them in source order so the
// assertions below read it as one text, unchanged.
const composerSource = readComposerSource(import.meta.url);
assert.match(attachmentHook, /attachmentsByChatRef/);
assert.match(
  attachmentHook,
  /if \(cached && loadedAttachmentKeysRef\.current\.has\(sid\)\) return;/,
);
assert.match(composerSource, /const pasteOwnerKey = activeChatKey;/);
// The decoded images must be filed under the key captured AT PASTE TIME,
// never the currently-active one — that is the invariant, independent of
// the callback's shape. (It destructures `{ images, errors }` now, so that
// rejected files can be reported instead of silently dropped.)
assert.match(
  composerSource,
  /addImagesForOwner\(pasteOwnerKey, images\)/,
);
// A file that failed to read must surface an error rather than vanish.
assert.match(composerSource, /if \(errors\.length/);
assert.match(
  composerSource,
  /useSessionStore\.getState\(\)\.activeChatKey === pasteOwnerKey/,
);

// FileReader / thumbnail completion can arrive after the user switches from
// draft A to draft B. The completion must finish A's placeholder in A's
// bucket, never patch B's visible state, and leave no loading entry in A.
const attachmentCachePath = new URL(
  "../components/chat/composer/attach/attachment-session-cache.ts",
  import.meta.url,
);
assert.ok(existsSync(attachmentCachePath), "per-draft attachment cache missing");
const attachmentCache = await import(attachmentCachePath.href);
const attachmentsByChat = new Map();
const draftA = "local_attachment-a";
const draftB = "local_attachment-b";
const imagePlaceholder = {
  id: "image-a",
  previewUrl: null,
  sizeBytes: 1,
  attachment: { type: "image", data: "", media_type: "image/png" },
  loading: true,
};
const docPlaceholder = {
  id: "doc-a",
  filename: "a.pdf",
  ext: "pdf",
  content: null,
  dataB64: null,
  sizeBytes: 1,
  loading: true,
};
attachmentCache.addImagesForChat(attachmentsByChat, draftA, [imagePlaceholder]);
attachmentCache.addDocsForChat(attachmentsByChat, draftA, [docPlaceholder]);
attachmentCache.addImagesForChat(attachmentsByChat, draftB, [{
  ...imagePlaceholder,
  id: "image-b",
}]);
attachmentCache.updateImageForChat(attachmentsByChat, draftA, imagePlaceholder.id, {
  previewUrl: "blob:a",
  attachment: { type: "image", data: "a-data", media_type: "image/png" },
  loading: false,
});
attachmentCache.updateDocForChat(attachmentsByChat, draftA, docPlaceholder.id, {
  dataB64: "doc-a-data",
  loading: false,
});
assert.equal(attachmentsByChat.get(draftA).images[0].attachment.data, "a-data");
assert.equal(attachmentsByChat.get(draftA).images[0].loading, false);
assert.equal(attachmentsByChat.get(draftA).docs[0].loading, false);
assert.equal(attachmentsByChat.get(draftB).images[0].id, "image-b");
assert.equal(attachmentsByChat.get(draftB).images[0].attachment.data, "");
const persistedBeforeDelete = {
  images: [{ ...imagePlaceholder, id: "persisted-image" }],
  docs: [{ ...docPlaceholder, id: "persisted-doc" }],
};
assert.deepEqual(
  attachmentCache.mergeAttachments(
    persistedBeforeDelete,
    { images: [], docs: [] },
    { cleared: true },
  ),
  { images: [], docs: [] },
  "clear during IndexedDB load must not resurrect persisted attachments",
);
assert.deepEqual(
  attachmentCache.mergeAttachments(
    persistedBeforeDelete,
    { images: [], docs: [] },
    {
      removedImageIds: ["persisted-image"],
      removedDocIds: ["persisted-doc"],
    },
  ),
  { images: [], docs: [] },
  "remove during IndexedDB load must remain removed",
);
assert.match(attachmentHook, /const ownerKey = activeChatKeyRef\.current;/);
assert.match(attachmentHook, /updateImageForOwner\(ownerKey,/);
assert.match(attachmentHook, /updateDocForOwner\(ownerKey,/);
assert.match(attachmentHook, /attachmentOwnerIsClosed\(chatKey\)/);

const bookmarkedUrl = "https://example.com/";
const driftedUrl = "https://example.org/after-navigation";
const bookmarkedTabId = webTabId(bookmarkedUrl);
useCenterTabs.setState({
  tabs: [
    {
      id: bookmarkedTabId,
      kind: "web",
      title: "Example Domain B",
      url: driftedUrl,
    },
    { id: "ntp:bookmark-test", kind: "ntp", title: "" },
  ],
  activeId: "ntp:bookmark-test",
});
useCenterTabs.getState().openWebTab(bookmarkedUrl);
const reopenedBookmark = useCenterTabs.getState();
assert.equal(reopenedBookmark.activeId, bookmarkedTabId);
assert.equal(reopenedBookmark.tabs.length, 1, "the active NTP is consumed");
assert.equal(reopenedBookmark.tabs[0].url, bookmarkedUrl);
assert.equal(reopenedBookmark.tabs[0].title, "example.com");

// ---- Builtin pages are singletons ----------------------------------
// Opening bookmarks/history twice must focus the existing tab, never
// stack duplicates; the two pages are independent of each other.
useCenterTabs.setState({
  tabs: [{ id: "ntp:builtin-test", kind: "ntp", title: "" }],
  activeId: "ntp:builtin-test",
});
useCenterTabs.getState().openBuiltinTab("bookmarks");
assert.equal(useCenterTabs.getState().activeId, builtinTabId("bookmarks"));
assert.equal(
  useCenterTabs.getState().tabs.length,
  1,
  "the active NTP is consumed by the builtin page",
);
useCenterTabs.getState().openBuiltinTab("history");
assert.equal(useCenterTabs.getState().activeId, builtinTabId("history"));
assert.equal(useCenterTabs.getState().tabs.length, 2, "the two pages are distinct tabs");
// Re-opening bookmarks focuses the one that already exists.
useCenterTabs.getState().openBuiltinTab("bookmarks");
assert.equal(useCenterTabs.getState().activeId, builtinTabId("bookmarks"));
assert.equal(
  useCenterTabs.getState().tabs.filter((tab) => tab.kind === "builtin").length,
  2,
  "openBuiltinTab must never duplicate a builtin tab",
);

// Tearing a tab into a NEW window: the fresh window bootstraps with a lone
// empty placeholder ("New chat" draft or ntp). Delivering the torn-off tab
// must CONSUME that placeholder, not sit beside it (else two tabs appear).
const { insertTransferredTabs, sessionTabId: sTabId } = tabsModule;
const deliver = (id) => ({
  tabs: [{ id: sTabId(id), kind: "session", title: "Torn", sessionId: id, draft: false }],
  source: { windowId: "w1", kind: "tab" },
  fileDrafts: [],
  chats: [{ chatKey: id, wasActive: true }],
});

for (const placeholder of [
  { id: "s:local_x", kind: "session", title: "", sessionId: "local_x", draft: true },
  { id: "ntp:x", kind: "ntp", title: "" },
]) {
  useCenterTabs.setState({ tabs: [placeholder], activeId: placeholder.id, groups: [] });
  // The real new-window self-pull passes consumePlaceholder: true (desktop-bridge
  // claimPending); only that flag consumes the lone empty placeholder. Merging
  // into an EXISTING window (L459 below) omits it, so the target tab survives.
  const r = insertTransferredTabs(
    deliver("torn1"),
    { kind: "strip-end", consumePlaceholder: true },
    { persist: false },
  );
  assert.ok(r.ok, "delivery into a fresh window succeeds");
  assert.deepEqual(
    useCenterTabs.getState().tabs.map((t) => t.id),
    [sTabId("torn1")],
    `an empty ${placeholder.kind} placeholder is consumed by the delivered tab`,
  );
}

// A window with a real tab (or a draft the user typed into) keeps it: ADD.
useCenterTabs.setState({
  tabs: [{ id: sTabId("real"), kind: "session", title: "Real", sessionId: "real", draft: false }],
  activeId: sTabId("real"),
  groups: [],
});
const added = insertTransferredTabs(deliver("torn2"), { kind: "strip-end" }, { persist: false });
assert.ok(added.ok);
assert.deepEqual(
  useCenterTabs.getState().tabs.map((t) => t.id),
  [sTabId("real"), sTabId("torn2")],
  "delivery into a window with real tabs appends, never consumes",
);

// (b) MERGE onto a lone draft: the draft is the merge target, so it must NOT
// be consumed — the result is a group of [draft, delivered], nothing lost.
useCenterTabs.setState({
  tabs: [{ id: "s:local_merge", kind: "session", title: "", sessionId: "local_merge", draft: true }],
  activeId: "s:local_merge",
  groups: [],
});
const merged = insertTransferredTabs(
  deliver("torn3"),
  { kind: "merge", targetTabId: "s:local_merge" },
  { persist: false },
);
assert.ok(merged.ok, "merge onto a lone draft succeeds");
assert.deepEqual(
  useCenterTabs.getState().tabs.map((t) => t.id),
  ["s:local_merge", sTabId("torn3")],
  "merge onto a lone draft keeps the draft as the merge target — never consumed",
);
assert.equal(
  useCenterTabs.getState().groups.length,
  1,
  "merge onto a lone draft forms one group of [draft, delivered]",
);
assert.deepEqual(
  useCenterTabs.getState().groups[0].memberIds,
  ["s:local_merge", sTabId("torn3")],
  "the merge group contains both the draft and the delivered tab",
);

// (c) a lone REAL (non-draft) tab is never a placeholder: strip-end appends.
useCenterTabs.setState({
  tabs: [{ id: sTabId("realsolo"), kind: "session", title: "Real", sessionId: "realsolo", draft: false }],
  activeId: sTabId("realsolo"),
  groups: [],
});
const appendedReal = insertTransferredTabs(deliver("torn4"), { kind: "strip-end" }, { persist: false });
assert.ok(appendedReal.ok);
assert.deepEqual(
  useCenterTabs.getState().tabs.map((t) => t.id),
  [sTabId("realsolo"), sTabId("torn4")],
  "a lone real tab is kept; strip-end appends beside it",
);

console.log("multi-draft checks passed");
