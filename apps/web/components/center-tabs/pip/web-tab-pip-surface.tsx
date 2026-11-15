"use client";

import { useEffect, useRef } from "react";
import * as desktop from "@/lib/desktop/desktop-bridge";
import { isWebTabOccluded, measureWebTabBounds, type WebTabBounds } from "@/lib/browser/web-tab/web-tab-bounds";
import { watchPosition } from "@/lib/browser/web-tab/watch-position";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { getSnapshot, setSnapshot, useWebTabPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import { PIP_PICTURE_WAIT_MS, PIP_RELAYOUT_MS } from "@/lib/browser/web-tab/web-tab-pip-motion";
import { pictureEdgeColour } from "@/lib/browser/web-tab/picture-edge";
import { useTranslation } from "@/lib/i18n";
import styles from "../browser/browser.module.css";

/** Why the preview shows a still picture of its page instead of the live
 *  native one: the native view sits above every page element and cannot
 *  move with the frame's transforms, fade, scale or be cut by a scrolling
 *  column, so whenever any of that happens the picture stands in.
 *  - `gesture`: a drag or resize of the frame, from its first movement
 *    (`op:pip-gesture-start/end`); a press alone holds nothing, and the
 *    picture taken at the press is the one shown once it moves;
 *  - `clip`: docked and partly scrolled out of the graph's side column
 *    (`data-pip-clipped` on the frame);
 *  - `cover`: a menu, dialog or popover over the page (OCCLUDERS), which
 *    the native view would draw over;
 *  - `motion`: the frame folding into the composer's web page chip or
 *    unfolding out of it (`op:pip-hold` / `op:pip-release`, and
 *    `holdOnMount` for a frame that unfolds as it appears).
 *  The page goes live again once no reason is left. */
export type PipHoldReason = "gesture" | "clip" | "cover" | "motion";
export type PipHoldDetail = { reason: PipHoldReason; ready?: () => void };

/** Page elements the native view would draw over: while one overlaps the
 *  preview, its picture stands in for the page (the `cover` hold). */
const OCCLUDERS = '[role="dialog"], [role="menu"], [role="listbox"], [data-native-view-occluder="true"]';

/** Each page's colour along the bottom of its last picture
 *  (picture-edge.ts), kept with the picture it was taken from. */
const pictureEdges = new Map<string, string>();

/** Hosts the existing Page live while the preview rests, and its still
 *  picture while any hold reason stands (see PipHoldReason). */
export function WebTabPipSurface({ tabId, url, native, holdOnMount = false }: {
  tabId: string; url: string; native: boolean; holdOnMount?: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const initialUrl = useRef(url);
  const initialHold = useRef(holdOnMount);
  const stillRef = useRef<HTMLImageElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const { text } = useTranslation();
  useEffect(() => {
    const bridge = desktop.desktopBridge();
    const body = bodyRef.current;
    if (!native || !bridge || !body) return;
    desktop.installDesktopMenuHandlers();
    desktop.ensureWebView(bridge, tabId, initialUrl.current);
    desktop.setFloatingWebTab(tabId, true);
    let disposed = false;
    let frame = 0;
    const holds = new Set<PipHoldReason>();
    // Between the last hold's release and the live page's first paint.
    let settling = false;
    let generation = 0;
    let preparing: Promise<void> = Promise.resolve();
    const waits = new Map<ReturnType<typeof setTimeout>, () => void>();
    // Capture/decoding failure must not leave a live page permanently hidden.
    const bounded = <T,>(work: Promise<T>, ms = 900): Promise<T | null> => new Promise(resolve => {
      const timer = setTimeout(() => { waits.delete(timer); resolve(null); }, ms);
      waits.set(timer, () => resolve(null));
      work.then(resolve, () => resolve(null)).finally(() => {
        clearTimeout(timer);
        waits.delete(timer);
      });
    });
    const captureFrame = async () => remember(await bridge.webTab.capture?.(tabId, "presentation"));
    /** Decode `data` and keep it as the page's last picture. */
    const remember = async (data: string | null | undefined) => {
      if (!data) return null;
      const decoded = document.createElement("img");
      decoded.src = data;
      await decoded.decode();
      const edge = pictureEdgeColour(decoded);
      if (edge) pictureEdges.set(tabId, edge);
      else pictureEdges.delete(tabId);
      setSnapshot(tabId, data);
      return data;
    };
    /** Show `picture` in place of the page: scaled to the frame's width
     *  from the top, as the live page is at any size, over the page's own
     *  bottom edge colour where the frame is taller (picture-edge.ts), so
     *  no frame of a held snap, unsnap or resize shows a bar of the
     *  preview's background beside the page. */
    const showStill = (picture: string) => {
      const image = stillRef.current;
      if (!image) return;
      image.src = picture;
      image.hidden = false;
      const edge = pictureEdges.get(tabId) ?? "";
      if (body.style.backgroundColor !== edge) body.style.backgroundColor = edge;
    };
    /** Put the still picture up and take the live page down. Resolves once
     *  the live page is down, within PIP_PICTURE_WAIT_MS: whatever moves
     *  next never moves under a page that stays put. A slower capture
     *  shows the last picture of this page meanwhile and replaces it when
     *  it comes. */
    const hold = (reason: PipHoldReason, taken?: Promise<string | null>): Promise<void> => {
      const first = holds.size === 0;
      holds.add(reason);
      if (!first) return preparing;
      const token = ++generation;
      const held = () => !disposed && token === generation && holds.size > 0;
      const down = (picture?: string | null) => {
        if (picture) showStill(picture);
        desktop.removeVisibleWebTabBounds(bridge, tabId);
        desktop.setWebTabReady(tabId, false);
      };
      preparing = (async () => {
        const capture = bounded(taken ?? captureFrame());
        // A popover paints in the frame it opens: waiting for a new
        // picture left the page drawn over it until then, a flash at every
        // open. Covered, the page goes down at once under its last picture
        // (kept fresh each time the page comes up), and the new one
        // replaces it when it comes.
        const now = reason === "cover" ? getSnapshot(tabId) : null;
        if (now) down(now);
        const data = await bounded(capture, PIP_PICTURE_WAIT_MS);
        if (!held()) return;
        if (!now) down(data ?? getSnapshot(tabId));
        else if (data) showStill(data);
        if (!data) void capture.then(late => { if (late && held()) showStill(late); });
      })();
      return preparing;
    };
    /** Drop `reason`; with none left, bring the live page back over the
     *  picture once it has painted at its bounds. */
    // The page lays out at the preview's virtual viewport (setPipZoom); it
    // must be laid out at the size it shows at before it shows.
    let zoomWidth = 0;
    let zoomHeight = 0;
    const fit = (bounds: { width: number; height: number }) => {
      if (zoomWidth === bounds.width && zoomHeight === bounds.height) return false;
      bridge.webTab.setPipZoom?.(tabId, bounds.width, bounds.height);
      zoomWidth = bounds.width;
      zoomHeight = bounds.height;
      return true;
    };
    /** Lay the hidden page out at `bounds` and wait for it to paint there. */
    const prepare = async (bounds: ReturnType<typeof measureWebTabBounds>) => {
      const token = generation;
      const relaid = fit(bounds);
      // Update the hidden native surface once; capturePage waits for a paint
      // without changing the fixed virtual viewport or attaching a debugger.
      bridge.webTab.setBounds(tabId, bounds);
      await snap();
      if (!relaid || disposed || token !== generation) return;
      // Pages that lay themselves out from script answer the new viewport
      // after a resize handler or two; give them that before showing.
      await bounded(new Promise(resolve => setTimeout(resolve, PIP_RELAYOUT_MS)), PIP_RELAYOUT_MS);
      // A hold taken meanwhile pictures the page itself.
      if (disposed || token !== generation) return;
      await snap();
    };
    /** Wait for the hidden page's next paint; what it painted becomes its
     *  last picture, the one a cover shows at once. */
    const snap = async () => {
      const shot = await bounded(bridge.webTab.capture?.(tabId, "presentation") ?? Promise.resolve(null));
      if (shot) void remember(shot).catch(() => null);
    };
    const release = async (reason: PipHoldReason) => {
      if (!holds.delete(reason) || holds.size) return;
      const token = generation;
      settling = true;
      await preparing;
      if (disposed || token !== generation || holds.size) { settling = false; return; }
      settleFrame();
      await prepare(measureWebTabBounds(body));
      settling = false;
      if (disposed || token !== generation || holds.size) return;
      reportSettled();
    };
    // Pressed, the frame may be about to move: its picture is taken now,
    // while the live page stays up, so it is ready by the first movement.
    let pressed: Promise<string | null> | null = null;
    const press = () => { pressed = captureFrame().catch(() => null); };
    const beginGesture = () => { void hold("gesture", pressed ?? undefined); pressed = null; };
    const endGesture = () => release("gesture");
    const onHold = (event: Event) => {
      const detail = (event as CustomEvent<PipHoldDetail>).detail;
      void hold(detail.reason).then(() => detail.ready?.());
    };
    const onRelease = (event: Event) => {
      void release((event as CustomEvent<PipHoldDetail>).detail.reason);
    };
    if (initialHold.current) {
      // Appearing with a fold motion: the last picture shows from the first
      // frame, and the live page waits for the motion's release.
      holds.add("motion");
      const picture = getSnapshot(tabId);
      if (picture) showStill(picture);
    }
    /** The still picture stands in for the live page while a hold stands,
     *  and goes once the page is up again: in the turn that registers the
     *  page's bounds as ready. Those bounds reach the browser process in a
     *  microtask, before the renderer's next frame, the first one without
     *  the picture, so no frame shows neither. Left up, it lay under the
     *  live page after every drag and showed, stale and stretched, when the
     *  page next went down at rest to lay out at a new size. */
    const dropStill = () => {
      const image = stillRef.current;
      if (image && !image.hidden) image.hidden = true;
      if (body.style.backgroundColor) body.style.backgroundColor = "";
    };
    // Declared here for `report`, which reads it; created with the other observers below.
    let mutation: MutationObserver | null = null;
    // What the last measurement found: where the page was, and the
    // occluders on the page then.
    let measured: WebTabBounds | null = null;
    let occluders: Element[] = [];
    const report = () => {
      if (disposed) return;
      // This measures the page as it stands, after every change still
      // queued: those need no measurement of their own in the next frame.
      // The frame's own place, written just before its geometry event, is
      // one of them in every frame a pane moves.
      mutation?.takeRecords();
      const bounds = measureWebTabBounds(body);
      measured = bounds;
      occluders = Array.from(document.querySelectorAll(OCCLUDERS));
      if (bounds.width <= 0 || bounds.height <= 0) {
        if (!holds.size) dropStill();
        desktop.removeVisibleWebTabBounds(bridge, tabId);
        desktop.setWebTabReady(tabId, false);
        return;
      }
      const wanted = {
        cover: isWebTabOccluded(bounds, occluders),
        clip: !!body.closest('[data-pip-clipped="true"]'),
      };
      let changed = false;
      for (const reason of ["cover", "clip"] as const) {
        if (wanted[reason] === holds.has(reason)) continue;
        changed = true;
        void (wanted[reason] ? hold(reason) : release(reason));
      }
      if (changed) return;
      if (holds.size || settling) return;
      if (zoomWidth !== bounds.width || zoomHeight !== bounds.height) {
        // First shown, or resized at rest: out of sight until it has laid
        // out at its new size, so it never shows at its standard size first.
        // Nothing is held, so no picture belongs here: an older one would
        // show stretched to the new size until the page came back.
        const token = generation;
        settling = true;
        dropStill();
        desktop.removeVisibleWebTabBounds(bridge, tabId);
        void prepare(bounds).then(() => {
          // A hold taken meanwhile owns the page now; its release shows it.
          if (disposed || token !== generation) return;
          settling = false;
          reportSettled();
        });
        return;
      }
      desktop.registerVisibleWebTabBounds(bridge, tabId, bounds);
      desktop.setWebTabReady(tabId, true);
      dropStill();
    };
    const pip = body.closest('[data-pip="true"]');
    // The frame is placed from script against a pane that layout moves (a
    // sidebar sliding open). Measured before the frame has followed, the
    // page would be sent to last frame's offset in this frame's pane and
    // slide about inside a frame that stays put; so the frame follows first
    // (web-tab-pip.tsx) whenever a measurement is not already inside its own
    // geometry event.
    const settleFrame = () => { pip?.dispatchEvent(new Event("op:pip-settle")); };
    const reportSettled = () => { settleFrame(); report(); };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(() => { frame = 0; reportSettled(); });
    };
    // Drag writes already occur once per animation frame. Publish in that frame,
    // rather than waiting for ResizeObserver (which does not observe transforms).
    pip?.addEventListener("op:pip-geometry", report);
    pip?.addEventListener("pointerdown", press, true);
    pip?.addEventListener("op:pip-gesture-start", beginGesture);
    pip?.addEventListener("op:pip-gesture-end", endGesture);
    pip?.addEventListener("op:pip-hold", onHold);
    pip?.addEventListener("op:pip-release", onRelease);
    // What can move the page or cover it, and so asks for a measurement:
    // its own size (this observer), its place in the window (the position
    // watch below), an attribute of the frame or of an element around it
    // (the frame's place, docked or clipped, a pane's style), and an
    // occluder coming, going or changing. Anything else on the page (the
    // composer's rows resizing beside it, a reply streaming in, an
    // animated icon) changes none of those, and is not measured for.
    const resize = new ResizeObserver(schedule);
    resize.observe(body);
    const holdsOccluder = (node: Node) => node.nodeType === 1 /* an element */
      && ((node as Element).matches(OCCLUDERS) || !!(node as Element).querySelector(OCCLUDERS));
    const affects = (record: MutationRecord) => {
      const target = record.target;
      // A drawing's parts (an animated icon or character, every frame
      // while it moves) cannot move anything outside the drawing's box.
      if ((target as SVGElement).ownerSVGElement) return false;
      // The frame's place, docked or clipped, or a style around it.
      if (record.type === "attributes" && target.contains(body)) return true;
      if (record.type === "childList") {
        return Array.from(record.addedNodes).some(holdsOccluder)
          || Array.from(record.removedNodes).some(holdsOccluder);
      }
      if (record.attributeName === "role" || record.attributeName === "data-native-view-occluder") return true;
      // An occluder's own change, or one on an element holding it (the
      // wrapper a menu is positioned by).
      return occluders.some(occluder => occluder.contains(target) || target.contains(occluder));
    };
    mutation = new MutationObserver((records) => {
      if (records.some(affects)) schedule();
    });
    mutation.observe(document.body, { subtree: true, childList: true, attributes: true });
    // A pure move (something above the pane went away) resizes nothing
    // around the page. Moves the page's own measurements already followed
    // ask for nothing more.
    const unwatch = watchPosition(body, (box) => {
      if (!measured || box.left !== measured.x || box.top !== measured.y
        || box.width !== measured.width || box.height !== measured.height) schedule();
    });
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
    const unsubscribe = bridge.webTab.onState(state => {
      if (state.id !== tabId) return;
      const patch: { url?: string; title?: string; faviconUrl?: string } = {};
      if (state.url) patch.url = state.url;
      if (state.title) patch.title = state.title;
      if (state.faviconUrl !== undefined) patch.faviconUrl = state.faviconUrl;
      if (Object.keys(patch).length) useCenterTabs.getState().updateWebTab(tabId, patch);
    });
    report();
    return () => {
      disposed = true;
      generation++;
      for (const [timer, cancel] of waits) { clearTimeout(timer); cancel(); }
      waits.clear();
      window.cancelAnimationFrame(frame);
      resize.disconnect();
      mutation?.disconnect();
      unwatch();
      pip?.removeEventListener("op:pip-geometry", report);
      pip?.removeEventListener("pointerdown", press, true);
      pip?.removeEventListener("op:pip-gesture-start", beginGesture);
      pip?.removeEventListener("op:pip-gesture-end", endGesture);
      pip?.removeEventListener("op:pip-hold", onHold);
      pip?.removeEventListener("op:pip-release", onRelease);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
      unsubscribe();
      desktop.setFloatingWebTab(tabId, false);
      desktop.removeVisibleWebTabBounds(bridge, tabId);
      desktop.setWebTabReady(tabId, false);
      // Folded into the chip, the page keeps the preview's layout, so it
      // comes back as it went; a pane showing it resets it there
      // (desktop-web-tab-pane.tsx).
      if (useWebTabPip.getState().backgroundTabId !== tabId) bridge.webTab.setPipZoom?.(tabId, null);
    };
  }, [native, tabId]);

  useEffect(() => {
    if (native) return;
    const body = bodyRef.current;
    const iframe = iframeRef.current;
    if (!body || !iframe) return;
    const fit = () => {
      const scale = Math.min(body.clientWidth / 1920, body.clientHeight / 1080);
      iframe.style.transform = `scale(${scale})`;
    };
    const resize = new ResizeObserver(fit);
    resize.observe(body);
    fit();
    return () => resize.disconnect();
  }, [native, tabId, url]);

  // Rounded at the top like the native page, whose view the desktop App
  // rounds on all four corners at the frame's radius (setBorderRadius in
  // apps/desktop/main/web-views/web-views.js): the still picture and the
  // edge colour under it keep that shape, so a hold changes no corner.
  if (native) return <div ref={bodyRef} className="absolute inset-[0] overflow-hidden rounded-t-lg" data-pip-live="native">
    {/* The still picture, shown only while a hold stands. */}
    <img ref={stillRef} hidden alt="" draggable={false} data-pip-gesture-frame="true" className={styles.webPipGestureFrame} />
  </div>;
  return <div className="flex flex-col flex-1 min-h-0" data-pip-live="iframe">
    <div className={styles.webPipEmbedHint}>{text(
      "Embedded page · some sites require Open page",
      "嵌入页面 · 部分网站需点击打开页面",
    )}</div>
    <div ref={bodyRef} className="relative flex-1 min-h-0 overflow-hidden">
    {/^https?:\/\//i.test(url) ? <iframe
      ref={iframeRef}
      key={tabId}
      src={url}
      title={text("Interactive page preview", "可交互页面预览")}
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      className={styles.webPipFrame}
    /> : <div className={`${styles.webPipFallback} text-fs-2xs text-text-muted`}>{text("This page cannot be embedded", "此页面无法嵌入")}</div>}
    </div>
  </div>;
}
