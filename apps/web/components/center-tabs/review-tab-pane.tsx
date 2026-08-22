"use client";

import { useEffect, useMemo, useState } from "react";
import { FileText } from "lucide-react";

import { FeatherIcon } from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { getSocket } from "@/lib/runtime-bridge/state";
import { UnifiedDiff } from "@/components/chat/messages/unified-diff";
import styles from "./review-tab-pane.module.css";

type ReviewScope = "turn" | "branch" | "workspace";
type ReviewCategory = "All" | "Code" | "Tests" | "Docs" | "Large";

interface ReviewFile {
  path: string;
  rel: string;
  op: string;
  added: number | null;
  removed: number | null;
  binary?: boolean;
  diff_state?: string;
  turn_ids?: string[];
}

interface ScopeState {
  loading: boolean;
  status: string;
  source?: string;
  files: ReviewFile[];
  file_count: number;
  added: number | null;
  removed: number | null;
  snapshot_id?: string;
  cursor?: number;
  next_cursor?: number | null;
  prev_cursor?: number | null;
  error?: string;
}

interface DiffState {
  loading: boolean;
  path?: string;
  diff?: string;
  diff_state?: string;
  cursor?: number;
  next_cursor?: number | null;
  prev_cursor?: number | null;
  line_count?: number;
  error?: string;
}

function send(payload: unknown): boolean {
  const socket = getSocket();
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(payload));
  return true;
}

function categoryOf(file: ReviewFile): ReviewCategory {
  if (file.diff_state && file.diff_state !== "available") return "Large";
  if (/(^|\/)(tests?|specs?)(\/|_|-)/i.test(file.rel)) return "Tests";
  if (/\.(md|mdx|rst|txt)$/i.test(file.rel)) return "Docs";
  return "Code";
}

