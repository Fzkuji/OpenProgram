"use client";

/**
 * A conversation's details on the phone, pushed over it: what the desktop
 * keeps in the right panel (changes, files, activity, resources), the way
 * it is shown, and what can be done to it (components/chat/pane/
 * session-actions-menu.tsx, the same actions as the chat pane's menu).
 * Each panel opens as a page of its own.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Copy, Link2, Pencil, Pin, PinOff, type LucideIcon } from "lucide-react";
import { Block, BlockTitle, Button, List, ListButton, ListItem, Navbar, Page, Segmented } from "framework7-react";

import { useSessionActions } from "@/components/chat/pane/session-actions-menu";
import { AgentMark } from "@/components/chat/top-bar/chips/model-mark";
import { ReviewTabPane } from "@/components/center-tabs/review/review-tab-pane";
import { FileTree } from "@/components/files/tree/file-tree";
import { DetailPanel } from "@/components/right-sidebar/detail-panel";
import { RunningPanel } from "@/components/right-sidebar/running/running-panel";
import { SessionResourcesPanel } from "@/components/session-resources/session-resources-panel";
import { SolarIcon, type SolarIconName } from "@/components/solar-icons";
import { displayTitle, type LegacyConv } from "@/components/sidebar/sessions/helpers";
import { autoRenameSession } from "@/lib/chat/session-auto-rename";
import { copyConversation } from "@/lib/chat/copy-conversation";
import { useCurrentProject } from "@/lib/files/files-shared";
import { useTranslation } from "@/lib/i18n";
import { agentName } from "@/lib/models/harnesses";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { setSessionTranscriptView, type TranscriptView } from "@/lib/prefs/transcript-view";

import { askDelete, askRename } from "./phone-dialogs";
import { openDetails, type DetailsPage } from "./phone-nav";
import { backTo } from "./phone-router";

/** A row of the details: an icon on a colour of its own, as in Settings. */
function Row({ glyph, tone, label, trail, onClick }: {
  glyph: SolarIconName;
  tone: string;
  label: string;
  trail?: ReactNode;
  onClick: () => void;
}) {
  return (
    <ListItem link="#" title={label} onClick={onClick}>
      <span slot="media" className="phone-row-icon" style={{ background: tone }} aria-hidden="true" data-not-f7>
        <SolarIcon name={glyph} size={16} />
      </span>
      {trail ? <span slot="after">{trail}</span> : null}
    </ListItem>
  );
}

// Framework7's List puts its rows in its <ul> by their component's name.
Row.displayName = "f7-list-item-details";

/** One of the actions under the chat's card: Framework7's tonal button,
 *  its icon over its name. */
function Tile({ icon: Icon, label, onClick }: { icon: LucideIcon; label: string; onClick: () => void }) {
  return (
    <Button tonal large className="phone-details-tile" onClick={onClick}>
      <Icon size={20} aria-hidden="true" />
      <span>{label}</span>
    </Button>
  );
}

/** The folder's uncommitted lines, for the Changes row (the git chip's
 *  light read). */
function useChangedLines(path: string | undefined) {
  const [lines, setLines] = useState<{ add: number; del: number } | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    void wsRequest<{ path: string; status: { changes?: { insertions: number; deletions: number } } }>(
      "git_folder_status", { path }, "git_folder_status", (d) => d.path === path, 6000,
    ).then((data) => {
      const changes = data?.status?.changes;
      if (live && changes && (changes.insertions || changes.deletions)) setLines({ add: changes.insertions, del: changes.deletions });
    });
    return () => { live = false; };
  }, [path]);
  return lines;
}

