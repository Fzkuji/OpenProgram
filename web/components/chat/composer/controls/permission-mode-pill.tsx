"use client";

/**
 * Permission-mode pill — a shield chip in the composer control row. Click to
 * open a dropdown of the six modes; pick one to set this session's permission
 * mode. Flows into the chat payload (composer sends permission_mode); the
 * backend stores it on the session run config.
 * See docs/design/runtime/permission-model.md §4.5.
 */
import React, { useEffect, useRef, useState } from "react";

import { ShieldCheckIcon, type AnimatedNavIconHandle } from "@/components/animated-icons";
import type { PermissionMode, PermissionModeOption } from "./use-permission-mode";

interface Props {
  mode: PermissionMode;
  options: PermissionModeOption[];
  onChange: (m: PermissionMode) => void;
}

// Warn-tint the chip when the mode is more permissive than "ask".
const MODE_TINT: Record<PermissionMode, string> = {
  ask: "var(--text-primary)",
  plan: "var(--text-primary)",
  auto: "var(--warning, #d78a18)",
  acceptEdits: "var(--warning, #d78a18)",
  dontAsk: "var(--warning, #d78a18)",
  bypass: "var(--danger, #d72518)",
};

export function PermissionModePill({ mode, options, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const iconRef = useRef<AnimatedNavIconHandle>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (hostRef.current && !hostRef.current.contains(e.target as Node)) setOpen(false);
    }
    const t = setTimeout(() => document.addEventListener("click", onDoc), 0);
    return () => { clearTimeout(t); document.removeEventListener("click", onDoc); };
  }, [open]);

  const openMenu = () => {
    if (open) { setOpen(false); return; }
    // fixed-position coords so the menu escapes the composer's overflow:hidden.
    const r = btnRef.current?.getBoundingClientRect();
    if (r) setPos({ left: r.left, bottom: window.innerHeight - r.top + 6 });
    setOpen(true);
  };

  const tint = MODE_TINT[mode] ?? "var(--text-primary)";

  return (
    <div ref={hostRef} className="relative inline-flex h-[32px] items-center">
      <button
        ref={btnRef}
        type="button"
        className="inline-flex h-[32px] w-[32px] items-center justify-center rounded-full select-none"
        style={{ backgroundColor: "color-mix(in srgb, " + tint + " 14%, transparent)", color: tint }}
        onClick={openMenu}
        onMouseEnter={() => iconRef.current?.startAnimation?.()}
        onMouseLeave={() => iconRef.current?.stopAnimation?.()}
        aria-label="权限模式"
      >
        <ShieldCheckIcon ref={iconRef} size={18} aria-hidden="true" />
      </button>
      {open && pos ? (
        <div
          role="menu"
          className="fixed z-[100] min-w-[200px] rounded-[10px] border border-[var(--border)] bg-bg-secondary py-[4px] shadow-lg"
          style={{ left: pos.left, bottom: pos.bottom }}
        >
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              className={
                "flex w-full items-center gap-[8px] px-[12px] py-[7px] text-left text-[13px] " +
                (o.value === mode ? "text-text-bright" : "text-text-primary hover:bg-bg-hover")
              }
              onClick={() => { onChange(o.value); setOpen(false); }}
            >
              <span className="w-[14px] shrink-0">{o.value === mode ? "✓" : ""}</span>
              {o.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
