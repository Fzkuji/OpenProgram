// A backend-run command from /api/commands (type "local": /goal, /rename …)
// leaves through the submitting composer's own send, never as a bare
// `{action:"chat", text}` frame. In a "New chat" draft the menu has no
// session id, and that bare frame made the server open a conversation of
// its own while the draft tab stayed empty.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../../_support/load.mjs";
import { installDom, render } from "../../../_support/dom.mjs";

installLoader({ stubs: {
  "@/lib/chat/transcript/apply-read": "export const appendLocalNotice = () => {};",
  // A draft tab: no conversation id yet.
  "@/lib/session-store": [
    "const state = { currentSessionId: null, setComposerInputFor() {}, setComposerInput() {} };",
    "export const useSessionStore = (select) => select(state);",
    "useSessionStore.getState = () => state;",
  ].join("\n"),
  "@/lib/abilities/skills-store": [
    "const state = { skills: [{ name: 'x' }], fetchSkills() {} };",
    "export const useSkills = (select) => select(state);",
  ].join("\n"),
  "@/lib/tabs/center-tabs-store": "export const useCenterTabs = { getState: () => ({ openDraftSessionTab: () => 'local_x' }) };",
  "@/lib/runtime-bridge/session/conversations": "export const newSession = () => {};",
  "@/lib/format-utils/toast": "export const showToast = () => {};",
  "@/lib/i18n": "export const translateText = (en) => en;",
  "@/lib/chat/rewind/rewind-actions": "export const openRewindPicker = () => {};",
} });
installDom();
globalThis.fetch = async (url) => ({
  ok: true,
  json: async () => (String(url) === "/api/commands"
    ? { commands: [{ name: "goal", source: "builtin", source_label: "Built-in", description: "Goal",
      argument_hint: "", user_invocable: true, hidden: false, type: "local" }] }
    : {}),
});

const { createElement, useRef } = await import("react");
const { useSlashMenu } = await import("../../../../components/chat/composer/slash/use-slash-menu.ts");

async function mountMenu(boundSessionId = null) {
  const frames = [];
  const hook = {};
  function Probe() {
    const textareaRef = useRef(null);
    hook.current = useSlashMenu({
      input: "", textareaRef, boundSessionId,
      send: (frame) => { frames.push(frame); return true; },
    });
    return null;
  }
  const view = await render(createElement(Probe));
  await view.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  return { hook, frames, view };
}

test("a local command from a draft goes through the composer's send, not a bare frame", async () => {
  const { hook, frames, view } = await mountMenu();
  const sent = [];
  const outcome = hook.current.runCommand("/goal ship it", {
    busy: false,
    sendChat: () => assert.fail("not a chat-type command"),
    sendCommand: (text) => { sent.push(text); return true; },
  });
  assert.equal(outcome, "ran");
  assert.deepEqual(sent, ["/goal ship it"]);
  assert.deepEqual(frames, []);
  await view.unmount();
});

test("a send the socket refused keeps the command in the box", async () => {
  const { hook, view } = await mountMenu("B");
  const outcome = hook.current.runCommand("/goal status", {
    busy: false, sendChat: () => true, sendCommand: () => false,
  });
  assert.equal(outcome, "failed");
  await view.unmount();
});
