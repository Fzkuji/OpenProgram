"use client";

/** Compact per-turn file card. Diffs live in the center Review tab. */
import { useEffect, useMemo, useRef, useState } from "react";

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

const COLLAPSE_AFTER = 3;
const MAX_CARD_FILES = 20;

interface TurnFile {
  path: string;
  rel: string;
  op: string;
  added: number | null;
  removed: number | null;
  binary?: boolean;
  diff_state?: string;
  recoverability?: string;
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
  sessionIdOverride,
}: {
  assistantMsgId: string;
  blocks?: AssistantBlock[];
  summary?: TurnFileSummary;
  initiallyReverted?: boolean;
  sessionIdOverride?: string;
}) {
  const { text } = useTranslation();
  const currentSessionId = useSessionStore((state) => state.currentSessionId);
  const sessionId = sessionIdOverride ?? currentSessionId;
  const updateMessage = useSessionStore((state) => state.updateMessage);
  const project = useCurrentProject();
  const embedded = useMemo(
    () => summaryFiles(summary, project?.path),
    [project?.path, summary],
  );
  const [files, setFiles] = useState<TurnFile[] | null>(embedded);
  const [fileCount, setFileCount] = useState(summary?.file_count ?? embedded?.length ?? 0);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<"undo" | "redo" | null>(null);
  const [reverted, setReverted] = useState(initiallyReverted);
  const [historyError, setHistoryError] = useState("");
  const [visible, setVisible] = useState(false);
  const [historyNonce, setHistoryNonce] = useState(0);
  const [historyState, setHistoryState] = useState<{
    status: string;
    operation: "undo" | "revert" | "redo" | "reapply" | null;
    error?: string;
  } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const probeRef = useRef<HTMLDivElement>(null);
  const openReviewTab = useCenterTabs((state) => state.openReviewTab);

  useEffect(() => {
    setReverted(initiallyReverted);
  }, [initiallyReverted]);

  useEffect(() => {
    if (embedded) setFileCount(summary?.file_count ?? embedded.length);
    const target = probeRef.current;
    if (!target || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "240px 0px" },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [embedded, summary?.file_count]);

  useEffect(() => {
    if (!visible || !sessionId) return;
    const socket = getSocket();
    if (!socket) return;
    const requestId = crypto.randomUUID();
    const onMessage = (event: MessageEvent) => {
      try {
        const frame = JSON.parse(event.data);
        const data = frame?.data ?? {};
        if (
          frame?.type !== "turn_history_state_result"
          || data.request_id !== requestId
          || data.assistant_msg_id !== assistantMsgId
        ) return;
        socket.removeEventListener("message", onMessage);
        setHistoryState({
          status: data.status ?? "error",
          operation: data.action ?? null,
          error: data.error,
        });
      } catch {
        /* ignore unrelated frames */
      }
    };
    socket.addEventListener("message", onMessage);
    if (!send({
      action: "turn_history_state",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
      request_id: requestId,
    })) socket.removeEventListener("message", onMessage);
    return () => socket.removeEventListener("message", onMessage);
  }, [assistantMsgId, historyNonce, sessionId, visible]);

  useEffect(() => {
    if (embedded) {
      setFiles(embedded);
      return;
    }
    if (!visible) return;
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
        setFiles((data.files ?? []).slice(0, MAX_CARD_FILES));
        setFileCount(data.file_count ?? data.files?.length ?? 0);
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
  }, [assistantMsgId, embedded, sessionId, visible]);

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
          setHistoryError(errors.join("; "));
          showToast(errors.join("; "));
          return;
        }
        setHistoryError("");
        setReverted(direction === "undo");
        updateMessage(sessionId, assistantMsgId, {
          reverted: direction === "undo",
        });
        window.dispatchEvent(new CustomEvent("turn-files-history-changed", {
          detail: { sessionId, assistantMsgId },
        }));
        setHistoryNonce((value) => value + 1);
        showToast(direction === "undo"
          ? text("Changes reverted", "修改已撤回")
          : text("Changes reapplied", "修改已重做"));
      } catch {
        /* ignore unrelated frames */
      }
    };
    socket.addEventListener("message", onMessage);
  }

  function loadMore() {
    if (!sessionId || loadingMore) return;
    const socket = getSocket();
    if (!socket) return;
    setLoadingMore(true);
    const onMessage = (event: MessageEvent) => {
      try {
        const frame = JSON.parse(event.data);
        const data = frame?.data ?? {};
        if (
          frame?.type !== "list_turn_files_result"
          || data.assistant_msg_id !== assistantMsgId
        ) return;
        socket.removeEventListener("message", onMessage);
        setLoadingMore(false);
        setFiles((data.files ?? []).slice(0, MAX_CARD_FILES));
        setFileCount(data.file_count ?? data.files?.length ?? 0);
        setShowAll(true);
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
      setLoadingMore(false);
    }
  }

  if (!files) {
    return <div ref={probeRef} className="turn-files-probe" aria-hidden="true" />;
  }
  if (files.length === 0) {
    if (allFileWritesFailed(blocks)) {
      return (
        <div className="turn-files-failed-note">
          {text("File changes in this turn did not go through.", "本轮文件操作未成功执行。")}
        </div>
      );
    }
    return null;
  }

  const totalAdded = summary?.added ?? (files.every((file) => typeof file.added === "number")
    ? files.reduce((total, file) => total + (file.added ?? 0), 0)
    : null);
  const totalRemoved = summary?.removed ?? (files.every((file) => typeof file.removed === "number")
    ? files.reduce((total, file) => total + (file.removed ?? 0), 0)
    : null);
  const shown = showAll
    ? files.slice(0, MAX_CARD_FILES)
    : files.slice(0, COLLAPSE_AFTER);
  const currentAction = historyState?.operation ?? null;
  const actionLabel = currentAction === "undo"
    ? text("Undo", "撤回")
    : currentAction === "revert"
      ? text("Revert", "还原本轮")
      : currentAction === "redo"
        ? text("Redo", "重做")
        : currentAction === "reapply"
          ? text("Reapply", "重新应用")
          : "";
  const totalLines = (totalAdded ?? 0) + (totalRemoved ?? 0);
  const addRatio = totalLines ? Math.round(((totalAdded ?? 0) / totalLines) * 100) : 50;

  return (
    <div
      ref={probeRef}
      className="turn-files-card"
      data-reverted={reverted ? "1" : "0"}
      data-reverting={busy ? "1" : "0"}
    >
      <div className="turn-files-summary">
        <span className="turn-files-logo" aria-hidden="true"><FeatherIcon size={19} /></span>
        <span className="turn-files-heading">
          <span
            className="turn-files-count"
            data-short={text(`${fileCount} files`, `${fileCount} 个文件`)}
          >
            {text(`${fileCount} file${fileCount === 1 ? "" : "s"} changed`, `${fileCount} 个文件已修改`)}
          </span>
          <span className="turn-files-summary-stats">
            <span className="turn-files-stat is-add">+{totalAdded ?? "—"}</span>
            <span className="turn-files-stat is-del">−{totalRemoved ?? "—"}</span>
            <span
              className="turn-files-meter"
              style={{ "--turn-files-add-ratio": `${addRatio}%` } as React.CSSProperties}
              aria-hidden="true"
            />
          </span>
        </span>
        <span className="turn-files-summary-actions">
          {currentAction ? (
            <button
              type="button"
              className="turn-files-action"
              disabled={Boolean(busy)}
              onClick={() => historyAction(
                currentAction === "redo" || currentAction === "reapply"
                  ? "redo"
                  : "undo",
              )}
            >
              <span>{busy ? text("Working…", "处理中…") : actionLabel}</span>
              <span className={`turn-files-action-icon${currentAction === "redo" || currentAction === "reapply" ? " turn-files-redo-icon" : ""}`}>
                <UndoIcon size={14} />
              </span>
            </button>
          ) : null}
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

      {!showAll && (files.length > COLLAPSE_AFTER || fileCount > files.length) ? (
        <button
          type="button"
          className="turn-files-more"
          disabled={loadingMore}
          onClick={() => fileCount > files.length ? loadMore() : setShowAll(true)}
        >
          {text(
            loadingMore
              ? "Loading more files…"
              : `Show ${Math.min(MAX_CARD_FILES - COLLAPSE_AFTER, fileCount - COLLAPSE_AFTER)} more files`,
            loadingMore
              ? "正在加载更多文件…"
              : `再显示 ${Math.min(MAX_CARD_FILES - COLLAPSE_AFTER, fileCount - COLLAPSE_AFTER)} 个文件`,
          )}
        </button>
      ) : showAll ? (
        <button type="button" className="turn-files-more" onClick={() => setShowAll(false)}>
          {text("Collapse", "收起")}
        </button>
      ) : null}
      {fileCount > files.length && (showAll || files.length <= COLLAPSE_AFTER) ? (
        <button
          type="button"
          className="turn-files-more"
          onClick={() => sessionId && openReviewTab(sessionId, assistantMsgId, "turn")}
        >
          {text(`Review all ${fileCount} files`, `审阅全部 ${fileCount} 个文件`)}
        </button>
      ) : null}
      {historyState && historyState.status !== "ready" ? (
        <div className="turn-files-blocked">
          {historyState.error || text(
            "The current file state does not pass history preflight. Review remains available.",
            "当前文件状态未通过历史操作预检，仍可审阅。",
          )}
        </div>
      ) : null}
      {historyError ? <div className="turn-files-blocked">{historyError}</div> : null}
      {reverted ? (
        <div className="turn-files-reverted">
          {text(
            "Historical original diff. These changes are no longer active.",
            "显示原始历史差异；这些修改当前已不生效。",
          )}
        </div>
      ) : null}
    </div>
  );
}
