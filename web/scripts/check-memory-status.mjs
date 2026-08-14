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
assert.doesNotMatch(memoryPage, /\/api\/memory\/status/);
assert.doesNotMatch(memoryPage, /Commitments|commitments/);
assert.doesNotMatch(memoryCss, /commitment/);

console.log("check-memory-status: ok");
