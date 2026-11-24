"use client";

/** The live view of another application's window, shared by the window tab
 *  and the chat's small preview.
 *
 *  `place` (a tab): the worker places the window — a child of the App window
 *  on Windows and Linux, right behind the view on macOS, where the view shows
 *  its live picture at the window's own size and forwards the pointer, wheel
 *  and keys. Without `place` (a preview): the window stays where it is; the
 *  view shows its live picture fitted whole and, where the platform allows,
 *  forwards input scaled to the window. Several views may show one window. */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import {
  attachWindow, detachWindow, embedHost, fittedFrame, fittedPoint, followWindow, frameCoalesced, heldModifiers,
  hostRect, inputQueue, layoutWindow, registerEmbedded, watchPlacement, windowPoint, windowState, wheelPixels,
  UNFOLLOWED_LOOK_MS, type EmbedHost, type EmbedMode, type PanelRect,
} from "@/lib/desktop/embedded-window/embedded-window";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type LiveWindowProblem =
  | { kind: "closed" }
  | { kind: "error"; message: string }
  /** The window's id now names another application's window (a restored tab). */
  | { kind: "replaced" };

export function LiveWindowView({
  windowId,
  app,
  place,
  className,
  onTitle,
  onProblem,
}: {
  windowId: number;
  app?: string;
  place: boolean;
  className?: string;
  onTitle?: (title: string) => void;
  onProblem: (problem: LiveWindowProblem) => void;
}) {
  const { text } = useTranslation();
  const holder = `${place ? "tab" : "view"}:${useId()}`;
  const boxRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const typingRef = useRef<HTMLTextAreaElement>(null);
  const hostRef = useRef<EmbedHost | null>(null);
  const queueRef = useRef<ReturnType<typeof inputQueue> | null>(null);
  const titleRef = useRef(onTitle);
  const problemRef = useRef(onProblem);
  titleRef.current = onTitle;
  problemRef.current = onProblem;
  const [live, setLive] = useState(false);
  const [mode, setMode] = useState<EmbedMode | null>(null);
  const [input, setInput] = useState(false);
  const [frame, setFrame] = useState<[number, number, number, number] | null>(null);
  const [box, setBox] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const [notice, setNotice] = useState("");

  const measure = useCallback((): { rect: PanelRect; visible: boolean } => {
    const el = boxRef.current;
    const area = el?.getBoundingClientRect();
    const visible = !!el && !!area && area.width > 1 && area.height > 1 && document.visibilityState === "visible";
    return { rect: { left: area?.left ?? 0, top: area?.top ?? 0, width: area?.width ?? 0, height: area?.height ?? 0 }, visible };
  }, []);

  // A preview fits the picture to its own size, so it follows that size.
  useEffect(() => {
    const el = boxRef.current;
    if (!el || place) return;
    const observer = new ResizeObserver(() => setBox({ width: el.clientWidth, height: el.clientHeight }));
    observer.observe(el);
    return () => observer.disconnect();
  }, [place]);

  useEffect(() => {
    let disposed = false;
    let stream: MediaStream | null = null;
    let lastSent: [number, number, number, number] | null = null;
    let lastVisible: boolean | null = null;
    let busy = false;
    let again = false;
    let force = false;
    let follows = false;
    let capturing = false;
    let captureLost = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    const stops: (() => void)[] = [];
    let unregister: (() => void) | null = null;
    const queue = inputQueue(windowId);
    queueRef.current = queue;

    const capture = async () => {
      if (capturing) return;
      capturing = true;
      captureLost = false;
      try {
        const source = await desktopBridge()?.embeddedWindow?.captureSource(windowId);
        if (!source || disposed) {
          if (!disposed) {
            captureLost = true;
            setNotice(text("The window's picture is not available. It may be on another desktop.",
              "拿不到这个窗口的画面，它可能在另一个桌面上。"));
          }
          return;
        }
        const constraints = { audio: false, video: { mandatory: {
          chromeMediaSource: "desktop", chromeMediaSourceId: source, maxFrameRate: place ? 60 : 30, maxWidth: 7680, maxHeight: 4320,
        } } } as unknown as MediaStreamConstraints;
        const next = await navigator.mediaDevices.getUserMedia(constraints);
        if (disposed) { next.getTracks().forEach((track) => track.stop()); return; }
        stream?.getTracks().forEach((track) => track.stop());
        stream = next;
        setNotice("");
        next.getVideoTracks()[0]?.addEventListener("ended", () => {
          // The window left the screen (another Space, minimized): capture it
          // again when it changes or this page shows again.
          if (disposed) return;
          captureLost = true;
          if (!follows) retryCapture(UNFOLLOWED_LOOK_MS);
        });
        if (videoRef.current) videoRef.current.srcObject = next;
      } catch (error) {
        captureLost = !disposed;
        throw error;
      } finally {
        capturing = false;
      }
    };
    const recapture = () => {
      if (!disposed && captureLost && !capturing) void capture().catch(() => {});
    };
    // Where the window announces nothing, look again shortly, while the view is open.
    const retryCapture = (ms: number) => {
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(() => {
        retryTimer = null;
        recapture();
        if (!disposed && captureLost && !follows) retryCapture(ms);
      }, ms);
    };

    const layout = async () => {
      const host = hostRef.current;
      if (!host) return;
      if (busy) { again = true; return; }
      const { rect, visible } = measure();
      const target = hostRect(host, rect);
      const forced = force;
      force = false;
      if (!forced && visible === lastVisible && (!visible || sameRect(lastSent, target))) return;
      busy = true;
      try {
        const state = await layoutWindow(windowId, host, rect, visible, holder);
        lastSent = target;
        lastVisible = visible;
        if (!state.open) problemRef.current({ kind: "closed" });
        else if (state.frame) setFrame(state.frame);
      } catch {
        /* the next change lays it out again */
      } finally {
        busy = false;
      }
      if (again && !disposed) { again = false; void layout(); }
    };
    const layoutSoon = frameCoalesced(() => { if (!disposed) void layout(); });

    const readState = async () => {
      try {
        const next = await windowState(windowId);
        if (disposed) return;
        if (!next.open) problemRef.current({ kind: "closed" });
        else {
          if (next.title) titleRef.current?.(next.title);
          if (!place && next.frame) setFrame(next.frame);
          recapture();
        }
      } catch {
        /* the next change reads again */
      }
    };
    const stateSoon = frameCoalesced(() => { if (!disposed) void readState(); });

    (async () => {
      try {
        const host = await embedHost();
        if (disposed) return;
        hostRef.current = host;
        const { rect, visible } = measure();
        const state = await attachWindow(windowId, host, rect, visible, { holder, place });
        if (disposed) { void detachWindow(windowId, holder).catch(() => {}); return; }
        if (state.app && app && state.app !== app) {
          await detachWindow(windowId, holder).catch(() => {});
          problemRef.current({ kind: "replaced" });
          return;
        }
        unregister = registerEmbedded(`${holder}:${windowId}`, () => detachWindow(windowId, holder));
        follows = !!state.follows;
        setMode(state.mode ?? null);
        setInput(!!state.input || (place && state.mode === "stream"));
        if (state.frame) setFrame(state.frame);
        if (state.title) titleRef.current?.(state.title);
        setLive(true);
        // The window moved, was resized, minimized, restored, renamed or closed.
        stops.push(followWindow(windowId, follows, () => stateSoon.schedule()), () => stateSoon.cancel());
        // Back on screen with this page: a picture lost meanwhile can be had again.
        const onShown = () => { if (document.visibilityState === "visible") recapture(); };
        document.addEventListener("visibilitychange", onShown);
        window.addEventListener("focus", onShown);
        stops.push(() => {
          document.removeEventListener("visibilitychange", onShown);
          window.removeEventListener("focus", onShown);
        });
        // A placed window on Windows and Linux is part of the App window
        // itself; everything else is seen through its live picture.
        if (!place || state.mode === "stream") await capture().catch(() => {
          if (!disposed) {
            captureLost = true;
            setNotice(text("The window's picture is not available.", "拿不到这个窗口的画面。"));
          }
        });
        if (disposed) return;
        if (captureLost && !follows) retryCapture(UNFOLLOWED_LOOK_MS);
        if (place) {
          // The tab moved or changed size within the page.
          const el = boxRef.current;
          if (el) stops.push(watchPlacement(el, () => layoutSoon.schedule()));
          // The App window moved, was resized or zoomed: on macOS the window
          // behind the tab follows it.
          const offHost = desktopBridge()?.embeddedWindow?.onHostChanged?.((fresh) => {
            const current = hostRef.current;
            hostRef.current = fresh;
            // Elsewhere the tab's rectangle is relative to the App window, which carries its child along.
            const moved = fresh.platform === "darwin"
              && (fresh.content.x !== current?.content.x || fresh.content.y !== current?.content.y);
            if (moved || fresh.zoom !== current?.zoom || fresh.scale !== current?.scale) force = true;
            layoutSoon.schedule();
          });
          if (offHost) stops.push(offHost);
          const onZoom = () => {
            void embedHost().then((fresh) => {
              if (disposed) return;
              if (fresh.zoom !== hostRef.current?.zoom) force = true;
              hostRef.current = fresh;
              layoutSoon.schedule();
            }).catch(() => {});
          };
          // A page zoom resizes the page in CSS px.
          window.addEventListener("resize", onZoom);
          stops.push(() => window.removeEventListener("resize", onZoom), () => layoutSoon.cancel());
          layoutSoon.schedule();
        }
      } catch (reason) {
        if (!disposed) problemRef.current({ kind: "error", message: String((reason as Error)?.message ?? reason) });
      }
    })();

    return () => {
      disposed = true;
      stops.forEach((stop) => stop());
      if (retryTimer) clearTimeout(retryTimer);
      queue.dispose();
      queueRef.current = null;
      stream?.getTracks().forEach((track) => track.stop());
      unregister?.();
      void detachWindow(windowId, holder).catch(() => {});
    };
  }, [app, holder, measure, place, text, windowId]);

  const zoom = hostRef.current?.zoom || 1;
  const fit = !place && frame ? fittedFrame(box, frame) : null;
  const local = (event: { clientX: number; clientY: number }) => {
    const area = boxRef.current?.getBoundingClientRect();
    const host = hostRef.current;
    if (!area || !host) return null;
    const x = event.clientX - area.left;
    const y = event.clientY - area.top;
    if (!place) return fit ? fittedPoint(fit, x, y) : null;
    return windowPoint(host, x, y);
  };
  const send = (event: Record<string, unknown>) => queueRef.current?.push(event);

  // Pointer, wheel and keys go to the window wherever the view forwards them;
  // a placed native child window takes them itself.
  const forward = live && input;
  useEffect(() => {
    const el = boxRef.current;
    if (!el || !forward) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const point = local(event);
      if (!point) return;
      const scale = fit ? 1 / fit.scale : 1;
      const { dx, dy } = wheelPixels(event, el.clientHeight);
      send({ type: "wheel", ...point, dx: dx * scale, dy: dy * scale, mods: heldModifiers(event) });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const showPicture = !place || mode === "stream";
  const pictureStyle: React.CSSProperties = fit
    ? { left: fit.left, top: fit.top, width: fit.width, height: fit.height }
    : place && frame
      ? { left: 0, top: 0, width: frame[2] / zoom, height: frame[3] / zoom }
      : { left: 0, top: 0, width: "100%", height: "100%", objectFit: "contain", objectPosition: place ? "left top" : "center" };

  return (
    <div
      ref={boxRef}
      className={cn("relative h-full w-full overflow-hidden outline-none", place ? "bg-background" : "bg-muted", className)}
      tabIndex={forward ? 0 : -1}
      data-live-window={place ? "tab" : "view"}
      onContextMenu={forward ? (event) => event.preventDefault() : undefined}
      onPointerDown={forward ? (event) => {
        const point = local(event);
        if (!point) return;
        event.preventDefault();
        event.stopPropagation();
        try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* not an active pointer */ }
        typingRef.current?.focus({ preventScroll: true });
        send({ type: "down", ...point, button: event.button, clicks: Math.max(1, event.detail), mods: heldModifiers(event) });
      } : undefined}
      onPointerUp={forward ? (event) => {
        const point = local(event);
        if (!point) return;
        send({ type: "up", ...point, button: event.button, clicks: Math.max(1, event.detail), mods: heldModifiers(event) });
      } : undefined}
      onPointerMove={forward ? (event) => {
        const point = local(event);
        if (point) send({ type: "move", ...point, buttons: event.buttons, mods: heldModifiers(event) });
      } : undefined}
    >
      {showPicture && (
        // A tab draws it at the window's own size from its top-left, over the
        // window itself, so it lines up with the window's menus and pop-ups.
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          className="pointer-events-none absolute max-w-none select-none"
          style={pictureStyle}
        />
      )}
      {forward && (
        // Keys land here so an input method can compose; finished text and
        // every other key go to the window.
        <textarea
          ref={typingRef}
          aria-label={text("Type into the window", "向窗口输入")}
          className="pointer-events-none absolute left-0 top-0 h-px w-px resize-none opacity-0"
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
            event.preventDefault();
            // The App's own shortcuts stay out of the way while typing into the window.
            event.stopPropagation();
            send({ type: "key", code: event.code, key: event.key, down: true, mods: heldModifiers(event) });
          }}
          onKeyUp={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
            event.preventDefault();
            event.stopPropagation();
            send({ type: "key", code: event.code, key: event.key, down: false, mods: heldModifiers(event) });
          }}
          onCompositionEnd={(event) => {
            if (event.data) send({ type: "text", text: event.data });
            event.currentTarget.value = "";
          }}
          onInput={(event) => {
            // Keys are held back above, so text that still arrives came without
            // one: dictation, the emoji picker, an input-method commit.
            if ((event.nativeEvent as InputEvent).isComposing) return;
            const value = event.currentTarget.value;
            event.currentTarget.value = "";
            if (value) send({ type: "text", text: value });
          }}
          onFocus={() => send({ type: "focus" })}
        />
      )}
      {notice && live && (
        <p role="status" className="absolute inset-x-0 bottom-0 p-3 text-center text-fs-sm text-muted-foreground">{notice}</p>
      )}
    </div>
  );
}

function sameRect(a: [number, number, number, number] | null, b: [number, number, number, number]) {
  return !!a && a.every((value, index) => Math.abs(value - b[index]) < 0.5);
}
