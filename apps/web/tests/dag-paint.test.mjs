import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

import {
  branchTagsSignature,
  contextRangeUnchanged,
  coveragePaintSignature,
  dagInputSignature,
  geometryInputSignature,
  hasAuthoritativeLayout,
  readHistoryEmitGate,
  shouldEmitHistorySvg,
} from "../lib/runtime-bridge/dag/paint-gate.ts";

const WEB_ROOT = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

function inputSig(over = {}) {
  return dagInputSignature({
    graph: [{ id: "a", role: "user", predecessor: null, status: "" }],
    headId: "a",
    threadOpen: {},
    summaryExpanded: {},
    locale: "en",
    contextSet: null,
    coverageSet: null,
    sessionId: "s1",
    branchTags: "",
    highlightMode: "viewport",
    ...over,
  });
}

test("coverage compare is a no-op when node_ids / aged / spilled match", () => {
  const prevIds = { a: true, b: true };
  const prevCov = { a: { aged: false, spilled: true }, b: { aged: true, spilled: false } };
  const ids = ["b", "a"];
  const coverage = [
    { node_id: "a", in_context: true, aged: false, spilled: true },
    { node_id: "b", in_context: true, aged: true, spilled: false },
  ];
  assert.equal(contextRangeUnchanged(prevIds, prevCov, ids, coverage), true);
});

test("coverage compare sees membership or aged / spilled change", () => {
  const prevIds = { a: true };
  const prevCov = { a: { aged: false, spilled: false } };
  assert.equal(contextRangeUnchanged(prevIds, prevCov, ["a", "b"], null), false);
  assert.equal(
    contextRangeUnchanged(
      prevIds,
      prevCov,
      ["a"],
      [{ node_id: "a", in_context: true, aged: true, spilled: false }],
    ),
    false,
  );
  assert.equal(
    contextRangeUnchanged(
      prevIds,
      prevCov,
      ["a"],
      [{ node_id: "a", in_context: true, aged: false, spilled: true }],
    ),
    false,
  );
});

test("empty range matches a cleared store", () => {
  assert.equal(contextRangeUnchanged(null, null, null), true);
  assert.equal(contextRangeUnchanged(null, null, []), true);
  assert.equal(contextRangeUnchanged({ a: true }, null, []), false);
  assert.equal(contextRangeUnchanged(null, null, ["a"]), false);
});

test("hidden panel or non-DAG view skips SVG emit", () => {
  assert.equal(shouldEmitHistorySvg("flex", "dag"), true);
  assert.equal(shouldEmitHistorySvg("none", "dag"), false);
  assert.equal(shouldEmitHistorySvg("flex", "session"), false);
  assert.equal(shouldEmitHistorySvg(null, "dag"), false);
});

test("ancestor display:none is treated as hidden", () => {
  const pane = {
    style: { display: "none" },
    getAttribute: (n) => (n === "data-center-view" ? "dag" : null),
    closest(sel) {
      return sel === "[data-center-view]" ? this : null;
    },
    parentElement: null,
  };
  const panel = {
    style: { display: "flex" },
    closest(sel) {
      return sel === "[data-center-view]" ? pane : null;
    },
    parentElement: pane,
  };
  const doc = { getElementById: (id) => (id === "historyPanel" ? panel : null) };
  assert.deepEqual(readHistoryEmitGate(doc), {
    panelDisplay: "none",
    centerView: "dag",
  });
});

test("un-hiding the chat host flushes a pending DAG emit", () => {
  const shell = readFileSync(
    new URL("../components/app-shell.tsx", import.meta.url),
    "utf8",
  );
  assert.match(shell, /enterExclusiveCoverageMode\(\)/);
  assert.match(
    shell,
    /if \(showChat && sessionPaneIndex >= 0 && activeTabDagView\)/,
  );
});

test("prefetch queue is not keyed on pathname", () => {
  const shell = readFileSync(
    new URL("../components/app-shell.tsx", import.meta.url),
    "utf8",
  );
  assert.match(shell, /pathnameRef\.current = pathname/);
  assert.match(shell, /route !== pathnameRef\.current/);
  assert.match(shell, /\}, \[router\]\);/);
  assert.doesNotMatch(shell, /\}, \[pathname, router\]\);/);
});

