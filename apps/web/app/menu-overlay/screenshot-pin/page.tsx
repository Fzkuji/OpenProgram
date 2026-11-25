"use client";

/**
 * A screenshot pinned on the screen (apps/desktop/main/windows/screenshot.js):
 * the picture fills its small window, which the main process moves while it
 * is dragged, zooms on the wheel and fades on the wheel with ⌘ or Ctrl held.
 * A double click or Esc closes it; a right click offers copy, save, actual
 * size and close.
 */
import { ScreenshotPin } from "@/components/screenshot/screenshot-pin";

export default function Page() {
  return <ScreenshotPin />;
}
