"use client";

import { useRef, useState, type MouseEvent } from "react";
import type { DesktopContextMenuItem } from "@/lib/desktop/desktop-bridge-types";

export type SidebarMenuItem = DesktopContextMenuItem & {
  onSelect?: () => void;
  children?: SidebarMenuItem[];
};

/** Sidebar menus stay in the renderer so selection and dismissal share one event lifecycle. */
export function useSidebarMenu() {
  const [open, setOpen] = useState(false);
  const [point, setPoint] = useState({ x: 0, y: 0 });
  const invoker = useRef<HTMLElement | null>(null);
  const anchor = useRef({ getBoundingClientRect: () => new DOMRect() });

  function close() {
    setOpen(false);
    if (invoker.current?.isConnected) invoker.current.focus();
  }

  function show(event: MouseEvent<HTMLElement>, _items: SidebarMenuItem[]) {
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget;
    const rect = target.getBoundingClientRect();
    const keyboard = event.type === "click" && event.detail === 0;
    const next = keyboard ? { x: rect.left, y: rect.bottom } : { x: event.clientX, y: event.clientY };
    invoker.current = target;
    anchor.current = { getBoundingClientRect: () => new DOMRect(next.x, next.y, 0, 0) };
    setPoint(next);
    setOpen(true);
  }

  return { open, native: false, point, anchor, show, close, onOpenChange: (value: boolean) => { if (!value) close(); } };
}
