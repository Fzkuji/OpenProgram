// The Context window card (the ring in the composer's controls row) is a
// plain toggle on the shared Popover: one click opens it, the next click on
// the ring closes it, and it also closes on a press outside, Escape, or the
// window losing focus (a press on a native page never reaches the document).
// A blur during the press on the ring used to close the card and let the
// click reopen it: the close-then-reopen flash. The card itself is the only
// native-view occluder, with no full-screen backdrop that would take down
// every native page on screen.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render, resizeObservers, setRect } from "../../_support/dom.mjs";

installLoader();
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
const { ContextBadge } = await import("../../../components/chat/badges/context-badge.tsx");
const { SessionScopeProvider } = await import("../../../lib/session-store/session-scope.tsx");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { useSessionScope } = await import("../../../lib/session-store/session-scope.tsx");

const source = readFileSync(new URL("../../../components/chat/badges/context-badge.tsx", import.meta.url), "utf8");

let view;
beforeEach(() => {
  globalThis.fetch = async () => ({ json: async () => ({ total_used: 1, window: 10 }) });
  useSessionStore.setState({ currentSessionId: "ring", heads: {}, tokens: {}, contextWindow: {}, runningTasks: {} });
});
afterEach(async () => { await view?.unmount(); view = undefined; });

const ring = () => view.container.querySelector(".context-ring-badge");
const card = () => document.querySelector(".context-breakdown-panel");
const mount = async (sid) => {
  view = await render(h(SessionScopeProvider, { sid }, h(ContextBadge, { sessionId: sid })));
};
// linkedom has no PointerEvent or MouseEvent, and it drops an event's target
// once dispatch ends; Radix reads the pointerdown's target again at the
// click, as a browser allows. React and Radix need only these fields.
class PressEvent extends window.Event {
  constructor(type, element) {
    super(type, { bubbles: true, cancelable: true });
    this.element = element;
  }
  get target() { return this.element; }
  set target(_value) {}
  get button() { return 0; }
  get pointerType() { return "mouse"; }
}
// A mouse press on an element: pointerdown and mousedown, then (in a later
// task, as in a browser, so React has rendered in between) the click.
const press = async (target, { during } = {}) => {
  const fire = (type) => target.dispatchEvent(new PressEvent(type, target));
  await view.act(() => {
    fire("pointerdown");
    fire("mousedown");
    during?.();
  });
  await view.act(() => {
    fire("pointerup");
    fire("mouseup");
    fire("click");
  });
};

test("a second click on the ring closes the card", async () => {
  await mount("ring-a");
  await press(ring());
  assert.equal(Boolean(card()), true, "the first click opens it");
  assert.equal(ring().getAttribute("aria-expanded"), "true");
  await press(ring());
  assert.equal(Boolean(card()), false, "the second click closes it");
  assert.equal(ring().getAttribute("aria-expanded"), "false");
  await press(ring());
  assert.equal(Boolean(card()), true, "a third click opens it again");
});

test("a blur during the press on the ring does not reopen the card", async () => {
  await mount("ring-b");
  await press(ring());
  assert.equal(Boolean(card()), true);
  await press(ring(), { during: () => window.dispatchEvent(new window.Event("blur")) });
  assert.equal(Boolean(card()), false);
});

test("a press outside, Escape and the window losing focus close the card", async () => {
  await mount("ring-c");
  const outside = document.createElement("button");
  document.body.append(outside);
  await press(ring());
  await press(outside);
  assert.equal(Boolean(card()), false, "outside press");
  await press(ring());
  await view.act(() => document.dispatchEvent(Object.defineProperty(new window.Event("keydown", { bubbles: true }), "key", { value: "Escape" })));
  assert.equal(Boolean(card()), false, "Escape");
  await press(ring());
  await view.act(() => window.dispatchEvent(new window.Event("blur")));
  assert.equal(Boolean(card()), false, "window blur");
  outside.remove();
});

// In a split view the single-pane shell stays mounted under display:none,
// with its own composer and ring for the same session, reading the same
// scope. Its card used to portal out of the hidden shell and sit on a 0x0
// ring at the window's top-left, a second card (and a stray occluder).
test("a ring under a display:none shell shows no card; the visible ring's does", async () => {
  const sid = "ring-split";
  let openContextPanel;
  // What the composer's /context does: open the panel in its session's scope.
  const SlashContext = () => {
    const setOpen = useSessionScope((s) => s.setContextPanelOpen);
    openContextPanel = () => setOpen(true);
    return null;
  };
  view = await render(h("div", null,
    h("div", { className: "hidden-shell", style: { display: "none" } },
      h(SessionScopeProvider, { sid }, h(ContextBadge, { sessionId: sid }))),
    h("div", { className: "visible-pane" },
      h(SessionScopeProvider, { sid }, h(SlashContext), h(ContextBadge, { sessionId: sid })))));
  const hiddenRing = view.container.querySelector(".hidden-shell .context-ring-badge");
  const visibleRing = view.container.querySelector(".visible-pane .context-ring-badge");
  setRect(visibleRing, { left: 400, top: 600, width: 20, height: 20 });
  const cards = () => document.querySelectorAll(".context-breakdown-panel");
  const openRings = () => [...view.container.querySelectorAll(".context-ring-badge")]
    .filter((ring) => ring.getAttribute("aria-expanded") === "true");

  await view.act(() => openContextPanel());
  assert.equal(cards().length, 1, "/context opens one card");
  assert.deepEqual(openRings(), [visibleRing], "the visible ring owns it");
  assert.equal(visibleRing.getAttribute("aria-controls"),
    cards()[0].closest("[role=dialog]").id, "the card is the visible ring's content");

  await press(visibleRing);
  assert.equal(cards().length, 0, "a click on the visible ring closes it");
  await press(visibleRing);
  assert.equal(cards().length, 1, "and opens it again, once");
  assert.deepEqual(openRings(), [visibleRing]);

  // The shells swap: the card follows the ring that is laid out.
  await view.act(() => {
    view.container.querySelector(".visible-pane").style.display = "none";
    view.container.querySelector(".hidden-shell").style.display = "";
    resizeObservers.trigger(visibleRing);
    resizeObservers.trigger(hiddenRing);
  });
  assert.equal(cards().length, 1);
  assert.deepEqual(openRings(), [hiddenRing]);
});

test("the card is the only occluder, on the shared Popover with no backdrop", () => {
  assert.match(source, /<PopoverContent\s+data-native-view-occluder="true"/);
  assert.equal(source.match(/data-native-view-occluder/g).length, 1);
  assert.doesNotMatch(source, /inset:\s*0/);
  assert.doesNotMatch(source, /createPortal/);
});

test("a ring whose window is unknown stays empty, with no made-up limit", async () => {
  useSessionStore.getState().setContextStats("ring", { total_used: 134_300 }, 0);
  await mount("ring");
  const arc = ring().querySelectorAll("circle")[1];
  const C = 2 * Math.PI * 5;
  assert.equal(Number(arc.getAttribute("stroke-dashoffset")), C, "no fill");
  assert.doesNotMatch(ring().outerHTML, /NaN/);

  await view.act(() => useSessionStore.getState().setContextStats("ring", { total_used: 134_300 }, 500_000));
  const filled = Number(ring().querySelectorAll("circle")[1].getAttribute("stroke-dashoffset"));
  assert.ok(Math.abs(filled - C * (1 - 134_300 / 500_000)) < 1e-9, "a known window fills its share");
});
