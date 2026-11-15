// The call-thread model (dag/rendering.md §12): one triangle is one turn's
// whole activity; folded is folded (a count on the node's shoulder), open is
// open (every call a node on the thread column, agents recursing one column
// further); a fork branch runs parallel to its sibling.
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

installLoader();
// The real i18n module answers in English (installDom stores the locale).
installDom();

const { buildThreadModel, isSpawnRoot, isChainNode } = await import("../../lib/runtime-bridge/dag/passes/thread.ts");
// Namespace import: `_threadOpen` is a live `let` the setter replaces.
const globals = await import("../../lib/runtime-bridge/dag/store/globals.ts");
const { setThreadOpen, toggleThreadOpen, setLastGraph, setLastSignature } = globals;
const { computeGeometry } = await import("../../lib/runtime-bridge/dag/layout/geometry.ts");
const { _buildTree } = await import("../../lib/runtime-bridge/dag/layout/build-tree.ts");
const { COL_W, ROW_H, PAD_X, PAD_Y } = await import("../../lib/runtime-bridge/dag/types.ts");
const { drawNodes } = await import("../../lib/runtime-bridge/dag/render/nodes.ts");
const { drawEdges } = await import("../../lib/runtime-bridge/dag/render/edges.ts");
const { _svg, _branchColor } = await import("../../lib/runtime-bridge/dag/render/shapes.ts");
const { setMainAgentShape, subAgentShape } = await import("../../lib/runtime-bridge/dag/render/agent-look.ts");
const { tileFor } = await import("../../lib/runtime-bridge/dag/render/tiles.ts");
const { _installInteractionHandlers } = await import("../../lib/runtime-bridge/dag/interaction/node/nodes.ts");
const { _roleLabel } = await import("../../lib/runtime-bridge/dag/interaction/node/inspector.ts");
const { wireGestures, WHEEL_ZOOM_RATE } = await import("../../lib/runtime-bridge/dag/interaction/view/gestures.ts");

