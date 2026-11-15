// The compaction capsule (dag/rendering.md §9): the fold pass decides what
// the graph draws, and the tile, the shoulder count and the click all key on
// the same field it reads (covers_ids).
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

installLoader({
  stubs: { "@/lib/format-utils/toast": "export const showToast = (...args) => (globalThis.__toasts ??= []).push(args);" },
});
installDom();

const { _foldSummaries, coversIds } = await import("../../lib/runtime-bridge/dag/passes/fold-summaries.ts");
// Namespace import: `_summaryExpanded` is a live `let` the setter replaces.
const globals = await import("../../lib/runtime-bridge/dag/store/globals.ts");
const { setSummaryExpanded, toggleSummaryExpanded, setLastGraph, setLastSignature, setThreadOpen } = globals;
const { buildThreadModel } = await import("../../lib/runtime-bridge/dag/passes/thread.ts");
const { _buildTree } = await import("../../lib/runtime-bridge/dag/layout/build-tree.ts");
const { drawNodes } = await import("../../lib/runtime-bridge/dag/render/nodes.ts");
const { _svg } = await import("../../lib/runtime-bridge/dag/render/shapes.ts");
const { tileFor } = await import("../../lib/runtime-bridge/dag/render/tiles.ts");
const { _installInteractionHandlers } = await import("../../lib/runtime-bridge/dag/interaction/node/nodes.ts");
const { forkAndEditNode } = await import("../../lib/runtime-bridge/dag/interaction/node/inspector.ts");
const { runtimeState } = await import("../../lib/runtime-bridge/state.ts");
const { useSessionStore } = await import("../../lib/session-store/index.ts");

