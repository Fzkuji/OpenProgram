import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const memoryPage = readFileSync(
  new URL("../components/memory/index.tsx", import.meta.url),
  "utf8",
);
assert.match(memoryPage, /\/api\/memory\/commitments\/transition/);
assert.match(memoryPage, /"done"/);
assert.match(memoryPage, /"dismissed"/);

console.log("check-memory-status: ok");
