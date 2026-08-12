import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { readCenterTabStripSource } from "./center-tab-strip-source.mjs";
import { readChatCss } from "./_chat-css.mjs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const welcome = source("components/chat/welcome-screen.tsx");
const welcomeCss = source("components/chat/welcome-screen.module.css");
const messageList = source("components/chat/messages/message-list.tsx");
// The strip is split across center-tab-strip.tsx and its submodules;
// read them as one text so the assertions below are unchanged.
const tabs = readCenterTabStripSource(import.meta.url);
const tabsCss = source("components/center-tabs/center-tabs.module.css");
const conversations = source("lib/runtime-bridge/conversations.ts");
const chatHandlers = source("lib/runtime-bridge/chat-handlers.ts");
const sessionStore = source("lib/session-store/index.ts");
const assistantBubble = source("components/chat/messages/assistant-bubble.tsx");
const runtimeHelpers = source("lib/runtime-bridge/helpers.ts");
const chatCss = readChatCss(root);

// Markdown must not depend on the optional CDN script. A missing/blocked CDN
// previously left every assistant response in renderMd's bordered <pre>
// fallback for the lifetime of the page.
assert.match(runtimeHelpers, /import \{ marked as npmMarked \} from "marked";/);
assert.match(runtimeHelpers, /window\.marked\s*\?\?\s*npmMarked/);
assert.doesNotMatch(runtimeHelpers, /return "<pre>" \+ escHtml\(str\) \+ "<\/pre>";/);

assert.match(welcome, /src=["{]?["']\/icon\.svg["']/);
assert.doesNotMatch(welcome, /styles\.(?:l1|l2|m|caret)\b/);
assert.doesNotMatch(welcomeCss, /@keyframes\s+logo(Type|Caret)/);
assert.match(welcomeCss, /\.mark\s*\{[^}]*width:\s*34px;[^}]*height:\s*34px;/s);
assert.match(welcomeCss, /\.tagline\s*\{[^}]*font-size:\s*14px;/s);

const scroll = await import("../lib/state/chat-scroll.ts");
class MemoryStorage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}
const storage = new MemoryStorage();
scroll.writeChatScroll(storage, "chat-a", 120.5);
scroll.writeChatScroll(storage, "chat-b", 480);
assert.equal(scroll.readChatScroll(storage, "chat-a"), 120.5);
assert.equal(scroll.readChatScroll(storage, "chat-b"), 480);
assert.equal(
  scroll.resolveChatScrollTop({
    keyChanged: true,
    seedChanged: false,
    saved: 120.5,
    scrollHeight: 900,
    currentTop: 480,
  }),
  120.5,
  "equal-length chat switches must restore the incoming chat position",
);
// Following a new turn is CONDITIONAL on where the reader is. Being
// yanked to the bottom mid-read is the thing this contract prevents;
// the three cases below are the whole rule.
assert.equal(
  scroll.resolveChatScrollTop({
    keyChanged: false,
    seedChanged: true,
    saved: 120.5,
    scrollHeight: 900,
    currentTop: 880,
    atBottom: true,
  }),
  900,
  "a new turn must follow to the bottom for a reader already at the bottom",
);
assert.equal(
  scroll.resolveChatScrollTop({
    keyChanged: false,
    seedChanged: true,
    saved: 120.5,
    scrollHeight: 900,
    currentTop: 120.5,
    atBottom: false,
  }),
  120.5,
  "a turn arriving while the reader is scrolled up must not move the view",
);
assert.equal(
  scroll.resolveChatScrollTop({
    keyChanged: false,
    seedChanged: true,
    saved: 120.5,
    scrollHeight: 900,
    currentTop: 120.5,
    atBottom: false,
    ownTurn: true,
  }),
  900,
  "the reader's OWN send must follow even from far up the history",
);
assert.equal(
  scroll.resolveChatScrollTop({
    keyChanged: false,
    seedChanged: true,
    saved: 120.5,
    scrollHeight: 900,
    currentTop: 120.5,
  }),
  900,
  "atBottom defaults to true — an untracked caller keeps following, "
    + "rather than silently freezing the view",
);
const area = { scrollTop: 33 };
assert.equal(
  scroll.restoreChatScrollIfCurrent(area, "chat-a", "chat-b", 120.5),
  false,
  "a stale animation-frame callback must not restore the old chat",
);
assert.equal(area.scrollTop, 33);
assert.equal(
  scroll.restoreChatScrollIfCurrent(area, "chat-b", "chat-b", 480),
  true,
);
assert.equal(area.scrollTop, 480);
storage.setItem(scroll.CHAT_SCROLL_STORAGE_KEY, "not-json");
assert.equal(scroll.readChatScroll(storage, "chat-a"), null);
assert.match(messageList, /const chatKey = useSessionStore\(\(s\) => s\.activeChatKey\);/);
// The hook needs the own-send signal to tell "I sent this" from "this
// arrived at me", and must hand back the jump-to-latest affordance —
// without it a reader who scrolls up has no way back to the tail.
assert.match(
  messageList,
  /useChatAreaStick\(\s*chatKey,\s*ids\.length,\s*lastRole === "user",?\s*\)/,
);
assert.match(messageList, /const \{ detached, jumpToLatest \} = useChatAreaStick/);
assert.match(messageList, /className="jump-latest"/);
assert.match(messageList, /previousKeyRef\.current !== chatKey/);
assert.doesNotMatch(conversations, /agentic_scroll/);
assert.doesNotMatch(chatHandlers, /agentic_scroll/);
assert.match(conversations, /readChatScroll\(sessionStorage, id\)/);
assert.match(chatHandlers, /writeChatScroll\(sessionStorage, chatKey, area\.scrollTop\)/);

