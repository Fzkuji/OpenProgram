import assert from "node:assert/strict";
import fs from "node:fs";

const card = fs.readFileSync("apps/web/components/chat/messages/turn-files-chips.tsx", "utf8");
const bubble = fs.readFileSync("apps/web/components/chat/messages/assistant-bubble.tsx", "utf8");
const rail = fs.readFileSync("apps/web/components/chat/messages/message-rail.tsx", "utf8");
const review = fs.readFileSync("apps/web/components/center-tabs/review-tab-pane.tsx", "utf8");
const store = fs.readFileSync("apps/web/lib/state/center-tabs-store.ts", "utf8");

assert.match(card, /if \(embedded\) \{\s*setFiles\(embedded\);\s*return;/);
assert.match(bubble, /summary=\{msg\.turnFiles\}/);
assert.match(rail, /summary=\{assistantTurnFiles\}/);
assert.doesNotMatch(card, /turn_file_diff|UnifiedDiff|aria-expanded/);
assert.match(card, /FeatherIcon/);
assert.match(card, /text\("Undo"/);
assert.match(card, /text\("Redo"/);
assert.match(card, /openReviewTab/);
assert.match(store, /openReviewTab:/);
assert.equal((review.match(/<UnifiedDiff/g) ?? []).length, 1);
assert.match(review, /data-mounted-diff-count=\{selectedPath \? "1" : "0"\}/);
assert.match(review, /action: "review_scope"/);
assert.match(review, /action: "review_file_diff"/);

console.log("check-review-ui: ok");
