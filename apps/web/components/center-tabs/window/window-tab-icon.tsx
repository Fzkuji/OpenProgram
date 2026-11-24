"use client";

import { AppWindow } from "lucide-react";
import { windowIcons } from "@/lib/desktop/embedded-window/embedded-window";

/** The icon of a tab that holds another application's window: that
 *  application's icon when the platform gave one, else a window. */
export function WindowTabIcon({ windowId, size }: { windowId?: number; size: number }) {
  const icon = windowId ? windowIcons.get(windowId) : undefined;
  if (icon) return <img src={icon} width={size} height={size} alt="" aria-hidden="true" className="shrink-0 object-contain" />;
  return <AppWindow size={size} aria-hidden="true" />;
}
