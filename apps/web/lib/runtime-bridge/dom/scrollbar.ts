/**
 * Overlay scrollbars. TS port of the legacy
 * `public/js/shared/scrollbar.js`.
 *
 * Native scrollbars are hidden on every element (app/styles/base/
 * scrollbar.css) — macOS Chromium flashes them whenever the pointer
 * enters a scrollable box, so a still pane lit up under a resting cursor.
 * In their place a floating thumb is drawn on top of the content: it takes
 * no layout width, so content extends edge to edge, and it shows only
 * while the box actually scrolls (or while the thumb is held), then fades.
 *
 * A box gets its thumbs the first time it scrolls — a capture-phase scroll
 * listener on the document sees every element's scroll — and drops them
 * once it leaves the DOM. `initOverlayScrollbars()` is called once by
 * AppShell on mount.
 *
 * The thumb paints above everything, so a box inside a dialog keeps its
 * thumb above the dialog. A box that carries `data-scrollbar-layer="content"`
 * keeps its thumb in the content layer instead (`.overlay-thumb-content`,
 * base/scrollbar.css): above its rows, under a control floating on its
 * edge such as the width handle on the Agents page's divider.
 */

const FADE_DELAY = 900;
const MIN_THUMB = 32;
// A scroll this soon after the box changed size is the content settling at
// its new size, not someone scrolling.
const RESIZE_SCROLL_MS = 200;

type Axis = "y" | "x";

const installed = new Map<Element, () => void>();

/** The vertical thumb runs down the container's right edge. When that edge
 *  is a rounded card's (a canvas pane, the centre column), the track stops
 *  short of the card's corners, so the thumb never pokes out past them. */
function track(el: HTMLElement): { top: number; height: number } {
  const rect = el.getBoundingClientRect();
  let topInset = 0, bottomInset = 0;
  for (let node = el.parentElement, depth = 0; node && depth < 6; node = node.parentElement, depth++) {
    const style = getComputedStyle(node);
    const tr = parseFloat(style.borderTopRightRadius) || 0;
    const br = parseFloat(style.borderBottomRightRadius) || 0;
    if (!tr && !br) continue;
    const box = node.getBoundingClientRect();
    if (Math.abs(box.right - rect.right) > 8) break;
    topInset = Math.max(0, tr - (rect.top - box.top));
    bottomInset = Math.max(0, br - (box.bottom - rect.bottom));
    break;
  }
  return { top: rect.top + topInset, height: Math.max(0, rect.height - topInset - bottomInset) };
}

/** One axis's thumb: where it sits, how long it is, and how a drag along
 *  it maps to the box's scroll offset. */
interface AxisModel {
  overflows(): boolean;
  /** Track start, track length and the thumb's length in it. */
  lane(): { start: number; length: number; thumb: number };
  scroll: number;
  maxScroll(): number;
  place(thumb: HTMLDivElement, start: number, length: number): void;
  pointer(e: MouseEvent): number;
}

function axisModel(el: HTMLElement, axis: Axis): AxisModel {
  const size = (lane: number, client: number, scroll: number) =>
    Math.min(lane, Math.max(MIN_THUMB, lane * (client / scroll)));
  if (axis === "y") {
    return {
      overflows: () => el.scrollHeight > el.clientHeight + 1 && el.getBoundingClientRect().height > 0,
      lane() {
        const t = track(el);
        return { start: t.top, length: t.height, thumb: size(t.height, el.clientHeight, el.scrollHeight) };
      },
      get scroll() { return el.scrollTop; },
      set scroll(v: number) { el.scrollTop = v; },
      maxScroll: () => el.scrollHeight - el.clientHeight,
      place(thumb, start, length) {
        const rect = el.getBoundingClientRect();
        thumb.style.top = start + "px";
        thumb.style.height = length + "px";
        thumb.style.right = window.innerWidth - rect.right + 2 + "px";
      },
      pointer: (e) => e.clientY,
    };
  }
  return {
    overflows: () => el.scrollWidth > el.clientWidth + 1 && el.getBoundingClientRect().width > 0,
    lane() {
      const rect = el.getBoundingClientRect();
      return { start: rect.left + 2, length: Math.max(0, rect.width - 4),
        thumb: size(Math.max(0, rect.width - 4), el.clientWidth, el.scrollWidth) };
    },
    get scroll() { return el.scrollLeft; },
    set scroll(v: number) { el.scrollLeft = v; },
    maxScroll: () => el.scrollWidth - el.clientWidth,
    place(thumb, start, length) {
      const rect = el.getBoundingClientRect();
      thumb.style.left = start + "px";
      thumb.style.width = length + "px";
      thumb.style.top = rect.bottom - 8 + "px";
    },
    pointer: (e) => e.clientX,
  };
}

