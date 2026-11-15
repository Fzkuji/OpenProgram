// How transcript rows draw from what they own: open steps survive a rebuild,
// a finished run shows no spinner, and a sub-agent's component does not depend
// on how it was started.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  replace: {
    "/components/chat/messages/turn-files/turn-files-chips.tsx": "export const TurnFilesChips = () => null;",
  },
});
installDom({ pathname: "/chat" });
const { act, createElement: h } = await import("react");
const { selectRow, useSessionStore } = await import("../../../lib/session-store/index.ts");
const { transcriptRows } = await import("../../_support/transcript-rows.mjs");
const rows = transcriptRows(useSessionStore, selectRow);
const { MessageRow } = await import("../../../components/chat/messages/list/rows/message-rows.tsx");
const { RuntimeBlock } = await import("../../../components/chat/messages/runtime/runtime-block.tsx");

const SID = "s1";
const seed = (...seeded) => useSessionStore.setState({
  currentSessionId: SID,
  rowsByView: { [SID]: Object.fromEntries(seeded.map((row) => [row.id, row])) },
  messageOrder: { [SID]: seeded.map((row) => row.id) },
  stepExpansion: {},
});
const row = (id, fields) => ({ id, role: "assistant", rowKind: "assistant", content: "", status: "done", timestamp: 1, ...fields });
const tool = (id, name = "lookup") => ({ type: "tool", tool: name, tool_call_id: id, input: "{}", result: "ok" });
const draw = (id) => render(h(MessageRow, { id, sessionIdOverride: SID }));
const update = (id, fields) => act(async () => {
  rows.patchRow(SID, id, fields);
});
const click = (element) => act(async () => { element.click(); });
const strip = (view) => view.container.querySelector(".tl");

beforeEach(() => seed());

test("an open strip stays open when the final write rebuilds the blocks in front of it", async () => {
  seed(row("r", { status: "streaming", blocks: [{ type: "text", text: "a" }, { type: "thinking", text: "t" }, tool("c1")] }));
  const view = await draw("r");
  await click(view.container.querySelector(".tl-toggle"));
  assert.equal(strip(view).getAttribute("data-open"), "1");
  const before = strip(view);
  await update("r", { status: "done", blocks: [{ type: "text", text: "a" }, { type: "text", text: "a2" }, { type: "thinking", text: "t" }, tool("c1"), { type: "text", text: "b" }] });
  assert.equal(strip(view), before, "the strip keeps its element");
  assert.equal(strip(view).getAttribute("data-open"), "1");
  await view.unmount();
});

test("an open step stays open across a rebuild and when its row is drawn again", async () => {
  seed(row("r", { blocks: [{ type: "thinking", text: "deep thought" }, tool("c1")] }));
  let view = await draw("r");
  await click(view.container.querySelector(".tl-toggle"));
  await click(view.container.querySelector(".tl-step-head"));
  const stepOpen = () => view.container.querySelector(".tl-step").classList.contains("open");
  assert.equal(stepOpen(), true);
  await update("r", { blocks: [{ type: "thinking", text: "deep thought" }, tool("c1"), { type: "text", text: "end" }] });
  assert.equal(stepOpen(), true);
  await view.unmount();
  view = await draw("r");
  assert.equal(strip(view).getAttribute("data-open"), "1");
  assert.equal(stepOpen(), true);
  await view.unmount();
});

test("a step opened before its card arrives stays open as the sub-agent step", async () => {
  seed(row("r", { status: "streaming", blocks: [tool("c1", "agent")] }));
  useSessionStore.getState().setStepExpansion(SID, "r\u0000c1", true);
  useSessionStore.getState().setStepExpansion(SID, "r\u0000exec:c1", true);
  const view = await draw("r");
  await update("r", { attachCards: [row("card", { rowKind: "sub_agent_step", blockId: "c1", function: "attach", display: "runtime", attach: { label: "x", status: "running", tool_call_id: "c1" } })] });
  const step = view.container.querySelector('.tl-step[data-msg-id="card"]');
  assert.ok(step, "the card draws on its block");
  assert.equal(step.classList.contains("open"), true);
  await view.unmount();
});

