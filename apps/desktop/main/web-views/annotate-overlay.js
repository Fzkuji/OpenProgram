// Annotate mode: the user marks up a web tab and sends the notes to a chat.
// The two functions below are injected as source text into the page's
// isolated world (webtab-annotate.js), so each must stay self-contained:
// no closures over this module, no require. The page can neither see nor
// call the isolated world, and the overlay lives in a closed shadow root,
// so the page's CSS cannot restyle it.
"use strict";

/** A short selector a reader (and an agent) can find the element by:
 *  `h1#firstHeading`, `nav.menu > a:nth-of-type(3)`. It climbs at most four
 *  levels and stops at an id or as soon as it is unique on the page. */
function shortSelector(el) {
  const ident = /^[A-Za-z_][\w-]*$/;
  const doc = el.ownerDocument;
  const unique = (selector) => {
    try { return doc.querySelectorAll(selector).length === 1; } catch (_error) { return false; }
  };
  const parts = [];
  for (let node = el; node && node.nodeType === 1 && parts.length < 4; node = node.parentElement) {
    const tag = node.tagName.toLowerCase();
    if (node.id && ident.test(node.id)) { parts.unshift(tag + "#" + node.id); break; }
    if (tag === "html" || tag === "body") { if (!parts.length) parts.unshift(tag); break; }
    let part = tag;
    // A readable class, not a generated one (css-1x2y3z, sc-4823).
    const cls = Array.from(node.classList || []).find((name) => ident.test(name) && name.length <= 32 && !/\d{3,}/.test(name));
    if (cls) part += "." + cls;
    const parent = node.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children);
      const sameTag = siblings.filter((child) => child.tagName === node.tagName);
      const matching = cls ? sameTag.filter((child) => child.classList.contains(cls)) : sameTag;
      if (matching.length > 1) part += ":nth-of-type(" + (sameTag.indexOf(node) + 1) + ")";
    }
    parts.unshift(part);
    if (unique(parts.join(" > "))) break;
  }
  return parts.join(" > ");
}

/** Installs the overlay on `win` and returns its API (also kept on
 *  `win.__openprogramAnnotate`). The host drives it with enter / exit /
 *  discard / sent / unsent and reads what happens with next(), which
 *  resolves with the next event: { type: "state", active, count } or
 *  { type: "send", url, title, viewport, notes }. */
