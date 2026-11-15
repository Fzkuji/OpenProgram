// The /context panel paints its cached snapshot on the first frame, never
// fetches or shows a loading state itself, and offers Compact only for a
// session that has something new to compact.
import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader();
// The real i18n module answers in English through the stored locale.
installDom({ pathname: "/chat" });
// linkedom computes no styles; Radix Popover's presence and placement read some.
globalThis.getComputedStyle = window.getComputedStyle = () => new Proxy({}, {
  get: (_target, key) => (key === "getPropertyValue" ? () => "" : ""),
});
globalThis.WebSocket ??= { OPEN: 1 };
// linkedom has no layout: an element has boxes unless it, or an ancestor, is
// display:none or it is out of the document, as getClientRects() in a browser.
const rendered = (node) => node?.nodeType !== 1
  || (node.style?.display !== "none" && rendered(node.parentNode));
window.Element.prototype.getClientRects = function getClientRects() {
  return this.isConnected && rendered(this) ? [this.getBoundingClientRect()] : [];
};
const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { ContextBreakdownPanel } = await import("../../../components/chat/badges/context-breakdown-panel.tsx");
const { ContextBadge } = await import("../../../components/chat/badges/context-badge.tsx");
const cache = await import("../../../lib/chat/context-breakdown-cache.ts");
const { setSocket } = await import("../../../lib/runtime-bridge/state.ts");
const { SessionScopeProvider } = await import("../../../lib/session-store/session-scope.tsx");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { handleChatResponse } = await import("../../../lib/runtime-bridge/session/chat-handlers.ts");
const { useCenterTabs } = await import("../../../lib/tabs/center-tabs-store.ts");
const { applyChatWsMessage } = await import("../../../lib/net/ws/chat-stream.ts");
const { setPendingUserText, clearPendingUserText } = await import("../../../lib/chat/send/pending-user-text.ts");

let fetches;
let view;
beforeEach(() => {
  fetches = [];
  globalThis.fetch = async (url) => {
    fetches.push(String(url));
    return { json: async () => ({ total_used: 1, window: 10 }) };
  };
  useSessionStore.setState({ rowsByView: {}, messageOrder: {}, runningTasks: {}, tokens: {}, contextWindow: {}, heads: {} });
});
afterEach(async () => { await view?.unmount(); view = undefined; setSocket(null); });

const compactButton = () => [...document.querySelectorAll("button")]
  .find((button) => /Compact conversation|Compacting…/.test(button.textContent));
function seedEvents(sessionId, rows) {
  useSessionStore.setState({
    rowsByView: { [sessionId]: Object.fromEntries(rows.map((row) => [row.id, row])) },
    messageOrder: { [sessionId]: rows.map((row) => row.id) },
  });
}

