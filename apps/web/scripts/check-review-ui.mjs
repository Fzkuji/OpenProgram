import assert from "node:assert/strict";
import fs from "node:fs";

import { historyPresentation } from "../components/chat/messages/turn-files-history-state.ts";

const read = (path) => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const card = read("components/chat/messages/turn-files-chips.tsx");
const bubble = read("components/chat/messages/assistant-bubble.tsx");
const rail = read("components/chat/messages/message-rail.tsx");
const review = read("components/center-tabs/review-tab-pane.tsx");
const store = read("lib/state/center-tabs-store.ts");
const reviewLayout = read("lib/state/review-tab-layout.ts");
const cardCss = read("app/styles/chat/turn-files-card.css");
const design = read("../../docs/reference/design/ui/chat-turn-visual-spec.html");

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
const historyResponseStart = card.indexOf('frame?.type !== "turn_history_state_result"');
const historyResponseEnd = card.indexOf("} catch", historyResponseStart);
assert.ok(historyResponseStart >= 0 && historyResponseEnd > historyResponseStart);
assert.match(
  card.slice(historyResponseStart, historyResponseEnd),
  /setHistoryError\(""\);\s*setHistoryState\(/,
);
assert.match(card, /\} = historyPresentation\([\s\S]*?historyState,[\s\S]*?historyError,[\s\S]*?Review remains available/);
const refreshed = historyPresentation(
  { status: "ready", operation: "undo" },
  "",
  "fallback",
);
assert.deepEqual(refreshed, { notice: "", operation: "undo" });
const blocked = historyPresentation(
  { status: "blocked", operation: null, error: "current file state does not match the recorded source" },
  "",
  "fallback",
);
assert.deepEqual(blocked, {
  notice: "current file state does not match the recorded source",
  operation: null,
});
assert.match(card, /\{currentAction \? \([\s\S]*?<\/button>\s*\) : historyNotice \? \([\s\S]*?className="turn-files-history-notice"[\s\S]*?title=\{historyNotice\}[\s\S]*?role="status"[\s\S]*?\{historyNotice\}[\s\S]*?\) : null\}\s*<button[\s\S]*?className="turn-files-review"/);
assert.doesNotMatch(card, /turn-files-blocked/);
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
assert.match(review, /data\.status === "stale" \|\| data\.error === "STALE_SNAPSHOT"/);
assert.match(review, /clearReviewForStale/);
assert.match(review, /staleRecoveryRef/);
assert.match(review, /setRefreshNonce\(\(value\) => value \+ 1\)/);
assert.match(review, /setSelectedPath\(""\);\s*setFileCursor\(null\);\s*setDiffCursor\(null\);\s*setDiffHistory\(\[\]\)/);
assert.match(review, /status: "stale"/);
assert.match(review, /data\.error === "STALE_CURSOR"/);
assert.match(review, /diffCursorRecoveryRef/);
assert.match(review, /data\.error === "STALE_CURSOR"[\s\S]*?setDiffCursor\(null\);\s*setDiffHistory\(\[\]\)/);
assert.match(review, /REVIEW_REQUEST_TIMEOUT_MS/);
assert.match(review, /addEventListener\("close", onClose\)/);
assert.match(review, /Review request timed out/);
assert.match(review, /Diff request timed out/);
assert.doesNotMatch(review, /queryInput|sortSelect|<input[\s\S]*Filter files|<select[\s\S]*Sort files/);
assert.match(cardCss, /\.turn-files-summary\{height:40px;min-height:40px/);
assert.match(cardCss, /\.turn-files-card\{container:turn-files\/inline-size/);
assert.match(cardCss, /\.turn-files-card\{[^}]*font-family:var\(--font-sans\)/);
assert.match(cardCss, /\.turn-files-summary\{[^}]*gap:12px[^}]*padding:4px 10px 4px 12px/);
assert.match(cardCss, /\.turn-files-summary\{[^}]*background:var\(--bg-tertiary\)/);
assert.match(cardCss, /\.turn-files-logo\{width:19px;height:19px/);
assert.match(cardCss, /\.turn-files-heading\{[^}]*align-items:center/);
assert.match(cardCss, /\.turn-files-heading\{[^}]*gap:12px/);
assert.match(cardCss, /\.turn-files-count\{[^}]*font-size:14px/);
assert.match(cardCss, /\.turn-files-action,\.turn-files-review\{[^}]*font-size:13px/);
assert.match(cardCss, /\.turn-files-list\{padding:4px;background:var\(--bg-primary\)/);
assert.match(cardCss, /\.turn-files-row\{[^}]*height:28px[^}]*padding:2px 6px/);
assert.match(cardCss, /grid-template-columns:minmax\(0,1fr\) 8ch 8ch/);
assert.match(cardCss, /\.turn-files-row>\.turn-files-stat\{text-align:right/);
assert.match(cardCss, /\.turn-files-row\{[^}]*background:transparent/);
assert.match(cardCss, /\.turn-files-row:hover\{[^}]*background:var\(--bg-hover\)/);
assert.match(cardCss, /\.turn-files-name\{[^}]*font-size:13px/);
assert.match(cardCss, /\.turn-files-stat\{[^}]*font-size:12px[^}]*font-variant-numeric:tabular-nums/);
assert.doesNotMatch(cardCss, /\.turn-files-name\{[^}]*var\(--font-mono\)/);
assert.doesNotMatch(cardCss, /\.turn-files-stat\{[^}]*var\(--font-mono\)/);
assert.doesNotMatch(cardCss, /\.turn-files-op\{[^}]*var\(--font-mono\)/);
assert.match(cardCss, /\.turn-files-more\{[^}]*background:var\(--bg-tertiary\)[^}]*font-size:13px/);
assert.match(cardCss, /\.turn-files-more:hover\{[^}]*background:var\(--bg-hover\)/);
assert.match(cardCss, /\.turn-files-history-notice\{display:block;min-width:0;max-width:min\(48ch,40cqi\);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var\(--accent-orange\);font-size:12px;line-height:1\.2\}/);
assert.doesNotMatch(cardCss, /\.turn-files-blocked/);
assert.match(cardCss, /:where\(\[data-theme-mode="light"\]\) \.turn-files-card,:where\(\[data-theme-mode="light"\]\) \.turn-files-summary,:where\(\[data-theme-mode="light"\]\) \.turn-files-review,:where\(\[data-theme-mode="light"\]\) \.turn-files-more\{background:var\(--bg-primary\);box-shadow:none\}/);
assert.match(cardCss, /\.turn-files-meter/);
assert.match(cardCss, /@media\(max-width:420px\)/);
assert.match(cardCss, /@media\(max-width:420px\)\{\.turn-files-summary\{gap:6px;padding-inline:6px\}\.turn-files-heading\{gap:5px\}/);
assert.match(cardCss, /@container turn-files \(max-width:420px\)\{\.turn-files-summary\{gap:6px;padding-inline:6px\}\.turn-files-heading\{gap:5px\}/);
assert.match(cardCss, /@media\(max-width:420px\)[^\n]*\.turn-files-history-notice\{max-width:92px\}/);
assert.match(cardCss, /@container turn-files \(max-width:420px\)[^\n]*\.turn-files-history-notice\{max-width:92px\}/);
assert.doesNotMatch(card, /Review all \$\{fileCount\} files|审阅全部 \$\{fileCount\} 个文件/);
assert.match(design, /\.change-summary\{height:40px;[^}]*background:var\(--bg-tertiary\)/);
assert.match(design, /\.change-card-demo\{container:change-card\/inline-size/);
assert.match(design, /\.change-card-demo\{[^}]*font-family:var\(--font-sans\)/);
assert.match(design, /\.change-summary\{[^}]*gap:12px[^}]*padding:4px 10px 4px 12px/);
assert.match(design, /@media\(max-width:420px\)\{\.change-summary\{[^}]*gap:6px;padding-inline:6px\}/);
assert.match(design, /@container change-card \(max-width:420px\)\{\.change-summary\{[^}]*gap:6px;padding-inline:6px\}/);
assert.match(design, /\.change-summary-title\{[^}]*font-size:14px/);
assert.match(design, /\.change-summary-stat\{[^}]*font:12px/);
assert.match(design, /\.change-summary-stat\{[^}]*var\(--font-sans\)[^}]*font-variant-numeric:tabular-nums/);
assert.match(design, /\.change-group\{padding:4px;background:var\(--bg-primary\)/);
assert.match(design, /\.change-row\{height:28px[^}]*padding:2px 6px/);
assert.match(design, /\.change-row-name\{[^}]*font:13px/);
assert.match(design, /\.change-row-name\{[^}]*var\(--font-sans\)/);
assert.match(design, /\.change-row-counts\{[^}]*grid-template-columns:8ch 8ch[^}]*font:12px[^}]*var\(--font-sans\)[^}]*font-variant-numeric:tabular-nums/);
assert.match(design, /<button class="change-collapse" type="button">Collapse<\/button>/);
assert.match(design, /\.change-collapse\{[^}]*background:var\(--bg-tertiary\)[^}]*font:13px/);
assert.match(design, /\.change-collapse:hover\{[^}]*background:var\(--bg-hover\)/);
assert.match(design, /\.change-summary-notice\{display:block;min-width:0;max-width:min\(48ch,40cqi\);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var\(--accent-orange\);font:12px\/1\.2 var\(--font-sans\)\}/);
assert.match(design, /<span class="change-summary-actions"><span class="change-summary-notice" title="current file state does not match the recorded source" role="status">current file state does not match the recorded source<\/span><button class="change-summary-action primary" type="button">Review<\/button><\/span>/);
assert.doesNotMatch(design, /<span class="change-summary-actions"><button class="change-summary-action"[^>]*>[\s\S]*?<span>Undo<\/span>/);
assert.match(design, /@media\(prefers-color-scheme:light\)\{\.change-card-demo,\.change-summary,\.change-summary-action\.primary,\.change-collapse\{background:var\(--bg-primary\);box-shadow:none\}\}/);
assert.doesNotMatch(design, /data-theme-mode="light"[^}]*\.change-card-demo/);
assert.doesNotMatch(
  cardCss,
  /@media\(max-width:420px\)[\s\S]*?turn-files-logo[^}]*?(?:width|height):17px/,
);

console.log("check-review-ui: ok");