function installAnnotate(win, shortSelector, shadowMode) {
  const doc = win.document;
  const host = doc.createElement("openprogram-annotate");
  for (const [name, value] of [
    ["all", "initial"], ["position", "fixed"], ["inset", "0"], ["z-index", "2147483647"],
    ["pointer-events", "none"], ["display", "block"], ["contain", "layout style"],
  ]) host.style.setProperty(name, value, "important");
  // Closed in the page; tests pass "open" to look inside.
  const root = host.attachShadow({ mode: shadowMode === "open" ? "open" : "closed" });

  const el = (tag, cls, text) => {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const style = el("style");
  style.textContent = [
    ":host{--op-bg:#fff;--op-border:rgba(0,0,0,.12);--op-text:#3d3d3a;--op-muted:#91908c;--op-accent:#3b82f6}",
    "*{box-sizing:border-box}",
    ".ui{position:fixed;inset:0;font:12px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;color:var(--op-text);pointer-events:none}",
    ".layer{position:fixed;inset:0;cursor:crosshair;pointer-events:auto;background:transparent}",
    ".hover{position:fixed;border:1.5px solid var(--op-accent);background:color-mix(in srgb,var(--op-accent) 10%,transparent);border-radius:3px;pointer-events:none}",
    ".drag,.outline{position:fixed;border-radius:3px;pointer-events:none}",
    ".drag,.outline.region{border:2px dashed var(--op-accent);background:color-mix(in srgb,var(--op-accent) 8%,transparent)}",
    ".outline.element{border:2px solid var(--op-accent)}",
    ".size{position:fixed;padding:1px 6px;border-radius:4px;background:#1e1e20;color:#fff;font-size:11px;pointer-events:none}",
    ".pin{position:fixed;width:20px;height:20px;margin:0;padding:0;border-radius:50%;border:2px solid #fff;background:var(--op-accent);color:#fff;font-family:inherit;font-weight:600;font-size:11px;line-height:16px;text-align:center;cursor:pointer;pointer-events:auto;box-shadow:0 1px 4px rgba(0,0,0,.3)}",
    ".hint{position:fixed;top:12px;left:50%;transform:translateX(-50%);padding:6px 14px;border-radius:999px;background:rgba(24,24,27,.92);color:#fff;white-space:nowrap;box-shadow:0 4px 16px rgba(0,0,0,.25)}",
    ".card{position:fixed;width:280px;padding:10px;border:1px solid var(--op-border);border-radius:10px;background:var(--op-bg);color:var(--op-text);pointer-events:auto;box-shadow:0 8px 28px rgba(0,0,0,.18);display:flex;flex-direction:column;gap:8px}",
    ".head{display:flex;align-items:center;gap:8px;min-width:0}",
    ".num{flex:none;width:18px;height:18px;border-radius:50%;background:var(--op-accent);color:#fff;font-weight:600;font-size:11px;line-height:18px;text-align:center}",
    ".sel{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--op-muted)}",
    "button{font:inherit;color:inherit;cursor:pointer}",
    ".ghost{border:0;background:transparent;padding:3px 8px;border-radius:6px;color:var(--op-muted)}",
    ".ghost:hover{background:color-mix(in srgb,var(--op-text) 8%,transparent);color:var(--op-text)}",
    ".primary{border:0;background:var(--op-accent);color:#fff;padding:4px 12px;border-radius:6px;font-weight:500}",
    ".primary:hover{filter:brightness(1.08)}",
    "textarea{width:100%;min-height:56px;resize:none;padding:6px 8px;border:1px solid var(--op-border);border-radius:6px;background:transparent;color:inherit;font:inherit;outline:none}",
    "textarea:focus{border-color:var(--op-accent)}",
    "textarea::placeholder{color:var(--op-muted)}",
    ".toggle{display:flex;align-items:center;gap:6px;width:100%;border:0;background:transparent;padding:2px 0;color:var(--op-muted);text-align:left}",
    ".toggle:hover{color:var(--op-text)}",
    ".chev{display:inline-block;transition:transform .15s}",
    ".toggle[aria-expanded=true] .chev{transform:rotate(90deg)}",
    ".adjust{display:grid;grid-template-columns:56px 1fr 52px;align-items:center;gap:6px 8px}",
    ".adjust[hidden]{display:none}",
    ".adjust label{color:var(--op-muted)}",
    ".adjust input[type=range]{width:100%;accent-color:var(--op-accent);margin:0}",
    ".adjust input[type=color]{width:100%;height:20px;padding:0;border:1px solid var(--op-border);border-radius:4px;background:transparent}",
    ".val{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--op-muted);text-align:right}",
    ".bar{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:6px;padding:6px 6px 6px 14px;border:1px solid var(--op-border);border-radius:10px;background:var(--op-bg);color:var(--op-text);pointer-events:auto;white-space:nowrap;box-shadow:0 8px 28px rgba(0,0,0,.18)}",
    ".count{margin-right:6px}",
    "[hidden]{display:none!important}",
  ].join("\n");
  const ui = el("div", "ui");
  const layer = el("div", "layer");
  const hover = el("div", "hover");
  const drag = el("div", "drag");
  const size = el("div", "size");
  const marks = el("div");
  const hint = el("div", "hint");
  const bar = el("div", "bar");
  const count = el("span", "count");
  const discardButton = el("button", "ghost");
  const sendButton = el("button", "primary");
  discardButton.type = sendButton.type = "button";
  bar.append(count, discardButton, sendButton);
  ui.append(layer, hover, drag, size, marks, hint, bar);
  root.append(style, ui);
  // Key presses typed into a note stay with the note: the page's own
  // shortcuts (bubbling listeners) never see them.
  for (const type of ["keydown", "keyup", "keypress"]) {
    root.addEventListener(type, (event) => { if (event.key !== "Escape") event.stopPropagation(); });
  }

  let labels = {};
  let active = false;
  let capturing = false;
  let notes = [];
  let open = null;
  let start = null;
  let dragRect = null;
  let frame = 0;
  const queue = [];
  let waiter = null;

  const emit = (event) => {
    if (waiter) { const resolve = waiter; waiter = null; resolve(event); return; }
    // Only the latest state matters to a host that has not asked yet.
    if (event.type === "state" && queue.length && queue[queue.length - 1].type === "state") queue.pop();
    queue.push(event);
  };
  const report = () => emit({ type: "state", active, count: notes.length });
  const label = (key, fallback) => (typeof labels[key] === "string" && labels[key]) || fallback;

  const clip = (value, limit) => {
    const flat = String(value || "").replace(/\s+/g, " ").trim();
    return flat.length > limit ? flat.slice(0, limit - 1) + "…" : flat;
  };
  const px = (value) => Math.round(parseFloat(value) || 0);
  const hex = (color) => {
    const m = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(color || "");
    if (!m) return "#000000";
    return "#" + [m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, "0")).join("");
  };
  const targetAt = (x, y) => {
    const hits = doc.elementsFromPoint(x, y);
    return hits.find((node) => node !== host && !host.contains(node)) || null;
  };
  const rectOf = (note) => {
    if (note.kind === "region") {
      return { left: note.doc.x - win.scrollX, top: note.doc.y - win.scrollY, width: note.doc.width, height: note.doc.height };
    }
    if (!note.el.isConnected) return null;
    const r = note.el.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  };
  const place = (node, r) => {
    node.style.left = r.left + "px";
    node.style.top = r.top + "px";
    node.style.width = r.width + "px";
    node.style.height = r.height + "px";
  };

  // --- live preview of Adjust ------------------------------------------
  const setPreview = (note, property, value) => {
    const s = note.el.style;
    if (!note.saved.has(property)) {
      note.saved.set(property, { value: s.getPropertyValue(property), priority: s.getPropertyPriority(property) });
    }
    s.setProperty(property, value, "important");
  };
  const restore = (note) => {
    if (!note.saved) return;
    for (const [property, old] of note.saved) {
      if (old.value) note.el.style.setProperty(property, old.value, old.priority);
      else note.el.style.removeProperty(property);
    }
    note.saved.clear();
  };

  // --- notes -----------------------------------------------------------
  const adjustRow = (note, key, title, input, show) => {
    const name = el("label", null, title);
    const value = el("span", "val", show());
    input.addEventListener("input", () => {
      const change = note.adjust[key];
      change.to = key === "color" ? input.value : input.value + "px";
      setPreview(note, key, change.to);
      value.textContent = show();
    });
    return [name, input, value];
  };
  const buildCard = (note) => {
    const card = el("div", "card");
    const head = el("div", "head");
    const num = el("span", "num", String(note.n));
    const sel = el("span", "sel", note.kind === "region"
      ? "region " + Math.round(note.doc.width) + "×" + Math.round(note.doc.height)
      : note.selector);
    sel.title = sel.textContent;
    const del = el("button", "ghost", label("delete", "Delete"));
    del.type = "button";
    del.addEventListener("click", () => removeNote(note));
    head.append(num, sel, del);
    const input = el("textarea");
    input.placeholder = label("placeholder", "What should change?");
    input.value = note.comment;
    input.addEventListener("input", () => { note.comment = input.value; });
    card.append(head, input);
    if (note.kind === "element") {
      const toggle = el("button", "toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      toggle.append(el("span", "chev", "›"), el("span", null, label("adjust", "Adjust")));
      const grid = el("div", "adjust");
      grid.hidden = true;
      toggle.addEventListener("click", () => {
        grid.hidden = !grid.hidden;
        toggle.setAttribute("aria-expanded", String(!grid.hidden));
        layout();
      });
      const sizeInput = el("input");
      sizeInput.type = "range"; sizeInput.min = "8"; sizeInput.max = "96"; sizeInput.step = "1";
      sizeInput.value = String(px(note.adjust["font-size"].from));
      const padInput = el("input");
      padInput.type = "range"; padInput.min = "0"; padInput.max = "64"; padInput.step = "1";
      padInput.value = String(px(note.adjust.padding.from));
      const colorInput = el("input");
      colorInput.type = "color";
      colorInput.value = note.adjust.color.from;
      grid.append(
        ...adjustRow(note, "font-size", label("size", "Size"), sizeInput, () => note.adjust["font-size"].to),
        ...adjustRow(note, "padding", label("spacing", "Spacing"), padInput, () => px(note.adjust.padding.to) + "px"),
        ...adjustRow(note, "color", label("color", "Color"), colorInput, () => note.adjust.color.to),
      );
      card.append(toggle, grid);
    }
    return { card, input };
  };
  const addNote = (fields) => {
    const note = { n: notes.length + 1, comment: "", saved: new Map(), adjust: {}, ...fields };
    if (note.kind === "element") {
      const cs = win.getComputedStyle(note.el);
      const color = hex(cs.color);
      note.selector = shortSelector(note.el) || note.el.tagName.toLowerCase();
      note.adjust = {
        "font-size": { from: cs.fontSize, to: cs.fontSize },
        padding: { from: cs.padding || [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft].join(" "), to: "" },
        color: { from: color, to: color },
      };
      note.adjust.padding.to = note.adjust.padding.from;
    }
    note.outline = el("div", "outline " + note.kind);
    note.pin = el("button", "pin", String(note.n));
    note.pin.type = "button";
    note.pin.addEventListener("click", () => openNote(open === note ? null : note));
    const { card, input } = buildCard(note);
    note.card = card;
    note.input = input;
    marks.append(note.outline, note.pin, note.card);
    notes.push(note);
    openNote(note);
    report();
  };
  const renumber = () => {
    notes.forEach((note, index) => {
      note.n = index + 1;
      note.pin.textContent = String(note.n);
      note.card.querySelector(".num").textContent = String(note.n);
    });
  };
  const removeNote = (note) => {
    restore(note);
    note.outline.remove(); note.pin.remove(); note.card.remove();
    notes = notes.filter((other) => other !== note);
    if (open === note) open = null;
    renumber();
    render();
    report();
  };
  const clearNotes = () => {
    for (const note of notes) {
      restore(note);
      note.outline.remove(); note.pin.remove(); note.card.remove();
    }
    notes = [];
    open = null;
  };
  const openNote = (note) => {
    open = note;
    render();
    if (note) win.requestAnimationFrame(() => note.input.focus());
  };

  // --- drawing ---------------------------------------------------------
  const layout = () => {
    const vw = win.innerWidth;
    const vh = win.innerHeight;
    for (const note of notes) {
      const r = rectOf(note);
      const shown = !!r;
      note.outline.hidden = !shown;
      note.pin.hidden = !shown;
      if (!shown) { note.card.hidden = true; continue; }
      place(note.outline, r);
      note.pin.style.left = Math.min(Math.max(2, r.left + r.width - 10), vw - 22) + "px";
      note.pin.style.top = Math.min(Math.max(2, r.top - 10), vh - 22) + "px";
      note.card.hidden = capturing || open !== note;
      if (note.card.hidden) continue;
      const cw = 280;
      const ch = note.card.offsetHeight || 140;
      let left = r.left + r.width + 12;
      let top = r.top;
      if (left + cw > vw - 8) left = r.left - 12 - cw;
      if (left < 8) {
        left = Math.min(Math.max(8, r.left), vw - cw - 8);
        top = r.top + r.height + 12;
        if (top + ch > vh - 8) top = r.top - ch - 12;
      }
      note.card.style.left = Math.max(8, left) + "px";
      note.card.style.top = Math.min(Math.max(8, top), Math.max(8, vh - ch - 8)) + "px";
    }
  };
  const render = () => {
    layer.hidden = !active || capturing;
    hint.hidden = !active || capturing;
    hint.textContent = label("hint", "Click an element or drag a box · Esc to finish");
    if (!active || capturing) hover.hidden = true;
    drag.hidden = size.hidden = !dragRect;
    bar.hidden = capturing || notes.length === 0;
    count.textContent = notes.length === 1
      ? label("countOne", "1 note on this page")
      : label("countMany", "{n} notes on this page").replace("{n}", String(notes.length));
    discardButton.textContent = label("discard", "Discard");
    sendButton.textContent = label("send", "Send to chat");
    layout();
    if (!active && notes.length === 0) unmount();
  };
  const tick = () => {
    frame = 0;
    if (!host.isConnected) return;
    layout();
    frame = win.requestAnimationFrame(tick);
  };
  const mount = () => {
    if (!host.isConnected) doc.documentElement.appendChild(host);
    if (!frame) frame = win.requestAnimationFrame(tick);
  };
  const unmount = () => {
    host.remove();
    if (frame) { win.cancelAnimationFrame(frame); frame = 0; }
  };

  // --- pointer and keys -------------------------------------------------
  const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
  layer.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    stop(event);
    start = { x: event.clientX, y: event.clientY };
    dragRect = null;
  });
  layer.addEventListener("mousemove", (event) => {
    if (!start) {
      const target = targetAt(event.clientX, event.clientY);
      hover.hidden = !target;
      if (target) {
        const r = target.getBoundingClientRect();
        place(hover, { left: r.left, top: r.top, width: r.width, height: r.height });
      }
      return;
    }
    stop(event);
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (!dragRect && Math.hypot(dx, dy) < 5) return;
    hover.hidden = true;
    const left = Math.max(0, Math.min(start.x, event.clientX));
    const top = Math.max(0, Math.min(start.y, event.clientY));
    const right = Math.min(win.innerWidth, Math.max(start.x, event.clientX));
    const bottom = Math.min(win.innerHeight, Math.max(start.y, event.clientY));
    dragRect = { left, top, width: right - left, height: bottom - top };
    place(drag, dragRect);
    size.textContent = Math.round(dragRect.width) + "×" + Math.round(dragRect.height);
    size.style.left = left + "px";
    size.style.top = (bottom + 22 < win.innerHeight ? bottom + 4 : Math.max(0, top - 20)) + "px";
    render();
  });
  layer.addEventListener("mouseleave", () => { hover.hidden = true; });
  win.addEventListener("mouseup", (event) => {
    if (!start || !active) return;
    stop(event);
    const from = start;
    const rect = dragRect;
    start = null;
    dragRect = null;
    if (rect) {
      render();
      if (rect.width >= 8 && rect.height >= 8) {
        addNote({ kind: "region", doc: { x: rect.left + win.scrollX, y: rect.top + win.scrollY, width: rect.width, height: rect.height } });
      }
      return;
    }
    const target = targetAt(from.x, from.y);
    if (!target) return;
    const existing = notes.find((note) => note.el === target);
    if (existing) openNote(existing);
    else addNote({ kind: "element", el: target });
  }, true);
  win.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !host.isConnected || capturing) return;
    if (!active && !open) return;
    stop(event);
    if (active) exit();
    else openNote(null);
  }, true);
  discardButton.addEventListener("click", () => api.discard());
  sendButton.addEventListener("click", () => {
    if (!notes.length || capturing) return;
    capturing = true;
    open = null;
    render();
    // The pins stay for the screenshot; the cards, hint and bar leave it.
    // Two frames (or 150 ms on a page that is not painting) let that show.
    let done = false;
    const settle = () => {
      if (done) return;
      done = true;
      emit({
        type: "send",
        url: win.location.href,
        title: doc.title,
        viewport: { width: win.innerWidth, height: win.innerHeight },
        notes: notes.map((note) => {
          const r = rectOf(note) || { left: 0, top: 0, width: 0, height: 0 };
          return {
            n: note.n,
            kind: note.kind,
            selector: note.kind === "element" ? note.selector : "",
            tag: note.kind === "element" ? note.el.tagName.toLowerCase() : "",
            text: note.kind === "element" ? clip(note.el.innerText || note.el.textContent, 160) : "",
            comment: note.comment.trim(),
            rect: { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) },
            adjust: note.kind === "element"
              ? Object.entries(note.adjust)
                .filter(([, change]) => change.to && change.to !== change.from)
                .map(([property, change]) => ({ property, from: change.from, to: change.to }))
              : [],
          };
        }),
      });
    };
    win.requestAnimationFrame(() => win.requestAnimationFrame(settle));
    win.setTimeout(settle, 150);
  });
  const onViewport = () => { if (host.isConnected) layout(); };
  win.addEventListener("scroll", onViewport, { capture: true, passive: true });
  win.addEventListener("resize", onViewport, { passive: true });

  const exit = () => {
    active = false;
    start = null;
    dragRect = null;
    open = null;
    render();
    report();
  };
  const api = {
    enter(next) {
      labels = (next && next.labels) || labels;
      const theme = (next && next.theme) || {};
      for (const key of ["bg", "border", "text", "muted", "accent"]) {
        if (typeof theme[key] === "string" && theme[key]) host.style.setProperty("--op-" + key, theme[key]);
      }
      if (win.__openprogramPick) win.__openprogramPick.cancel();
      active = true;
      mount();
      render();
      report();
    },
    exit() { if (active) exit(); },
    discard() {
      clearNotes();
      capturing = false;
      exit();
    },
    /** The host sent the notes: clear them and leave annotate mode. */
    sent() {
      capturing = false;
      clearNotes();
      exit();
    },
    /** The host could not send: everything comes back as it was. */
    unsent() {
      capturing = false;
      render();
    },
    next() {
      if (queue.length) return Promise.resolve(queue.shift());
      if (waiter) waiter({ type: "superseded" });
      return new Promise((resolve) => { waiter = resolve; });
    },
  };
  win.__openprogramAnnotate = api;
  return api;
}

/** The isolated-world source for one host command. Only "start" installs
 *  the overlay; it reports whether it had to (a fresh page). */
function annotateCommandSource(action, options) {
  if (action === "start") {
    return "(() => {"
      + "const fresh = !window.__openprogramAnnotate;"
      + "const api = window.__openprogramAnnotate || (" + installAnnotate.toString() + ")(window, " + shortSelector.toString() + ");"
      + "api.enter(" + JSON.stringify(options || {}) + ");"
      + "return { fresh };"
      + "})()";
  }
  if (action === "next") return "window.__openprogramAnnotate ? window.__openprogramAnnotate.next() : null";
  return "(() => { const api = window.__openprogramAnnotate; if (!api) return false; api["
    + JSON.stringify(action) + "](); return true; })()";
}

module.exports = { shortSelector, installAnnotate, annotateCommandSource };