/** A thumb for one axis of ``el``. The element is made on first need, so a
 *  box that never overflows sideways carries no horizontal thumb. */
function axisThumb(el: HTMLElement, axis: Axis, eager: boolean) {
  const model = axisModel(el, axis);
  let thumb: HTMLDivElement | null = null;
  let fadeTimer: ReturnType<typeof setTimeout> | null = null;
  let dragging = false;
  let dragStart = 0;
  let dragStartScroll = 0;

  function make(): HTMLDivElement {
    if (thumb) return thumb;
    const t = document.createElement("div");
    t.className = axis === "y" ? "overlay-thumb" : "overlay-thumb overlay-thumb-x";
    if (el.dataset.scrollbarLayer === "content") t.classList.add("overlay-thumb-content");
    t.style.display = "none";
    t.addEventListener("mousedown", (e) => {
      if (e.button !== 0 || !model.overflows()) return;
      dragging = true;
      dragStart = model.pointer(e);
      dragStartScroll = model.scroll;
      t.classList.add("dragging", "visible");
      e.preventDefault();
    });
    // Only a shown thumb takes the pointer (base/scrollbar.css); resting
    // on it holds it while the box is grabbed or about to be.
    t.addEventListener("mouseenter", () => {
      if (fadeTimer) clearTimeout(fadeTimer);
    });
    t.addEventListener("mouseleave", () => {
      if (dragging) return;
      fadeLater();
    });
    document.body.appendChild(t);
    thumb = t;
    return t;
  }
  if (eager) make();

  function hide(): void {
    if (!thumb || dragging || thumb.matches(":hover")) return;
    thumb.classList.remove("visible");
  }
  function fadeLater(): void {
    if (fadeTimer) clearTimeout(fadeTimer);
    fadeTimer = setTimeout(hide, FADE_DELAY);
  }

  return {
    get visible() { return !!thumb?.classList.contains("visible"); },
    apply(reveal: boolean): void {
      if (!model.overflows()) {
        if (thumb) thumb.style.display = "none";
        return;
      }
      const t = make();
      const lane = model.lane();
      const max = model.maxScroll();
      const ratio = max > 0 ? model.scroll / max : 0;
      model.place(t, lane.start + ratio * (lane.length - lane.thumb), lane.thumb);
      t.style.display = "";
      if (reveal) {
        t.classList.add("visible");
        fadeLater();
      }
    },
    move(e: MouseEvent): void {
      if (!dragging || !model.overflows()) return;
      const lane = model.lane();
      const room = lane.length - lane.thumb;
      if (room <= 0) return;
      model.scroll = dragStartScroll + ((model.pointer(e) - dragStart) / room) * model.maxScroll();
    },
    end(): void {
      if (!dragging) return;
      dragging = false;
      thumb?.classList.remove("dragging");
      fadeLater();
    },
    dispose(): void {
      if (fadeTimer) clearTimeout(fadeTimer);
      thumb?.remove();
    },
  };
}