const card = (view) => view.container.querySelector(".attach-card");
const cardRow = (view) => view.container.querySelector(".attach-card-row");

test("an agent's card is one row until it is opened", async () => {
  seed({ ...attach("ref", { manual: true, prompt: "Run the tests\nand report", usage: { duration_s: 41, cost_usd: "0.020000", tokens: 18400, tool_calls: 12 },
    files: { count: 2, branch: "pr-verifier-7f2a" } }, "external_reference"), content: "All 48 tests pass." });
  const view = await draw("ref");
  assert.equal(card(view).getAttribute("data-expanded"), "0");
  assert.equal(cardRow(view).getAttribute("aria-expanded"), "false");
  assert.equal(view.container.querySelector(".attach-card-name").textContent, "helper");
  assert.equal(view.container.querySelector(".attach-card-task").textContent, "Run the tests");
  assert.equal(view.container.querySelector(".attach-card-facts").textContent, "41 s · $0.02");
  assert.match(cardRow(view).textContent, /Done/);
  assert.equal(view.container.querySelector(".attach-card-body"), null);
  assert.equal(view.container.querySelector(".attach-card-open"), null);
  await click(cardRow(view));
  assert.equal(card(view).getAttribute("data-expanded"), "1");
  assert.match(view.container.querySelector(".attach-card-preview").textContent, /All 48 tests pass\./);
  assert.match(view.container.querySelector(".attach-card-files").textContent, /2 files changed/);
  assert.equal(view.container.querySelector(".attach-card-files code").textContent, "pr-verifier-7f2a");
  const go = view.container.querySelector(".attach-card-open");
  assert.match(go.textContent, /^Switch/);
  assert.equal(go.getAttribute("aria-label"), "Switch to this branch");
  await click(cardRow(view));
  assert.equal(view.container.querySelector(".attach-card-body"), null);
  await view.unmount();
});

test("a card that failed or waits for approval says so on its closed row", async () => {
  seed(attach("bad", { session_id: "other", status: "errored" }, "external_reference"),
    attach("asks", { session_id: "other", status: "running", execution_id: "job-9", status_version: 1 }, "external_reference"));
  let view = await draw("bad");
  assert.equal(card(view).getAttribute("data-state"), "failed");
  assert.match(cardRow(view).textContent, /Failed/);
  await view.unmount();
  view = await draw("asks");
  assert.match(cardRow(view).textContent, /Running/);
  await act(async () => {
    useSessionStore.setState({ pendingDecisions: [{ id: "q1", sessionId: SID, executionId: "job-9", waitGeneration: 1, expectedVersion: 1,
      kind: "approval", prompt: "[helper] run it?", options: [], multi: false, allow_custom: false }] });
  });
  assert.equal(card(view).getAttribute("data-state"), "needs_approval");
  assert.match(cardRow(view).textContent, /Needs approval/);
  // The badge is the Activity roster's: outlined, with the yellow dot.
  const badge = cardRow(view).querySelector("[data-slot=badge]");
  assert.equal(badge.getAttribute("data-variant"), "outline");
  assert.ok(badge.querySelector(".indicator-dot.--warn"));
  assert.equal(view.container.querySelector(".attach-card-body"), null);
  await act(async () => { useSessionStore.setState({ pendingDecisions: [] }); });
  await view.unmount();
});

