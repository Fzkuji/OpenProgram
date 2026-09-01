"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileText } from "lucide-react";

import { FeatherIcon } from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { getSocket } from "@/lib/runtime-bridge/state";
import { UnifiedDiff } from "@/components/chat/messages/unified-diff";
import styles from "./review-tab-pane.module.css";

type ReviewScope = "turn" | "branch" | "workspace";
type ReviewCategory = "All" | "Code" | "Tests" | "Docs" | "Large";
type ReviewSort = "path" | "alpha" | "category" | "recent";

interface ReviewFile {
  path: string;
  rel: string;
  op: string;
  added: number | null;
  removed: number | null;
  binary?: boolean;
  diff_state?: string;
  turn_ids?: string[];
  producer_turn_id?: string;
  origin_turn_id?: string;
  actor_id?: string;
  actor_ids?: string[];
  job_id?: string | null;
  job_ids?: string[];
}

interface LinkedImpact {
  job_id?: string;
  relation?: string;
  status?: string;
  origin_turn_id?: string;
  worktree_id?: string | null;
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
  cursor?: string | null;
  next_cursor?: string | null;
  prev_cursor?: string | null;
  page?: number;
  error?: string;
  linked_impacts: LinkedImpact[];
  category?: ReviewCategory;
  query?: string;
  sort?: ReviewSort;
}

interface DiffState {
  loading: boolean;
  path?: string;
  diff?: string;
  diff_state?: string;
  cursor?: string | null;
  next_cursor?: string | null;
  prev_cursor?: string | null;
  line_count?: number;
  error?: string;
}