function install(el: HTMLElement, axes: Axis[], scrolling = false): () => void {
  const existing = installed.get(el);
  if (existing) return existing;
  el.classList.add("scroll-overlay");

  const thumbs = axes.map((axis, i) => axisThumb(el, axis, i === 0));
  let rafPending = false;
  let reveal = false;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let frameId = 0;
  let disposed = false;

  function frame(show: boolean): void {
    reveal = reveal || show;
    if (rafPending || disposed) return;
    rafPending = true;
    frameId = requestAnimationFrame(() => {
      rafPending = false;
      const shown = reveal;
      reveal = false;
      thumbs.forEach((t) => t.apply(shown));
    });
  }

  function schedule(show: boolean): void {
    if (show || thumbs.some((t) => t.visible)) {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = null;
      frame(show);
      return;
    }
    // A hidden thumb only needs its place once things settle. Measuring it
    // every frame of a pane drag or resize forces a layout per frame.
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      idleTimer = null;
      frame(false);
    }, 120);
  }

  // Only a scroll reveals the thumb (macOS style): entering the box,
  // resizing it or the window, and content changes just re-measure it. A
  // resize re-wraps the text and so moves the offset too, once a frame
  // through a whole sidebar drag; those scrolls measure, they do not reveal.
  let size = `${el.clientWidth}x${el.clientHeight}`;
  let resizedAt = -Infinity;
  const onScroll = () => schedule(performance.now() - resizedAt > RESIZE_SCROLL_MS);
  const onMeasure = () => schedule(false);
  const onResize = () => {
    const next = `${el.clientWidth}x${el.clientHeight}`;
    if (next !== size) {
      size = next;
      resizedAt = performance.now();
    }
    onMeasure();
  };
  el.addEventListener("scroll", onScroll, { passive: true });
  el.addEventListener("overlay-scrollbar:measure", onMeasure);

  const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(onResize) : null;
  resize?.observe(el);
  const mutations = typeof MutationObserver !== "undefined" ? new MutationObserver(onMeasure) : null;
  mutations?.observe(el, { childList: true, subtree: true });

  const onDragMove = (e: MouseEvent) => thumbs.forEach((t) => t.move(e));
  const onDragEnd = () => thumbs.forEach((t) => t.end());
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onDragEnd);

  schedule(scrolling);
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(frameId);
    installed.delete(el);
    el.classList.remove("scroll-overlay");
    el.removeEventListener("scroll", onScroll);
    el.removeEventListener("overlay-scrollbar:measure", onMeasure);
    resize?.disconnect();
    mutations?.disconnect();
    window.removeEventListener("mousemove", onDragMove);
    window.removeEventListener("mouseup", onDragEnd);
    if (idleTimer) clearTimeout(idleTimer);
    thumbs.forEach((t) => t.dispose());
  };
  installed.set(el, dispose);
  return dispose;
}

/** The overlay thumb on a scroller the document's listener cannot see,
 *  such as one inside a shadow root. The caller hides that scroller's
 *  native vertical bar; the returned function removes the thumb. */
export function attachOverlayScrollbar(el: HTMLElement): () => void {
  return install(el, ["y"]);
}

let started = false;

export function initOverlayScrollbars(): void {
  if (started) return;
  started = true;
  // Every element's scroll passes the document in the capture phase, so
  // the first real scroll of any box installs its thumbs and shows them.
  document.addEventListener("scroll", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLElement) || installed.has(el)) return;
    // A canvas ghost is a still copy of a leaving pane, gone in a moment.
    if (el.closest("[data-canvas-ghost]")) return;
    // A box whose first scroll comes from a sidebar drag is no scroll either.
    install(el, ["y", "x"], !document.querySelector('.rail-resize-handle[data-resizing="true"]'));
  }, { capture: true, passive: true });
  // A box that left the DOM takes its thumbs with it. rAF-coalesced so a
  // burst of React commits costs one pass.
  if (typeof MutationObserver !== "undefined") {
    let sweepPending = false;
    new MutationObserver(() => {
      if (sweepPending) return;
      sweepPending = true;
      requestAnimationFrame(() => {
        sweepPending = false;
        installed.forEach((dispose, el) => { if (!el.isConnected) dispose(); });
      });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
  window.addEventListener("resize", () => {
    installed.forEach((_dispose, el) => {
      el.dispatchEvent(new Event("overlay-scrollbar:measure"));
    });
  });
}
