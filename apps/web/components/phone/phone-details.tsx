"use client";

/**
 * A conversation's details on the phone, pushed over it: what the desktop
 * keeps in the right panel (changes, files, activity, resources), the way
 * it is shown, and what can be done to it (components/chat/pane/
 * session-actions-menu.tsx, the same actions as the chat pane's menu).
 * Each panel opens as a page of its own.
 */

import { useEffect, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { useSessionActions } from "@/components/chat/pane/session-actions-menu";
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
import { wsRequest } from "@/lib/net/ws/ws-request";
import { setSessionTranscriptView, type TranscriptView } from "@/lib/prefs/transcript-view";

import { openDetails, type DetailsPage } from "./phone-nav";
import { PhoneNavBar } from "./phone-nav-bar";

function Row({ glyph, tone, label, trail, onClick, danger }: {
  glyph?: SolarIconName;
  tone?: string;
  label: ReactNode;
  trail?: ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button type="button" className="phone-app-row" data-danger={danger ? "" : undefined} onClick={onClick}>
      {glyph ? (
        <span className="phone-app-row-icon" style={{ background: tone }} aria-hidden="true">
          <SolarIcon name={glyph} size={16} />
        </span>
      ) : null}
      <span className="phone-app-row-label">{label}</span>
      {trail != null ? <span className="phone-app-row-trail">{trail}</span> : null}
      {glyph ? <ChevronRight size={18} className="phone-app-row-chevron" aria-hidden="true" /> : null}
    </button>
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
  const { conv, transcriptView, actions, dialogs } = useSessionActions(() => {
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
      <div className="phone-app-page phone-details-page" data-page={page}>
        <PhoneNavBar back={page === "step" ? text("Chat", "对话") : text("Details", "详情")} title={names[page]} />
        <div className="phone-app-panel">
          {page === "changes" ? <ReviewTabPane sessionId={sessionId} initialScope="unstaged" /> : null}
          {page === "files" ? (project?.id
            ? <FileTree projectId={project.id} />
            : <p className="phone-app-empty">{text("This chat has no project folder", "这个对话没有绑定项目文件夹")}</p>) : null}
          {page === "activity" ? <RunningPanel sessionId={sessionId} active /> : null}
          {page === "resources" ? <SessionResourcesPanel sessionId={sessionId} active /> : null}
          {page === "step" ? <DetailPanel /> : null}
        </div>
      </div>
    );
  }

  const views: [TranscriptView, string][] = [
    ["normal", text("Normal", "普通")],
    ["thinking", text("Thinking", "显示思考")],
    ["verbose", text("Verbose", "详细")],
  ];
  return (
    <div className="phone-app-page phone-details">
      <PhoneNavBar back={text("Chat", "对话")} title={text("Details", "详情")} />
      <div className="phone-app-scroll">
        <div className="phone-details-head">
          <h1>{title}</h1>
          {conv?.project ? <p>{conv.project}</p> : null}
        </div>
        <div className="phone-app-list">
          <Row glyph="git-pull-request" tone="#3d9a5b" label={text("Changes", "改动")} onClick={() => openDetails(sessionId, "changes")}
            trail={lines ? <span className="phone-lines"><span data-add>+{lines.add}</span> <span data-del>−{lines.del}</span></span> : undefined} />
          <Row glyph="folder-open" tone="#2f6fe4" label={text("Files", "文件")} onClick={() => openDetails(sessionId, "files")} />
          <Row glyph="history" tone="#8a63d2" label={text("Activity", "活动")} onClick={() => openDetails(sessionId, "activity")} />
          <Row glyph="box" tone="#c96442" label={text("Resources", "资源")} onClick={() => openDetails(sessionId, "resources")} />
        </div>
        <h2 className="phone-app-section-title">{text("Show", "显示方式")}</h2>
        <div className="phone-app-segmented" role="radiogroup">
          {views.map(([view, label]) => (
            <button key={view} type="button" role="radio" aria-checked={transcriptView === view}
              data-on={transcriptView === view ? "" : undefined}
              onClick={() => setSessionTranscriptView(sessionId, view)}>{label}</button>
          ))}
        </div>
        <div className="phone-app-list">
          <Row label={conv?.pinned ? t("sidebar.unpin") : t("sidebar.pin")} onClick={actions.p} />
          <Row label={t("sidebar.rename")} onClick={actions.r} />
          <Row label={t("sidebar.auto_rename")} onClick={() => void autoRenameSession(sessionId)} />
          <Row label={t("sidebar.copy_link")} onClick={actions.c} />
          <Row label={text("Copy conversation", "复制会话")} onClick={() => void copyConversation(sessionId, text)} />
        </div>
        <div className="phone-app-list">
          <Row label={conv?.archived ? t("sidebar.unarchive") : t("sidebar.archive")} onClick={actions.a} />
          <Row label={t("sidebar.delete")} onClick={actions.d} danger />
        </div>
      </div>
      {dialogs}
    </div>
  );
}