function send(payload: unknown): boolean {
  const socket = getSocket();
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(payload));
  return true;
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
  const [fileCursor, setFileCursor] = useState<string | null>(null);
  const [diffCursor, setDiffCursor] = useState<string | null>(null);
  const [diffHistory, setDiffHistory] = useState<string[]>([]);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [category, setCategory] = useState<ReviewCategory>("All");
  // The protocol supports query/sort, but this pane currently keeps the
  // default values without adding controls to the existing toolbar layout.
  const query = "";
  const sort: ReviewSort = "path";
  const staleRecoveryRef = useRef<string | null>(null);
  const [scopeState, setScopeState] = useState<ScopeState>({
    loading: true,
    status: "loading",
    files: [],
    file_count: 0,
    added: null,
    removed: null,
    linked_impacts: [],
  });
  const [diffState, setDiffState] = useState<DiffState>({ loading: false });

  const clearReviewForStale = useCallback(() => {
    setSelectedPath("");
    setFileCursor(null);
    setDiffCursor(null);
    setDiffHistory([]);
    setDiffState({ loading: false });
    setScopeState((current) => ({
      ...current,
      loading: true,
      status: "loading",
      files: [],
      file_count: 0,
      added: null,
      removed: null,
      snapshot_id: undefined,
      cursor: null,
      next_cursor: null,
      prev_cursor: null,
      page: undefined,
      error: undefined,
    }));
  }, []);

  useEffect(() => {
    staleRecoveryRef.current = null;
    setScope(initialScope);
    setSelectedPath(initialPath ?? "");
    setFileCursor(null);
    setDiffCursor(null);
    setDiffHistory([]);
    setScopeState((current) => ({ ...current, snapshot_id: undefined }));
  }, [initialScope, initialPath, sessionId, assistantMsgId]);

  useEffect(() => {
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent).detail ?? {};
      if (detail.sessionId === sessionId) {
        staleRecoveryRef.current = null;
        setRefreshNonce((value) => value + 1);
      }
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
        linked_impacts: [],
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
        if (data.category !== category || data.query !== query || data.sort !== sort) return;
        socket.removeEventListener("message", onMessage);
        const stale = data.status === "stale" || data.error === "STALE_SNAPSHOT";
        if (stale) {
          const recoveryKey = `${scope}\u0000${category}\u0000${query}\u0000${sort}\u0000${fileCursor ?? ""}`;
          const retry = staleRecoveryRef.current !== recoveryKey;
          staleRecoveryRef.current = recoveryKey;
          clearReviewForStale();
          if (retry) {
            setRefreshNonce((value) => value + 1);
            return;
          }
          setScopeState((current) => ({
            ...current,
            loading: false,
            status: "stale",
            error: data.error ?? "STALE_SNAPSHOT",
          }));
          return;
        }
        const files: ReviewFile[] = data.files ?? [];
        setScopeState({
          loading: false,
          status: data.status ?? "error",
          source: data.source,
          files,
          file_count: data.file_count ?? files.length,
          added: data.added ?? null,
          removed: data.removed ?? null,
          snapshot_id: data.status === "ready" ? data.snapshot_id : undefined,
          cursor: data.cursor ?? null,
          next_cursor: data.next_cursor,
          prev_cursor: data.prev_cursor,
          page: data.page ?? 1,
          error: data.error,
          linked_impacts: data.linked_impacts ?? [],
          category: data.category,
          query: data.query,
          sort: data.sort,
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
      category,
      query,
      sort,
      cursor: fileCursor,
      limit: 100,
      ...(fileCursor && scopeState.snapshot_id
        ? { snapshot_id: scopeState.snapshot_id }
        : {}),
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
  }, [assistantMsgId, category, clearReviewForStale, fileCursor, query, refreshNonce, scope, sessionId, sort, text]);

  useEffect(() => {
    const socket = getSocket();
    if (!socket || !selectedPath || !scopeState.snapshot_id || scopeState.status !== "ready") {
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
          || data.snapshot_id !== scopeState.snapshot_id
          || data.category !== category
          || data.query !== query
          || data.sort !== sort
        ) return;
        socket.removeEventListener("message", onMessage);
        if (data.error === "STALE_SNAPSHOT") {
          const recoveryKey = `${scope}\u0000${category}\u0000${query}\u0000${sort}\u0000${selectedPath}\u0000${diffCursor ?? ""}`;
          const retry = staleRecoveryRef.current !== recoveryKey;
          staleRecoveryRef.current = recoveryKey;
          clearReviewForStale();
          if (retry) setRefreshNonce((value) => value + 1);
          else {
            setScopeState((current) => ({
              ...current,
              loading: false,
              status: "stale",
              error: data.error,
            }));
          }
          return;
        }
        setDiffState({
          loading: false,
          path: selectedPath,
          diff: data.diff ?? "",
          diff_state: data.diff_state ?? "unavailable",
          cursor: data.cursor ?? null,
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
      category,
      query,
      sort,
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
    category,
    diffCursor,
    query,
    scope,
    scopeState.snapshot_id,
    selectedPath,
    sessionId,
    sort,
    text,
    clearReviewForStale,
  ]);

  const selected = useMemo(
    () => scopeState.files.find((file) => file.path === selectedPath),
    [scopeState.files, selectedPath],
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
        {scopeState.linked_impacts.length ? (
          <span
            className={styles.linkedImpacts}
            title={scopeState.linked_impacts
              .map((impact) => `${impact.job_id ?? "actor"} · ${impact.status ?? impact.relation ?? "linked"}`)
              .join("\n")}
          >
            {text(
              `${scopeState.linked_impacts.length} linked`,
              `${scopeState.linked_impacts.length} 个关联任务`,
            )}
          </span>
        ) : null}
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
                setSelectedPath("");
                setFileCursor(null);
                setDiffCursor(null);
                setDiffHistory([]);
                setScopeState((current) => ({ ...current, snapshot_id: undefined }));
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
                onClick={() => {
                  setCategory(value);
                  staleRecoveryRef.current = null;
                  setSelectedPath("");
                  setFileCursor(null);
                  setDiffCursor(null);
                  setDiffHistory([]);
                  setScopeState((current) => ({ ...current, snapshot_id: undefined }));
                }}
              >
                {value}
              </button>
            ))}
          </div>
          {scopeState.loading ? (
            <div className={styles.empty}>{text("Loading files…", "正在加载文件…")}</div>
          ) : scopeState.error ? (
            <div className={styles.empty}>{scopeState.error}</div>
          ) : scopeState.files.length === 0 ? (
            <div className={styles.empty}>{text("No changes in this scope", "此范围没有修改")}</div>
          ) : scopeState.files.map((file) => (
            <button
              type="button"
              key={file.path}
              className={file.path === selectedPath ? styles.fileActive : styles.file}
              onClick={() => {
                setSelectedPath(file.path);
                setDiffCursor(null);
                setDiffHistory([]);
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
                onClick={() => setFileCursor(scopeState.prev_cursor ?? null)}
              >
                {text("Previous", "上一页")}
              </button>
              <span>{scopeState.page ?? 1}</span>
              <button
                type="button"
                disabled={scopeState.next_cursor == null}
                onClick={() => setFileCursor(scopeState.next_cursor ?? null)}
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
            {selected ? (
              <small>
                {[
                  selected.actor_ids?.length || selected.actor_id
                    ? text(
                      `Actor: ${(selected.actor_ids ?? [selected.actor_id]).filter(Boolean).join(", ")}`,
                      `执行者：${(selected.actor_ids ?? [selected.actor_id]).filter(Boolean).join("、")}`,
                    )
                    : null,
                  selected.producer_turn_id
                    ? text(`Producer: ${selected.producer_turn_id.slice(0, 8)}`, `来源轮次：${selected.producer_turn_id.slice(0, 8)}`)
                    : null,
                  selected.turn_ids?.length
                    ? text(`${selected.turn_ids.length} turn(s)`, `${selected.turn_ids.length} 轮`)
                    : null,
                ].filter(Boolean).join(" · ")}
              </small>
            ) : null}
            {selectedPath ? (
              <div className={styles.diffPagination}>
                <button
                  type="button"
                  disabled={diffHistory.length === 0 || diffState.loading}
                  onClick={() => {
                    const prior = diffHistory[diffHistory.length - 1] ?? null;
                    setDiffHistory((history) => history.slice(0, -1));
                    setDiffCursor(prior);
                  }}
                >
                  {text("Previous", "上一页")}
                </button>
                <button
                  type="button"
                  disabled={diffState.next_cursor == null || diffState.loading}
                  onClick={() => {
                    if (diffCursor) {
                      setDiffHistory((history) => [...history, diffCursor]);
                    }
                    setDiffCursor(diffState.next_cursor ?? null);
                  }}
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
