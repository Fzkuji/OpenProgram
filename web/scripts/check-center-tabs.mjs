import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(
  new URL("../components/center-tabs/center-tabs.module.css", import.meta.url),
  "utf8",
);
const ntp = readFileSync(
  new URL("../components/center-tabs/new-tab-page.tsx", import.meta.url),
  "utf8",
);
const conversations = readFileSync(
  new URL("../lib/runtime-bridge/conversations.ts", import.meta.url),
  "utf8",
);
const strip = readFileSync(
  new URL("../components/center-tabs/center-tab-strip.tsx", import.meta.url),
  "utf8",
);

assert.match(css, /max-width: calc\(100% - 36px\);/);
assert.match(css, /:global\(html\.is-desktop\) \.strip \{[^}]*padding-right: 10px;/s);
assert.match(css, /:global\(html\.is-desktop\) \.tabsFlow > \.tab \{[^}]*width: 240px;[^}]*flex: 0 1 240px;[^}]*max-width: 240px;/s);
assert.match(css, /@keyframes desktopTabEnter \{\s*from \{ opacity: 0; \}\s*\}/);
assert.match(css, /:global\(html\.is-desktop\) \.tabEnter \{[^}]*animation: desktopTabEnter 120ms ease-out;/s);
assert.match(css, /:global\(html\.is-desktop\) \.tabExit \{[^}]*animation: tabExit 160ms ease-in forwards;/s);
assert.match(ntp, /newSession\?\.\(\);[\s\S]*claimDraftSessionTab\(\);/);
assert.match(strip, /currentSessionId === null[\s\S]*activeTab\?\.id === DRAFT_SESSION_TAB_ID/);
assert.match(strip, /closingInstances = useRef<Map<string, CenterTab>>/);
assert.match(strip, /const \[closingIds, setClosingIds\] = useState<Set<string>>/);
assert.match(
  strip,
  /closingIds\.has\(tab\.id\)[\s\S]*closingInstances\.current\.get\(tab\.id\) === tab/,
);
assert.match(strip, /tab\.id === DRAFT_SESSION_TAB_ID && currentTab !== closingInstance/);
assert.doesNotMatch(
  strip,
  /\}, \[activeId, currentSessionId, pathname, openSessionTab, openDraftSessionTab\]\);/,
);

const newSession = conversations.slice(conversations.indexOf("export function newSession"));
assert.ok(
  newSession.indexOf("W.currentSessionId = null") <
    newSession.indexOf("if (needsNavigation)"),
  "newSession must clear the current session before SPA navigation",
);
assert.ok(
  newSession.indexOf("setCurrentConv(null)") <
    newSession.indexOf("if (needsNavigation)"),
  "newSession must clear the React session store before SPA navigation",
);

console.log("center-tabs checks passed");
