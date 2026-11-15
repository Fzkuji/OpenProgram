// Bottom corners of web tabs. A web tab's native view escapes the card's
// rounded clip, and Electron rounds a view's four corners alike, while the
// page needs square top corners under the toolbar and rounded bottom ones
// where it meets the card edge. Two small transparent views above each
// page paint what lies outside the card's arc there: the background behind
// the card and the card's own shadow.
const SAFE_CSS = /^[#a-z0-9(),.%\s/+-]*$/i;

function cornerSpec(corners, zoom) {
  const radius = Number(corners?.radius);
  if (!Number.isFinite(radius) || radius <= 0 || radius > 64) return null;
  const outside = String(corners?.outside || "");
  const shadow = String(corners?.shadow || "");
  if (!outside || !SAFE_CSS.test(outside) || !SAFE_CSS.test(shadow)) return null;
  const scale = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  return { radius, outside, shadow, zoom: scale, size: Math.round(radius * scale) };
}

/** The page painted by one corner view: a hole of the card's radius whose
 *  box-shadow draws the card's shadow and, spread wide, the outside colour. */
function cornerPage(side, spec) {
  const r = spec.radius;
  const left = side === "left" ? 0 : -r;
  const shadows = [spec.shadow, `0 0 0 ${Math.ceil(r * 4)}px ${spec.outside}`].filter(Boolean).join(",");
  const html = `<html style="background:transparent"><body style="margin:0;overflow:hidden;zoom:${spec.zoom}">`
    + `<div style="position:absolute;left:${left}px;top:${-r}px;width:${2 * r}px;height:${2 * r}px;`
    + `border-radius:${r}px;box-shadow:${shadows}"></div></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function cornerBounds(side, bounds, size) {
  return {
    x: side === "left" ? bounds.x : bounds.x + bounds.width - size,
    y: bounds.y + bounds.height - size,
    width: size,
    height: size,
  };
}

function createWebCorners({ WebContentsView }) {
  function createCorner(ctx) {
    const view = new WebContentsView({
      webPreferences: { transparent: true, sandbox: true, javascript: false, spellcheck: false },
    });
    view.setBackgroundColor("#00000000");
    view.setVisible(false);
    return { view, url: "" };
  }

  /** Stack `view` directly above `below` (its page, or the page's other
   *  corner), so whatever is stacked above the page stays above it too. */
  function placeAbove(ctx, view, below) {
    const children = ctx.win.contentView.children || [];
    const at = children.indexOf(view), base = children.indexOf(below);
    if (base < 0 || at === base + 1) return;
    // The index is the view's place once it is out of the list.
    ctx.win.contentView.addChildView(view, at >= 0 && at < base ? base : base + 1);
  }

  /** Place corners on every visible page that meets a rounded card edge:
   *  `items` are `{ record, bounds, corners }` with bounds in window DIPs and
   *  `corners` as the renderer measured them (CSS px, with its zoom). */
  function syncWebCorners(ctx, items) {
    if (!ctx || ctx.win.isDestroyed()) return;
    const pool = ctx.webCorners || (ctx.webCorners = []);
    let used = 0;
    for (const { record, bounds, corners } of items) {
      const spec = cornerSpec(corners, corners?.zoom);
      if (!record?.view || !spec || bounds.width < spec.size * 2 || bounds.height < spec.size) continue;
      let below = record.view;
      for (const side of ["left", "right"]) {
        const corner = pool[used] || (pool[used] = createCorner(ctx));
        used += 1;
        const url = cornerPage(side, spec);
        if (corner.url !== url) {
          corner.url = url;
          corner.view.webContents.loadURL(url).catch(() => {});
        }
        placeAbove(ctx, corner.view, below);
        below = corner.view;
        corner.view.setBounds(cornerBounds(side, bounds, spec.size));
        corner.view.setVisible(true);
      }
    }
    for (let index = used; index < pool.length; index += 1) pool[index].view.setVisible(false);
  }

  function closeWebCorners(ctx) {
    for (const corner of ctx?.webCorners || []) {
      try { corner.view.webContents.close(); } catch { /* gone */ }
    }
    if (ctx) ctx.webCorners = [];
  }

  return { closeWebCorners, syncWebCorners };
}

module.exports = { cornerBounds, cornerPage, cornerSpec, createWebCorners };
