"use client";

/**
 * ViewControls — the pair of buttons at the top-right of the chat pane,
 * modelled on Obsidian's per-pane controls:
 *
 *   1. a perspective toggle (transcript ⇄ session context DAG), whose
 *      tooltip names the CURRENT perspective and what a click does;
 *   2. a `…` overflow menu with the session-level actions.
 *
 * The overflow menu is a second entry point for the actions the Recents
 * row already offers (`components/sidebar/conv-menu.tsx`) — it invents
 * nothing. "Move to group" and "New group" are deliberately left out:
 * they need the cross-session group list, which is the sidebar's job.
 *
 * Perspective state is per center tab (`CenterTab.dagView`) and flips
 * on click before anything is fetched — both surfaces stay mounted, so
 * the swap is a `display` change with no load.
 */

import { useState } from "react";
import { MoreHorizontal } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { HoverTip } from "@/components/ui/tooltip";
import {
  MENU_PANEL,
  MENU_SEPARATOR,
  SHORTCUT,
  itemCls,
} from "@/components/chat/top-bar/menu-styles";
import { ConfirmDialog } from "@/components/sidebar/sessions-list/confirm-dialog";
import { RenameDialog } from "@/components/chat/rename-dialog";
import {
  GitGraphIcon,
  MessageCircleIcon,
} from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import type { ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { newSession } from "@/lib/runtime-bridge/conversations";
import { wsSend } from "@/components/sidebar/sessions-list/helpers";
import { showToast } from "@/lib/format-utils/toast";

export function ViewControls() {
  const { t, text, locale } = useTranslation();
  const activeId = useCenterTabs((s) => s.activeId);
  const tab = useCenterTabs((s) => s.tabs.find((x) => x.id === s.activeId));
  const setTabDagView = useCenterTabs((s) => s.setTabDagView);
  const conversations = useSessionStore((s) => s.conversations);
  const upsertConversation = useSessionStore((s) => s.upsertConversation);
  const removeConversation = useSessionStore((s) => s.removeConversation);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

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
    const url = `${location.origin}/s/${sessionId}`;
    navigator.clipboard?.writeText(url).then(
      () => showToast(t("sidebar.link_copied")),
      () => showToast(url),
    );
  }

  function del() {
    if (!sessionId) return;
    wsSend({ action: "delete_session", session_id: sessionId });
    delete runtimeState.conversations[sessionId];
    removeConversation(sessionId);
    if (runtimeState.currentSessionId === sessionId) newSession();
  }

  return (
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
          {dagView ? <GitGraphIcon size={16} /> : <MessageCircleIcon size={16} />}
        </button>
      </HoverTip>

      {saved ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="chat-view-control-btn"
              aria-label={text("Session actions", "会话操作")}
              title={text("Session actions", "会话操作")}
            >
              <MoreHorizontal size={16} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={MENU_PANEL + " min-w-[200px]"}>
            <DropdownMenuItem
              className={itemCls(false)}
              onSelect={() => setRenaming(true)}
            >
              <span className="flex-1">{t("sidebar.rename")}</span>
              <span className={SHORTCUT + " w-[16px] text-center"}>R</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              className={itemCls(false)}
              onSelect={() => setFlags({ pinned: !conv?.pinned })}
            >
              <span className="flex-1">
                {conv?.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
              </span>
              <span className={SHORTCUT + " w-[16px] text-center"}>P</span>
            </DropdownMenuItem>
            <DropdownMenuItem className={itemCls(false)} onSelect={copyLink}>
              <span className="flex-1">{t("sidebar.copy_link")}</span>
              <span className={SHORTCUT + " w-[16px] text-center"}>C</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              className={itemCls(false)}
              onSelect={() => setFlags({ archived: !conv?.archived })}
            >
              <span className="flex-1">
                {conv?.archived ? t("sidebar.unarchive") : t("sidebar.archive")}
              </span>
              <span className={SHORTCUT + " w-[16px] text-center"}>A</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <DropdownMenuItem
              className={itemCls(false, true)}
              onSelect={() => setConfirmDelete(true)}
            >
              <span className="flex-1">{t("sidebar.delete")}</span>
              <span className={SHORTCUT + " w-[16px] text-center"}>D</span>
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
          message={
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
  );
}
