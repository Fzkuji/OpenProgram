import assert from "node:assert/strict";
import fs from "node:fs";

const read = (path) => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const card = read("components/chat/messages/turn-files-chips.tsx");
const bubble = read("components/chat/messages/assistant-bubble.tsx");
const rail = read("components/chat/messages/message-rail.tsx");
const review = read("components/center-tabs/review-tab-pane.tsx");
const store = read("lib/state/center-tabs-store.ts");
const reviewLayout = read("lib/state/review-tab-layout.ts");
const cardCss = read("app/styles/chat/turn-files-card.css");

assert.match(card, /if \(embedded\) \{\s*setFiles\(embedded\);\s*return;/);
assert.match(bubble, /summary=\{msg\.turnFiles\}/);
assert.match(rail, /summary=\{assistantTurnFiles\}/);
assert.doesNotMatch(card, /turn_file_diff|UnifiedDiff|aria-expanded/);
assert.match(card, /FeatherIcon/);
assert.match(card, /text\("Undo"/);
assert.match(card, /text\("Redo"/);
assert.match(card, /openReviewTab/);
assert.match(card, /IntersectionObserver/);
assert.match(card, /const MAX_CARD_FILES = 20/);
assert.match(card, /function loadMore\(\)/);
assert.match(card, /action: "turn_history_state"/);
assert.match(card, /operation: null,[\s\S]*?setHistoryNonce/);
assert.match(card, /updateMessage\(sessionId, assistantMsgId/);
assert.match(card, /turn-files-history-changed/);
assert.match(review, /turn-files-history-changed/);
assert.doesNotMatch(card, /Code \{codeCount\}|Tests \{testCount\}/);
assert.match(store, /openReviewTab:/);
assert.match(reviewLayout, /reviewTabId\(sessionId, assistantMsgId\)/);
assert.match(reviewLayout, /groupCenterTabs/);
assert.equal((review.match(/<UnifiedDiff/g) ?? []).length, 1);
assert.match(review, /data-mounted-diff-count=\{selectedPath \? "1" : "0"\}/);
assert.match(review, /limit: 100/);
assert.match(review, /request_id: requestId/);
assert.match(review, /snapshot_id: scopeState\.snapshot_id/);
assert.match(review, /action: "review_scope"/);
assert.match(review, /action: "review_file_diff"/);
assert.match(cardCss, /\.turn-files-summary\{height:40px;min-height:40px/);
assert.match(cardCss, /\.turn-files-logo\{width:19px;height:19px/);
assert.match(cardCss, /\.turn-files-heading\{[^}]*align-items:center/);
assert.match(cardCss, /\.turn-files-count\{[^}]*font-size:14px/);
assert.match(cardCss, /\.turn-files-action,\.turn-files-review\{[^}]*font-size:13px/);
assert.match(cardCss, /\.turn-files-list\{[^}]*background:var\(--bg-primary\)/);
assert.match(cardCss, /\.turn-files-row\{[^}]*height:30px/);
assert.match(cardCss, /\.turn-files-name\{[^}]*font-size:13px/);
assert.match(cardCss, /\.turn-files-more\{[^}]*background:var\(--bg-tertiary\)[^}]*font-size:13px/);
assert.match(cardCss, /\.turn-files-meter/);
assert.match(cardCss, /@media\(max-width:420px\)/);
assert.doesNotMatch(card, /Review all \$\{fileCount\} files|审阅全部 \$\{fileCount\} 个文件/);
assert.doesNotMatch(
  cardCss,
  /@media\(max-width:420px\)[\s\S]*?turn-files-logo[^}]*?(?:width|height):17px/,
);

console.log("check-review-ui: ok");
