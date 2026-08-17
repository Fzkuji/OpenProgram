import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const memoryPage = readFileSync(
  new URL("../components/memory/index.tsx", import.meta.url),
  "utf8",
);
const memoryCss = readFileSync(
  new URL("../components/memory/memory-page.module.css", import.meta.url),
  "utf8",
);
const memoryMarkdown = readFileSync(
  new URL("../components/memory/markdown.ts", import.meta.url),
  "utf8",
);
assert.doesNotMatch(memoryPage, /styles\.writerStatus/);
assert.doesNotMatch(memoryPage, /pending turns/);
assert.doesNotMatch(memoryCss, /\.writerStatus/);
assert.doesNotMatch(memoryPage, /\/api\/memory\/status/);
assert.doesNotMatch(memoryPage, /Commitments|commitments/);
assert.doesNotMatch(memoryCss, /commitment/);
assert.match(memoryPage, /useState<"injected" \| "records">\("injected"\)/);
assert.match(memoryPage, /renderedTokens/);
assert.match(memoryPage, /topics\/core\.md/);
assert.match(memoryPage, /styles\.coreViewSwitch/);
assert.match(memoryCss, /\.coreViewSwitch\s*\{/);
assert.match(memoryPage, /data\.injected_content/);
assert.match(memoryPage, /injectionEnabled/);
assert.match(memoryPage, /const submittedContent = coreEditor\.content/);
assert.match(memoryPage, /fetchCore\(submittedContent\)/);
assert.match(memoryPage, /e\.content === submittedContent/);
assert.match(memoryPage, /coreSaveStatus\(e\.content, submittedContent, r\.ok\)/);
assert.match(memoryMarkdown, /sanitizeHtml\(marked\.parse\(/);

console.log("check-memory-status: ok");
