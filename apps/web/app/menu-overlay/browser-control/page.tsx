"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { Clock3, Eye, Pause, Play, X } from "lucide-react";

import { CursorClickIcon } from "@/components/animated-icons";
import type { DesktopBrowserControlOverlay } from "@/lib/desktop-bridge-types";

type OverlayUpdate = DesktopBrowserControlOverlay & { collapsed?: boolean };

interface OverlayBridge {
  ready(): void;
  event(payload: Record<string, unknown>): void;
  onUpdate(cb: (payload: OverlayUpdate | null) => void): () => void;
}

const THRESH = 6;

function overlayBridge(): OverlayBridge | null {
  const api = (
    window as unknown as {
      openprogramDesktop?: { browserControlOverlay?: OverlayBridge };
    }
  ).openprogramDesktop?.browserControlOverlay;
  return api ?? null;
}

function BrowserControlOverlayPage() {
  const [payload, setPayload] = useState<OverlayUpdate | null>(null);
  const [noticeOpen, setNoticeOpen] = useState(true);
  const rootRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    id: number;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
    moved: boolean;
  } | null>(null);

  useEffect(() => {
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    const api = overlayBridge();
    if (!api) return;
    const stop = api.onUpdate((next) => {
      setPayload(next);
      if (!next) setNoticeOpen(true);
    });
    api.ready();
    return stop;
  }, []);

  useEffect(() => {
    if (!payload || !rootRef.current) return;
    const box = rootRef.current.getBoundingClientRect();
    overlayBridge()?.event({
      type: "layout",
      collapsed: payload.collapsed !== false,
      width: Math.max(1, box.width),
      height: Math.max(1, box.height),
    });
  }, [payload]);

  if (!payload) return null;

  const collapsed = payload.collapsed !== false;
  const send = (extra: Record<string, unknown>) => {
    overlayBridge()?.event({
      id: payload.resourceId,
      generation: payload.generation,
      ...extra,
    });
  };

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button")) return;
    drag.current = {
      id: event.pointerId,
      startX: event.screenX,
      startY: event.screenY,
      lastX: event.screenX,
      lastY: event.screenY,
      moved: false,
    };
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* jsdom */ }
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    const dx = event.screenX - current.lastX;
    const dy = event.screenY - current.lastY;
    if (!current.moved) {
      if (Math.hypot(event.screenX - current.startX, event.screenY - current.startY) < THRESH) return;
      current.moved = true;
    }
    current.lastX = event.screenX;
    current.lastY = event.screenY;
    send({ type: "move", dx, dy });
  };
  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    drag.current = null;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* jsdom */ }
    if (current.moved) return;
    if (event.type === "pointercancel") return;
    send({ type: "layout", collapsed: !collapsed, width: collapsed ? 280 : 36, height: collapsed ? 44 : 36 });
  };

  if (collapsed) {
    return (
      <div
        ref={rootRef}
        data-browser-control="float"
        data-collapsed="true"
        role="button"
        tabIndex={0}
        aria-label={payload.dragLabel || payload.expandLabel}
        style={{
          width: 36,
          height: 36,
          borderRadius: 12,
          background: "rgba(32,33,36,.55)",
          color: "#f4f5f8",
          display: "grid",
          placeItems: "center",
          touchAction: "none",
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            send({ type: "layout", collapsed: false, width: 280, height: 44 });
          }
        }}
      >
        <CursorClickIcon size={20} play="hover" />
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      data-browser-control="float"
      data-collapsed="false"
      role="group"
      aria-label={payload.dragLabel}
      style={{
        display: "flex",
        gap: 6,
        alignItems: "center",
        padding: "8px 10px",
        borderRadius: 12,
        background: "rgba(32,33,36,.55)",
        color: "#f4f5f8",
        whiteSpace: "nowrap",
        touchAction: "none",
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <span title={payload.status}>{payload.status}</span>
      {noticeOpen && payload.notice ? (
        <span role="status">
          {payload.notice}
          <button
            type="button"
            aria-label={payload.dismissLabel || "Dismiss"}
            onClick={() => setNoticeOpen(false)}
          >
            <X size={12} />
          </button>
        </span>
      ) : null}
      <button
        type="button"
        aria-pressed={payload.showActions}
        aria-label={payload.showLabel}
        title={payload.showLabel}
        onClick={() => send({ type: "toggle-show" })}
      >
        <Eye size={14} />
      </button>
      <button
        type="button"
        aria-label={payload.historyLabel}
        title={payload.historyLabel}
        onClick={() => send({ type: "history" })}
      >
        <Clock3 size={14} />
      </button>
      {payload.showTakeover ? (
        <button
          type="button"
          disabled={payload.controlState === "paused" ? payload.resumeDisabled : payload.pauseDisabled}
          aria-label={payload.pauseLabel}
          title={payload.pauseLabel}
          onClick={() => send({
            type: payload.controlState === "paused" ? "resume" : "pause",
          })}
        >
          {payload.controlState === "paused" ? <Play size={14} /> : <Pause size={14} />}
        </button>
      ) : null}
      <button
        type="button"
        aria-label={payload.foldLabel || "Fold"}
        onClick={() => send({ type: "layout", collapsed: true, width: 36, height: 36 })}
      >
        {payload.foldLabel || "Fold"}
      </button>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <BrowserControlOverlayPage />
    </Suspense>
  );
}
