import assert from "node:assert/strict";
import test from "node:test";

import { checksLabel, fixChecksPrompt } from "../../../lib/git/pr-checks.ts";

const en = (value) => value;

test("the checks line leaves out empty counts", () => {
  assert.equal(checksLabel({ total: 4, passed: 3, pending: 0, failed_count: 1, failed: [] }, en), "Checks: 1 failing · 3 passed");
  assert.equal(checksLabel({ total: 2, passed: 0, pending: 2, failed_count: 0, failed: [] }, en), "Checks: 2 running");
});

test("the fix prompt names the failing checks and the pull request", () => {
  const pr = {
    number: 12, url: "https://example.test/pr/12",
    checks: { total: 13, passed: 0, pending: 0, failed_count: 12, failed: [{ name: "lint", url: null }, { name: "unit", url: "u" }] },
  };
  const prompt = fixChecksPrompt(pr, en);
  assert.match(prompt, /^Pull request #12 \(https:\/\/example\.test\/pr\/12\) has failing CI checks: lint, unit and others\./);
  assert.match(prompt, /push the fix to this branch\.$/);
});
