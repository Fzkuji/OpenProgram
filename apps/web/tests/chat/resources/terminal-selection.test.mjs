import assert from "node:assert/strict";
import test from "node:test";

import { terminalSelectionText } from "../../../lib/chat/resources/terminal-selection.ts";

test("a terminal selection becomes a fenced block without trailing blank lines", () => {
  assert.equal(terminalSelectionText("$ ls\nREADME.md\n\n  "), "From the terminal:\n```text\n$ ls\nREADME.md\n```");
});

test("a selection holding a fence gets a longer fence", () => {
  assert.equal(terminalSelectionText("```js\nx\n```"), "From the terminal:\n````text\n```js\nx\n```\n````");
});
