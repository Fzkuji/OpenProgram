"use client";

import { useEffect, useRef, type ReactNode } from "react";

export interface CtxItem {
  label: ReactNode;
  onClick: () => void;
  danger?: boolean;
}

interface Props {
  x: number;
  y: number;
  items: (CtxItem | "sep")[];
  onClose: () => void;
}

export function ContextMenu({ x, y, items, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [onClose]);

  // Clamp to viewport after mount
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.right > window.innerWidth) {
      el.style.left = window.innerWidth - r.width - 4 + "px";
    }
    if (r.bottom > window.innerHeight) {
      el.style.top = window.innerHeight - r.height - 4 + "px";
    }
  }, []);

  return (
    <div
      ref={ref}
      style={{
        position: "fixed",
        left: x,
        top: y,
        background: "var(--bg-tertiary)",
        border: "1px solid var(--border-color)",
        borderRadius: "6px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
        minWidth: 180,
        zIndex: 1000,
        padding: "4px 0",
      }}
    >
      {items.map((it, i) =>
        it === "sep" ? (
          <div
            key={`sep-${i}`}
            style={{
              height: 1,
              margin: "4px 0",
              background: "var(--border-color)",
            }}
          />
        ) : (
          <div
            key={i}
            onClick={() => {
              it.onClick();
              onClose();
            }}
            style={{
              padding: "6px 12px",
              fontSize: 13,
              cursor: "pointer",
              color: it.danger ? "var(--accent-red)" : "var(--text-primary)",
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.background = "var(--bg-hover)")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.background = "transparent")
            }
          >
            {it.label}
          </div>
        )
      )}
    </div>
  );
}