export function ReviewTabPane({
  sessionId,
  assistantMsgId,
  initialScope = "turn",
  initialPath,
}: {
  sessionId: string;
  assistantMsgId?: string;
  initialScope?: ReviewScope;
  initialPath?: string;
}) {
  const { text } = useTranslation();
  const [scope, setScope] = useState<ReviewScope>(initialScope);
  const [selectedPath, setSelectedPath] = useState(initialPath ?? "");
  const [fileCursor, setFileCursor] = useState(0);
  const [diffCursor, setDiffCursor] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [category, setCategory] = useState<ReviewCategory>("All");
  const [scopeState, setScopeState] = useState<ScopeState>({
    loading: true,
    status: "loading",
    files: [],
    file_count: 0,
    added: null,
    removed: null,
  });
  const [diffState, setDiffState] = useState<DiffState>({ loading: false });

  useEffect(() => {
    setScope(initialScope);
    setSelectedPath(initialPath ?? "");
    setFileCursor(0);
    setDiffCursor(0);
  }, [initialScope, initialPath, sessionId, assistantMsgId]);

  useEffect(() => {
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent).detail ?? {};
      if (detail.sessionId === sessionId) setRefreshNonce((value) => value + 1);
    };
    window.addEventListener("turn-files-history-changed", refresh);
    return () => window.removeEventListener("turn-files-history-changed", refresh);
  }, [sessionId]);

  useEffect(() => {
    const socket = getSocket();
    if (!socket || !sessionId || (scope === "turn" && !assistantMsgId)) {
      setScopeState({
        loading: false,
        status: "error",
        files: [],
        file_count: 0,
        added: null,
        removed: null,
        error: text("Review source is unavailable", "审阅来源不可用"),
      });
      return;
    }
    setScopeState((current) => ({ ...current, loading: true, error: undefined }));
    const requestId = crypto.randomUUID();
    const onMessage = (event: MessageEvent) => {
      try {
        const frame = JSON.parse(event.data);
        const data = frame?.data ?? {};
        if (frame?.type !== "review_scope_result") return;
        if (data.request_id !== requestId) return;
        if (data.session_id !== sessionId || data.scope !== scope) return;
        if (scope === "turn" && data.assistant_msg_id !== assistantMsgId) return;
        socket.removeEventListener("message", onMessage);
        const files: ReviewFile[] = data.files ?? [];
        setScopeState({
          loading: false,
          status: data.status ?? "error",
          source: data.source,
          files,
          file_count: data.file_count ?? files.length,
          added: data.added ?? null,
          removed: data.removed ?? null,
          snapshot_id: data.snapshot_id,
          cursor: data.cursor ?? 0,
          next_cursor: data.next_cursor,
          prev_cursor: data.prev_cursor,
          error: data.error,
        });
        setSelectedPath((current) => {
          if (current && files.some((file) => file.path === current)) return current;
          return files[0]?.path ?? "";
        });
      } catch {
        /* ignore unrelated malformed frames */
      }
    };
    socket.addEventListener("message", onMessage);
    const sent = send({
      action: "review_scope",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
      scope,
      cursor: fileCursor,
      limit: 100,
      request_id: requestId,
    });
    if (!sent) {
      socket.removeEventListener("message", onMessage);
      setScopeState((current) => ({
        ...current,
        loading: false,
        status: "error",
        error: text("Not connected", "连接已断开"),
      }));
    }
    return () => socket.removeEventListener("message", onMessage);
  }, [assistantMsgId, fileCursor, refreshNonce, scope, sessionId, text]);

  useEffect(() => {
    const socket = getSocket();
    if (!socket || !selectedPath || !scopeState.snapshot_id) {
      setDiffState({ loading: false });
      return;
    }
    setDiffState({ loading: true, path: selectedPath });
    const requestId = crypto.randomUUID();
    const onMessage = (event: MessageEvent) => {
      try {
        const frame = JSON.parse(event.data);
        const data = frame?.data ?? {};
        if (frame?.type !== "review_file_diff_result") return;
        if (data.request_id !== requestId) return;
        if (
          data.session_id !== sessionId
          || data.scope !== scope
          || data.path !== selectedPath
        ) return;
        socket.removeEventListener("message", onMessage);
        setDiffState({
          loading: false,
          path: selectedPath,
          diff: data.diff ?? "",
          diff_state: data.diff_state ?? "unavailable",
          cursor: data.cursor ?? 0,
          next_cursor: data.next_cursor,
          prev_cursor: data.prev_cursor,
          line_count: data.line_count,
          error: data.error,
        });
      } catch {
        /* ignore unrelated malformed frames */
      }
    };
    socket.addEventListener("message", onMessage);
    if (!send({
      action: "review_file_diff",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
      scope,
      path: selectedPath,
      cursor: diffCursor,
      snapshot_id: scopeState.snapshot_id,
      request_id: requestId,
    })) {
      socket.removeEventListener("message", onMessage);
      setDiffState({
        loading: false,
        path: selectedPath,
        error: text("Not connected", "连接已断开"),
      });
    }
    return () => socket.removeEventListener("message", onMessage);
  }, [
    assistantMsgId,
    diffCursor,
    scope,
    scopeState.snapshot_id,
    selectedPath,
    sessionId,
    text,
  ]);

  const selected = useMemo(
    () => scopeState.files.find((file) => file.path === selectedPath),
    [scopeState.files, selectedPath],
  );
  const visibleFiles = useMemo(
    () => category === "All"
      ? scopeState.files
      : scopeState.files.filter((file) => categoryOf(file) === category),
    [category, scopeState.files],
  );
  const sourceLabel = scopeState.source === "git"
    ? "Git workspace"
    : text("Mutation journal", "修改日志");

  return (
    <div className={styles.page} data-testid="review-tab-pane">
      <header className={styles.header}>
        <span className={styles.logo} aria-hidden="true"><FeatherIcon size={18} /></span>
        <div className={styles.heading}>
          <h1>{text("Review", "审阅")}</h1>
          <span>{sourceLabel}</span>
        </div>
        <div className={styles.scopeTabs} role="tablist" aria-label={text("Review scope", "审阅范围")}>
          {([
            ["turn", text("This turn", "本轮")],
            ["branch", text("Current branch", "当前分支")],
            ["workspace", text("Workspace", "工作区")],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={scope === value}
              className={scope === value ? styles.scopeActive : styles.scope}
              onClick={() => {
                setScope(value);
                setFileCursor(0);
                setDiffCursor(0);
              }}
              disabled={value === "turn" && !assistantMsgId}
            >
              {label}
            </button>
          ))}
        </div>
        <div className={styles.totals} aria-label={text("Change totals", "修改统计")}>
          <b>{scopeState.file_count}</b>
          <span>{text("files", "个文件")}</span>
          <i>+{scopeState.added ?? "—"}</i>
          <em>−{scopeState.removed ?? "—"}</em>
        </div>
      </header>

      <div className={styles.body}>
        <aside className={styles.files} aria-label={text("Changed files", "修改的文件")}>
          <div className={styles.categories} aria-label={text("File category", "文件分类")}>
            {(["All", "Code", "Tests", "Docs", "Large"] as const).map((value) => (
              <button
                type="button"
                key={value}
                aria-pressed={category === value}
                onClick={() => setCategory(value)}
              >
                {value}
              </button>
            ))}
          </div>
          {scopeState.loading ? (
            <div className={styles.empty}>{text("Loading files…", "正在加载文件…")}</div>
          ) : scopeState.error ? (
            <div className={styles.empty}>{scopeState.error}</div>
          ) : visibleFiles.length === 0 ? (
            <div className={styles.empty}>{text("No changes in this scope", "此范围没有修改")}</div>
          ) : visibleFiles.map((file) => (
            <button
              type="button"
              key={file.path}
              className={file.path === selectedPath ? styles.fileActive : styles.file}
              onClick={() => {
                setSelectedPath(file.path);
                setDiffCursor(0);
              }}
              title={file.path}
            >
              <FileText size={14} aria-hidden="true" />
              <span>{file.rel}</span>
              <i>+{file.added ?? "—"}</i>
              <em>−{file.removed ?? "—"}</em>
            </button>
          ))}
          {!scopeState.loading && scopeState.files.length ? (
            <div className={styles.pagination}>
              <button
                type="button"
                disabled={scopeState.prev_cursor == null}
                onClick={() => setFileCursor(scopeState.prev_cursor ?? 0)}
              >
                {text("Previous", "上一页")}
              </button>
              <span>{Math.floor((scopeState.cursor ?? 0) / 100) + 1}</span>
              <button
                type="button"
                disabled={scopeState.next_cursor == null}
                onClick={() => setFileCursor(scopeState.next_cursor ?? 0)}
              >
                {text("Next", "下一页")}
              </button>
            </div>
          ) : null}
        </aside>

        <main
          className={styles.diff}
          data-mounted-diff-count={selectedPath ? "1" : "0"}
          data-mounted-diff-lines={diffState.line_count ?? 0}
        >
          <div className={styles.diffHeader}>
            <span>{selected?.rel ?? text("Select a file", "选择一个文件")}</span>
            {selected?.turn_ids?.length ? (
              <small>{text(`${selected.turn_ids.length} turn(s)`, `${selected.turn_ids.length} 轮`)}</small>
            ) : null}
            {selectedPath ? (
              <div className={styles.diffPagination}>
                <button
                  type="button"
                  disabled={diffState.prev_cursor == null || diffState.loading}
                  onClick={() => setDiffCursor(diffState.prev_cursor ?? 0)}
                >
                  {text("Previous", "上一页")}
                </button>
                <button
                  type="button"
                  disabled={diffState.next_cursor == null || diffState.loading}
                  onClick={() => setDiffCursor(diffState.next_cursor ?? 0)}
                >
                  {text("Next", "下一页")}
                </button>
              </div>
            ) : null}
          </div>
          <div className={styles.diffBody}>
            {!selectedPath ? (
              <div className={styles.empty}>{text("Select a file to inspect", "选择文件以查看差异")}</div>
            ) : diffState.loading ? (
              <div className={styles.empty}>{text("Loading diff…", "正在加载差异…")}</div>
            ) : diffState.error ? (
              <div className={styles.empty}>{diffState.error}</div>
            ) : diffState.diff_state === "large" || diffState.diff_state === "large_line" ? (
              <div className={styles.empty}>{text("Diff exceeds the bounded preview size", "差异超过预览大小限制")}</div>
            ) : diffState.diff_state === "binary" ? (
              <div className={styles.empty}>{text("Binary file", "二进制文件")}</div>
            ) : diffState.diff ? (
              <UnifiedDiff diff={diffState.diff} />
            ) : (
              <div className={styles.empty}>{text("No textual changes", "没有文本改动")}</div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
