/**
 * Overlay scrollbars. TS port of the legacy
 * `public/js/shared/scrollbar.js`.
 *
 * Native scrollbar is hidden on selector-matched containers and a
 * floating thumb is drawn on top instead — the thumb takes no layout
 * width, so content extends edge to edge.
 *
 * `initOverlayScrollbars()` is called once by AppShell on mount.
 */

const SELECTORS = [
  ".chat-area",
  ".detail-body",
  ".sidebar-conversations",
  ".sidebar-conv-list",
  ".sidebar",
  ".pg-folders-nav",
  ".settings-content",
  ".live-exec-tree",
  ".main",
  ".code-modal-body",
];

const FADE_DELAY = 900;
const MIN_THUMB = 32;

const installed = new WeakSet<Element>();

function install(el: HTMLElement): void {
  if (installed.has(el)) return;
  installed.add(el);
  el.classList.add("scroll-overlay");

  const thumb = document.createElement("div");
  thumb.className = "overlay-thumb";
  thumb.style.display = "none";
  document.body.appendChild(thumb);

  let fadeTimer: ReturnType<typeof setTimeout> | null = null;
  let rafPending = false;
  let dragging = false;
  let dragStartY = 0;
  let dragStartScrollTop = 0;

  function show(): void {
    thumb.classList.add("visible");
    if (fadeTimer) clearTimeout(fadeTimer);
    fadeTimer = setTimeout(hide, FADE_DELAY);
  }
  function hide(): void {
    if (dragging) return;
    if (thumb.matches(":hover")) return;
    thumb.classList.remove("visible");
  }

  function apply(reveal: boolean): void {
    rafPending = false;
    const rect = el.getBoundingClientRect();
    const sh = el.scrollHeight;
    const ch = el.clientHeight;
    const needs = sh > ch + 1 && rect.height > 0;
    if (!needs) {
      thumb.style.display = "none";
      return;
    }
    const ratio = ch / sh;
    const thumbH = Math.max(MIN_THUMB, rect.height * ratio);
    const maxScroll = sh - ch;
    const scrollRatio = maxScroll > 0 ? el.scrollTop / maxScroll : 0;
    const top = rect.top + scrollRatio * (rect.height - thumbH);
    const right = window.innerWidth - rect.right + 2;
    thumb.style.display = "";
    thumb.style.height = thumbH + "px";
    thumb.style.top = top + "px";
    thumb.style.right = right + "px";
    if (reveal) show();
  }

  function schedule(reveal: boolean): void {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => apply(reveal));
  }

  el.addEventListener("scroll", () => schedule(true), { passive: true });
  el.addEventListener("mouseenter", () => schedule(true));

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => schedule(false)).observe(el);
  }
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(() => schedule(false)).observe(el, {
      childList: true,
      subtree: true,
    });
  }

  thumb.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (el.scrollHeight <= el.clientHeight + 1) return;
    dragging = true;
    dragStartY = e.clientY;
    dragStartScrollTop = el.scrollTop;
    thumb.classList.add("dragging");
    thumb.classList.add("visible");
    e.preventDefault();
  });

  function onDragMove(e: MouseEvent): void {
    if (!dragging) return;
    const rect = el.getBoundingClientRect();
    const sh = el.scrollHeight;
    const ch = el.clientHeight;
    if (sh <= ch + 1) return;
    const thumbH = Math.max(MIN_THUMB, rect.height * (ch / sh));
    const trackH = rect.height - thumbH;
    if (trackH <= 0) return;
    const maxScroll = sh - ch;
    const scrollDelta = ((e.clientY - dragStartY) / trackH) * maxScroll;
    el.scrollTop = dragStartScrollTop + scrollDelta;
  }
  function onDragEnd(): void {
    if (!dragging) return;
    dragging = false;
    thumb.classList.remove("dragging");
    if (fadeTimer) clearTimeout(fadeTimer);
    fadeTimer = setTimeout(hide, FADE_DELAY);
  }
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onDragEnd);

  thumb.addEventListener("mouseenter", () => {
    if (fadeTimer) clearTimeout(fadeTimer);
    thumb.classList.add("visible");
  });
  thumb.addEventListener("mouseleave", () => {
    if (dragging) return;
    if (fadeTimer) clearTimeout(fadeTimer);
    fadeTimer = setTimeout(hide, FADE_DELAY);
  });

  schedule(false);
}

function scan(root?: ParentNode): void {
  (root || document)
    .querySelectorAll<HTMLElement>(SELECTORS.join(","))
    .forEach(install);
}

let started = false;

export function initOverlayScrollbars(): void {
  if (started) return;
  started = true;
  scan(document);
  // Re-scan periodically to pick up dynamically-added containers.
  setInterval(() => scan(document), 2000);
  window.addEventListener("resize", () => {
    document.querySelectorAll(".scroll-overlay").forEach((el) => {
      el.dispatchEvent(new Event("scroll"));
    });
  });
  (window as unknown as { installOverlayScrollbar?: typeof install }).installOverlayScrollbar =
    install;
}
