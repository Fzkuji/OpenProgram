"use client";

/**
 * The open conversation's actions menu: how it is shown, rename, pin, copy
 * its link or the whole conversation, archive, delete. The chat pane's `…`
 * (view-controls.tsx) and, on a phone, the navigation bar's title
 * (components/layout/phone-title-menu.tsx) open it; the phone puts its own
 * rows first through `leading`.
 */

import { useState, type ReactNode } from "react";

import { autoRenameSession } from "@/lib/chat/session-auto-rename";
import { copyConversation } from "@/lib/chat/copy-conversation";
import { copyText } from "@/lib/clipboard";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { HoverTip } from "@/components/ui/tooltip";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RenameDialog } from "@/components/chat/pane/rename-dialog";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import type { ConvSummary } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
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

/** The open session tab, its saved row and its title. */
export function useOpenSession() {
  const { t } = useTranslation();
  const tab = useCenterTabs((s) => s.tabs.find((x) => x.id === s.activeId));
  const sessionId = tab?.kind === "session" ? tab.sessionId ?? null : null;
  const conv = useSessionStore((s) => (sessionId ? s.conversations[sessionId] : undefined)) as ConvSummary | undefined;
  const title = conv?.title || tab?.title || t("sidebar.untitled");
  // A draft session has no server-side row yet: nothing to rename, pin,
  // link to or delete.
  const saved = !!sessionId && tab?.kind === "session" && !tab.draft;
  return { tab, sessionId, conv, title, saved };
}

/** What can be done to the open conversation, and the dialogs those
 *  actions open (render `dialogs` once). Shared by the chat pane's menu
 *  and the phone's details page. */
export function useSessionActions(onDeleted?: () => void, forSessionId?: string | null) {
  const { t, locale } = useTranslation();
  // A given conversation (a row of the phone's list) or the open one.
  const open = useOpenSession();
  const given = useSessionStore((s) => (forSessionId ? s.conversations[forSessionId] : undefined)) as ConvSummary | undefined;
  const sessionId = forSessionId ?? open.sessionId;
  const conv = forSessionId ? given : open.conv;
  const title = forSessionId ? given?.title || t("sidebar.untitled") : open.title;
  const saved = forSessionId ? !!given : open.saved;
  const conversations = useSessionStore((s) => s.conversations);
  const upsertConversation = useSessionStore((s) => s.upsertConversation);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const transcriptView = useTranscriptView(sessionId);
  const defaultView = useDefaultTranscriptView();

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
    onDeleted?.();
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
  const dialogs = (
    <>
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
    </>
  );
  return { sessionId, conv, title, saved, transcriptView, defaultView, actions, dialogs };
}

export function SessionActionsMenu({
  children,
  leading,
  tip,
  compact = false,
  align = "end",
}: {
  /** The trigger, passed to DropdownMenuTrigger asChild. */
  children: ReactNode;
  /** Rows above the session actions. */
  leading?: ReactNode;
  /** The trigger's hover tip. */
  tip?: string;
  /** A phone's menu: without "Use this view by default", which is a
   *  setting rather than something done to this conversation. */
  compact?: boolean;
  align?: "start" | "center" | "end";
}) {
  const { t, text } = useTranslation();
  const { sessionId, conv, saved, transcriptView, defaultView, actions, dialogs } = useSessionActions();
  const [menuOpen, setMenuOpen] = useState(false);

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
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        {tip ? (
          <HoverTip label={tip} side="bottom">
            <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
          </HoverTip>
        ) : (
          <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
        )}
        {saved ? (
            <DropdownMenuContent align={align} className={MENU_PANEL} onKeyDown={onMenuKey}>
              {leading}
              {leading ? <DropdownMenuSeparator className={MENU_SEPARATOR} /> : null}
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
              {compact ? null : (
<DropdownMenuItem
                className={cn(itemCls(false), MENU_ITEM_STATES)}
                disabled={transcriptView === defaultView}
                onSelect={() => setDefaultTranscriptView(transcriptView)}
              >
                <MenuRow glyph="check-circle" label={text("Use this view by default", "设为默认显示方式")} />
              </DropdownMenuItem>
              )}
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
        ) : leading ? (
          <DropdownMenuContent align={align} className={MENU_PANEL}>{leading}</DropdownMenuContent>
        ) : null}
      </DropdownMenu>
    {dialogs}
    </>
  );
}