// ── A mid-turn load_session must not wipe the streaming reply ────────
// `load_session` lands DURING a run on several paths that need no user
// action: WS reconnect (use-ws onopen), a `session_reload` frame,
// switching back from a sub-agent, and `hydrateTranscriptForTreeUpdate`
// — which fires mid-turn by design. The server's row for the in-flight
// turn is an empty placeholder (output="", status="running"), so a
// naive rebuild replaces everything streamed so far with a blank
// bubble. `setMessages` must keep the live row when the reload has
// nothing to add.
const setMessagesBody = sessionStore.slice(
  sessionStore.indexOf("setMessages: (sessionId, msgs)"),
  sessionStore.indexOf("appendMessage: (sessionId, msg)"),
);
assert.ok(setMessagesBody, "setMessages not found in the session store");
assert.match(
  setMessagesBody,
  /isLiveRow\(cur\)\s*&&\s*isEmptyRow\(m\)\s*\?\s*cur\s*:\s*m/,
  "setMessages must preserve an in-flight streaming row when the "
    + "incoming load_session payload row is an empty placeholder",
);
// The two predicates are what make that guard correct — a live row is
// any not-yet-finalized status, and emptiness must consider every
// channel the stream writes into, not just `content`.
const isLive = sessionStore.slice(
  sessionStore.indexOf("function isLiveRow"),
  sessionStore.indexOf("function isEmptyRow"),
);
for (const s of ["streaming", "running", "pending"]) {
  assert.match(isLive, new RegExp(`"${s}"`), `isLiveRow must treat "${s}" as live`);
}
const isEmpty = sessionStore.slice(
  sessionStore.indexOf("function isEmptyRow"),
  sessionStore.indexOf("interface ConvState"),
);
for (const field of ["content", "thinking", "blocks", "tools"]) {
  assert.match(
    isEmpty,
    new RegExp(`m\\.${field}`),
    `isEmptyRow must check m.${field} — the stream writes into it, so a `
      + "row holding only that would be treated as empty and overwritten",
  );
}

// ── Agentic runtime cards are matched by ORDER, never by a `_rt_` id ──
// `_wrap_agentic_runtime_block` persists no placeholder row, so no id
// of the form `<msg_id>_rt_<tool_call_id>` is ever minted. Matching on
// one was dead code that silently degraded to FIFO.
assert.doesNotMatch(
  assistantBubble,
  /_rt_/,
  "runtime children carry no tool_call_id back-reference — a `_rt_` id "
    + "match is dead code; keep the ordered claim explicit instead",
);
assert.doesNotMatch(
  assistantBubble,
  /runtimeByToolId/,
  "runtimeByToolId was always empty; the ordered fifo is the real path",
);

// ── MessageList must not re-render on every streaming delta ──────────
// `updateMessage` returns a fresh `messagesById` each token, so
// subscribing to the whole map re-renders the list — and re-maps every
// id — per delta. Only the last row's role is actually needed.
assert.doesNotMatch(
  messageList,
  /useSessionStore\(\(s\) => s\.messagesById\)/,
  "MessageList must select the one field it needs, not the whole "
    + "messagesById map (re-renders on every streaming token)",
);
assert.match(messageList, /s\.messagesById\[lastId\]\?\.role/);
assert.match(messageList, /showPending = runningTask !== null && lastRole === "user"/);

