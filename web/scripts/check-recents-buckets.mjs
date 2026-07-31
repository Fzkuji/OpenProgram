// Guards the recency bucketing shared by the sidebar Recents list and
// the /chats page. Both surfaces must agree on what "Today" means and
// must bucket by LAST ACTIVITY, not creation time — /chats used to do
// its own rolling-24h math off created_at and disagreed with the sidebar.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const helpersPath = new URL("../components/sidebar/sessions-list/helpers.ts", import.meta.url);
const chatsPagePath = new URL("../components/chats/chats-page.tsx", import.meta.url);
const sessionsListPath = new URL("../components/sidebar/sessions-list.tsx", import.meta.url);

const helpersSrc = readFileSync(helpersPath, "utf8");
const chatsSrc = readFileSync(chatsPagePath, "utf8");
const sidebarSrc = readFileSync(sessionsListPath, "utf8");

/* ---- 1. both surfaces use the shared helpers, not private copies ---- */

for (const [name, src] of [["chats-page", chatsSrc], ["sessions-list", sidebarSrc]]) {
  assert.match(
    src,
    /bucketKey/,
    `${name} must bucket via the shared bucketKey helper`,
  );
}
assert.match(
  chatsSrc,
  /from "@\/components\/sidebar\/sessions-list\/helpers"/,
  "chats-page must import the shared sessions-list helpers",
);
assert.doesNotMatch(
  chatsSrc,
  /function bucketOf/,
  "chats-page must not reintroduce its own bucketing function",
);

/* ---- 2. /chats reads the shared store, not a second WebSocket ---- */

assert.match(
  chatsSrc,
  /useSessionStore\(\(s\) => s\.conversations\)/,
  "chats-page must read conversations from the session store",
);
assert.doesNotMatch(
  chatsSrc,
  /new WebSocket\(/,
  "chats-page must not open its own WebSocket — the store is fed by the runtime-bridge",
);

/* ---- 3. bucketing / sorting keys off last activity ---- */

assert.match(
  helpersSrc,
  /export function activityTs[\s\S]*?updated_at \|\| c\.created_at/,
  "activityTs must prefer updated_at and fall back to created_at",
);
assert.doesNotMatch(
  chatsSrc,
  /bucketKey\(\s*c?\.?created_at/,
  "chats-page must bucket by activityTs, not created_at",
);

/* ---- 4. bucketKey behaviour: calendar days, not rolling 24h ---- */

const { outputText } = ts.transpileModule(helpersSrc, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
// The helper module imports a React component for attachment parsing;
// only the pure date functions are under test, so evaluate them alone.
const pure = outputText.slice(outputText.indexOf("export function bucketKey"));
const mod = await import(
  "data:text/javascript," + encodeURIComponent(pure.slice(0, pure.indexOf("const CHANNEL_BRAND")))
);
const { bucketKey, activityTs } = mod;

const at = (y, m, d, h = 12) => Math.floor(new Date(y, m - 1, d, h).getTime() / 1000);
const now = at(2026, 7, 31, 10);

assert.equal(bucketKey(at(2026, 7, 31, 1), now), "today", "same calendar day = today");
// The rolling-24h bug: 20:00 yesterday is <24h before 10:00 today, but
// it is NOT today.
assert.equal(bucketKey(at(2026, 7, 30, 20), now), "past7", "yesterday evening is not today");
assert.equal(bucketKey(at(2026, 7, 25), now), "past7");
assert.equal(bucketKey(at(2026, 7, 10), now), "past30");
// Beyond 30 days: same-year months bucket by month, previous years by year.
assert.equal(bucketKey(at(2026, 5, 1), now), "m-2026-4", "current-year month bucket");
assert.equal(bucketKey(at(2025, 11, 20), now), "y-2025", "previous-year bucket");
// A conversation created long ago but active today buckets as today.
assert.equal(
  bucketKey(activityTs({ created_at: at(2026, 1, 1), updated_at: at(2026, 7, 31, 9) }), now),
  "today",
  "recently-active old conversation must bucket as today",
);
assert.equal(activityTs({ created_at: 111 }), 111, "falls back to created_at");
assert.equal(activityTs({}), 0);

console.log("check-recents-buckets: ok");
