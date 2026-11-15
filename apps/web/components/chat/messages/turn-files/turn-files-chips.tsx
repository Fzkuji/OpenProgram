"use client";

/** Compact per-turn file card. Diffs live in the center Review tab. */
import { useEffect, useMemo, useReducer, useRef, useState, type ReactElement } from "react";

import { FileTypeIcon } from "@/components/files/file-type-icon";
import { FilePathContextMenu } from "@/components/files/file-path-menu";
import { SolarIcon } from "@/components/solar-icons";
import { buttonVariants } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import {
  idempotencyKeyFor,
  MutationRegistryCapacityError,
  wsMutationRequest,
  wsRequest,
} from "@/lib/net/ws/ws-request";
import { selectRow, useSessionStore } from "@/lib/session-store";
import type {
  AssistantBlock,
  TurnFileSummary,
} from "@/lib/session-store/types";
import { showToast } from "@/lib/format-utils/toast";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useCurrentProject } from "@/lib/files/files-shared";
import { openLocalFile } from "@/lib/files/open-local-file";

import {
  historyPresentation,
  SPANS_TURNS_STATE,
  type TurnHistoryOperation,
  type TurnHistoryState,
} from "./turn-files-history-state";
import {
  fileWriteState,
  initialLegacyTurnFilesLoadState,
  legacyTurnFilesLoadReducer,
  TURN_FILES_MAX_CARD_FILES as MAX_CARD_FILES,
  turnFilesListLayout,
  turnFilesSummaryComplete,
  type FileWriteState,
} from "./turn-files-presentation";
import { useRowSessionId } from "../list/transcript-view-scope";

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

const HISTORY_OPERATIONS = new Set<TurnHistoryOperation>(["undo", "revert", "redo", "reapply"]);