export function PhoneDetails({ sessionId, page }: { sessionId: string; page: DetailsPage | null }) {
  const { t, text } = useTranslation();
  // A deleted conversation leaves for the list, not for a new chat.
  const { conv, transcriptView, actions, rename, remove, title: name } = useSessionActions(() => {
    setTimeout(() => window.history.replaceState(null, "", "/chats"), 0);
  });
  const project = useCurrentProject({ sessionId, chatKey: sessionId });
  const title = (conv && displayTitle(conv as LegacyConv)) || text("New chat", "新对话");
  const lines = useChangedLines(page ? undefined : project?.path);

  if (page) {
    const names: Record<DetailsPage, string> = {
      changes: text("Changes", "改动"),
      files: text("Files", "文件"),
      activity: text("Activity", "活动"),
      resources: text("Resources", "资源"),
      step: text("Step", "步骤"),
    };
    return (
      <Page pageContent={false} className="phone-details-page" data-page={page}>
        <Navbar {...backTo(page === "step" ? text("Chat", "对话") : text("Details", "详情"))} title={names[page]} />
        <div className="phone-details-panel" data-not-f7>
          {page === "changes" ? <ReviewTabPane sessionId={sessionId} initialScope="unstaged" /> : null}
          {page === "files" ? (project?.id
            ? <FileTree projectId={project.id} />
            : <p className="phone-details-empty">{text("This chat has no project folder", "这个对话没有绑定项目文件夹")}</p>) : null}
          {page === "activity" ? <RunningPanel sessionId={sessionId} active /> : null}
          {page === "resources" ? <SessionResourcesPanel sessionId={sessionId} active /> : null}
          {page === "step" ? <DetailPanel /> : null}
        </div>
      </Page>
    );
  }

  const views: [TranscriptView, string][] = [
    ["normal", text("Normal", "普通")],
    ["thinking", text("Thinking", "显示思考")],
    ["verbose", text("Verbose", "详细")],
  ];
  const harness = conv?.harness ? agentName(conv.harness) ?? conv.harness : "OpenProgram";
  // Laid out as Settings is: the chat as the card at the top (the agent's
  // mark, its name, where it runs), the common actions under it, then
  // grouped cards, the dangerous ones last.
  return (
    <Page className="phone-details">
      <Navbar {...backTo(text("Chat", "对话"))} title={text("Details", "详情")} />
      <List strong inset dividersIos mediaList>
        <ListItem title={title} text={[conv?.project, harness].filter(Boolean).join(" · ")}>
          <span slot="media" className="phone-details-mark" aria-hidden="true" data-not-f7>
            {conv?.harness
              ? <AgentMark harness={conv.harness} size={40} fit="contain" />
              : <img src="/icons/icon-192.png" alt="" />}
          </span>
        </ListItem>
      </List>
      <Block>
        <div className="grid grid-cols-4 grid-gap">
          <Tile icon={conv?.pinned ? PinOff : Pin} label={conv?.pinned ? t("sidebar.unpin") : t("sidebar.pin")} onClick={actions.p} />
          <Tile icon={Pencil} label={t("sidebar.rename")} onClick={() => askRename(text, name, rename)} />
          <Tile icon={Link2} label={t("sidebar.copy_link")} onClick={actions.c} />
          <Tile icon={Copy} label={text("Copy chat", "复制对话")} onClick={() => void copyConversation(sessionId, text)} />
        </div>
      </Block>
      <List strong inset dividersIos>
        <Row glyph="git-pull-request" tone="#34c759" label={text("Changes", "改动")} onClick={() => openDetails(sessionId, "changes")}
          trail={lines ? <>+{lines.add} −{lines.del}</> : undefined} />
        <Row glyph="folder-open" tone="#007aff" label={text("Files", "文件")} onClick={() => openDetails(sessionId, "files")} />
        <Row glyph="history" tone="#af52de" label={text("Activity", "活动")} onClick={() => openDetails(sessionId, "activity")} />
        <Row glyph="box" tone="#ff9500" label={text("Resources", "资源")} onClick={() => openDetails(sessionId, "resources")} />
      </List>
      <BlockTitle>{text("Show", "显示方式")}</BlockTitle>
      <Block>
        <Segmented strong>
          {views.map(([view, label]) => (
            <Button key={view} active={view === transcriptView} onClick={() => setSessionTranscriptView(sessionId, view)}>{label}</Button>
          ))}
        </Segmented>
      </Block>
      <List strong inset dividersIos>
        <ListButton title={t("sidebar.auto_rename")} onClick={() => void autoRenameSession(sessionId)} />
      </List>
      <List strong inset dividersIos>
        <ListButton title={conv?.archived ? t("sidebar.unarchive") : t("sidebar.archive")} onClick={actions.a} />
        <ListButton color="red" title={t("sidebar.delete")} onClick={() => askDelete(text, name, remove)} />
      </List>
    </Page>
  );
}
