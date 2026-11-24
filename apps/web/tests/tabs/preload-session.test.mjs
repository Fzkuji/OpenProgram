// Opening a conversation acts on the click itself: one this client already
// holds becomes current at once (drawn from its rows, no read here); any other
// is asked for.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";

const state = globalThis.__preload = { sent: [], pages: {}, order: {}, current: null, made: [] };
installLoader({ stubs: {
  "@/lib/chat/history/session-history": "export const holdsHistoryWindow=p=>!!p; export const useSessionHistory={getState:()=>({pages:globalThis.__preload.pages})};",
  "@/lib/runtime-bridge/session/session-load": "export const requestSessionLoad=p=>{globalThis.__preload.sent.push(p);return true;};",
  "@/lib/session-store": "export const useSessionStore={getState:()=>({messageOrder:globalThis.__preload.order,currentSessionId:globalThis.__preload.current,setCurrentConv:id=>{globalThis.__preload.made.push(id);globalThis.__preload.current=id;}})};",
  "@/lib/tabs/center-tabs-store": "export const isDraftSession=id=>id.startsWith('draft');",
} });
const { preloadSession, sessionHeld } = await import("../../lib/tabs/navigation/preload-session.ts");

beforeEach(() => Object.assign(state, { sent: [], pages: {}, order: {}, current: null, made: [] }));

test("a conversation not held yet is read on the click", () => {
  preloadSession("s1");
  assert.deepEqual(state.sent, [{ action: "load_session", session_id: "s1" }]);
});

test("a held conversation becomes current on the click and is not read here", () => {
  state.pages.s1 = {};
  state.order.s1 = ["m1"];
  assert.equal(sessionHeld("s1"), true);
  preloadSession("s1");
  assert.deepEqual(state.made, ["s1"]);
  assert.equal(state.current, "s1");
  assert.deepEqual(state.sent, []);
});

test("the one on screen and a draft are left alone", () => {
  state.current = "s2";
  preloadSession("s2");
  preloadSession("draft-1");
  preloadSession("");
  assert.deepEqual(state.sent, []);
  assert.deepEqual(state.made, []);
});

test("a window without rows is not held", () => {
  state.pages.s1 = {};
  assert.equal(sessionHeld("s1"), false);
  preloadSession("s1");
  assert.equal(state.sent.length, 1);
  assert.deepEqual(state.made, []);
});
