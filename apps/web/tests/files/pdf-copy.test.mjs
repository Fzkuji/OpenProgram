import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizePdfCopy } from "../../lib/documents/pdf-copy.ts";
test("PDF copy joins layout wraps and preserves paragraphs", () => {
  assert.equal(normalizePdfCopy("A paper\nuses several\nvisual lines.\n\nSecond paragraph."), "A paper uses several visual lines.\n\nSecond paragraph.");
});
test("PDF copy preserves ordinary hyphens, math, and Unicode", () => {
  assert.equal(normalizePdfCopy("self-\nassessment and dis\u00ad\ntribution\nα + β = 2"), "self-assessment and distribution α + β = 2");
  assert.equal(normalizePdfCopy("中文论文\n阅读。\n\n下一段。"), "中文论文阅读。\n\n下一段。");
  assert.equal(normalizePdfCopy("a\r\nb\r\nc"), "a b c");
});
