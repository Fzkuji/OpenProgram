import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { messageArrivedWithin, noteMessageArrival, rekeyMessageArrival } from "../../../lib/session-store/messages/message-arrivals.ts";

test("Only a message that just arrived counts as new", () => {
  assert.equal(messageArrivedWithin("loaded-with-history", 1000), false);
  noteMessageArrival("local-1");
  assert.equal(messageArrivedWithin("local-1", 1000), true);
  assert.equal(messageArrivedWithin("local-1", 0), false);
});

test("A local message keeps its arrival when the server renames it", () => {
  noteMessageArrival("local-2");
  rekeyMessageArrival("local-2", "server-2");
  assert.equal(messageArrivedWithin("local-2", 1000), false);
  assert.equal(messageArrivedWithin("server-2", 1000), true);
});

test("Rows that mount again do not fade up from nothing", () => {
  const css = readFileSync(new URL("../../../app/styles/chat/pane/transcript.css", import.meta.url), "utf8");
  assert.match(css, /\.msg-slot:not\(\[data-appear\]\) \.message,\n\.msg-row:not\(\[data-appear\]\) \.message/);
});
