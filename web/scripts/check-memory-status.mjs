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
assert.doesNotMatch(memoryPage, /styles\.writerStatus/);
assert.doesNotMatch(memoryPage, /pending turns/);
assert.doesNotMatch(memoryCss, /\.writerStatus/);
assert.match(memoryPage, /fetch\("\/api\/memory\/status"\)/);
assert.match(memoryPage, /base_revision: memoryStatus\.revision/);
assert.match(memoryPage, /\/api\/memory\/commitments\/transition/);
assert.match(memoryPage, /"done"/);
assert.match(memoryPage, /"dismissed"/);

console.log("check-memory-status: ok");
