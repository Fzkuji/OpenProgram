"use client";

import React, { useLayoutEffect, useState } from "react";

/** Drop-overlay positioned over the central chat column rather than
 *  the whole window. Anchored to ``#chatArea`` by bounding rect so
 *  the sidebars stay clear. Falls back to centred-of-viewport when
 *  the element isn't found (settings / functions / etc routes). */
export function ScopedDropOverlay() {
  const [rect, setRect] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    function measure() {
      const el = document.getElementById("chatArea")
        // ``main`` is the shared shell wrapper used by other pages
        // (functions / skills / mcp / memory). Fallback so drag-into-
        // settings still shows a sensible overlay.
        || document.querySelector(".main");
      if (el) setRect(el.getBoundingClientRect());
      else setRect(null);
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const style: React.CSSProperties = rect ? {
    position: "fixed",
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  } : {
    position: "fixed",
    inset: 0,
  };
  return (
    <div
      style={{
        ...style,
        zIndex: 10_000,
        background: "rgba(10,10,12,0.55)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        pointerEvents: "none",
        borderRadius: 8,
        animation: "overlayIn 140ms ease-out",
      }}
    >
      <div
        style={{
          padding: "32px 48px",
          borderRadius: 14,
          border: "2px dashed rgba(255,255,255,0.4)",
          background: "rgba(20,20,24,0.85)",
          color: "var(--text-primary, #f5f5f5)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        }}
      >
        <span style={{ fontSize: 36 }} aria-hidden>📎</span>
        <span style={{ fontSize: 16, fontWeight: 600 }}>
          Drop to attach
        </span>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          Images preview inline · text files inline as content ·
          others attach by name
        </span>
      </div>
    </div>
  );
}
