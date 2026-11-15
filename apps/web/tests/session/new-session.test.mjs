import assert from "node:assert/strict";
import test from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

// newSession runs before the SPA navigation to /chat. CenterTabStrip watches
// that route together with the current session id, so both session sources
// must already be cleared, and the claimed draft selected, by the time the
// history entry is pushed; otherwise the strip replaces the new draft tab
// with the stale session.
installLoader({ cssModules: "empty" });
installDom({ pathname: "/s/old-session" });

const { newSession } = await import("../../lib/runtime-bridge/session/conversations.ts");
const { runtimeState } = await import("../../lib/runtime-bridge/state.ts");
const { useSessionStore } = await import("../../lib/session-store/index.ts");

globalThis.fetch = async () => new Response("{}", { headers: { "content-type": "application/json" } });

function captureAtNavigation() {
  const seen = [];
  window.history.pushState = (_state, _title, path) => {
    const store = useSessionStore.getState();
    seen.push({
      path,
      runtimeSessionId: runtimeState.currentSessionId,
      storeSessionId: store.currentSessionId,
      activeChatKey: store.activeChatKey,
    });
  };
  return seen;
}

test("newSession with a claimed draft clears both session sources and selects the draft before navigating", () => {
  runtimeState.currentSessionId = "old-session";
  useSessionStore.setState({ currentSessionId: "old-session", activeChatKey: null });
  const seen = captureAtNavigation();

  newSession("draft-1");

  assert.equal(seen.length, 1, "one navigation to /chat");
  assert.deepEqual(seen[0], {
    path: "/chat",
    runtimeSessionId: null,
    storeSessionId: null,
    activeChatKey: "draft-1",
  });
});

test("newSession without a draft leaves no current conversation when it navigates", () => {
  runtimeState.currentSessionId = "old-session";
  useSessionStore.setState({ currentSessionId: "old-session", activeChatKey: null });
  const seen = captureAtNavigation();

  newSession();

  assert.equal(seen.length, 1);
  assert.equal(seen[0].path, "/chat");
  assert.equal(seen[0].runtimeSessionId, null);
  assert.equal(seen[0].storeSessionId, null);
});
