/**
 * A conversation made with "Copy conversation" opens with a "Copied from
 * <title>" banner that links back to the source.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/tabs/navigation/open-session": "export const openSession = (id) => globalThis.__opened.push(id);",
  },
});
installDom({ pathname: "/s/copy-1" });
globalThis.__opened = [];

const { createElement: h } = await import("react");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { runtimeState } = await import("../../../lib/runtime-bridge/state.ts");
const { BranchedFromBanner } = await import("../../../components/chat/messages/cards/branched-from-banner.tsx");

let view;
afterEach(async () => { await view?.unmount(); view = undefined; });

test("the banner names the source and opens it", async () => {
  runtimeState.conversations["copy-1"] = { id: "copy-1", branched_from: { session_id: "src", msg_id: "r1", title: "Trip plan", exists: true } };
  useSessionStore.setState({ currentSessionId: "copy-1", conversations: {} });
  view = await render(h(BranchedFromBanner));
  assert.match(view.container.textContent, /Copied from Trip plan/);
  assert.doesNotMatch(view.container.textContent, /Branched/);
  const open = [...view.container.querySelectorAll("button")].find((b) => b.textContent === "Open original");
  await view.act(async () => open.click());
  assert.deepEqual(globalThis.__opened, ["src"]);
});

test("a conversation that is not a copy has no banner", async () => {
  runtimeState.conversations["plain"] = { id: "plain" };
  useSessionStore.setState({ currentSessionId: "plain", conversations: {} });
  view = await render(h(BranchedFromBanner));
  assert.equal(view.container.textContent, "");
});
