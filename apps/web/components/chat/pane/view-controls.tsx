"use client";

/**
 * ViewControls — the column of buttons at the top-right of the chat pane,
 * modelled on Obsidian's per-pane controls:
 *
 *   1. a perspective toggle (transcript ⇄ session context DAG), whose
 *      tooltip names the CURRENT perspective and what a click does;
 *   2. focus mode: full screen for the session pane, the one button for
 *      both perspectives (lib/tabs/canvas/canvas-fullscreen.ts);
 *   3. a `…` overflow menu with the session-level actions.
 *
 * Left of the column, in the conversation perspective, is the dock for
 * the conversation's floating web preview: the graph's side column
 * (dag/panels/tools.css .dag-side-column) holding only the preview's
 * place, so the preview sits in the top-right corner in both
 * perspectives and moves between the two docks as they swap.
 *
 * The overflow menu (session-actions-menu.tsx) is a second entry point for the actions the Recents
 * row already offers (`components/sidebar/sessions/conv-menu.tsx`), plus
 * "Copy conversation" (lib/chat/copy-conversation.ts), which only this menu
 * has. "Move to group" and "New group" are deliberately left out: they
 * need the cross-session group list, which is the sidebar's job.
 *
 * Perspective state is per center tab (`CenterTab.dagView`) and flips
 * on click before anything is fetched — both surfaces stay mounted, so
 * the swap is a `display` change with no load.
 */

import { memo, useEffect, useSyncExternalStore } from "react";

import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { PipDockSlot } from "@/components/center-tabs/pip/pip-dock-slot";
import { SessionActionsMenu } from "@/components/chat/pane/session-actions-menu";
import { SolarIcon } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  canvasFullscreen,
  setCanvasFullscreen,
  subscribeCanvasFullscreen,
} from "@/lib/tabs/canvas/canvas-fullscreen";

// Memoized: the shell redraws on every tab and route change, and this takes no part in those.
export const ViewControls = memo(function ViewControls() {
  const { text } = useTranslation();
  const activeId = useCenterTabs((s) => s.activeId);
  const tab = useCenterTabs((s) => s.tabs.find((x) => x.id === s.activeId));
  const setTabDagView = useCenterTabs((s) => s.setTabDagView);
  const full = useSyncExternalStore(subscribeCanvasFullscreen, canvasFullscreen, () => false);
  const sessionTab = tab?.kind === "session";
  // Full screen is the session pane's: another kind of tab coming to the
  // front, or every tab closing, ends it.
  useEffect(() => { if (full && !sessionTab) setCanvasFullscreen(false); }, [full, sessionTab]);

  // Only session tabs have a perspective / session actions. File, web
  // and new-tab panes render their own chrome.
  if (!tab || tab.kind !== "session" || !activeId) return null;

  const dagView = !!tab.dagView;
  const sessionId = tab.sessionId ?? null;
  // A draft session has no server-side row yet: nothing to rename, pin,
  // link to or delete. Keep the perspective toggle, drop the menu.
  const saved = !!sessionId && !tab.draft;

  const toggleLabel = dagView
    ? text(
        "Current view: context graph. Click to switch to the conversation view.",
        "当前为上下文图视图，点击切换到会话视图",
      )
    : text(
        "Current view: conversation. Click to switch to the context graph view.",
        "当前为会话视图，点击切换到上下文图视图",
      );

  /* ---- session actions (same WS verbs the Recents menu uses) ------ */

  return (
    <>
    {dagView ? null : (
      <div className="dag-side-column chat-side-column">
        <PipDockSlot sessionId={sessionId} />
      </div>
    )}
    <div className="chat-view-controls">
      <HoverTip label={toggleLabel} side="bottom">
        <button
          type="button"
          className="chat-view-control-btn"
          id="sessionPerspectiveToggle"
          data-session-id={sessionId}
          data-tab-id={activeId}
          aria-label={toggleLabel}
          aria-pressed={dagView}
          onClick={() => setTabDagView(activeId!, !dagView)}
        >
          <SolarIcon name={dagView ? "git-graph" : "chat-round-line"} size={16} />
        </button>
      </HoverTip>

      <HoverTip
        label={full
          ? <TipBody title={text("Leave full screen", "退出全屏")} detail="Esc" />
          : <TipBody title={text("Full screen", "全屏")} detail={text("Hide the sidebars and other panes", "隐藏侧栏和其他面板")} />}
        side="bottom"
      >
        <button
          type="button"
          className="chat-view-control-btn"
          aria-label={full ? text("Leave full screen", "退出全屏") : text("Full screen", "全屏")}
          aria-pressed={full}
          aria-keyshortcuts={full ? "Escape" : undefined}
          onClick={() => setCanvasFullscreen(!full)}
        >
          <SolarIcon name={full ? "quit-full-screen-square" : "full-screen-square"} size={16} />
        </button>
      </HoverTip>

      {saved ? (
        <SessionActionsMenu tip={text("Session actions", "会话操作")}>
          <button
            type="button"
            className="chat-view-control-btn"
            aria-label={text("Session actions", "会话操作")}
          >
            <SolarIcon name="menu-dots" size={16} />
          </button>
        </SessionActionsMenu>
      ) : null}

    </div>
    </>
  );
});
