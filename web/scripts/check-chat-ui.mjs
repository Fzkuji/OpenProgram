import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const source = (path) => readFileSync(new URL(path, root), "utf8");

const welcome = source("components/chat/welcome-screen.tsx");
const welcomeCss = source("components/chat/welcome-screen.module.css");
const messageList = source("components/chat/messages/message-list.tsx");
const tabs = source("components/center-tabs/center-tab-strip.tsx");
const tabsCss = source("components/center-tabs/center-tabs.module.css");
const conversations = source("lib/runtime-bridge/conversations.ts");
const chatHandlers = source("lib/runtime-bridge/chat-handlers.ts");

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
assert.equal(
  scroll.resolveChatScrollTop({
    keyChanged: false,
    seedChanged: true,
    saved: 120.5,
    scrollHeight: 900,
    currentTop: 120.5,
  }),
  900,
  "a new turn in the same chat must move to the bottom",
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
assert.match(messageList, /useChatAreaStick\(chatKey, ids\.length\);/);
assert.match(messageList, /previousKeyRef\.current !== chatKey/);
assert.doesNotMatch(conversations, /agentic_scroll/);
assert.doesNotMatch(chatHandlers, /agentic_scroll/);
assert.match(conversations, /readChatScroll\(sessionStorage, id\)/);
assert.match(chatHandlers, /writeChatScroll\(sessionStorage, chatKey, area\.scrollTop\)/);

assert.match(tabs, /role="tablist"/);
assert.match(tabs, /role="tab"/);
assert.match(tabs, /aria-selected=\{active\}/);
assert.match(tabs, /tabIndex=\{active \? 0 : -1\}/);
assert.match(tabs, /onTabListKeyDown/);
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

console.log("chat-ui checks passed");
