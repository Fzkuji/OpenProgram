// Web-tab bottom corners: two masks per page meeting a rounded card edge,
// stacked directly above that page, under whatever is stacked above it.
const assert = require("node:assert/strict");
const { cornerBounds, cornerPage, cornerSpec, createWebCorners } = require("../main/windows/web-corners");

class FakeView {
  constructor() { this.visible = true; this.bounds = null; this.loaded = []; this.webContents = { loadURL: (url) => { this.loaded.push(url); return Promise.resolve(); }, close() {} }; }
  setBackgroundColor() {}
  setVisible(value) { this.visible = value; }
  setBounds(value) { this.bounds = value; }
}
function contentView() {
  const children = [];
  return {
    children,
    addChildView(view, index) {
      const at = children.indexOf(view);
      if (at >= 0) children.splice(at, 1);
      children.splice(index ?? children.length, 0, view);
    },
  };
}

const spec = cornerSpec({ radius: 12, outside: "oklch(0.965 0.0076 264.88)", shadow: "rgba(0, 0, 0, 0.1) 0px 1px 3px 0px" }, 0.9129);
assert.equal(spec.size, 11);
assert.equal(cornerSpec({ radius: 12, outside: "red;</style><script>", shadow: "" }, 1), null, "markup never reaches the page");
assert.equal(cornerSpec({ radius: 0, outside: "red", shadow: "" }, 1), null);
assert.deepEqual(cornerBounds("left", { x: 263, y: 109, width: 975, height: 724 }, 11), { x: 263, y: 822, width: 11, height: 11 });
assert.deepEqual(cornerBounds("right", { x: 263, y: 109, width: 975, height: 724 }, 11), { x: 1227, y: 822, width: 11, height: 11 });
const page = decodeURIComponent(cornerPage("right", spec));
assert.match(page, /left:-12px;top:-12px;width:24px;height:24px;border-radius:12px/);
assert.match(page, /box-shadow:rgba\(0, 0, 0, 0\.1\) 0px 1px 3px 0px,0 0 0 48px oklch\(0\.965 0\.0076 264\.88\)/);

const { syncWebCorners } = createWebCorners({ WebContentsView: FakeView });
const pageA = new FakeView(), overlay = new FakeView(), pageB = new FakeView();
const ctx = { win: { isDestroyed: () => false, contentView: contentView() }, views: new Map([["a", { view: pageA }], ["b", { view: pageB }]]) };
ctx.win.contentView.addChildView(pageA);
ctx.win.contentView.addChildView(overlay);
const corners = { radius: 12, outside: "rgb(240, 242, 247)", shadow: "", zoom: 1 };
const item = (corners) => [{ record: ctx.views.get("a"), bounds: { x: 0, y: 40, width: 800, height: 600 }, corners }];
syncWebCorners(ctx, item(corners));
const [left, right] = ctx.webCorners;
const order = () => ctx.win.contentView.children.map((v) => v === pageA ? "a" : v === pageB ? "b" : v === overlay ? "overlay" : v === left.view ? "L" : "R").join(",");
assert.equal(order(), "a,L,R,overlay", "overlays stay on top");
assert.equal(left.view.visible && right.view.visible, true);
assert.equal(left.view.loaded.length, 1);

// Another page view added later leaves the masks on their page; the same corners reload nothing.
ctx.win.contentView.addChildView(pageB);
syncWebCorners(ctx, item(corners));
assert.equal(order(), "a,L,R,overlay,b");
assert.equal(left.view.loaded.length, 1);
// When that page is the one shown, its masks move up to it.
syncWebCorners(ctx, [{ record: ctx.views.get("b"), bounds: { x: 0, y: 40, width: 800, height: 600 }, corners }]);
assert.equal(order(), "a,overlay,b,L,R");

// Pages without corners (a PiP, an inset pane) hide the masks.
syncWebCorners(ctx, item(null));
assert.equal(left.view.visible || right.view.visible, false);
console.log("web corners ok");