test("a running agent's card updates in place and stays open", async () => {
  seed(attach("live", { session_id: "other", status: "running", execution_id: "job-1", status_version: 1 }, "external_reference"));
  const view = await draw("live");
  await click(cardRow(view));
  const before = card(view);
  assert.ok(view.container.querySelector(".attach-card-cancel"), "a running agent can be cancelled from the open card");
  assert.equal(view.container.querySelector(".attach-card-preview"), null);
  await update("live", { content: "Mapped the module.", attach: { session_id: "other", head_id: "h1", label: "helper", status: "completed",
    execution_id: "job-1", status_version: 2, usage: { duration_s: 12, cost_usd: "0.010000" } } });
  assert.equal(card(view), before, "the card keeps its element");
  assert.equal(card(view).getAttribute("data-expanded"), "1");
  assert.equal(card(view).getAttribute("data-state"), "done");
  assert.equal(view.container.querySelector(".attach-card-facts").textContent, "12 s · $0.01");
  assert.match(view.container.querySelector(".attach-card-preview").textContent, /Mapped the module\./);
  assert.equal(view.container.querySelector(".attach-card-cancel"), null);
  assert.match(view.container.querySelector(".attach-card-open").textContent, /^Open/);
  await view.unmount();
});

test("a card with a long name and task keeps one row, and a cost under a cent reads as the roster's", async () => {
  const name = "一个名字特别特别长的子代理用来检查卡片这一行不会被撑开";
  seed({ ...attach("long", { manual: true, label: name, status: "completed", prompt: `${"审查整个认证模块并报告每一个问题 ".repeat(20)}\nsecond line`,
    usage: { duration_s: 3725, cost_usd: "0.004200", tokens: 18400, tool_calls: 1 } }, "external_reference"), content: "ok" });
  const view = await draw("long");
  assert.deepEqual([...cardRow(view).children].map((child) => /attach-card-[a-z]+/.exec(child.getAttribute("class") || "")?.[0]).filter(Boolean),
    ["attach-card-agent", "attach-card-name", "attach-card-task", "attach-card-facts", "attach-card-chevron"]);
  assert.equal(view.container.querySelector(".attach-card-name").textContent, name);
  assert.ok(view.container.querySelector(".attach-card-task").textContent.length <= 121, "the task is cut to one short line");
  assert.doesNotMatch(view.container.querySelector(".attach-card-task").textContent, /second line/);
  assert.match(view.container.querySelector(".attach-card-facts").textContent, / · <\$0\.01$/);
  await view.unmount();
});

const handed = (id, content) => ({ id, role: "user", rowKind: "received_message", origin: "agent_delivery", content, status: "done", timestamp: 1 });

test("a message between agents is one line that opens in place; a person's stays a bubble", async () => {
  seed(handed("in", "📨 Message from “Butler” (conv_1):\n\nCheck the deploy.\n\n(Another of your conversations handed this to you. Handle it here; your final answer goes back to it on its own.)"),
    handed("back", "↩️ Reply from “Butler” (conv_1) to the message you sent it:\n\nYes, 3 files."),
    { id: "typed", role: "user", rowKind: "user", origin: "person", content: "hello", status: "done", timestamp: 1 });
  let view = await draw("in");
  const line = view.container.querySelector('.agent-event[data-msg-id="in"]');
  assert.equal(line.querySelector(".agent-event-label").textContent, "Message from “Butler”");
  assert.equal(line.querySelector(".agent-event-detail"), null);
  assert.equal(view.container.querySelector(".message.user"), null);
  await click(line.querySelector(".agent-event-head"));
  assert.equal(line.querySelector(".agent-event-detail").textContent.trim(), "Check the deploy.");
  await view.unmount();
  view = await draw("back");
  assert.equal(view.container.querySelector(".agent-event-label").textContent, "“Butler” replied");
  await view.unmount();
  view = await draw("typed");
  assert.ok(view.container.querySelector(".message.user"));
  assert.equal(view.container.querySelector(".agent-event"), null);
  await view.unmount();
});

