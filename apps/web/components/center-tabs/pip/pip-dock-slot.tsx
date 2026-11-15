"use client";

import { useCallback, useRef } from "react";

import { PIP_DOCK_HEIGHT } from "@/lib/browser/web-tab/web-tab-pip-dock";
import { useWebTabPip } from "@/lib/browser/web-tab/web-tab-pip-store";
import {
  getPreviewPreference,
  useBrowserResourceStore,
  viewedBranchFor,
} from "@/lib/chat/resources/session-resources";

/** The floating preview's place in the graph's side column: an empty box
 *  the preview (web-tab-pip.tsx) sits over, so the panels under it make
 *  room. Shown while this conversation's preview is docked, or while a
 *  drag of it is about to drop in; an expanded preview floats instead. */
export function PipDockSlot({ sessionId }: { sessionId: string | null }) {
  const tabId = useWebTabPip((s) => s.tabId);
  const ownerSessionId = useWebTabPip((s) => s.ownerSessionId);
  const docked = useWebTabPip((s) => s.docked);
  const dockHover = useWebTabPip((s) => s.dockHover);
  useBrowserResourceStore((s) => s.preferences);
  const expanded = !!sessionId && !!getPreviewPreference(sessionId, viewedBranchFor(sessionId))?.expanded;
  const mounted = useRef<HTMLDivElement | null>(null);
  const register = useCallback((el: HTMLDivElement | null) => {
    if (el) useWebTabPip.setState({ dockSlot: el });
    else if (useWebTabPip.getState().dockSlot === mounted.current) useWebTabPip.setState({ dockSlot: null });
    mounted.current = el;
  }, []);
  if (!tabId || !sessionId || ownerSessionId !== sessionId || expanded || !(docked || dockHover)) return null;
  return <div ref={register} className="dag-pip-dock" data-pip-dock="" style={{ height: PIP_DOCK_HEIGHT }} aria-hidden="true" />;
}
