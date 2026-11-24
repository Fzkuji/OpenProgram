// "Back to original" on a fork: open the source, and once its transcript is
// on screen scroll to the prompt the fork branched at and flash it. A prompt
// that is not there any more leaves the conversation simply open.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/tabs/navigation/open-session": "export const openSession = (id) => globalThis.__opened.push(id);",
    "@/lib/runtime-bridge/session/session-history-loader":
      "export const loadSessionHistoryWindow = async (...args) => { globalThis.__pages.push(args); return true; };",
  },
});
installDom({
  pathname: "/s/fork",
  html: '<!doctype html><html><body><div id="chatArea"><div id="chatMessages"></div></div></body></html>',
});
globalThis.__opened = [];
globalThis.__pages = [];
globalThis.CSS ??= { escape: (v) => v };
window.CSS ??= globalThis.CSS;
globalThis.requestAnimationFrame ??= (fn) => setTimeout(fn, 0);
window.HTMLElement.prototype.scrollIntoView ??= function scrollIntoView() {};

const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { openSessionAtMessage } = await import("../../../lib/chat/history/reveal-message.ts");
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("opens the source, then reveals and flashes the prompt once it is read", async () => {
  useSessionStore.setState({
    currentSessionId: "fork", transcriptLoadingId: null,
    transcriptReadStatus: {}, messageOrder: { src: ["u1", "r1"] },
  });
  document.getElementById("chatMessages").innerHTML =
    '<div data-msg-id="u1"></div><div data-msg-id="r1"></div>';
  openSessionAtMessage("src", "u1");
  assert.deepEqual(globalThis.__opened, ["src"]);
  // Still reading the source: nothing moves yet.
  useSessionStore.setState({ currentSessionId: "src", transcriptReadStatus: { src: "loading" } });
  await wait(300);
  assert.equal(document.querySelector(".dag-flash"), null);
  useSessionStore.setState({ transcriptReadStatus: { src: "ready" } });
  await wait(300);
  assert.equal(document.querySelector(".dag-flash")?.getAttribute("data-msg-id"), "u1");
  assert.deepEqual(globalThis.__pages, []);
});

test("without a prompt it only opens the source", async () => {
  globalThis.__opened = [];
  openSessionAtMessage("src", null);
  assert.deepEqual(globalThis.__opened, ["src"]);
});