test("a find match inside a closed line or card opens it", async () => {
  const { revealForFind } = await import("../../../lib/chat/transcript/conversation-find.ts");
  seed(handed("back", "↩️ Reply from “Butler” (conv_1) to the message you sent it:\n\nYes, 3 files."),
    { ...attach("ref", { manual: true }, "external_reference"), content: "the needle" });
  let view = await draw("back");
  await act(async () => { assert.equal(revealForFind("back"), true); });
  assert.match(view.container.querySelector(".agent-event-detail").textContent, /Yes, 3 files\./);
  await view.unmount();
  assert.equal(revealForFind("back"), false, "an unmounted row no longer answers");
  view = await draw("ref");
  await act(async () => { assert.equal(revealForFind("ref"), true); });
  assert.match(view.container.querySelector(".attach-card-preview").textContent, /the needle/);
  await view.unmount();
});

test("the same row id in two views keeps two choices", () => {
  const state = useSessionStore.getState();
  state.setStepExpansion("a", "k", true);
  state.setStepExpansion("b", "k", false);
  const { stepExpansion } = useSessionStore.getState();
  assert.equal(stepExpansion.a.k, true);
  assert.equal(stepExpansion.b.k, false);
});

test("removing a conversation forgets its choices and its sub-agent views' choices", () => {
  const state = useSessionStore.getState();
  state.setStepExpansion("gone", "k", true);
  state.setStepExpansion("gone#sub-agent:job", "k", true);
  state.setStepExpansion("kept", "k", true);
  state.removeConversation("gone");
  assert.deepEqual(Object.keys(useSessionStore.getState().stepExpansion), ["kept"]);
});

const runtime = (fields) => render(h(RuntimeBlock, { msg: row("run", { rowKind: "function_run", display: "runtime", function: "demo", ...fields }), sessionIdOverride: SID }));
const openRun = async (view) => { await click(view.container.querySelector(".tl-toggle")); };

test("a finished runtime row with no tree shows no spinner", async () => {
  const view = await runtime({ status: "done" });
  await openRun(view);
  assert.ok(view.container.querySelector(".tl-step"), "the step is drawn");
  assert.equal(view.container.querySelector(".tl-step .is-running"), null);
  assert.equal(view.container.textContent.includes("Running"), false);
  await view.unmount();
});

test("a runtime row with no status and no tree shows no spinner", async () => {
  const view = await runtime({ status: undefined });
  await openRun(view);
  assert.equal(view.container.querySelector(".tl-step .is-running"), null);
  await view.unmount();
});

test("a running row with no tree yet still shows it is running", async () => {
  const view = await runtime({ status: "running" });
  await openRun(view);
  assert.ok(view.container.querySelector(".tl-step .is-running"));
  await view.unmount();
});

const attach = (id, fields = {}, rowKind = "sub_agent_step") => row(id, { rowKind, blockId: "c1", function: "attach", display: "runtime",
  attach: { session_id: SID, head_id: "h1", label: "helper", status: "completed", ...fields } });
const stepsOf = async (id) => {
  const view = await draw(id);
  await click(view.container.querySelector(".tl-toggle"));
  const steps = [...view.container.querySelectorAll(".tl-step")];
  const shape = steps.map((step) => [step.getAttribute("data-msg-id"), step.querySelector(".tl-step-icon").className]);
  const hasCard = !!view.container.querySelector(".attach-card");
  await view.unmount();
  return { shape, hasCard };
};

test("a /task agent and a tool-started agent draw the same component", async () => {
  seed(attach("card"), row("r", { blocks: [tool("c1", "agent")], attachCards: [attach("card")] }));
  const started = await stepsOf("card");
  const tooled = await stepsOf("r");
  assert.equal(started.hasCard, false);
  assert.deepEqual(started, tooled);
  assert.equal(started.shape[0][0], "card");
});

test("a manual attach and a cross-session attach keep their card", async () => {
  seed(attach("manual", { manual: true }, "external_reference"), attach("foreign", { session_id: "other" }, "external_reference"));
  for (const id of ["manual", "foreign"]) {
    const view = await draw(id);
    assert.ok(view.container.querySelector(".attach-card"), id);
    assert.equal(view.container.querySelector(".tl"), null, id);
    await view.unmount();
  }
});