/** Split a display path into its folder part (muted) and file name. */
function splitPath(path: string): [string, string] {
  const position = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return position >= 0 ? [path.slice(0, position + 1), path.slice(position + 1)] : ["", path];
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

export function TurnFilesChips({
  assistantMsgId,
  blocks,
  summary,
  initiallyReverted = false,
  sessionIdOverride,
  writeState,
}: {
  assistantMsgId: string;
  blocks?: AssistantBlock[];
  summary?: TurnFileSummary;
  initiallyReverted?: boolean;
  sessionIdOverride?: string;
  writeState?: FileWriteState;
}) {
  const { text } = useTranslation();
  const sessionId = useRowSessionId(sessionIdOverride);
  const applyLocalChange = useSessionStore((state) => state.applyLocalChange);
  const project = useCurrentProject();
  const ownedOrder = useSessionStore((state) => sessionId ? state.messageOrder[sessionId] : undefined);
  const retainedSummary = useSessionStore((state) => selectRow(sessionId, assistantMsgId)(state)?.turnFiles);
  summary = summary ?? (ownedOrder?.includes(assistantMsgId) ? retainedSummary : undefined);
  const spansTurns = Boolean(summary?.spansTurns);
  const embedded = useMemo(
    () => summaryFiles(summary, project?.path),
    [project?.path, summary],
  );
  // A bounded summary (older transcripts kept only the first rows) counts
  // more files than it lists; load the full card list from review_scope.
  const embeddedComplete = embedded !== null
    && turnFilesSummaryComplete(embedded.length, summary?.file_count ?? embedded.length);
  const writesFailed = (writeState ?? fileWriteState(blocks)) === "failed";
  const [files, setFiles] = useState<TurnFile[] | null>(
    embedded ?? (writesFailed ? [] : null),
  );
  const [fileCount, setFileCount] = useState(summary?.file_count ?? embedded?.length ?? 0);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<"undo" | "redo" | null>(null);
  const [reverted, setReverted] = useState(initiallyReverted);
  const [historyError, setHistoryError] = useState("");
  const [visible, setVisible] = useState(false);
  const [historyNonce, setHistoryNonce] = useState(0);
  const [historyState, setHistoryState] = useState<TurnHistoryState | null>(null);
  const [legacyLoad, dispatchLegacyLoad] = useReducer(
    legacyTurnFilesLoadReducer,
    initialLegacyTurnFilesLoadState,
  );
  const historyControllerRef = useRef<AbortController | null>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const openReviewTab = useCenterTabs((state) => state.openReviewTab);

  useEffect(() => {
    setReverted(initiallyReverted);
  }, [initiallyReverted]);

  useEffect(() => () => historyControllerRef.current?.abort(), [assistantMsgId, sessionId]);

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
    if (!visible || !sessionId || spansTurns) return;
    const controller = new AbortController();
    void wsRequest<{ status?: string; operation?: string | null; reason?: string; error?: string; session_id?: string; assistant_msg_id?: string }>(
      "turn_history_state",
      { session_id: sessionId, assistant_msg_id: assistantMsgId },
      "turn_history_state_result",
      (data) => data.session_id === sessionId && data.assistant_msg_id === assistantMsgId,
      4000,
      { signal: controller.signal, requestId: true },
    ).then((data) => {
      if (!data || controller.signal.aborted) return;
        setHistoryError("");
        setHistoryState({
          status: data.status ?? "error",
          operation: HISTORY_OPERATIONS.has(data.operation as TurnHistoryOperation)
            ? (data.operation as TurnHistoryOperation)
            : null,
          reason: data.reason,
          error: data.error,
        });
    });
    return () => controller.abort();
  }, [assistantMsgId, historyNonce, sessionId, spansTurns, visible]);

  useEffect(() => {
    if (embedded) {
      setFiles(embedded);
    }
  }, [embedded]);

  useEffect(() => {
    // The server lists one turn, so its answer would replace a joined list
    // with one part's. Files a joined list can't name stay counted.
    if (embeddedComplete || spansTurns || writesFailed || !visible || !sessionId || !assistantMsgId) return;
    const controller = new AbortController();
    void wsRequest<{
      files?: TurnFile[];
      file_count?: number;
      added?: number | null;
      removed?: number | null;
      reverted?: boolean;
      error?: string;
      session_id?: string;
      assistant_msg_id?: string;
    }>(
      "review_scope",
      { session_id: sessionId, assistant_msg_id: assistantMsgId, scope: "turn" },
      "review_scope_result",
      (data) => data.session_id === sessionId && data.assistant_msg_id === assistantMsgId,
      4000,
      { signal: controller.signal, requestId: true },
    ).then((data) => {
      if (controller.signal.aborted) return;
      if (!data || data.error) {
        dispatchLegacyLoad({ type: "resolved", ok: false });
        return;
      }
      const loadedFiles = (data.files ?? []).slice(0, MAX_CARD_FILES);
      // Retain historical summaries with their owned message, including empty
      // results. Preflight and mutations still read current server state.
      const state = useSessionStore.getState();
      if (state.messageOrder[sessionId]?.includes(assistantMsgId)) {
        const sum = (key: "added" | "removed") => typeof data[key] === "number"
          ? data[key] as number
          : (data.file_count ?? data.files?.length ?? 0) === loadedFiles.length && loadedFiles.every((file) => typeof file[key] === "number")
            ? loadedFiles.reduce((total, file) => total + (file[key] ?? 0), 0) : null;
        applyLocalChange(sessionId, {
          kind: "overlay_set", rowId: assistantMsgId,
          fields: {
            turnFiles: { version: 1, files: loadedFiles, file_count: data.file_count ?? data.files?.length ?? 0,
              added: sum("added"), removed: sum("removed") },
            reverted: Boolean(data.reverted),
          },
          clear: ["until_field_matches", "until_row_gone"],
        });
      }
      setFiles(loadedFiles);
      setFileCount(data.file_count ?? data.files?.length ?? 0);
      setReverted(Boolean(data.reverted));
      dispatchLegacyLoad({ type: "resolved", ok: true });
    });
    return () => controller.abort();
    // `embeddedComplete` (a boolean) gates the load, not `embedded` itself:
    // persisting the loaded list replaces the summary object, which must not
    // trigger another request when the server also returns fewer rows.
  }, [assistantMsgId, embeddedComplete, legacyLoad.attempt, sessionId, spansTurns, visible, writesFailed, applyLocalChange]);

  function historyAction(direction: "undo" | "redo") {
    if (!sessionId || busy) return;
    setBusy(direction);
    historyControllerRef.current?.abort();
    const controller = new AbortController();
    historyControllerRef.current = controller;
    const action = direction === "undo" ? "revert_turn" : "reapply_turn";
    const responseType = direction === "undo"
      ? "revert_turn_result"
      : "reapply_turn_result";
    const operationPayload = { session_id: sessionId, msg_id: assistantMsgId };
    let operationKey: string;
    try {
      operationKey = idempotencyKeyFor(`${action}:${sessionId}`, operationPayload);
    } catch (error) {
      setBusy(null);
      if (error instanceof MutationRegistryCapacityError) {
        showToast(text("Too many file operations are still pending.", "仍有太多文件操作未完成。"));
      }
      return;
    }
    void wsMutationRequest<{ session_id?: string; msg_id?: string; status?: string; errors?: string[]; error?: string; error_code?: string; request_id?: string }>(
      operationKey,
      (signal) => wsRequest(
        action,
        { ...operationPayload, idempotency_key: operationKey },
        responseType,
        (data) => data.session_id === sessionId && data.msg_id === assistantMsgId,
        4000,
        { requestId: true, signal },
      ),
      { signal: controller.signal },
    ).then((data) => {
      if (controller.signal.aborted) return;
      if (!data) {
        setBusy(null);
        showToast(text("History action failed: not connected", "历史操作失败：连接已断开"));
        return;
      }
      if (data.status === "in_progress") {
        setBusy(null);
        setHistoryState({
          status: "in_progress",
          operation: null,
          reason: "in_progress",
          error: text("History action is still in progress.", "历史操作仍在进行中。"),
        });
        return;
      }
        setBusy(null);
        const errors: string[] = data.errors ?? (data.error
          ? [data.error_code ?? data.error] : []);
        if (errors.length) {
          const message = errors.join("; ");
          setHistoryError(message);
          setHistoryState({
            status: data.status ?? "blocked",
            operation: null,
            error: message,
          });
          setHistoryNonce((value) => value + 1);
          showToast(errors.join("; "));
          return;
        }
        setHistoryError("");
        setReverted(direction === "undo");
        applyLocalChange(sessionId, {
          kind: "overlay_set", rowId: assistantMsgId,
          fields: { reverted: direction === "undo" },
          clear: ["until_field_matches", "until_row_gone"],
        });
        window.dispatchEvent(new CustomEvent("turn-files-history-changed", {
          detail: { sessionId, assistantMsgId },
        }));
        setHistoryNonce((value) => value + 1);
        showToast(direction === "undo"
          ? text("Changes reverted", "修改已撤回")
          : text("Changes reapplied", "修改已重做"));
    });
  }

  if (!files) {
    if (legacyLoad.status === "error") {
      return (
        <div className="turn-files-load-error" role="status">
          <span>{text("Could not load file changes.", "无法加载文件修改。")}</span>
          <button
            type="button"
            onClick={() => dispatchLegacyLoad({ type: "retry" })}
          >
            {text("Retry", "重试")}
          </button>
        </div>
      );
    }
    return (
      <div ref={probeRef} className="turn-files-probe" aria-hidden="true" />
    );
  }
  if (files.length === 0) {
    if (writesFailed) {
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
  const layout = turnFilesListLayout(files.length, fileCount, showAll);
  const shown = files.slice(0, layout.shown);
  const headerCount = layout.total;
  const {
    notice: historyNotice,
    operation: currentAction,
  } = historyPresentation(
    spansTurns ? SPANS_TURNS_STATE : historyState,
    historyError,
    {
      files_changed: text(
        "Files from this turn were edited afterwards. Undoing now would overwrite those newer edits.",
        "这一轮改过的文件之后又被改动了，现在撤回会覆盖后来的修改。",
      ),
      not_recoverable: text(
        "Some changes in this turn were not fully recorded, so they can't be restored.",
        "这一轮有些修改没有完整记录，无法恢复到修改前。",
      ),
      inactive_branch: text(
        "This turn is not on the conversation's current branch.",
        "这一轮不在对话的当前分支上。",
      ),
      agent_running: text(
        "An agent started from this turn is still running. Undo becomes available when it finishes.",
        "这一轮启动的智能体还在运行，结束后才能撤回。",
      ),
      run_active: text(
        "This conversation is still running. Undo becomes available when the run finishes.",
        "对话还在运行，运行结束后才能撤回。",
      ),
      in_progress: text("An undo for this turn is already in progress.", "这一轮的撤回正在进行中。"),
      spans_turns: text(
        "This reply continued after a restart. Undo works on one turn at a time, so it can't cover all of these files.",
        "这条回复在重启后继续。撤回一次只处理一轮，无法覆盖这里的全部文件。",
      ),
      fallback: text(
        "Undo isn't available for this turn right now. You can still review the changes.",
        "这一轮暂时无法撤回，仍可审阅修改。",
      ),
    },
  );
  const actionLabel = currentAction === "undo"
    ? text("Undo", "撤回")
    : currentAction === "revert"
      ? text("Revert", "还原本轮")
      : currentAction === "redo"
        ? text("Redo", "重做")
        : currentAction === "reapply"
          ? text("Reapply", "重新应用")
          : reverted
            ? text("Redo", "重做")
            : text("Undo", "撤回");
  const actionRedo = currentAction === "redo" || currentAction === "reapply"
    || (!currentAction && reverted);
  // Right-click on a row: the file menu every chat file shares. A deleted
  // file has nothing left to open, copy or show.
  const fileMenu = (file: TurnFile, row: ReactElement) => file.op === "delete" || !sessionId ? row : (
    <FilePathContextMenu
      key={file.path}
      path={file.path}
      sessionId={sessionId}
      project={project}
      onOpen={() => openLocalFile(file.path, sessionId, project)}
    >
      {row}
    </FilePathContextMenu>
  );

  return (
    <div
      ref={probeRef}
      className="turn-files-card"
      data-reverted={reverted ? "1" : "0"}
      data-reverting={busy ? "1" : "0"}
    >
      <div className="turn-files-summary">
        <span className="turn-files-logo" aria-hidden="true"><SolarIcon name="pen-new-square" size={20} motionPreset="none" /></span>
        <span className="turn-files-heading">
          <span
            className="turn-files-count"
            data-short={text(`${headerCount} files`, `${headerCount} 个文件`)}
          >
            {text(`${headerCount} file${headerCount === 1 ? "" : "s"} changed`, `${headerCount} 个文件已修改`)}
          </span>
          <span className="turn-files-summary-stats turn-files-diff">
            <span className="turn-files-stat is-add"><span>+</span>{totalAdded ?? "—"}</span>
            <span className="turn-files-stat is-del"><span>−</span>{totalRemoved ?? "—"}</span>
          </span>
        </span>
        <span className="turn-files-summary-actions">
          {currentAction ? (
            <button
              type="button"
              className={cn(buttonVariants({ variant: "elevated", size: "xs" }), "turn-files-action")}
              disabled={Boolean(busy)}
              onClick={() => historyAction(actionRedo ? "redo" : "undo")}
            >
              <span className={`turn-files-action-icon${actionRedo ? " turn-files-redo-icon" : ""}`}>
                <SolarIcon name="fa-rotate-left" size={14} motionPreset="none" />
              </span>
              <span>{busy ? text("Working…", "处理中…") : actionLabel}</span>
            </button>
          ) : historyNotice ? (
            // Stays a focusable control (aria-disabled, not disabled) so the
            // hover tip can say why the action is withheld.
            <HoverTip
              label={(
                <TipBody
                  title={actionRedo ? text("Can't redo now", "暂时无法重做") : text("Can't undo now", "暂时无法撤回")}
                  detail={historyNotice}
                />
              )}
            >
              <button
                type="button"
                className={cn(buttonVariants({ variant: "elevated", size: "xs" }), "turn-files-action")}
                aria-disabled="true"
                aria-label={`${actionLabel}: ${historyNotice}`}
              >
                <span className={`turn-files-action-icon${actionRedo ? " turn-files-redo-icon" : ""}`}>
                  <SolarIcon name="fa-rotate-left" size={14} motionPreset="none" />
                </span>
                <span>{actionLabel}</span>
              </button>
            </HoverTip>
          ) : null}
          <button
            type="button"
            className={cn(buttonVariants({ variant: "elevated", size: "xs" }), "turn-files-review")}
            onClick={() => sessionId && openReviewTab(sessionId, assistantMsgId, "turn")}
          >
            <SolarIcon name="fa-eye" size={14} motionPreset="none" />
            <span>{text("Review", "审阅")}</span>
          </button>
        </span>
      </div>

      <div className="turn-files-list">
        {shown.map((file) => fileMenu(file,
          <button
            type="button"
            className="turn-files-row"
            key={file.path}
            onClick={() => sessionId && openReviewTab(
              sessionId, assistantMsgId, "turn", file.path,
            )}
          >
            <span className="turn-files-file-icon" aria-hidden="true">
              <FileTypeIcon name={file.path} size={16} />
            </span>
            <span className="turn-files-name">
              <span className="turn-files-dir">{splitPath(file.rel || basename(file.path))[0]}</span>
              <span className="turn-files-base">{splitPath(file.rel || basename(file.path))[1]}</span>
            </span>
            {file.op === "rename" && !(file.added || file.removed) ? (
              <span className="turn-files-op">{text("renamed", "重命名")}</span>
            ) : file.op === "delete" && !(file.added || file.removed) ? (
              <span className="turn-files-op">{text("deleted", "已删除")}</span>
            ) : (
              <span className="turn-files-diff is-plain">
                <span className="turn-files-stat is-add"><span>+</span>{file.added ?? "—"}</span>
                <span className="turn-files-stat is-del"><span>−</span>{file.removed ?? "—"}</span>
              </span>
            )}
          </button>,
        ))}
        {layout.overflow > 0 ? (
          <button
            type="button"
            className="turn-files-row turn-files-overflow"
            onClick={() => sessionId && openReviewTab(sessionId, assistantMsgId, "turn")}
          >
            <span className="turn-files-file-icon" aria-hidden="true">
              <SolarIcon name="fa-eye" size={14} motionPreset="none" />
            </span>
            <span className="turn-files-name">
              {text(
                `and ${layout.overflow} more file${layout.overflow === 1 ? "" : "s"} — open Review`,
                `还有 ${layout.overflow} 个文件 — 在审阅中查看`,
              )}
            </span>
          </button>
        ) : null}
      </div>

      {layout.more > 0 ? (
        <button
          type="button"
          className="turn-files-more"
          onClick={() => setShowAll(true)}
        >
          {text(
            `Show ${layout.more} more file${layout.more === 1 ? "" : "s"}`,
            `再显示 ${layout.more} 个文件`,
          )}
        </button>
      ) : layout.collapse ? (
        <button type="button" className="turn-files-more" onClick={() => setShowAll(false)}>
          {text("Collapse", "收起")}
        </button>
      ) : null}
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
