// A rail with a storage key keeps its width in this browser and can go back to its default.
import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";

const { window } = parseHTML("<!doctype html><html><body></body></html>");
const saved = new Map();
Object.assign(globalThis, { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true });
window.localStorage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key) };
globalThis.requestAnimationFrame = callback => setTimeout(() => callback(0), 0);
window.requestAnimationFrame = globalThis.requestAnimationFrame;
window.cancelAnimationFrame = clearTimeout;
window.setTimeout = setTimeout;
window.clearTimeout = clearTimeout;

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { snapWidth, useResizableRail } = await import("../../components/layout/use-resizable-rail.ts");

let rail;
function Host(props) { rail = useResizableRail({ open: true, minWidth: 240, maxWidth: 720, defaultWidth: 288, direction: -1, ...props }); return null; }
async function mount(props) {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => root.render(createElement(Host, props)));
  return () => act(async () => root.unmount());
}
const key = name => ({ key: name, preventDefault() {} });
test.beforeEach(() => saved.clear());

test("a stored width comes back, clamped to the rail's limits", async () => {
  saved.set("rail", "500");
  let unmount = await mount({ storageKey: "rail" });
  assert.equal(rail.style.width, "500px");
  await unmount();
  saved.set("rail", "9000");
  unmount = await mount({ storageKey: "rail" });
  assert.equal(rail.style.width, "720px");
  await unmount();
});

test("a nonsense stored width is ignored", async () => {
  saved.set("rail", "wide");
  const unmount = await mount({ storageKey: "rail" });
  assert.equal(rail.style.width, "288px");
  await unmount();
});

test("the arrow keys move the handle by 24 and save it; the right rail grows leftward", async () => {
  const unmount = await mount({ storageKey: "rail" });
  assert.equal(rail.resizeHandleProps.role, "separator");
  await act(async () => rail.resizeHandleProps.onKeyDown(key("ArrowLeft")));
  assert.equal(rail.style.width, "312px");
  assert.equal(saved.get("rail"), "312");
  await act(async () => rail.resizeHandleProps.onKeyDown(key("ArrowRight")));
  assert.equal(rail.style.width, "288px");
  await unmount();
});

test("a drag saves when it ends, not while it moves", async () => {
  const unmount = await mount({ storageKey: "rail" });
  const target = { setPointerCapture() {} };
  await act(async () => rail.resizeHandleProps.onPointerDown({ button: 0, clientX: 500, pointerId: 1, preventDefault() {}, currentTarget: target }));
  await act(async () => rail.resizeHandleProps.onPointerMove({ buttons: 1, clientX: 400 }));
  assert.equal(rail.style.width, "388px");
  assert.equal(saved.has("rail"), false);
  await act(async () => rail.resizeHandleProps.onPointerUp());
  assert.equal(saved.get("rail"), "388");
  await unmount();
});