// u0 a0 u1 a1 with a summary standing in for the first turn; the kept tail
// keeps its own edges and the summary hangs at the segment's start.
const graph = () => [
  { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0"] },
  { id: "u0", role: "user", predecessor: null },
  { id: "a0", role: "assistant", predecessor: "u0" },
  { id: "u1", role: "user", predecessor: "a0" },
  { id: "a1", role: "assistant", predecessor: "u1" },
];
const visibleIds = (out) => out.visible.map((n) => n.id);
const reset = () => setSummaryExpanded(Object.create(null));

afterEach(reset);

describe("fold pass", () => {
  test("folded by default the covered range is elided and the capsule stays", () => {
    reset();
    const out = _foldSummaries(graph(), "a1");
    assert.deepEqual(visibleIds(out), ["sum1", "u1", "a1"]);
    assert.deepEqual(Object.entries(out.coversOf), [["sum1", ["u0", "a0"]]]);
  });

  test("expanding brings every covered node back as a ghost; toggling again folds it", () => {
    reset();
    toggleSummaryExpanded("sum1");
    assert.equal(globals._summaryExpanded.sum1, true);
    const out = _foldSummaries(graph(), "a1");
    assert.deepEqual(visibleIds(out), ["sum1", "u0", "a0", "u1", "a1"]);
    assert.deepEqual(Object.entries(out.coversOf), [["sum1", ["u0", "a0"]]], "coversOf does not change with the toggle");
    const byId = Object.fromEntries(out.visible.map((n) => [n.id, n]));
    assert.equal(byId.u0._ghost, true);
    assert.ok(!byId.u1._ghost, "the kept tail is never a ghost");
    assert.ok(!byId.sum1._ghost && !byId.sum1._summaryInert);
    toggleSummaryExpanded("sum1");
    assert.equal(globals._summaryExpanded.sum1, undefined);
  });

  test("two capsules covering the same turn never fold each other away", () => {
    const nested = [
      { id: "s1", role: "assistant", covers_ids: ["u0"] },
      { id: "s2", role: "assistant", covers_ids: ["u0"] },
      { id: "u0", role: "user" },
    ];
    const ids = visibleIds(_foldSummaries(nested, "u0"));
    assert.ok(ids.includes("s1") && ids.includes("s2"));
    assert.ok(!ids.includes("u0"));
  });

  test("a graph without summaries comes through untouched, by identity", () => {
    const plain = [{ id: "u0", role: "user" }];
    const out = _foldSummaries(plain, "u0");
    assert.equal(out.visible, plain);
    assert.deepEqual(Object.keys(out.coversOf), []);
  });

  test("covered caller subtrees fold with their turn and keep the carrying branch live", () => {
    const withTools = [
      { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0", "tool1"] },
      { id: "u0", role: "user", predecessor: null },
      { id: "a0", role: "assistant", predecessor: "u0" },
      { id: "tool1", role: "tool", caller: "a0", predecessor: "a0" },
      { id: "u1", role: "user", predecessor: "a0" },
      { id: "a1", role: "assistant", predecessor: "u1" },
    ];
    const out = _foldSummaries(withTools, "a1");
    assert.deepEqual(visibleIds(out), ["sum1", "u1", "a1"]);
    assert.ok(!out.visible.find((n) => n.id === "sum1")._summaryInert);
  });

  test("coversIds reads only a non-empty range", () => {
    assert.equal(coversIds({ id: "x" }), null);
    assert.equal(coversIds({ id: "x", covers_ids: [] }), null);
    assert.deepEqual(coversIds({ id: "x", covers_ids: ["a"] }), ["a"]);
  });

  test("on a branch without the covered segment nothing folds and the capsule is inert; the carrier keeps it open", () => {
    const forked = [
      { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0"] },
      { id: "u0", role: "user", predecessor: null },
      { id: "a0", role: "assistant", predecessor: "u0" },
      { id: "f0", role: "user", predecessor: "u0" },
      { id: "f0r", role: "assistant", predecessor: "f0" },
    ];
    const byId = Object.fromEntries(_foldSummaries(forked, "f0r").visible.map((n) => [n.id, n]));
    assert.ok(byId.u0 && byId.a0);
    assert.ok(!byId.u0._ghost && !byId.a0._ghost);
    assert.equal(byId.sum1._summaryInert, true);
    const back = Object.fromEntries(_foldSummaries(forked, "a0").visible.map((n) => [n.id, n]));
    assert.equal(back.u0?._ghost, true, "returning to the carrier finds the seen range open");
  });

  test("summaries fold before threads attribute events, so a covered turn's calls vanish with it", () => {
    const g = [...graph(), { id: "t0", role: "tool", caller: "a0", predecessor: "a0", display: "runtime" }];
    g[0].covers_ids = ["u0", "a0", "t0"];
    const folded = _foldSummaries(g, "a1");
    setThreadOpen(Object.create(null));
    const model = buildThreadModel(folded.visible, "a1");
    assert.ok(!model.visible.some((n) => n.id === "t0"));
    assert.equal(model.events.a0, undefined);
  });
});

function paintCapsule(graphIn, headId) {
  const out = _foldSummaries(graphIn, headId);
  setThreadOpen(Object.create(null));
  const model = buildThreadModel(out.visible, headId);
  const tree = _buildTree(model.visible);
  const nodeG = _svg("g", {});
  drawNodes(nodeG, tree, () => ({ x: 0, y: 0 }), headId, {}, {}, {}, {}, null, null, out.coversOf, model);
  return (id) => nodeG.querySelector(`[data-msg-id="${id}"]`);
}

describe("capsule paint", () => {
  test("the summary tile is the archive, chosen by covers_ids", () => {
    assert.equal(tileFor({ id: "s", role: "assistant", covers_ids: ["u0"] }).icon, "archive");
    assert.notEqual(tileFor({ id: "s", role: "assistant" }).icon, "archive");
  });

  test("a folded capsule publishes its covered count and wears it on the shoulder, with no caption", () => {
    reset();
    const node = paintCapsule(graph(), "a1");
    const capsule = node("sum1");
    assert.ok(capsule.classList.contains("is-summary"));
    assert.equal(capsule.getAttribute("data-summary"), "2");
    assert.equal(capsule.getAttribute("data-summary-open"), "0");
    const texts = [...capsule.querySelectorAll("text")];
    assert.deepEqual(texts.map((t) => [t.getAttribute("class"), t.textContent]), [["history-thread-count", "+2"]]);
  });

  test("an open capsule drops its count; an inert one advertises no fold", () => {
    setSummaryExpanded({ sum1: true });
    let node = paintCapsule(graph(), "a1");
    assert.equal(node("sum1").getAttribute("data-summary-open"), "1");
    assert.equal(node("sum1").querySelector(".history-thread-count"), null);
    assert.ok(node("u0").classList.contains("is-ghost"));
    reset();
    const forked = [
      { id: "sum1", role: "assistant", predecessor: null, covers_ids: ["u0", "a0"] },
      { id: "u0", role: "user", predecessor: null },
      { id: "a0", role: "assistant", predecessor: "u0" },
      { id: "f0", role: "user", predecessor: "u0" },
      { id: "f0r", role: "assistant", predecessor: "f0" },
    ];
    node = paintCapsule(forked, "f0r");
    assert.equal(node("sum1").getAttribute("data-summary"), "");
    assert.equal(node("sum1").querySelector(".history-thread-count"), null);
  });
});

describe("capsule and turn interactions", () => {
  let rerenders = 0;
  _installInteractionHandlers(() => { rerenders += 1; });
  const nodeEl = (attrs) => {
    const g = document.createElement("g");
    g.setAttribute("class", "history-node");
    for (const [key, value] of Object.entries(attrs)) g.setAttribute(key, value);
    const inner = document.createElement("rect");
    g.appendChild(inner);
    document.body.appendChild(g);
    return { g, inner };
  };
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
    setLastGraph(null, null);
    document.body.querySelectorAll(".history-node").forEach((el) => el.remove());
  });

  test("clicking a capsule toggles its fold ahead of any thread and busts the render signature", () => {
    reset();
    rerenders = 0;
    setLastGraph(graph(), "a1");
    setLastSignature("cached");
    const { inner } = nodeEl({ "data-msg-id": "sum1", "data-summary": "2", "data-thread": "1" });
    inner.dispatchEvent(new window.Event("click", { bubbles: true }));
    assert.equal(globals._summaryExpanded.sum1, true);
    assert.ok(!globals._threadOpen.sum1, "the capsule's click is its fold, not the thread's");
    assert.equal(globals._lastSignature, null);
    assert.equal(rerenders, 1);
  });

  /** Record the composer writes fork & edit makes through the session store. */
  function spyComposer() {
    const composer = { inputs: [], focused: 0 };
    useSessionStore.setState({
      setComposerInput: (value) => composer.inputs.push(value),
      focusComposer: () => { composer.focused += 1; },
    });
    return composer;
  }

  function serveCheckout(ok = true) {
    const calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push([url, JSON.parse(init.body)]);
      return new Response(JSON.stringify(ok ? {} : { error: "busy" }), { status: ok ? 200 : 409 });
    };
    return calls;
  }

  test("fork & edit checks out the turn's predecessor and puts its text in the focused composer", async () => {
    runtimeState.currentSessionId = "s1";
    const calls = serveCheckout();
    const composer = spyComposer();
    await forkAndEditNode({ id: "u1", role: "user", predecessor: "a0", content: "try again" });
    assert.deepEqual(calls, [["/api/chat/checkout", { session_id: "s1", msg_id: "a0" }]]);
    assert.deepEqual(composer.inputs, ["try again"]);
    assert.equal(composer.focused, 1);
  });

  test("fork & edit never falls back to the caller edge, and a failed checkout leaves the composer alone", async () => {
    runtimeState.currentSessionId = "s1";
    const calls = serveCheckout(false);
    const composer = spyComposer();
    await forkAndEditNode({ id: "s", role: "user", source: "agent_spawn", predecessor: null, caller: "a0", content: "x" });
    assert.deepEqual(calls, [], "a spawn root has no fork point in its own branch");
    await forkAndEditNode({ id: "u1", role: "user", predecessor: "a0", content: "x" });
    assert.equal(calls.length, 1);
    assert.deepEqual(composer.inputs, []);
    assert.equal(composer.focused, 0);
  });

  test("double-clicking a user turn is fork & edit", async () => {
    runtimeState.currentSessionId = "s1";
    const calls = serveCheckout();
    setLastGraph(graph(), "a1");
    const { inner } = nodeEl({ "data-msg-id": "u1", "data-internal": "0" });
    inner.dispatchEvent(new window.Event("dblclick", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(calls[0], ["/api/chat/checkout", { session_id: "s1", msg_id: "a0" }]);
  });
});
