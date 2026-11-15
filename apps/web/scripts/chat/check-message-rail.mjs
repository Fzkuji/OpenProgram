/**
 * Rule: the message rail never subscribes to the whole `messagesById` map
 * (rebuilt on every streaming token, so the rail would re-render and
 * re-derive per delta); it selects packed rows through `useShallow`.
 * Behaviour (row packing, ticks, seeking) is in
 * tests/chat/messages/message-rail-row.test.mjs.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const rail = readFileSync(
  new URL("../../components/chat/messages/navigation/message-rail.tsx", import.meta.url),
  "utf8",
);

assert.doesNotMatch(
  rail,
  /useSessionStore\(\(s\) => s\.messagesById\)/,
  "message-rail subscribes to the whole messagesById map; that re-renders the rail on every streaming token",
);
assert.match(rail, /useSessionStore\(\s*useShallow\(/, "message-rail lost its useShallow subscription");

console.log("check-message-rail: ok");
