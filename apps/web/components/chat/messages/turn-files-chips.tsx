"use client";

/** Compact per-turn file card. Diffs live in the center Review tab. */
import { useEffect, useMemo, useState } from "react";

import {
  FeatherIcon,
  UndoIcon,
} from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { getSocket } from "@/lib/runtime-bridge/state";
import { useSessionStore } from "@/lib/session-store";
import type {
  AssistantBlock,
  TurnFileSummary,
} from "@/lib/session-store/types";
import { showToast } from "@/lib/format-utils/toast";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useCurrentProject } from "@/lib/state/files-shared";

const COLLAPSE_AFTER = 5;

interface TurnFile {
  path: string;
  rel: string;
  op: string;
  added: number | null;
  removed: number | null;
  binary?: boolean;
  diff_state?: string;
}

const FILE_WRITING_TOOLS = new Set(["write", "edit", "apply_patch"]);

function allFileWritesFailed(blocks?: AssistantBlock[]): boolean {
  if (!blocks) return false;
  const writes = blocks.filter(
    (block) => block.type === "tool"
      && FILE_WRITING_TOOLS.has((block.tool || "").toLowerCase()),
  );
  return writes.length > 0 && writes.every((block) => block.is_error === true);
}

function basename(path: string): string {
  const position = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return position >= 0 ? path.slice(position + 1) : path;
}

function summaryFiles(summary?: TurnFileSummary, projectRoot?: string): TurnFile[] | null {
  if (!summary) return null;
  const root = projectRoot
    ? projectRoot.replace(/[\\/]+$/, "") + "/"
    : "";
  return summary.files.map((file) => ({
    ...file,
    rel: root && file.path.startsWith(root)
      ? file.path.slice(root.length)
      : basename(file.path),
  }));
}

function send(payload: unknown): boolean {
  const socket = getSocket();
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(payload));
  return true;
}

