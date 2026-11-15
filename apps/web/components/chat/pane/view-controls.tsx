"use client";
import { copyText } from "@/lib/clipboard";

import { autoRenameSession } from "@/lib/chat/session-auto-rename";
import { copyConversation } from "@/lib/chat/copy-conversation";

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
 * The overflow menu is a second entry point for the actions the Recents
 * row already offers (`components/sidebar/sessions/conv-menu.tsx`), plus
 * "Copy conversation" (lib/chat/copy-conversation.ts), which only this menu
 * has. "Move to group" and "New group" are deliberately left out: they
 * need the cross-session group list, which is the sidebar's job.
 *
 * Perspective state is per center tab (`CenterTab.dagView`) and flips
 * on click before anything is fetched — both surfaces stay mounted, so
 * the swap is a `display` change with no load.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import {
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { PipDockSlot } from "@/components/center-tabs/pip/pip-dock-slot";
import { RenameDialog } from "@/components/chat/pane/rename-dialog";
import { SolarIcon } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import type { ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import {
  canvasFullscreen,
  setCanvasFullscreen,
  subscribeCanvasFullscreen,
} from "@/lib/tabs/canvas/canvas-fullscreen";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { deleteSessionAndRefocus } from "@/lib/tabs/navigation/open-session";
import { wsSend } from "@/components/sidebar/sessions/helpers";
import { showToast } from "@/lib/format-utils/toast";
import {
  setDefaultTranscriptView,
  setSessionTranscriptView,
  useDefaultTranscriptView,
  useTranscriptView,
  type TranscriptView,
} from "@/lib/prefs/transcript-view";

export function ViewControls() {
  const { t, text, locale } = useTranslation();
  const activeId = useCenterTabs((s) => s.activeId);
  const tab = useCenterTabs((s) => s.tabs.find((x) => x.id === s.activeId));
  const setTabDagView = useCenterTabs((s) => s.setTabDagView);
  const conversations = useSessionStore((s) => s.conversations);
  const upsertConversation = useSessionStore((s) => s.upsertConversation);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const transcriptView = useTranscriptView(tab?.kind === "session" ? tab.sessionId : null);
  const defaultView = useDefaultTranscriptView();
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
  const conv = (sessionId ? conversations[sessionId] : undefined) as
    | ConvSummary
    | undefined;
  const title = conv?.title || tab.title || t("sidebar.untitled");
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

  function patchConv(fields: Partial<ConvSummary>) {
    if (!sessionId) return;
    const heavy = runtimeState.conversations[sessionId];
    if (heavy) Object.assign(heavy, fields);
    const prev = conversations[sessionId];
    if (prev) upsertConversation({ ...prev, ...fields, id: sessionId });
  }

  function rename(next: string) {
    const clean = next.trim();
    if (!sessionId || !clean || clean === title) return;
    patchConv({ title: clean });
    useCenterTabs.getState().renameSessionTab(sessionId, clean);
    wsSend({ action: "rename_session", session_id: sessionId, title: clean });
  }

  function setFlags(fields: { pinned?: boolean; archived?: boolean }) {
    if (!sessionId) return;
    patchConv(fields);
    wsSend({ action: "update_session_flags", session_id: sessionId, ...fields });
  }

  function copyLink() {
    if (!sessionId) return;
    const url = `${location.origin}/s/${encodeURIComponent(sessionId)}`;
    void copyText(url).then(success => showToast(
      success ? t("sidebar.link_copied") : `${t("sidebar.link_copy_failed")} ${url}`,
      success ? undefined : { tone: "error" },
    ));
  }

  function del() {
    if (!sessionId) return;
    deleteSessionAndRefocus(sessionId);
  }

  // The letters on the rows work while the menu is open, as in the
  // sidebar's chat menu (conv-menu.tsx). A modifier leaves the key alone.
  const actions: Record<"r" | "p" | "c" | "a" | "d", () => void> = {
    r: () => setRenaming(true),
    p: () => setFlags({ pinned: !conv?.pinned }),
    c: copyLink,
    a: () => setFlags({ archived: !conv?.archived }),
    d: () => setConfirmDelete(true),
  };
  function onMenuKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const action = actions[e.key.toLowerCase() as keyof typeof actions];
    if (!action) return;
    e.preventDefault();
    setMenuOpen(false);
    action();
  }

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
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <HoverTip label={text("Session actions", "会话操作")} side="bottom">
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="chat-view-control-btn"
                aria-label={text("Session actions", "会话操作")}
              >
                <SolarIcon name="menu-dots" size={16} />
              </button>
            </DropdownMenuTrigger>
          </HoverTip>
          <DropdownMenuContent align="end" className={MENU_PANEL} onKeyDown={onMenuKey}>
            <div className={GROUP_LABEL}>{text("View", "显示方式")}</div>
            {([
              ["normal", "chat-round-line", text("Normal", "普通")],
              ["thinking", "lightbulb-bolt", text("Thinking", "显示思考")],
              ["verbose", "document-text", text("Verbose", "详细")],
            ] as [TranscriptView, MenuGlyph, string][]).map(([view, glyph, label]) => (
              <DropdownMenuItem
                key={view}
                className={cn(itemCls(false), MENU_ITEM_STATES)}
                onSelect={() => { if (sessionId) setSessionTranscriptView(sessionId, view); }}
              >
                <MenuRow glyph={glyph} label={label} checked={transcriptView === view} />
              </DropdownMenuItem>
            ))}
            <DropdownMenuItem
              className={cn(itemCls(false), MENU_ITEM_STATES)}
              disabled={transcriptView === defaultView}
              onSelect={() => setDefaultTranscriptView(transcriptView)}
            >
              <MenuRow glyph="check-circle" label={text("Use this view by default", "设为默认显示方式")} />
            </DropdownMenuItem>
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={actions.r}>
              <MenuRow glyph="pen" label={t("sidebar.rename")} keys={{ key: "R" }} />
            </DropdownMenuItem>
            <DropdownMenuItem
              className={cn(itemCls(false), MENU_ITEM_STATES)}
              onSelect={() => { if (sessionId) void autoRenameSession(sessionId); }}
            >
              <MenuRow glyph="stars" label={t("sidebar.auto_rename")} />
            </DropdownMenuItem>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={actions.p}>
              <MenuRow glyph="pin" label={conv?.pinned ? t("sidebar.unpin") : t("sidebar.pin")} keys={{ key: "P" }} />
            </DropdownMenuItem>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={actions.c}>
              <MenuRow glyph="link" label={t("sidebar.copy_link")} keys={{ key: "C" }} />
            </DropdownMenuItem>
            <DropdownMenuItem
              className={cn(itemCls(false), MENU_ITEM_STATES)}
              onSelect={() => { if (sessionId) void copyConversation(sessionId, text); }}
            >
              <MenuRow glyph="copy" label={text("Copy conversation", "复制会话")} />
            </DropdownMenuItem>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={actions.a}>
              <MenuRow glyph={conv?.archived ? "archive-up" : "archive"} label={conv?.archived ? t("sidebar.unarchive") : t("sidebar.archive")} keys={{ key: "A" }} />
            </DropdownMenuItem>
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <DropdownMenuItem
              className={cn(itemCls(false, true), MENU_ITEM_STATES, "data-[highlighted]:bg-[color-mix(in_srgb,var(--accent-red)_15%,transparent)] data-[highlighted]:text-accent-red")}
              onSelect={actions.d}
            >
              <MenuRow glyph="trash-bin-trash" label={t("sidebar.delete")} keys={{ key: "D" }} danger />
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}

      {renaming ? (
        <RenameDialog
          initial={title}
          onCancel={() => setRenaming(false)}
          onSubmit={(next) => {
            setRenaming(false);
            rename(next);
          }}
        />
      ) : null}
      {confirmDelete ? (
        <ConfirmDialog
          title={t("sidebar.delete_chat")}
          confirmLabel={t("sidebar.delete")}
          destructive
          description={
            locale === "zh"
              ? `确定要删除「${title}」吗？`
              : `Are you sure you want to delete "${title}"?`
          }
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            del();
          }}
        />
      ) : null}
    </div>
    </>
  );
}
