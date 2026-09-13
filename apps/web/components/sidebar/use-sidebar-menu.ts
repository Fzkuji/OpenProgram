"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";
import type { DesktopContextMenuItem } from "@/lib/desktop/desktop-bridge-types";

export type SidebarMenuItem = DesktopContextMenuItem & {
  onSelect?: () => void;
  children?: SidebarMenuItem[];
};

/** One invocation point and lifetime for native and Web sidebar menus. */
export function useSidebarMenu() {
  const [open, setOpen] = useState(false);
  const [native, setNative] = useState(false);
  const [point, setPoint] = useState({ x: 0, y: 0 });
  const pending = useRef<{ id: string; cancel: () => void } | null>(null);
  const invoker = useRef<HTMLElement | null>(null);
  const anchor = useRef({ getBoundingClientRect: () => new DOMRect() });

  useEffect(() => () => {
    const request = pending.current;
    pending.current = null;
    request?.cancel();
  }, []);

  function close() {
    const request = pending.current;
    pending.current = null;
    request?.cancel();
    setOpen(false);
    if (invoker.current?.isConnected) invoker.current.focus();
  }

  function show(event: MouseEvent<HTMLElement>, items: SidebarMenuItem[]) {
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
    const api = window.openprogramDesktop?.contextMenu;
    setNative(!!api);
    if (!api) return;
    pending.current?.cancel();
    const id = crypto.randomUUID();
    pending.current = { id, cancel: () => api.close(id) };
    const actions = new Map<string, () => void>();
    function describe(entries: SidebarMenuItem[]): DesktopContextMenuItem[] {
      return entries.map(({ onSelect, children, ...item }) => {
        if (onSelect && !item.disabled) actions.set(item.id, onSelect);
        return { ...item, ...(children ? { children: describe(children) } : {}) };
      });
    }
    api.popup({ requestId: id, ...next, items: describe(items) }).then(choice => {
      if (pending.current?.id !== id) return;
      pending.current = null;
      setOpen(false);
      if (target.isConnected) target.focus();
      if (choice) actions.get(choice)?.();
    }, () => {
      if (pending.current?.id !== id) return;
      pending.current = null;
      setNative(false);
    });
  }

  return { open, native, point, anchor, show, close, onOpenChange: (value: boolean) => { if (!value) close(); } };
}