export function TurnFilesChips({
  assistantMsgId,
  blocks,
  summary,
  initiallyReverted = false,
}: {
  assistantMsgId: string;
  blocks?: AssistantBlock[];
  summary?: TurnFileSummary;
  initiallyReverted?: boolean;
}) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((state) => state.currentSessionId);
  const project = useCurrentProject();
  const embedded = useMemo(
    () => summaryFiles(summary, project?.path),
    [project?.path, summary],
  );
  const [files, setFiles] = useState<TurnFile[] | null>(embedded);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<"undo" | "redo" | null>(null);
  const [reverted, setReverted] = useState(initiallyReverted);
  const openReviewTab = useCenterTabs((state) => state.openReviewTab);

  useEffect(() => {
    setReverted(initiallyReverted);
  }, [initiallyReverted]);

  useEffect(() => {
    if (embedded) {
      setFiles(embedded);
      return;
    }
    if (!sessionId || !assistantMsgId) return;
    const socket = getSocket();
    if (!socket) return;
    const onMessage = (event: MessageEvent) => {
      try {
        const frame = JSON.parse(event.data);
        const data = frame?.data ?? {};
        if (
          frame?.type !== "list_turn_files_result"
          || data.assistant_msg_id !== assistantMsgId
        ) return;
        socket.removeEventListener("message", onMessage);
        setFiles(data.files ?? []);
        setReverted(Boolean(data.reverted));
      } catch {
        /* ignore unrelated frames */
      }
    };
    socket.addEventListener("message", onMessage);
    if (!send({
      action: "list_turn_files",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
    })) {
      socket.removeEventListener("message", onMessage);
    }
    return () => socket.removeEventListener("message", onMessage);
  }, [assistantMsgId, embedded, sessionId]);

  function historyAction(direction: "undo" | "redo") {
    if (!sessionId || busy) return;
    setBusy(direction);
    const action = direction === "undo" ? "revert_turn" : "reapply_turn";
    const responseType = direction === "undo"
      ? "revert_turn_result"
      : "reapply_turn_result";
    const socket = getSocket();
    if (!socket || !send({
      action,
      session_id: sessionId,
      msg_id: assistantMsgId,
      idempotency_key: crypto.randomUUID(),
    })) {
      setBusy(null);
      showToast(text("History action failed: not connected", "历史操作失败：连接已断开"));
      return;
    }
    const onMessage = (event: MessageEvent) => {
      try {
        const frame = JSON.parse(event.data);
        const data = frame?.data ?? {};
        if (frame?.type !== responseType || data.msg_id !== assistantMsgId) return;
        socket.removeEventListener("message", onMessage);
        setBusy(null);
        const errors: string[] = data.errors ?? [];
        if (errors.length) {
          showToast(errors.join("; "));
          return;
        }
        setReverted(direction === "undo");
        showToast(direction === "undo"
          ? text("Changes reverted", "修改已撤回")
          : text("Changes reapplied", "修改已重做"));
      } catch {
        /* ignore unrelated frames */
      }
    };
    socket.addEventListener("message", onMessage);
  }

  if (!files || files.length === 0) {
    if (allFileWritesFailed(blocks)) {
      return (
        <div className="turn-files-failed-note">
          {text("File changes in this turn did not go through.", "本轮文件操作未成功执行。")}
        </div>
      );
    }
    return null;
  }

  const totalAdded = files.every((file) => typeof file.added === "number")
    ? files.reduce((total, file) => total + (file.added ?? 0), 0)
    : null;
  const totalRemoved = files.every((file) => typeof file.removed === "number")
    ? files.reduce((total, file) => total + (file.removed ?? 0), 0)
    : null;
  const testCount = files.filter((file) => /(^|\/)(test|tests|spec|specs)(\/|_)/i.test(file.path)).length;
  const codeCount = files.length - testCount;
  const shown = showAll ? files : files.slice(0, COLLAPSE_AFTER);

  return (
    <div
      className="turn-files-card"
      data-reverted={reverted ? "1" : "0"}
      data-reverting={busy ? "1" : "0"}
    >
      <div className="turn-files-summary">
        <span className="turn-files-logo" aria-hidden="true"><FeatherIcon size={17} /></span>
        <span className="turn-files-heading">
          <span className="turn-files-count">
            {text(`Edited ${files.length} files`, `已编辑 ${files.length} 个文件`)}
          </span>
          <span className="turn-files-summary-stats">
            <span className="turn-files-stat is-add">+{totalAdded ?? "—"}</span>
            <span className="turn-files-stat is-del">−{totalRemoved ?? "—"}</span>
            {codeCount ? <span className="turn-files-kind">Code {codeCount}</span> : null}
            {testCount ? <span className="turn-files-kind">Tests {testCount}</span> : null}
          </span>
        </span>
        <span className="turn-files-summary-actions">
          <button
            type="button"
            className="turn-files-action"
            disabled={Boolean(busy) || reverted}
            onClick={() => historyAction("undo")}
          >
            <span className="turn-files-action-icon"><UndoIcon size={14} /></span>
            {busy === "undo" ? text("Undoing…", "撤回中…") : text("Undo", "撤回")}
          </button>
          <button
            type="button"
            className="turn-files-action"
            disabled={Boolean(busy) || !reverted}
            onClick={() => historyAction("redo")}
          >
            <span className="turn-files-action-icon turn-files-redo-icon">
              <UndoIcon size={14} />
            </span>
            {busy === "redo" ? text("Redoing…", "重做中…") : text("Redo", "重做")}
          </button>
          <button
            type="button"
            className="turn-files-review"
            onClick={() => sessionId && openReviewTab(sessionId, assistantMsgId, "turn")}
          >
            {text("Review", "审阅")}
          </button>
        </span>
      </div>

      <div className="turn-files-list">
        {shown.map((file) => (
          <button
            type="button"
            className="turn-files-row"
            key={file.path}
            title={file.path}
            onClick={() => sessionId && openReviewTab(
              sessionId, assistantMsgId, "turn", file.path,
            )}
          >
            <span className="turn-files-name">{file.rel || basename(file.path)}</span>
            {file.op === "rename" && !(file.added || file.removed) ? (
              <span className="turn-files-op">{text("renamed", "重命名")}</span>
            ) : file.op === "delete" && !(file.added || file.removed) ? (
              <span className="turn-files-op">{text("deleted", "已删除")}</span>
            ) : (
              <>
                <span className="turn-files-stat is-add">+{file.added ?? "—"}</span>
                <span className="turn-files-stat is-del">−{file.removed ?? "—"}</span>
              </>
            )}
          </button>
        ))}
      </div>

      {files.length > COLLAPSE_AFTER ? (
        <button type="button" className="turn-files-more" onClick={() => setShowAll((value) => !value)}>
          {showAll
            ? text("Collapse", "收起")
            : text(`Show ${files.length - COLLAPSE_AFTER} more files`, `再显示 ${files.length - COLLAPSE_AFTER} 个文件`)}
        </button>
      ) : null}
    </div>
  );
}