const handle = { setPointerCapture() {} };
// The way back to the default is walked over a few frames.
const arrived = async () => {
  for (let tries = 0; tries < 200 && (rail.style.width !== "288px" || rail.style.transition === "none"); tries += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
};
const drag = async (from, to) => {
  await act(async () => rail.resizeHandleProps.onPointerDown({ button: 0, clientX: from, pointerId: 1, preventDefault() {}, currentTarget: handle }));
  await act(async () => rail.resizeHandleProps.onPointerMove({ buttons: 1, clientX: to }));
  await act(async () => rail.resizeHandleProps.onPointerUp());
};

test("a double-click puts the rail back at its default and forgets the stored width", async () => {
  saved.set("rail", "500");
  const unmount = await mount({ storageKey: "rail" });
  await act(async () => rail.resizeHandleProps.onDoubleClick());
  assert.equal(saved.has("rail"), false, "forgotten at once, not when the move ends");
  assert.notEqual(rail.style.width, "288px", "it travels there");
  assert.equal(rail.style.transition, "none");
  await arrived();
  assert.equal(rail.style.width, "288px");
  assert.notEqual(rail.style.transition, "none");
  assert.equal(saved.has("rail"), false);
  await unmount();
});

test("Enter on the handle does what a double-click does", async () => {
  saved.set("rail", "500");
  const unmount = await mount({ storageKey: "rail" });
  await act(async () => rail.resizeHandleProps.onKeyDown(key("Enter")));
  assert.equal(saved.has("rail"), false, "forgotten at once, not when the move ends");
  assert.notEqual(rail.style.width, "288px", "it travels there");
  assert.equal(rail.style.transition, "none");
  await arrived();
  assert.equal(rail.style.width, "288px");
  assert.notEqual(rail.style.transition, "none");
  assert.equal(saved.has("rail"), false);
  await unmount();
});

test("a drag ending near the default lands on it and stores nothing", async () => {
  saved.set("rail", "400");
  const unmount = await mount({ storageKey: "rail" });
  await drag(500, 607);
  assert.equal(rail.style.width, "288px");
  assert.equal(saved.has("rail"), false);
  await drag(500, 490);
  assert.equal(rail.style.width, "298px");
  assert.equal(saved.get("rail"), "298");
  await unmount();
});

test("the handle says when a drag rests on the default width", async () => {
  saved.set("rail", "400");
  const unmount = await mount({ storageKey: "rail" });
  assert.equal(rail.resizeHandleProps["data-snapped"], undefined);
  await act(async () => rail.resizeHandleProps.onPointerDown({ button: 0, clientX: 500, pointerId: 1, preventDefault() {}, currentTarget: handle }));
  assert.equal(rail.resizeHandleProps["data-snapped"], undefined);
  await act(async () => rail.resizeHandleProps.onPointerMove({ buttons: 1, clientX: 607 }));
  assert.equal(rail.resizeHandleProps["data-snapped"], "");
  await act(async () => rail.resizeHandleProps.onPointerUp());
  assert.equal(rail.resizeHandleProps["data-snapped"], undefined);
  await unmount();
});

test("a drag rests on the nearest snap width within reach, and is free elsewhere", () => {
  const snaps = [288, 544, 800];
  assert.equal(snapWidth(292, snaps), 288);
  assert.equal(snapWidth(537, snaps), 544);
  assert.equal(snapWidth(808, snaps), 800);
  assert.equal(snapWidth(535, snaps), 535);
  assert.equal(snapWidth(420, snaps), 420);
  assert.equal(snapWidth(300, [296, 304]), 296, "the nearer of two");
});

test("a rail with several snap widths rests on each, and the handle says so", async () => {
  saved.set("rail", "288");
  const unmount = await mount({ storageKey: "rail", maxWidth: 800, snapWidths: [288, 544, 800] });
  await act(async () => rail.resizeHandleProps.onPointerDown({ button: 0, clientX: 1000, pointerId: 1, preventDefault() {}, currentTarget: handle }));
  await act(async () => rail.resizeHandleProps.onPointerMove({ buttons: 1, clientX: 1000 - 230 }));
  assert.equal(rail.style.width, "518px");
  assert.equal(rail.resizeHandleProps["data-snapped"], undefined);
  await act(async () => rail.resizeHandleProps.onPointerMove({ buttons: 1, clientX: 1000 - 260 }));
  assert.equal(rail.style.width, "544px", "two columns' width");
  assert.equal(rail.resizeHandleProps["data-snapped"], "");
  await act(async () => rail.resizeHandleProps.onPointerMove({ buttons: 1, clientX: 1000 - 515 }));
  assert.equal(rail.style.width, "800px", "three columns' width");
  await act(async () => rail.resizeHandleProps.onPointerUp());
  assert.equal(saved.get("rail"), "800");
  await unmount();
});

test("the window limits what is drawn, and the stored width comes back when it widens", async () => {
  saved.set("rail", "700");
  window.innerWidth = 1000;
  let listener;
  const added = window.addEventListener, removed = window.removeEventListener;
  window.addEventListener = (name, callback) => { if (name === "resize") listener = callback; else added.call(window, name, callback); };
  window.removeEventListener = () => {};
  const unmount = await mount({ storageKey: "rail", maxWindowShare: 0.45 });
  assert.equal(rail.style.width, "450px");
  assert.equal(saved.get("rail"), "700");
  window.innerWidth = 1600;
  await act(async () => listener());
  assert.equal(rail.style.width, "700px");
  window.innerWidth = 800;
  await act(async () => listener());
  assert.equal(rail.style.width, "700px", "a floating rail takes nothing from the centre column");
  await unmount();
  window.addEventListener = added;
  window.removeEventListener = removed;
});

test("without a storage key the rail stores nothing and the handle takes no keys", async () => {
  saved.set("rail", "500");
  const unmount = await mount({});
  assert.equal(rail.style.width, "288px");
  assert.equal(rail.resizeHandleProps.onKeyDown, undefined);
  assert.equal(rail.resizeHandleProps.onDoubleClick, undefined);
  assert.equal(rail.resizeHandleProps.role, undefined);
  await unmount();
});
