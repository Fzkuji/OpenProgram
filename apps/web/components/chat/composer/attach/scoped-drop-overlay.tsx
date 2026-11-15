"use client";

import React, { useLayoutEffect, useState } from "react";
import { SolarIcon } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";

/** Drop-overlay positioned over the central chat column rather than
 *  the whole window. Anchored to ``#chatArea`` by bounding rect so
 *  the sidebars stay clear; in a split view, to the pane (``within``)
 *  the file is over. Falls back to centred-of-viewport when
 *  the element isn't found (settings / functions / etc routes).
 *  Colours are theme tokens: a scrim of the page's own background and
 *  the app's card material, so it reads right in light and dark. */
export function ScopedDropOverlay({ within }: { within?: HTMLElement | null }) {
  const { text } = useTranslation();
  const [rect, setRect] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    function measure() {
      const el = within || document.getElementById("chatArea")
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
  }, [within]);

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
      data-native-view-occluder="true"
      style={{
        ...style,
        zIndex: 10_000,
        background: "color-mix(in srgb, var(--bg-primary) 65%, transparent)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        pointerEvents: "none",
        borderRadius: "var(--radius-md)",
        animation: "overlayIn 140ms ease-out",
      }}
    >
      <div
        style={{
          padding: "32px 48px",
          borderRadius: "var(--radius-2xl)",
          border: "1.5px dashed color-mix(in srgb, var(--accent-blue) 55%, transparent)",
          background: "var(--bg-input)",
          boxShadow: "var(--composer-shadow-focus)",
          color: "var(--text-bright)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          fontFamily: "var(--font-sans)",
        }}
      >
        <SolarIcon name="paperclip" size={36} motionPreset="none" aria-hidden style={{ color: "var(--accent-blue)" }} />
        <span style={{ fontSize: "var(--fs-md)", fontWeight: 600 }}>
          {text("Drop to attach", "拖到这里添加附件")}
        </span>
        <span style={{ fontSize: "var(--fs-xs)", color: "var(--text-secondary)" }}>
          {text(
            "Images preview inline · text files inline as content · others attach by name",
            "图片直接预览 · 文本文件按内容附上 · 其他文件按名称附上",
          )}
        </span>
      </div>
    </div>
  );
}