test("input signature is stable when graph and view state match", () => {
  assert.equal(inputSig(), inputSig());
  assert.equal(
    coveragePaintSignature({ a: true }, { a: { aged: true, spilled: false } }),
    coveragePaintSignature({ a: true }, { a: { aged: true, spilled: false } }),
  );
});

test("preview-only graph change does not bust the input signature", () => {
  const a = inputSig({
    graph: [{ id: "a", role: "user", status: "running", preview: "hi" }],
  });
  const b = inputSig({
    graph: [{ id: "a", role: "user", status: "running", preview: "hi there" }],
  });
  assert.equal(a, b);
});

test("thread open, coverage, locale, or status change busts the signature", () => {
  const base = inputSig();
  assert.notEqual(inputSig({ threadOpen: { a: true } }), base);
  assert.notEqual(inputSig({ summaryExpanded: { cap: true } }), base);
  assert.notEqual(inputSig({ locale: "zh" }), base);
  assert.notEqual(
    inputSig({
      contextSet: { a: true },
      coverageSet: { a: { aged: true, spilled: false } },
    }),
    base,
  );
  assert.notEqual(
    inputSig({ graph: [{ id: "a", role: "user", status: "done" }] }),
    base,
  );
  assert.notEqual(inputSig({ headId: "b" }), base);
  assert.notEqual(
    inputSig({ branchTags: branchTagsSignature([{ head_msg_id: "a", name: "main", active: true }]) }),
    base,
  );
  assert.notEqual(
    inputSig({
      graph: [
        { id: "b", role: "assistant", status: "" },
        { id: "a", role: "user", status: "" },
      ],
    }),
    inputSig({
      graph: [
        { id: "a", role: "user", status: "" },
        { id: "b", role: "assistant", status: "" },
      ],
    }),
  );
});

test("pipeline compares the input signature before expensive passes or SVG", () => {
  const src = readFileSync(
    new URL("../lib/runtime-bridge/dag/pipeline.ts", import.meta.url),
    "utf8",
  );
  const renderSrc = src.slice(src.indexOf("export function render("));
  const gate = renderSrc.indexOf("const sig = _inputSignature(graphIn, headIdIn)");
  const skip = renderSrc.search(/if \(sig === _lastSignature\)[\s\S]{0,80}return/);
  const emitGate = renderSrc.indexOf("if (!historySvgEmitAllowed())");
  const patch = renderSrc.indexOf("tryStatusPatch(");
  const merge = renderSrc.indexOf("const merged = _mergeRuns");
  const fold = renderSrc.indexOf("_foldSummaries");
  const thread = renderSrc.indexOf("buildThreadModel(graph)");
  const svg = renderSrc.indexOf('_svg("svg"');
  const replace = renderSrc.indexOf("replaceChildren");
  const attach = renderSrc.indexOf("attachCanvas");
  assert.ok(gate >= 0 && skip >= 0 && emitGate >= 0 && patch >= 0);
  assert.ok(gate < merge && skip < merge && emitGate < merge && patch < merge);
  assert.ok(skip < fold && skip < thread && skip < svg && skip < replace);
  assert.ok(patch < svg && patch < replace && patch < attach);
  assert.match(renderSrc, /if \(tryStatusPatch\([^)]*\)\) return/);
});

test("geometry signature ignores status and is_error", () => {
  const node = { id: "a", role: "user", predecessor: null, _depth: 1, _lane: 0 };
  const a = inputSig({ graph: [{ ...node, status: "running", is_error: false }] });
  const b = inputSig({ graph: [{ ...node, status: "done", is_error: true }] });
  assert.notEqual(a, b);
  assert.equal(
    geometryInputSignature({
      graph: [{ ...node, status: "running", is_error: false }],
      headId: "a",
      threadOpen: {},
      summaryExpanded: {},
      locale: "en",
      contextSet: null,
      coverageSet: null,
      sessionId: "s1",
      branchTags: "",
      highlightMode: "viewport",
    }),
    geometryInputSignature({
      graph: [{ ...node, status: "done", is_error: true }],
      headId: "a",
      threadOpen: {},
      summaryExpanded: {},
      locale: "en",
      contextSet: null,
      coverageSet: null,
      sessionId: "s1",
      branchTags: "",
      highlightMode: "viewport",
    }),
  );
});

