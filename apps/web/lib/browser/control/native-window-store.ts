"use client";

import { create } from "zustand";
import { followNativeWindow } from "../../chat/resources/session-resources.ts";
import type { NativeActionCue, NativeControl } from "../../desktop/bridge/bridge-api.ts";
import { showActionsEnabled } from "./browser-control.ts";

/** The native app window an Agent last operated in a conversation. */
export type NativeWindow = {
  sessionId: string;
  executionId: string;
  windowId: number;
  appName: string;
  sequence: number;
  hasFrame: boolean;
};

export const useNativeWindows = create<{ windows: Record<string, NativeWindow> }>(() => ({
  windows: {},
}));

export function ingestNativeWindow(data: unknown): NativeWindow | null {
  if (!data || typeof data !== "object") return null;
  const item = data as Record<string, unknown>;
  if (typeof item.session_id !== "string" || !item.session_id
      || !Number.isInteger(item.window_id) || typeof item.app_name !== "string"
      || !Number.isInteger(item.sequence)) return null;
  const next: NativeWindow = {
    sessionId: item.session_id,
    executionId: typeof item.execution_id === "string" ? item.execution_id : "",
    windowId: item.window_id as number,
    appName: item.app_name,
    sequence: item.sequence as number,
    hasFrame: item.has_frame === true,
  };
  const previous = useNativeWindows.getState().windows[next.sessionId];
  // Events can arrive twice or out of order across reconnects.
  if (previous && previous.sequence >= next.sequence) return previous;
  useNativeWindows.setState(state => ({ windows: { ...state.windows, [next.sessionId]: next } }));
  followNativeWindow(next.sessionId, next.windowId);
  return next;
}

/** Cache-busted address of the latest frame the Agent saw of this window. */
export function nativeWindowFrameUrl(operated: NativeWindow): string | null {
  if (!operated.hasFrame) return null;
  const session = encodeURIComponent(operated.sessionId);
  return `/api/session/${session}/native-window/frame?window_id=${operated.windowId}&v=${operated.sequence}`;
}

/** Hand an Agent step's screen point to the desktop shell's agent cursor,
 *  unless the user turned action cues off. */
function cueNativeAction(data: unknown): void {
  const cue = data as Partial<NativeActionCue> | null;
  if (!cue || typeof cue.session_id !== "string" || ![cue.x, cue.y, cue.sequence, cue.at]
    .every((value) => typeof value === "number" && Number.isFinite(value))) return;
  if (!showActionsEnabled()) return;
  window.openprogramDesktop?.nativeActionCue?.(cue as NativeActionCue);
}

/** Hand the controlled window's place to the desktop shell's control
 *  overlay. With action cues off, only the end of control goes through, so
 *  an overlay already up still comes down. */
function showNativeControl(data: unknown): void {
  const control = data as Partial<NativeControl> | null;
  if (!control || typeof control.session_id !== "string" || !Number.isInteger(control.window_id)) return;
  if (control.active === true && !showActionsEnabled()) return;
  window.openprogramDesktop?.nativeControl?.(control as NativeControl);
}

/** The conversation's turn is over: whatever control overlay it had comes
 *  down, whether or not the worker managed to report the end of control
 *  (a failed turn, a restarted worker, a dropped socket all skip it). */
export function endNativeControl(sessionId: string): void {
  if (!sessionId) return;
  window.openprogramDesktop?.nativeControl?.({
    session_id: sessionId, window_id: 0, active: false, app_name: "",
    frame: null, buttons: null, occluders: [],
  });
}

if (typeof window !== "undefined") {
  window.addEventListener("op:ws-message", (event) => {
    const detail = (event as CustomEvent<{ type?: string; data?: unknown }>).detail;
    if (detail?.type === "native.window") ingestNativeWindow(detail.data);
    if (detail?.type === "native.action") cueNativeAction(detail.data);
    if (detail?.type === "native.control") showNativeControl(detail.data);
  });
}