// ── Markdown must not widen the chat column ─────────────────────────
// A wide table or one long unbroken token used to push #chatArea into
// horizontal scroll. marked emits a bare <table> with no wrapper, so
// the table itself has to be the scroll box — which needs
// `display:block`, since a display:table box ignores overflow-x.
const mdTable = chatCss.slice(
  chatCss.indexOf(".message-content table {"),
  chatCss.indexOf(".message-content th,"),
);
assert.ok(mdTable, ".message-content table rule not found");
for (const decl of ["display: block", "overflow-x: auto", "max-width: 100%"]) {
  assert.match(
    mdTable,
    new RegExp(decl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
    `.message-content table needs \`${decl}\` or a wide table overflows `
      + "the bubble and scrolls the whole chat area sideways",
  );
}
const mdContent = chatCss.slice(
  chatCss.indexOf(".message-content {"),
  chatCss.indexOf(".message-content pre {"),
);
assert.match(
  mdContent,
  /overflow-wrap: anywhere/,
  "`word-wrap: break-word` never breaks a single unbroken token (long "
    + "URL / hash / base64) — `.message-content` needs overflow-wrap: anywhere",
);
const mdCode = chatCss.slice(
  chatCss.indexOf(".message-content code {"),
  chatCss.indexOf(".message-content pre code"),
);
assert.match(mdCode, /overflow-wrap: anywhere/, "inline code must wrap too");

assert.match(tabs, /role="tablist"/);
assert.match(tabs, /role="tab"/);
assert.match(tabs, /aria-selected=\{active\}/);
assert.match(tabs, /const \[focusedTabId, setFocusedTabId\] = useState/);
assert.match(tabs, /tabIndex=\{tabStop \? 0 : -1\}/);
assert.match(tabs, /onTabListKeyDown/);
const rovingFocus = tabs.slice(
  tabs.indexOf("function onTabListKeyDown"),
  tabs.indexOf("function onTabListWheel"),
);
assert.match(rovingFocus, /"ArrowLeft"/);
assert.match(rovingFocus, /"ArrowRight"/);
assert.match(rovingFocus, /"Home"/);
assert.match(rovingFocus, /"End"/);
assert.match(rovingFocus, /items\[nextIndex\]\.focus\(\)/);
assert.doesNotMatch(
  rovingFocus,
  /items\[nextIndex\]\.click\(\)/,
  "roving focus must not activate the focused tab",
);
const compoundStart = tabs.indexOf("function CompoundTabItem");
const tabItemStart = tabs.indexOf("function TabItem");
assert.ok(compoundStart >= 0 && tabItemStart > compoundStart);
const compound = tabs.slice(compoundStart, tabItemStart);
const tabItem = tabs.slice(tabItemStart);
assert.match(compound, /role="presentation"/);
assert.match(compound, /active \? styles\.compoundTabActive : ""/);
assert.doesNotMatch(compound, /active \? styles\.tabActive : ""/);
assert.match(compound, /group\.memberIds\.map\(\(tabId\) =>/);
assert.match(compound, /<TabItem/);
assert.match(compound, /enter=\{enteringIds\.has\(tab\.id\)\}/);
assert.match(compound, /closing=\{closingIds\.has\(tab\.id\)\}/);
assert.match(compound, /onClose=\{onClose\}/);
assert.match(compound, /onExited=\{onExited\}/);
assert.match(tabItem, /className=\{styles\.tabTarget\}[\s\S]*role="tab"/);
assert.match(tabItem, /data-tab-id=\{tab\.id\}/);
assert.match(tabItem, /e\.shiftKey && e\.key === "F10"/);
assert.match(tabItem, /onContextMenu=/);
assert.match(
  tabItem,
  /className=\{styles\.tabTarget\}[\s\S]*<\/div>[\s\S]*<button[\s\S]*className=\{styles\.tabClose\}/,
);
assert.match(tabs, /className=\{styles\.tabTarget\}[\s\S]*role="tab"/);
assert.match(tabs, /<button[\s\S]*className=\{styles\.tabClose\}[\s\S]*tabIndex=\{active \? 0 : -1\}/);
const tabTargetStart = tabs.indexOf("className={styles.tabTarget}");
const tabTargetEnd = tabs.indexOf("</div>", tabTargetStart);
const closeButtonStart = tabs.indexOf("<button", tabTargetStart);
assert.ok(tabTargetStart >= 0 && tabTargetEnd > tabTargetStart);
assert.ok(
  closeButtonStart > tabTargetEnd,
  "the close button must be a sibling of the role=tab target",
);
assert.match(tabsCss, /\.tab:has\(\.tabTarget:focus-visible\)/);
assert.match(tabsCss, /\.tabClose:focus-visible/);

const menuStart = tabs.indexOf("role=\"menu\"");
assert.ok(menuStart >= 0, "compound tab actions must use an ARIA menu");
const menu = tabs.slice(menuStart);
assert.match(menu, /<button[\s\S]*type="button"[\s\S]*role="menuitem"/);
for (const label of [
  "Move left",
  "Move right",
  "New split view with this tab",
  "Remove from group",
  "Move to new window",
]) {
  assert.match(menu, new RegExp(label));
}
assert.match(menu, /disabled=\{!canMoveToNewWindow\}/);
assert.match(tabs, /role="status"/);
assert.match(tabs, /aria-live="polite"/);
assert.match(tabs, /function returnFocusToMenuInvoker/);
assert.match(
  tabs,
  /querySelectorAll<HTMLElement>\(\s*'\[role="tab"\]\[data-tab-id\]'/,
);
assert.match(tabs, /if \(e\.key !== "Escape"\) return;[\s\S]*setTabMenu\(null\)/);
assert.match(tabsCss, /\.tabMenu\s*\{/);
assert.match(tabsCss, /\.tabMenuItem\s*\{/);

console.log("chat-ui checks passed");