// One turn makes calls and spawns an agent; the agent runs its own turns and
// calls; the agent's return triggers a followup reply. Lane/tier/depth as the
// API stamps them.
const G = () => [
  { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0, created_at: 0 },
  { id: "u0", role: "user", predecessor: "ROOT", _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
  { id: "a0", role: "assistant", predecessor: "u0", _lane: 0, _tier: 2, _depth: 2, created_at: 2 },
  { id: "e1", role: "tool", display: "runtime", caller: "a0", _lane: 0, _tier: 3, _depth: 3, created_at: 3 },
  { id: "s1", role: "user", source: "agent_spawn", caller: "a0", spawned_from: { label: "后端架构" },
    _lane: 4, _tier: 0, _depth: 3, created_at: 4 },
  { id: "s1_reply", role: "assistant", predecessor: "s1", _lane: 4, _tier: 1, _depth: 4, created_at: 5 },
  { id: "s1e1", role: "tool", display: "runtime", caller: "s1_reply", _lane: 4, _tier: 2, _depth: 5, created_at: 6 },
  { id: "s1e2", role: "tool", display: "runtime", caller: "s1_reply", _lane: 4, _tier: 2, _depth: 6, created_at: 7 },
  { id: "e2", role: "tool", display: "runtime", caller: "a0", _lane: 0, _tier: 3, _depth: 4, created_at: 8 },
  { id: "f1", role: "assistant", source: "job_followup", predecessor: "a0", _lane: 0, _tier: 2, _depth: 7, created_at: 9 },
];

// A composer-launched function with two ROOT-hung (scarred) agent spawns.
const WF = () => [
  { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0, created_at: 0 },
  { id: "wf", role: "tool", function: "agentic_workflow", predecessor: "ROOT", caller: "", _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
  { id: "s1", role: "user", source: "agent_spawn", predecessor: "ROOT", caller: "ROOT", _lane: 1, _tier: 1, _depth: 1, created_at: 2 },
  { id: "s1r", role: "assistant", source: "agent_spawn", predecessor: "s1", _lane: 1, _tier: 2, _depth: 2, created_at: 3 },
  { id: "s2", role: "user", source: "agent_spawn", predecessor: "ROOT", caller: "ROOT", _lane: 2, _tier: 1, _depth: 1, created_at: 4 },
  { id: "s2r", role: "assistant", source: "agent_spawn", predecessor: "s2", _lane: 2, _tier: 2, _depth: 2, created_at: 5 },
];

// A fork: b0 rewrites the turn u1, both off a0.
const FORK = () => [
  { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0, created_at: 0 },
  { id: "u0", role: "user", predecessor: "ROOT", _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
  { id: "a0", role: "assistant", predecessor: "u0", _lane: 0, _tier: 2, _depth: 2, created_at: 2 },
  { id: "u1", role: "user", predecessor: "a0", _lane: 0, _tier: 1, _depth: 3, created_at: 3 },
  { id: "b0", role: "user", predecessor: "a0", _lane: 7, _tier: 5, _depth: 4, created_at: 4 },
];

const ids = (model) => model.visible.map((node) => node.id);
const layoutOf = (graph) => {
  const m = buildThreadModel(graph);
  const tree = _buildTree(m.visible);
  return { m, tree, geom: computeGeometry(tree.byId, m) };
};
const withOpen = (open, fn) => {
  setThreadOpen(Object.assign(Object.create(null), open));
  try { return fn(); } finally { setThreadOpen(Object.create(null)); }
};
const noOverlap = (pos) => {
  const taken = new Map();
  for (const id of Object.keys(pos)) {
    const key = `${pos[id].x},${pos[id].y}`;
    assert.ok(!taken.has(key), `${id} lands on ${taken.get(key)}`);
    taken.set(key, id);
  }
};

describe("thread pass", () => {
  test("folded: only the chain shows; calls and the spawn are the turn's thread in call order", () => withOpen({}, () => {
    const m = buildThreadModel(G());
    assert.deepEqual(ids(m).sort(), ["ROOT", "a0", "u0"]);
    assert.deepEqual((m.events.a0 || []).map((e) => e.id), ["e1", "s1", "e2"]);
    assert.deepEqual((m.events.s1 || []).map((e) => e.id), ["s1_reply", "s1e1", "s1e2"],
      "the spawn's thread carries the agent's turns and calls in time order");
    assert.equal(m.anchorOf("f1"), "a0", "a followup reply anchors to the turn that received the return");
    assert.equal(m.anchorOf("s1_reply"), "s1", "an agent-internal turn anchors to the agent's head");
  }));

  test("a scarred followup merges like any other, with no orphan branch", () => withOpen({}, () => {
    const g = G();
    g.push({ id: "f2", role: "assistant", source: "job_followup", predecessor: "a0", _lane: 9, _tier: 0, _depth: 8, created_at: 10 });
    const m = buildThreadModel(g);
    assert.ok(!ids(m).includes("f2"));
    assert.equal(m.anchorOf("f2"), "a0");
  }));

  test("opening a turn shows its calls and spawn head; the agent opens one level at a time", () => {
    setThreadOpen(Object.create(null));
    toggleThreadOpen("a0");
    let m = buildThreadModel(G());
    assert.ok(["e1", "e2", "s1"].every((id) => ids(m).includes(id)));
    assert.ok(!ids(m).includes("s1e1") && !ids(m).includes("s1_reply"));
    assert.equal(m.nameOf.s1, "后端架构", "the agent's name rides the model for tooltip and inspector");
    toggleThreadOpen("s1");
    m = buildThreadModel(G());
    assert.ok(ids(m).includes("s1e1") && ids(m).includes("s1e2"));
    toggleThreadOpen("a0");
    assert.ok(!globals._threadOpen.a0, "a second toggle folds it again");
    setThreadOpen(Object.create(null));
  });

  test("an open agent under a folded turn shows nothing: visibility follows the whole ancestor chain", () => withOpen({ s1: true }, () => {
    const m = buildThreadModel(G());
    assert.ok(!ids(m).includes("s1") && !ids(m).includes("s1e1"));
  }));

  test("spawn roots and chain nodes are told apart by their fields", () => {
    assert.equal(isSpawnRoot({ id: "x", role: "user", source: "agent_spawn", predecessor: "p" }), false,
      "a node with a predecessor is inside a branch");
    assert.equal(isSpawnRoot({ id: "x", role: "user" }), false);
    assert.equal(isSpawnRoot({ id: "x", role: "user", source: "agent_spawn" }), true);
    assert.equal(isSpawnRoot({ id: "s", role: "user", source: "agent_spawn", predecessor: "ROOT", caller: "ROOT" }), true,
      "the ROOT predecessor sentinel still marks a spawn root");
    assert.equal(isChainNode({ id: "x", role: "assistant", function: "merge" }), true, "merge is a chain operation");
    assert.equal(isChainNode({ id: "x", role: "tool" }), false);
    assert.equal(isChainNode({ id: "wf", role: "tool", function: "agentic_workflow", predecessor: "ROOT", caller: "" }), true,
      "a composer-launched function hung on ROOT is a chain square");
  });

  test("ROOT-hung spawns are the composer function's thread, not fork lanes off the root", () => {
    withOpen({}, () => {
      const m = buildThreadModel(WF());
      assert.deepEqual(ids(m).sort(), ["ROOT", "wf"]);
      assert.deepEqual((m.events.wf || []).map((e) => e.id), ["s1", "s2"]);
    });
    withOpen({ wf: true }, () => {
      const m = buildThreadModel(WF());
      assert.ok(ids(m).includes("s1") && ids(m).includes("s2"));
      assert.ok(!ids(m).includes("s1r") && !ids(m).includes("s2r"), "each agent's reply stays folded");
    });
  });

  test("the spawn name falls back to the first branch name on the spawn's lane", () => withOpen({}, () => {
    const g = G();
    delete g.find((n) => n.id === "s1").spawned_from;
    g.find((n) => n.id === "s1_reply").branch_name = "后端架构";
    assert.equal(buildThreadModel(g).nameOf.s1, "后端架构");
    g.find((n) => n.id === "s1").branch_name = " own name ";
    assert.equal(buildThreadModel(g).nameOf.s1, "own name", "the spawn's own branch name wins");
  }));

  test("a ROOT-hung spawn belongs to the latest composer function started before it", () => withOpen({}, () => {
    const g = WF();
    g.push({ id: "wf2", role: "tool", function: "agentic_workflow", predecessor: "ROOT", caller: "",
      _lane: 0, _tier: 1, _depth: 1, created_at: 3.5 });
    const m = buildThreadModel(g);
    assert.equal(m.spawnOwnerOf.s1, "wf");
    assert.equal(m.spawnOwnerOf.s2, "wf2");
  }));
});

describe("thread geometry", () => {
  test("open threads hang one column right, one row per event, and nested agents push later items down", () => withOpen({ a0: true, s1: true }, () => {
    const { pos } = layoutOf(G()).geom;
    for (const id of Object.keys(pos)) {
      assert.equal((pos[id].x - PAD_X) % COL_W, 0, `${id} off-grid in x`);
      assert.equal((pos[id].y - PAD_Y) % ROW_H, 0, `${id} off-grid in y`);
    }
    assert.equal(pos.e1.x, pos.a0.x + COL_W);
    assert.equal(pos.e1.y, pos.a0.y + ROW_H);
    assert.equal(pos.s1.x, pos.e1.x, "the spawn head sits on the thread");
    assert.equal(pos.s1.y, pos.e1.y + ROW_H);
    assert.equal(pos.s1_reply.x, pos.s1.x + COL_W, "the nested thread is one column further");
    assert.equal(pos.s1_reply.y, pos.s1.y + ROW_H);
    assert.equal(pos.s1e1.x, pos.s1_reply.x);
    assert.equal(pos.s1e1.y, pos.s1_reply.y + ROW_H);
    assert.equal(pos.s1e2.y, pos.s1e1.y + ROW_H);
    assert.equal(pos.e2.y, pos.s1e2.y + ROW_H, "expansion inserts rows; the parent's next item continues below");
    noOverlap(pos);
  }));

  test("a fork branch shares its sibling's row, three columns out", () => withOpen({}, () => {
    const { pos, forkSibOf } = layoutOf(FORK()).geom;
    assert.equal(forkSibOf.b0, "u1");
    assert.equal(pos.b0.y, pos.u1.y);
    assert.equal(pos.b0.x, pos.a0.x + 3 * COL_W);
  }));

  test("an open thread pushes later chain turns below it", () => withOpen({ a0: true }, () => {
    const g = [
      ...G().slice(0, 4),
      { id: "e2", role: "tool", display: "runtime", caller: "a0", _lane: 0, _tier: 3, _depth: 4, created_at: 4 },
      { id: "u1", role: "user", predecessor: "a0", _lane: 0, _tier: 1, _depth: 5, created_at: 5 },
      { id: "a1", role: "assistant", predecessor: "u1", _lane: 0, _tier: 2, _depth: 6, created_at: 6 },
    ];
    const { pos } = layoutOf(g).geom;
    assert.equal(pos.e1.y, pos.a0.y + ROW_H);
    assert.equal(pos.e2.y, pos.e1.y + ROW_H);
    assert.equal(pos.u1.y, pos.e2.y + ROW_H);
    assert.equal(pos.a1.y, pos.u1.y + ROW_H);
    noOverlap(pos);
  }));

  test("a composer function hung on ROOT sits on the main lane, on its own row before later turns", () => withOpen({}, () => {
    const g = [
      { id: "ROOT", display: "root", _lane: 0, _tier: 0, _depth: 0, created_at: 0 },
      { id: "fn", role: "tool", name: "bash", caller: "ROOT", _lane: 0, _tier: 1, _depth: 1, created_at: 1 },
      { id: "u0", role: "user", predecessor: "ROOT", _lane: 0, _tier: 1, _depth: 2, created_at: 2 },
    ];
    const { m, geom } = layoutOf(g);
    assert.ok(ids(m).includes("fn"));
    assert.equal(geom.pos.fn.x, geom.pos.u0.x);
    assert.ok(geom.pos.fn.y < geom.pos.u0.y);
  }));
});

/** Paint the graph the way the pipeline does: edges, then nodes. */
function paint(graph, headId) {
  const { m, tree, geom } = layoutOf(graph);
  const pos = (n) => geom.pos[n.id] || { x: 0, y: 0 };
  const world = _svg("g", {});
  const edgeG = _svg("g", { class: "history-edges" });
  const nodeG = _svg("g", { class: "history-nodes" });
  world.appendChild(edgeG);
  world.appendChild(nodeG);
  const onHead = Object.fromEntries(m.visible.map((n) => [n.id, true]));
  drawEdges(edgeG, tree, graph, pos, {}, geom);
  drawNodes(nodeG, tree, pos, headId, onHead, {}, {}, {}, null, null, {}, m);
  const node = (id) => nodeG.querySelector(`[data-msg-id="${id}"]`);
  const lines = (cls) => [...edgeG.querySelectorAll(`line.${cls}`)].map((line) =>
    ["x1", "y1", "x2", "y2"].map((k) => Number(line.getAttribute(k))));
  return { m, geom, nodeG, edgeG, node, lines };
}

describe("thread paint", () => {
  test("a folded thread is a count on the node's shoulder, and there are no captions", () => withOpen({}, () => {
    const { node, nodeG } = paint(G(), "a0");
    const a0 = node("a0");
    assert.equal(a0.getAttribute("data-thread"), "3");
    assert.equal(a0.getAttribute("data-thread-open"), "0");
    assert.ok(a0.classList.contains("has-thread"));
    assert.equal(a0.querySelector(".history-thread-count").textContent, "+3");
    assert.equal(node("u0").getAttribute("data-thread"), "");
    assert.equal(node("u0").querySelector(".history-thread-count"), null);
    const texts = [...nodeG.querySelectorAll("text")];
    assert.ok(texts.every((t) => t.classList.contains("history-thread-count")),
      "the canvas carries glyphs and counts only; names live in the tooltip and inspector");
  }));

  test("HEAD is marked on its own glyph", () => withOpen({}, () => {
    const { node } = paint(G(), "a0");
    assert.equal(node("a0").querySelector("[data-node-shape]").getAttribute("data-head"), "1");
    assert.ok(node("a0").classList.contains("is-head"));
    assert.equal(node("u0").querySelector("[data-head]"), null);
  }));

  test("an open thread drops its count; its spawn head publishes its own thread and name", () => withOpen({ a0: true }, () => {
    const { node } = paint(G(), "a0");
    assert.equal(node("a0").getAttribute("data-thread-open"), "1");
    assert.equal(node("a0").querySelector(".history-thread-count"), null);
    const s1 = node("s1");
    assert.equal(s1.getAttribute("data-thread"), "3");
    assert.equal(s1.getAttribute("data-spawn-name"), "后端架构");
    assert.equal(s1.querySelector(".history-thread-count").textContent, "+3");
  }));

  test("a sub-agent's tile is the agent glyph, chosen by the same field the thread pass reads", () => {
    assert.deepEqual(
      (({ icon, tone }) => ({ icon, tone }))(tileFor({ id: "s1", role: "user", source: "agent_spawn" })),
      { icon: "ghost", tone: "agent" },
    );
    assert.notEqual(tileFor({ id: "s1", role: "user", source: "agent_spawn", predecessor: "x" }).tone, "agent");
  });

  test("a reply wears the conversation Agent's character, a sub-agent the one its name gives, in their branch colours", () => withOpen({ a0: true, s1: true }, () => {
    const glyph = (g) => g.querySelector("[data-bot-glyph]")?.getAttribute("data-bot-glyph");
    const tone = (g) => g.querySelector(".dag-tile").style.getPropertyValue("--tone");
    setMainAgentShape("rocket");
    try {
      const { node } = paint(G(), "a0");
      assert.equal(glyph(node("a0")), "rocket");
      assert.equal(tone(node("a0")), _branchColor(node("a0")._nodeData));
      const sub = subAgentShape("后端架构");
      assert.equal(glyph(node("s1")), sub);
      assert.equal(glyph(node("s1_reply")), sub);
      assert.equal(tone(node("s1")), _branchColor(node("s1")._nodeData));
      // Your message and a call keep their icons.
      assert.equal(glyph(node("u0")), undefined);
      assert.equal(glyph(node("e1")), undefined);
    } finally {
      setMainAgentShape(null);
    }
  }));

  test("the thread line runs down the anchor's column, then right into each item", () => withOpen({ a0: true }, () => {
    const { geom, lines } = paint(G(), "a0");
    const { pos } = geom;
    const thread = lines("thread-edge");
    for (const id of ["e1", "s1", "e2"]) {
      assert.ok(thread.some(([x1, , x2, y2]) => x1 === pos.a0.x && x2 === pos.a0.x && y2 === pos[id].y),
        `a vertical belt down a0's column reaches ${id}'s row`);
      assert.ok(thread.some(([x1, y1, x2, y2]) => x1 === pos.a0.x && y1 === pos[id].y && x2 === pos[id].x && y2 === pos[id].y),
        `a stub runs right into ${id}`);
    }
  }));

  test("chain edges end at node centres", () => withOpen({}, () => {
    const { geom, lines } = paint(G(), "a0");
    const chain = lines("history-edge").filter((line) => line);
    for (const id of ["u0", "a0"]) {
      const c = geom.pos[id];
      assert.ok(chain.some(([, , x2, y2]) => x2 === c.x && y2 === c.y), `an edge ends at ${id}'s centre`);
    }
  }));

  test("a fork with a level sibling gets the dashed bridge; nothing draws a spawn S-curve", () => withOpen({ a0: true }, () => {
    assert.ok(paint(FORK(), "u1").edgeG.querySelector(".fork-edge"));
    assert.equal(paint(G(), "a0").edgeG.querySelector(".spawn-edge"), null);
  }));
});

describe("thread interaction", () => {
  test("clicking a node with a thread toggles it and repaints past the render signature", async () => {
    setThreadOpen(Object.create(null));
    let rerenders = 0;
    _installInteractionHandlers(() => { rerenders += 1; });
    setLastGraph(G(), "a0");
    setLastSignature("cached");
    const g = document.createElement("g");
    g.setAttribute("class", "history-node");
    g.setAttribute("data-msg-id", "a0");
    g.setAttribute("data-thread", "3");
    const inner = document.createElement("rect");
    g.appendChild(inner);
    document.body.appendChild(g);
    inner.dispatchEvent(new window.Event("click", { bubbles: true }));
    assert.equal(globals._threadOpen.a0, true);
    assert.equal(globals._lastSignature, null, "the toggle busts the signature or the repaint no-ops");
    assert.equal(rerenders, 1);
    inner.dispatchEvent(new window.Event("click", { bubbles: true }));
    assert.ok(!globals._threadOpen.a0, "a second click folds it");
    g.remove();
    setLastGraph(null, null);
    setThreadOpen(Object.create(null));
  });

  test("the inspector titles a spawn root as the sub-agent it heads", () => {
    const el = document.createElement("g");
    el.setAttribute("data-spawn-name", "后端架构");
    assert.equal(_roleLabel({ id: "s1", role: "user", source: "agent_spawn" }, el), "sub-agent · 后端架构");
    assert.equal(_roleLabel({ id: "s1", role: "user", source: "agent_spawn" }), "sub-agent");
    assert.equal(_roleLabel({ id: "s1", role: "user", source: "agent_spawn", predecessor: "p" }), "user");
  });
});

describe("canvas wheel gestures", () => {
  function wire() {
    const listeners = {};
    const host = {
      addEventListener: (type, fn) => { (listeners[type] ??= []).push(fn); },
      getBoundingClientRect: () => ({ left: 10, top: 20, width: 500, height: 400 }),
    };
    const calls = [];
    wireGestures(host, {
      zoomAt: (x, y, factor) => calls.push(["zoom", x, y, factor]),
      dismiss: () => {},
      moved: () => calls.push(["moved"]),
    });
    let time = 10_000;
    const wheel = (fields) => {
      time += 1000; // a fresh gesture each time
      const event = { deltaMode: 0, deltaX: 0, deltaY: 0, clientX: 110, clientY: 120, timeStamp: time, preventDefault() {}, ...fields };
      for (const fn of listeners.wheel) fn(event);
    };
    return { calls, wheel };
  }

  test("a pinch (ctrl or cmd wheel) zooms about the cursor at the pinch rate", () => {
    const { calls, wheel } = wire();
    wheel({ ctrlKey: true, deltaY: 10 });
    wheel({ metaKey: true, deltaY: -10 });
    assert.deepEqual(calls[0].slice(0, 3), ["zoom", 100, 100]);
    assert.ok(Math.abs(calls[0][3] - Math.exp(-10 * 0.01)) < 1e-12);
    assert.ok(Math.abs(calls[1][3] - Math.exp(10 * 0.01)) < 1e-12);
  });

  test("a mouse wheel zooms at the wheel rate; a trackpad swipe pans both axes", () => {
    globals.setView(0, 0, 1);
    const { calls, wheel } = wire();
    wheel({ deltaY: 100, wheelDeltaY: -120 });
    assert.equal(calls[0][0], "zoom");
    assert.ok(Math.abs(calls[0][3] - Math.exp(-100 * WHEEL_ZOOM_RATE)) < 1e-12);
    wheel({ deltaX: 5, deltaY: 7 });
    assert.deepEqual(calls[1], ["moved"]);
    assert.deepEqual([globals._viewTx, globals._viewTy, globals._viewScale], [-5, -7, 1]);
    wheel({ deltaY: 4, wheelDeltaY: -12 });
    assert.deepEqual(calls[2], ["moved"], "a vertical trackpad swipe (wheelDeltaY = -3 x deltaY) pans");
    assert.deepEqual([globals._viewTx, globals._viewTy], [-5, -11]);
  });
});
