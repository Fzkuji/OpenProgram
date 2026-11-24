/**
 * A conversation made with "Copy conversation" opens with a "Copied from
 * <title>" banner that links back to the source; one made with "Fork from
 * here" reads "Branched from <title> · at “<prompt>”" and goes back to
 * that prompt in the source.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installLoader } from "../../../_support/load.mjs";
import { installDom, render } from "../../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/tabs/navigation/open-session": "export const openSession = (id) => globalThis.__opened.push(id);",
    "@/lib/chat/history/reveal-message": "export const openSessionAtMessage = (id, msg) => globalThis.__openedAt.push([id, msg]);",
  },
});
installDom({ pathname: "/s/copy-1" });
globalThis.__opened = [];
globalThis.__openedAt = [];

const { createElement: h } = await import("react");
const { useSessionStore } = await import("../../../../lib/session-store/index.ts");
const { runtimeState } = await import("../../../../lib/runtime-bridge/state.ts");
const { BranchedFromBanner } = await import("../../../../components/chat/messages/cards/branched-from-banner.tsx");

let view;
afterEach(async () => {
  await view?.unmount(); view = undefined;
  globalThis.__opened = []; globalThis.__openedAt = [];
});

const button = (label) => [...view.container.querySelectorAll("button")].find((b) => b.textContent === label);

test("a whole copy names the source and opens it", async () => {
  runtimeState.conversations["copy-1"] = { id: "copy-1", branched_from: { session_id: "src", msg_id: null, title: "Trip plan", exists: true } };
  useSessionStore.setState({ currentSessionId: "copy-1", conversations: {} });
  view = await render(h(BranchedFromBanner));
  assert.match(view.container.textContent, /Copied from Trip plan/);
  assert.doesNotMatch(view.container.textContent, /Branched/);
  await view.act(async () => button("Open original").click());
  assert.deepEqual(globalThis.__opened, ["src"]);
  assert.deepEqual(globalThis.__openedAt, []);
});

test("a fork quotes the prompt it branched at and goes back to it", async () => {
  runtimeState.conversations["fork-1"] = { id: "fork-1", branched_from: {
    session_id: "src", msg_id: "r1", title: "Kyoto trip planning", exists: true,
    at_msg_id: "u1", msg_preview: "Check current flight prices…",
  } };
  useSessionStore.setState({ currentSessionId: "fork-1", conversations: {} });
  view = await render(h(BranchedFromBanner));
  assert.equal(view.container.querySelector("strong").textContent, "Kyoto trip planning");
  assert.match(view.container.textContent, /Branched from Kyoto trip planning · at “Check current flight prices…”/);
  assert.equal(button("Open original"), undefined);
  await view.act(async () => button("Back to original").click());
  assert.deepEqual(globalThis.__openedAt, [["src", "u1"]]);
});

test("a fork whose prompt is gone only opens the source", async () => {
  runtimeState.conversations["fork-2"] = { id: "fork-2", branched_from: {
    session_id: "src", msg_id: "r1", title: "Kyoto trip planning", exists: true, at_msg_id: null, msg_preview: null,
  } };
  useSessionStore.setState({ currentSessionId: "fork-2", conversations: {} });
  view = await render(h(BranchedFromBanner));
  assert.match(view.container.textContent, /^Branched from Kyoto trip planningBack to original$/);
  await view.act(async () => button("Back to original").click());
  assert.deepEqual(globalThis.__openedAt, [["src", null]]);
});

test("a conversation that is not a copy has no banner", async () => {
  runtimeState.conversations["plain"] = { id: "plain" };
  useSessionStore.setState({ currentSessionId: "plain", conversations: {} });
  view = await render(h(BranchedFromBanner));
  assert.equal(view.container.textContent, "");
});