describe("ContextBreakdownPanel", () => {
  test("the first paint shows the session snapshot without a fetch or a loading state", () => {
    cache.writeContextBreakdownCache("panel-a", "head-a", { total_used: 12_345, window: 200_000 });
    const html = renderToStaticMarkup(h(ContextBreakdownPanel, { sessionId: "panel-a", headId: "head-a" }));
    assert.match(html, /12\.3k \/ 200\.0k/);
    assert.doesNotMatch(html, /Loading|加载中/);
    assert.deepEqual(fetches, []);
  });

  test("a chat with no session paints the draft estimate and has no compact row", async () => {
    useSessionStore.setState({ agentSettings: { ...useSessionStore.getState().agentSettings, chat: { provider: "p", model: "m" } }, activeChatKey: null });
    globalThis.fetch = async () => ({ json: async () => ({ total_used: 3_000, window: 100_000 }) });
    cache.warmDraftContextBreakdown("p", "m", null);
    await new Promise((resolve) => setTimeout(resolve, 0));
    view = await render(h(ContextBreakdownPanel, { sessionId: null }));
    assert.match(view.container.textContent, /3\.0k \/ 100\.0k/);
    assert.equal(compactButton(), undefined);
  });

  test("a snapshot written while open replaces the shown value", async () => {
    cache.writeContextBreakdownCache("panel-b", null, { total_used: 1_000, window: 10_000 });
    view = await render(h(ContextBreakdownPanel, { sessionId: "panel-b", headId: null }));
    assert.match(view.container.textContent, /1\.0k \/ 10\.0k/);
    await view.act(() => cache.writeContextBreakdownCache("panel-b", null, { total_used: 2_000, window: 10_000 }));
    assert.match(view.container.textContent, /2\.0k \/ 10\.0k/);
    assert.deepEqual(fetches, []);
  });

  test("a model whose window is unknown shows its tokens without a limit or percentage", () => {
    cache.writeContextBreakdownCache("panel-u", "head-u", {
      total_used: 134_300, window: null, context_window: null, free_space: null, messages: 134_300,
    });
    const html = renderToStaticMarkup(h(ContextBreakdownPanel, { sessionId: "panel-u", headId: "head-u" }));
    assert.match(html, /134\.3k/);
    assert.doesNotMatch(html, /134\.3k \//);
    assert.doesNotMatch(html, /128\.0k|200\.0k/);
    // The headline is the count alone; rows still give their share of it.
    assert.match(html, /<span class="text-text-bright">134\.3k<\/span><\/div>/);
    assert.match(html, /134\.3k · 100\.0%/);
  });

  test("the running turn's growth is a Conversation row", async () => {
    cache.writeContextBreakdownCache("panel-t", null, {
      total_used: 1_000, window: 2_000, messages: 800,
      messages_detail: { groups: [{ kind: "turn", tokens: 600, count: 0 }], top: [] },
    });
    view = await render(h(ContextBreakdownPanel, { sessionId: "panel-t", headId: null }));
    const row = [...document.querySelectorAll("button")].find((b) => /Conversation/.test(b.textContent));
    await view.act(() => row.click());
    assert.match(view.container.textContent, /This turn so far/);
    assert.doesNotMatch(view.container.textContent, /0 messages/);
  });

  test("an unknown window clears the one shown before", () => {
    useSessionStore.getState().setContextStats("panel-w", { total_used: 1 }, 128_000);
    assert.equal(useSessionStore.getState().contextWindow["panel-w"], 128_000);
    useSessionStore.getState().setContextStats("panel-w", { total_used: 2 }, 0);
    assert.equal(useSessionStore.getState().contextWindow["panel-w"], undefined);
  });

  test("Compact sends the compact action for the session", async () => {
    const sent = [];
    setSocket({ readyState: 1, send: (frame) => sent.push(JSON.parse(frame)) });
    seedEvents("panel-c", [{ id: "u1", role: "user", content: "hi" }]);
    view = await render(h(ContextBreakdownPanel, { sessionId: "panel-c" }));
    assert.equal(compactButton().getAttribute("aria-disabled"), "false");
    await view.act(() => compactButton().click());
    assert.deepEqual(sent, [{ action: "compact", session_id: "panel-c" }]);
  });

  test("while compacting the row says so and sends nothing", async () => {
    const sent = [];
    setSocket({ readyState: 1, send: (frame) => sent.push(frame) });
    // /compact is the branch's running operation, so a reload shows it too.
    useSessionStore.setState({ runningTasks: { "panel-d": { msg_id: "compact_1", op_kind: "compact", op_id: "op_1" } } });
    view = await render(h(ContextBreakdownPanel, { sessionId: "panel-d" }));
    const button = compactButton();
    assert.equal(button.textContent, "Compacting…");
    assert.equal(button.getAttribute("aria-busy"), "true");
    await view.act(() => button.click());
    assert.deepEqual(sent, []);
  });

  test("a finished compaction leaves Compact available; the server answers a repeat as a no-op", async () => {
    seedEvents("panel-e", [
      { id: "u1", role: "user", content: "hi" },
      { id: "c1", role: "system", kind: "compaction", slot: "event", content: "" },
    ]);
    view = await render(h(ContextBreakdownPanel, { sessionId: "panel-e" }));
    assert.equal(compactButton().getAttribute("aria-disabled"), "false");
  });

  test("a turn compacting its own context shows Compacting…", async () => {
    useSessionStore.setState({ runningTasks: { "panel-f": { msg_id: "m1", execution_id: "e1", phase: "compact" } } });
    view = await render(h(ContextBreakdownPanel, { sessionId: "panel-f" }));
    assert.equal(compactButton().textContent, "Compacting…");
  });
});

describe("ContextBadge", () => {
  // A new chat before its first context_stats: the card showed the cached
  // estimate while the ring read only the (empty) stats and said "Context 0".
  test("before any context_stats the ring fills from the snapshot the card shows", async () => {
    cache.writeContextBreakdownCache("badge-n", null, { total_used: 9_200, window: 500_000, basis: "estimated", tools: [] });
    useSessionStore.setState({ currentSessionId: "badge-n", heads: {} });
    view = await render(h(SessionScopeProvider, { sid: "badge-n" }, h(ContextBadge, { sessionId: "badge-n" })));
    const C = 2 * Math.PI * 5;
    const offset = Number(view.container.querySelectorAll(".context-ring-badge circle")[1].getAttribute("stroke-dashoffset"));
    assert.ok(Math.abs(offset - C * (1 - 9_200 / 500_000)) < 1e-9, "the ring shows 9.2k of 500k");
    await view.act(() => view.container.querySelector(".context-ring-badge").click());
    assert.match(document.querySelector(".context-breakdown-panel").textContent, /9\.2k \/ 500\.0k/);
  });

  test("new context_stats move the ring and the card together", async () => {
    cache.writeContextBreakdownCache("badge-m", null, { total_used: 9_200, window: 500_000, basis: "estimated", tools: [] });
    useSessionStore.setState({ currentSessionId: "badge-m", heads: {} });
    view = await render(h(SessionScopeProvider, { sid: "badge-m" }, h(ContextBadge, { sessionId: "badge-m" })));
    await view.act(() => view.container.querySelector(".context-ring-badge").click());
    await view.act(() => handleChatResponse({ type: "context_stats", session_id: "badge-m", total_used: 50_000, window: 500_000, basis: "measured" }));
    const C = 2 * Math.PI * 5;
    const offset = Number(view.container.querySelectorAll(".context-ring-badge circle")[1].getAttribute("stroke-dashoffset"));
    assert.ok(Math.abs(offset - C * (1 - 50_000 / 500_000)) < 1e-9);
    assert.match(document.querySelector(".context-breakdown-panel").textContent, /50\.0k \/ 500\.0k/);
  });


  test("focusing a session warms its snapshot in the background", async () => {
    useSessionStore.setState({ currentSessionId: "badge-a", heads: { "badge-a": "head-1" } });
    view = await render(h(SessionScopeProvider, { sid: "badge-a" }, h(ContextBadge, { sessionId: "badge-a" })));
    assert.deepEqual(fetches, ["/api/sessions/badge-a/context?head_id=head-1"]);
  });

  test("switching the branch head under an open panel shows that head's snapshot at once", async () => {
    cache.writeContextBreakdownCache("badge-b", "h1", { total_used: 1_000, window: 10_000, tools: [] });
    cache.writeContextBreakdownCache("badge-b", "h2", { total_used: 5_000, window: 10_000, tools: [] });
    useSessionStore.setState({ currentSessionId: "badge-b", heads: { "badge-b": "h1" } });
    view = await render(h(SessionScopeProvider, { sid: "badge-b" }, h(ContextBadge, { sessionId: "badge-b" })));
    await view.act(() => view.container.querySelector(".context-ring-badge").click());
    assert.match(document.querySelector(".context-breakdown-panel").textContent, /1\.0k \/ 10\.0k/);
    await view.act(() => useSessionStore.setState({ heads: { "badge-b": "h2" } }));
    assert.match(document.querySelector(".context-breakdown-panel").textContent, /5\.0k \/ 10\.0k/);
  });

  // The first Send of a New chat: when chat_ack made the draft a session,
  // the session had no snapshot until its GET /context came back, and the
  // ring, its tip and the card said "Context 0" with no window meanwhile.
  test("a draft's estimate carries into its session until /context and context_stats replace it", async () => {
    // The tab a New chat opens holds the draft's local_* key.
    const DRAFT = useCenterTabs.getState().openDraftSessionTab();
    const C = 2 * Math.PI * 5;
    const ringShows = (used, window) => {
      const offset = Number(view.container.querySelectorAll(".context-ring-badge circle")[1].getAttribute("stroke-dashoffset"));
      assert.ok(Math.abs(offset - C * (1 - used / window)) < 1e-9, `the ring shows ${used} of ${window}`);
    };
    const cardShows = (text) => assert.match(document.querySelector(".context-breakdown-panel").textContent, text);
    let answerSession;
    globalThis.fetch = async (url) => {
      fetches.push(String(url));
      if (String(url).startsWith("/api/context/draft")) {
        return { json: async () => ({ total_used: 10_300, window: 272_000, basis: "estimated", tools: [] }) };
      }
      return new Promise((resolve) => { answerSession = (data) => resolve({ json: async () => data }); });
    };
    useSessionStore.setState({
      agentSettings: { ...useSessionStore.getState().agentSettings, chat: { provider: "pa", model: "ma" } },
      activeChatKey: DRAFT, currentSessionId: null,
    });
    cache.warmDraftContextBreakdown("pa", "ma", null);
    await new Promise((resolve) => setTimeout(resolve, 0));
    view = await render(h(SessionScopeProvider, { sid: DRAFT }, h(ContextBadge, {})));
    ringShows(10_300, 272_000);
    await view.act(() => view.container.querySelector(".context-ring-badge").click());
    cardShows(/10\.3k \/ 272\.0k/);

    // Send → chat_ack: the server adopts the draft's key as the session id.
    setPendingUserText(DRAFT, "hello");
    await view.act(() => applyChatWsMessage({ type: "chat_ack", data: { session_id: DRAFT } }));
    assert.equal(useSessionStore.getState().currentSessionId, DRAFT);
    ringShows(10_300, 272_000);
    cardShows(/10\.3k \/ 272\.0k/);
    assert.ok(fetches.includes(`/api/sessions/${DRAFT}/context`), "the session's own estimate is asked for");

    // GET /context: the session's estimate.
    await view.act(async () => {
      answerSession({ total_used: 9_200, window: 272_000, basis: "estimated", tools: [] });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    ringShows(9_200, 272_000);
    cardShows(/9\.2k \/ 272\.0k/);

    // context_stats: the measured request.
    await view.act(() => handleChatResponse({ type: "context_stats", session_id: DRAFT, total_used: 12_000, window: 272_000, basis: "measured" }));
    ringShows(12_000, 272_000);
    cardShows(/12\.0k \/ 272\.0k/);
    clearPendingUserText(DRAFT);
  });
});