test("adding a node or opening a thread breaks the geometry signature", () => {
  const node = { id: "a", role: "user", status: "running", _depth: 1, _lane: 0 };
  const base = geometryInputSignature({
    graph: [node],
    headId: "a",
    threadOpen: {},
    summaryExpanded: {},
    locale: "en",
    contextSet: null,
    coverageSet: null,
    sessionId: "s1",
    branchTags: "",
    highlightMode: "viewport",
  });
  assert.notEqual(
    geometryInputSignature({
      graph: [node, { id: "b", role: "assistant", predecessor: "a", _depth: 2, _lane: 0 }],
      headId: "b",
      threadOpen: {},
      summaryExpanded: {},
      locale: "en",
      contextSet: null,
      coverageSet: null,
      sessionId: "s1",
      branchTags: "",
      highlightMode: "viewport",
    }),
    base,
  );
  assert.notEqual(
    geometryInputSignature({
      graph: [node],
      headId: "a",
      threadOpen: { a: true },
      summaryExpanded: {},
      locale: "en",
      contextSet: null,
      coverageSet: null,
      sessionId: "s1",
      branchTags: "",
      highlightMode: "viewport",
    }),
    base,
  );
});

test("missing depth or lane is not an authoritative layout", () => {
  assert.equal(hasAuthoritativeLayout([{ id: "a", _depth: 1, _lane: 0 }]), true);
  assert.equal(hasAuthoritativeLayout([{ id: "a", _lane: 0 }]), false);
  assert.equal(hasAuthoritativeLayout([{ id: "a", _depth: 1 }]), false);
  assert.equal(
    hasAuthoritativeLayout([{ id: "a", _depth: 1, _lane: 0 }, { id: "b", _lane: 0 }]),
    false,
  );
});

test("status-only patch keeps the SVG root and does not replaceChildren", async () => {
  const { document } = parseHTML(`<!doctype html><html><body>
    <div class="history-body">
      <svg class="history-svg"><g class="history-world"><g class="history-nodes">
        <g class="history-node" data-msg-id="a">
          <circle data-node-shape="1" data-base-stroke="#888" data-base-stroke-width="2.2" stroke="#888"></circle>
        </g>
      </g></g></svg>
    </div>
  </body></html>`);
  globalThis.document = document;
  const { patchHistoryStatus } = await import(
    "../lib/runtime-bridge/dag/render/nodes.ts"
  );
  const host = document.querySelector(".history-body");
  const svg = host.querySelector("svg.history-svg");
  let replaced = 0;
  const orig = host.replaceChildren.bind(host);
  host.replaceChildren = (...args) => {
    replaced += 1;
    return orig(...args);
  };
  const ok = patchHistoryStatus(host, [
    { id: "a", status: "running", _depth: 1, _lane: 0 },
  ]);
  assert.equal(ok, true);
  assert.equal(replaced, 0);
  assert.equal(host.querySelector("svg.history-svg"), svg);
  assert.ok(host.querySelector(".history-node").classList.contains("is-running"));
  assert.equal(
    host.querySelector("[data-node-shape]").getAttribute("stroke-dasharray"),
    "4 3",
  );
  const again = patchHistoryStatus(host, [
    { id: "a", status: "error", is_error: true, _depth: 1, _lane: 0 },
  ]);
  assert.equal(again, true);
  assert.equal(replaced, 0);
  assert.equal(host.querySelector("svg.history-svg"), svg);
  assert.equal(host.querySelector("[data-status-bang]").textContent, "!");
  assert.equal(
    host.querySelector("[data-node-shape]").getAttribute("stroke"),
    "#e5534b",
  );
});

test("branches_list does not repaint tags before a payload graph", () => {
  const src = readFileSync(
    new URL("../lib/runtime-bridge/conversations.ts", import.meta.url),
    "utf8",
  );
  assert.match(
    src,
    /if \(Array\.isArray\(payload\.graph\)\) \{[\s\S]*?renderHistoryGraph[\s\S]*?\} else \{\s*repaintBranchTags\(\);/,
  );
  assert.doesNotMatch(src, /repaintBranchTags\(\);\s*renderBranchesPanel/);
});
