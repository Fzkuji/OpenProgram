"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Eye, FileDiff, Minus, MoreHorizontal, Plus, Sparkles, Square } from "lucide-react";
import { FileTypeIcon } from "@/components/files/file-type-icon";
import { FileViewer } from "@/components/files/file-viewer";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CHECK_SLOT_PAD,
  MENU_ITEM_STATES,
  MENU_NOTE,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { ReviewDiff } from "./review-diff";
import { canPreview } from "./review-diff-rows.ts";
import styles from "./review-tab-pane.module.css";

import type {
  DiffState,
  LinkedImpact,
  ReviewCategory,
  ReviewFile,
  ReviewScope,
  ReviewSort,
  ScopeState,
} from "./review-tab-types";
import { requestReviewScope } from "./use-review-scope";
import { cancelAgentReview, requestAgentReview, requestReviewDiff, requestRevertHunk, requestStage } from "./use-review-diff";
import {
  findingFixText,
  hunkRange,
  reviewCommentsMessage,
  rowLine,
  type DiffLine,
  type ReviewHunk,
} from "./review-diff-rows.ts";
import { finishReview, sessionComments, sessionFindings, startReview, stopReview, useReviewNotes } from "./review-notes-store";
import type { ReviewFinding } from "./review-tab-types";
import { sendFromTranscript } from "@/lib/chat/send/send-queue";
import { showToast } from "@/lib/format-utils/toast";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { useNow } from "@/lib/clock/use-now";
import { ELAPSED_CADENCE, formatElapsed } from "@/lib/format-utils/elapsed";
import { cn } from "@/lib/utils";

/** What a Review pane last showed, by its source. A pane mounts again
 *  whenever its window comes back (a tab switch, a merge), and starting
 *  from "Loading files…" each time blanked it for the round trip; it
 *  shows this instead and refreshes quietly. */
type ReviewMemory = {
  scope: ReviewScope;
  selectedPath: string;
  category: ReviewCategory;
  view: "changes" | "preview";
  scopeState: ScopeState;
  diffState: DiffState;
};
/** The scope menu's sections: the agent's turn, the Git index, the branch. */
const SCOPE_GROUPS: readonly (readonly ReviewScope[])[] = [["turn"], ["unstaged", "staged"], ["branch"]];
const CATEGORY_GLYPHS: Record<"All" | "Code" | "Tests" | "Docs" | "Large", MenuGlyph> = {
  All: "list",
  Code: "code-square",
  Tests: "checklist",
  Docs: "document-text",
  Large: "database",
};

const MEMORY_LIMIT = 16;
const remembered = new Map<string, ReviewMemory>();
function remember(key: string, memory: ReviewMemory) {
  remembered.delete(key);
  remembered.set(key, memory);
  if (remembered.size > MEMORY_LIMIT) remembered.delete(remembered.keys().next().value!);
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
  const memoryKey = `${sessionId}\u0000${assistantMsgId ?? ""}\u0000${initialScope}\u0000${initialPath ?? ""}`;
  const [memory] = useState(() => remembered.get(memoryKey));
  // The first load after a restore keeps showing what was remembered.
  const quiet = useRef({ reset: !!memory, scope: !!memory });
  const [scope, setScope] = useState<ReviewScope>(memory?.scope ?? initialScope);
  const [selectedPath, setSelectedPath] = useState(memory?.selectedPath ?? initialPath ?? "");
  const [fileCursor, setFileCursor] = useState<string | null>(null);
  const [diffCursor, setDiffCursor] = useState<string | null>(null);
  const [diffHistory, setDiffHistory] = useState<string[]>([]);
  const [refreshNonce, setRefreshNonce] = useState(0);
  // A read that got no reply is asked again, quietly, with backoff; what
  // the pane shows stays meanwhile.
  const [diffRetry, setDiffRetry] = useState(0);
  const failedReads = useRef({ scope: 0, diff: 0 });
  const [category, setCategory] = useState<ReviewCategory>(memory?.category ?? "All");
  // Documents and images can be read rendered; the choice carries over
  // to the next file that supports it.
  const [view, setView] = useState<"changes" | "preview">(memory?.view ?? "changes");
  // The protocol supports query/sort, but this pane currently keeps the
  // default values without adding controls to the existing toolbar layout.
  const query = "";
  const sort: ReviewSort = "path";
  const staleRecoveryRef = useRef<string | null>(null);
  const diffCursorRecoveryRef = useRef<string | null>(null);
  const [scopeState, setScopeState] = useState<ScopeState>(() => memory?.scopeState ?? {
    loading: true,
    status: "loading",
    files: [],
    file_count: 0,
    added: null,
    removed: null,
    linked_impacts: [],
  });
  const [diffState, setDiffState] = useState<DiffState>(() => memory?.diffState ?? { loading: false });
  const diffShown = useRef(diffState);
  diffShown.current = diffState;
  useEffect(() => {
    if (scopeState.loading || diffState.loading) return;
    remember(memoryKey, { scope, selectedPath, category, view, scopeState, diffState });
  }, [memoryKey, scope, selectedPath, category, view, scopeState, diffState]);

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
    if (quiet.current.reset) { quiet.current.reset = false; return; }
    staleRecoveryRef.current = null;
    diffCursorRecoveryRef.current = null;
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
    // A request sent before the socket opened (App start, worker restart)
    // comes back disconnected; ask again once the socket is up.
    const reconnect = (event: Event) => {
      if ((event as CustomEvent).detail?.connected) {
        staleRecoveryRef.current = null;
        setRefreshNonce((value) => value + 1);
      }
    };
    window.addEventListener("turn-files-history-changed", refresh);
    window.addEventListener("op:browser-connection", reconnect);
    return () => {
      window.removeEventListener("turn-files-history-changed", refresh);
      window.removeEventListener("op:browser-connection", reconnect);
    };
  }, [sessionId]);

  // The scope request sends the snapshot it last saw as a hint; a new
  // snapshot arriving is that request's own answer, not a reason to ask again.
  const knownSnapshot = useEffectEvent(() => scopeState.snapshot_id);
  useEffect(() => {
    if (!sessionId || (scope === "turn" && !assistantMsgId)) {
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
    if (quiet.current.scope) quiet.current.scope = false;
    else setScopeState((current) => ({ ...current, loading: true, error: undefined }));
    const controller = new AbortController();
    void (async () => {
const data = await requestReviewScope({
        sessionId,
        assistantMsgId,
        scope,
        category,
        query,
        sort,
        cursor: fileCursor,
        snapshotId: knownSnapshot(),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (!data) {
        console.warn("review scope read got no reply; reading again", { sessionId, scope });
        const wait = Math.min(30_000, 1000 * 2 ** failedReads.current.scope++);
        const timer = setTimeout(() => setRefreshNonce((value) => value + 1), wait);
        controller.signal.addEventListener("abort", () => clearTimeout(timer));
        return;
      }
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
        // Still stale after the fresh request: nothing is shown for it,
        // and the scope is asked again later, with backoff.
        setScopeState((current) => ({
          ...current,
          loading: false,
          status: "stale",
          error: data.error ?? "STALE_SNAPSHOT",
        }));
        console.warn("review scope is still stale; reading again", { sessionId, scope });
        const wait = Math.min(30_000, 1000 * 2 ** failedReads.current.scope++);
        const timer = setTimeout(() => setRefreshNonce((value) => value + 1), wait);
        controller.signal.addEventListener("abort", () => clearTimeout(timer));
        return;
      }
      failedReads.current.scope = 0;
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
        error: data.error as string | undefined,
        linked_impacts: (data.linked_impacts as LinkedImpact[] | undefined) ?? [],
        category: data.category as ReviewCategory | undefined,
        query: data.query as string | undefined,
        sort: data.sort as ReviewSort | undefined,
        staging: data.staging === true,
      });
      setSelectedPath((current) => {
        if (current && files.some((file) => file.path === current)) return current;
        return files[0]?.path ?? "";
      });
    })();
    return () => {
      controller.abort();
    };
  }, [assistantMsgId, category, clearReviewForStale, fileCursor, query, refreshNonce, scope, sessionId, sort, text]);

  useEffect(() => {
    if (!selectedPath || !scopeState.snapshot_id || scopeState.status !== "ready") {
      setDiffState({ loading: false });
      return;
    }
    // The first page of the diff on screen stays up while it reloads (a
    // restore, or a fresh snapshot of the same files): only another file
    // or page shows "Loading diff…".
    const shown = diffShown.current;
    const same = shown.path === selectedPath && !shown.loading && shown.diff !== undefined
      && diffCursor == null && (shown.cursor ?? null) === null;
    if (!same) setDiffState({ loading: true, path: selectedPath });
    const controller = new AbortController();
    const snapshotId = scopeState.snapshot_id;
    void (async () => {
      const data = await requestReviewDiff({
        sessionId,
        assistantMsgId,
        scope,
        category,
        query,
        sort,
        path: selectedPath,
        cursor: diffCursor,
        snapshotId,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (!data) {
        console.warn("review diff read got no reply; reading again", { sessionId, path: selectedPath });
        const wait = Math.min(30_000, 1000 * 2 ** failedReads.current.diff++);
        const timer = setTimeout(() => setDiffRetry((value) => value + 1), wait);
        controller.signal.addEventListener("abort", () => clearTimeout(timer));
        return;
      }
      failedReads.current.diff = 0;
      // A stale answer is read once more from a fresh snapshot of the scope.
      // Stale again, or any other refusal: the file list stays and this
      // file's diff is not drawn; asking again would get the same answer.
      const refreshSnapshotOnce = () => {
        const recoveryKey = `${scope}\u0000${category}\u0000${query}\u0000${sort}\u0000${selectedPath}\u0000${diffCursor ?? ""}`;
        if (staleRecoveryRef.current === recoveryKey) return false;
        staleRecoveryRef.current = recoveryKey;
        clearReviewForStale();
        setRefreshNonce((value) => value + 1);
        return true;
      };
      if (data.error === "STALE_SNAPSHOT") {
        if (!refreshSnapshotOnce()) setDiffState({ loading: false, path: selectedPath, error: data.error });
        return;
      }
      if (data.error === "STALE_CURSOR") {
        const recoveryKey = `${scope}\u0000${category}\u0000${query}\u0000${sort}\u0000${selectedPath}`;
        const retry = diffCursorRecoveryRef.current !== recoveryKey;
        diffCursorRecoveryRef.current = recoveryKey;
        setDiffCursor(null);
        setDiffHistory([]);
        if (retry) {
          setDiffState({ loading: true, path: selectedPath });
        } else if (!refreshSnapshotOnce()) {
          setDiffState({ loading: false, path: selectedPath, error: data.error });
        }
        return;
      }
      diffCursorRecoveryRef.current = null;
      setDiffState({
        loading: false,
        path: selectedPath,
        diff: data.diff as string | undefined ?? "",
        diff_state: data.diff_state as string | undefined ?? "unavailable",
        cursor: data.cursor as string | null | undefined ?? null,
        next_cursor: data.next_cursor as string | null | undefined,
        prev_cursor: data.prev_cursor as string | null | undefined,
        line_count: data.line_count as number | undefined,
        error: data.error as string | undefined,
      });
    })();
    return () => {
      controller.abort();
    };
  }, [
    assistantMsgId,
    category,
    diffCursor,
    query,
    scope,
    scopeState.snapshot_id,
    scopeState.status,
    selectedPath,
    sessionId,
    sort,
    text,
    clearReviewForStale,
    diffRetry,
  ]);

  const selected = useMemo(
    () => scopeState.files.find((file) => file.path === selectedPath),
    [scopeState.files, selectedPath],
  );
  const selectedIndex = scopeState.files.findIndex((file) => file.path === selectedPath);
  const previewable = selected ? canPreview(selected) : false;
  const showPreview = view === "preview" && previewable;
  const sourceLabel = scopeState.source === "git"
    ? "Git workspace"
    : text("Mutation journal", "修改日志");
  const scopeLabels: Record<ReviewScope, string> = {
    turn: text("This turn", "本轮"),
    unstaged: text("Unstaged", "未暂存"),
    staged: text("Staged", "已暂存"),
    branch: text("Branch", "分支"),
  };
  const categoryLabels: Record<ReviewCategory, string> = {
    All: text("All types", "全部类型"),
    Code: text("Code", "代码"),
    Tests: text("Tests", "测试"),
    Docs: text("Docs", "文档"),
    Large: text("Large", "大文件"),
  };

  const chooseScope = (value: ReviewScope) => {
    setScope(value);
    setSelectedPath("");
    setFileCursor(null);
    setDiffCursor(null);
    setDiffHistory([]);
    setScopeState((current) => ({ ...current, snapshot_id: undefined }));
  };
  const chooseCategory = (value: ReviewCategory) => {
    setCategory(value);
    staleRecoveryRef.current = null;
    setSelectedPath("");
    setFileCursor(null);
    setDiffCursor(null);
    setDiffHistory([]);
    setScopeState((current) => ({ ...current, snapshot_id: undefined }));
  };
  const chooseFile = (path: string) => {
    setSelectedPath(path);
    setDiffCursor(null);
    setDiffHistory([]);
  };
  const filePages = scopeState.prev_cursor != null || scopeState.next_cursor != null;
  const noChanges = reviewScopeIsEmpty(scopeState);
  const diffPages = Boolean(selected) && !showPreview
    && (diffHistory.length > 0 || diffState.next_cursor != null);


  /** Revert one hunk, then reload the scope: the file changed, so the
   *  snapshot the pane holds is stale. The selection stays on the file. */
  const revertHunk = async (hunk: ReviewHunk) => {
    const snapshotId = scopeState.snapshot_id;
    if (!selected || !snapshotId) return;
    const result = await requestRevertHunk({
      sessionId, assistantMsgId, scope, category, query, sort,
      path: selected.path, snapshotId, hunk,
    });
    const lines = hunkRange(`@@ -0 +${hunk.new_start},${Math.max(hunk.new_lines.length, 1)} @@`);
    const where = lines ? text(`lines ${lines.from}–${lines.to}`, `第 ${lines.from}–${lines.to} 行`) : "";
    if (result?.status === "ready") {
      showToast(text(`Reverted ${where} of ${fileName(selected.rel)}`, `已撤销 ${fileName(selected.rel)} ${where}的改动`));
    } else if (result?.status === "conflict") {
      showToast(text("This change is no longer in the file; the diff was refreshed", "文件里已没有这处改动，差异已刷新"));
    } else if (result?.status === "blocked") {
      showToast(text("Wait for the current turn to finish, then revert", "请等当前这一轮结束后再撤销"));
      return;
    } else if (result?.error_code === "STALE_SNAPSHOT") {
      showToast(text("The changes were refreshed; try again", "改动已刷新，请重试"));
    } else {
      showToast(text(`Could not revert: ${result?.error ?? "no response"}`, `无法撤销：${result?.error ?? "没有响应"}`));
      return;
    }
    setScopeState((current) => ({ ...current, snapshot_id: undefined }));
    window.dispatchEvent(new CustomEvent("turn-files-history-changed", { detail: { sessionId } }));
  };

  const [stagingFile, setStagingFile] = useState(false);
  // Unstaged scope stages, Staged scope unstages; only a local Git project.
  const staging: "stage" | "unstage" | undefined = !scopeState.staging ? undefined
    : scope === "unstaged" ? "stage" : scope === "staged" ? "unstage" : undefined;

  /** Stage or unstage one hunk, or the whole file without one, then
   *  reload the scope: the index moved, so the snapshot is stale. */
  const stageChange = async (hunk?: ReviewHunk) => {
    const snapshotId = scopeState.snapshot_id;
    if (!selected || !snapshotId || !staging) return;
    const result = await requestStage({
      sessionId, scope, category, query, sort, path: selected.path, snapshotId, hunk,
    });
    if (result?.status === "conflict") {
      showToast(text("The index no longer matches this change; the diff was refreshed", "暂存区已和这处改动不一致，差异已刷新"));
    } else if (result?.status === "blocked") {
      showToast(text("Wait for the current turn to finish, then try again", "请等当前这一轮结束后再试"));
      return;
    } else if (result?.status !== "ready" && result?.error_code !== "STALE_SNAPSHOT") {
      showToast(staging === "stage"
        ? text(`Could not stage: ${result?.error ?? "no response"}`, `无法暂存：${result?.error ?? "没有响应"}`)
        : text(`Could not unstage: ${result?.error ?? "no response"}`, `无法取消暂存：${result?.error ?? "没有响应"}`));
      return;
    }
    setScopeState((current) => ({ ...current, snapshot_id: undefined }));
    setRefreshNonce((value) => value + 1);
    window.dispatchEvent(new CustomEvent("op:git-changed", { detail: {} }));
  };

  // Line comments wait here, per conversation, until they are sent together.
  const comments = useReviewNotes(sessionComments(sessionId));
  const findings = useReviewNotes(sessionFindings(sessionId));
  const reviewing = useReviewNotes((state) => state.reviewing[sessionId] ?? 0);
  const reviewNow = useNow(reviewing ? ELAPSED_CADENCE : null, reviewing);
  const notes = useReviewNotes.getState();
  const fileComments = useMemo(
    () => comments.filter((comment) => comment.scope === scope && comment.path === selected?.path),
    [comments, scope, selected?.path],
  );
  const fileFindings = useMemo(
    () => findings.filter((finding) => finding.scope === scope && finding.rel === selected?.rel),
    [findings, scope, selected?.rel],
  );

  const commentOnLine = (row: DiffLine, comment: string) => {
    if (!selected) return;
    const { side, line } = rowLine(row);
    notes.addComment(sessionId, {
      id: globalThis.crypto.randomUUID(),
      scope,
      path: selected.path,
      rel: selected.rel,
      side,
      line,
      quote: row.text.slice(1),
      body: comment.trim(),
    });
  };
  const sendComments = () => {
    if (!comments.length) return;
    if (sendFromTranscript(sessionId, reviewCommentsMessage(comments))) notes.clearComments(sessionId);
  };
  const fixFinding = (finding: ReviewFinding) => {
    if (sendFromTranscript(sessionId, findingFixText(finding))) notes.removeFinding(sessionId, finding.id);
  };

  /** Review with agent: the conversation's model reviews this scope. Its
   *  findings replace the ones it gave this scope before. A failure only
   *  makes the button usable again. While it runs the same button stops
   *  it, and the server cancels the model request. */
  const reviewWithAgent = async () => {
    const snapshotId = scopeState.snapshot_id;
    if (!snapshotId || reviewing) return;
    const reviewed = scope;
    const { reviewId, signal } = startReview(sessionId);
    try {
      const result = await requestAgentReview({
        sessionId, assistantMsgId, scope: reviewed, category, query, sort, snapshotId, reviewId, signal,
      });
      if (signal.aborted || result?.status !== "ready" || !result.findings) return;
      const kept = (useReviewNotes.getState().findings[sessionId] ?? []).filter((finding) => finding.scope !== reviewed);
      notes.setFindings(sessionId, [
        ...kept,
        ...result.findings.map((finding) => ({
          id: globalThis.crypto.randomUUID(),
          scope: reviewed,
          rel: finding.path,
          line: finding.line,
          severity: finding.severity,
          body: finding.body,
        })),
      ]);
      showToast(result.findings.length
        ? text(
          `Agent review: ${result.findings.length} ${result.findings.length === 1 ? "finding" : "findings"}`,
          `Agent 审阅：${result.findings.length} 条意见`,
        )
        : text("Agent review found nothing to raise", "Agent 审阅没有发现问题"));
    } finally {
      finishReview(sessionId, reviewId);
    }
  };

  const stopAgentReview = () => {
    const reviewId = stopReview(sessionId);
    if (reviewId) cancelAgentReview(reviewId);
  };

  return (
    <div className={styles.page} data-testid="review-tab-pane">
      <header className={styles.header}>
        {/* One row, as in Codex's Changes pane: the scope menu is the title
            and carries the totals; everything used less often sits in the
            … menu. A running review adds its time and a stop button. */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="ghost" size="sm" className={styles.scopeButton} aria-label={text("Review scope", "审阅范围")}>
              <span className={styles.scopeName}>{scopeLabels[scope]}</span>
              <span className={styles.totals}>
                <i>+{scopeState.added ?? "—"}</i>
                <em>−{scopeState.removed ?? "—"}</em>
              </span>
              <ChevronDown aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className={cn(MENU_PANEL, "min-w-[200px] text-text-primary")}>
            {SCOPE_GROUPS.map((group, index) => (
              <span key={group.join()} className="contents">
                {index ? <DropdownMenuSeparator className={MENU_SEPARATOR} /> : null}
                {group.map((value) => (
                  <DropdownMenuItem
                    key={value}
                    className={cn(itemCls(false), MENU_ITEM_STATES)}
                    disabled={value === "turn" && !assistantMsgId}
                    onSelect={() => chooseScope(value)}
                  >
                    <MenuRow label={scopeLabels[value]} checked={scope === value} />
                  </DropdownMenuItem>
                ))}
              </span>
            ))}
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <div className={MENU_NOTE}>
              {`${sourceLabel} · ${text(
                `${scopeState.file_count} ${scopeState.file_count === 1 ? "file" : "files"}`,
                `${scopeState.file_count} 个文件`,
              )}`}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
        {scopeState.linked_impacts.length ? (
          <span
            className={styles.linkedImpacts}
            data-tip={scopeState.linked_impacts
              .map((impact) => `${impact.job_id ?? "actor"} · ${impact.status ?? impact.relation ?? "linked"}`)
              .join("\n")}
          >
            {text(
              `${scopeState.linked_impacts.length} linked`,
              `${scopeState.linked_impacts.length} 个关联任务`,
            )}
          </span>
        ) : null}
        <span className="flex-1" />
        {reviewing ? (
          <span className={styles.reviewing} role="status">
            <Sparkles aria-hidden="true" className={styles.busy} />
            <span>{text("Reviewing", "审阅中")}</span>
            <span className={styles.elapsed}>{formatElapsed((reviewNow - reviewing) / 1000, text)}</span>
            <HoverTip label={text("Stop review", "停止审阅")}>
              <Button type="button" variant="ghost" size="icon-xs" aria-label={text("Stop review", "停止审阅")} onClick={stopAgentReview}>
                <Square aria-hidden="true" fill="currentColor" />
              </Button>
            </HoverTip>
          </span>
        ) : null}
        <DropdownMenu>
          <HoverTip label={text("Review options", "审阅选项")}>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={text("Review options", "审阅选项")}>
                <MoreHorizontal aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
          </HoverTip>
          <DropdownMenuContent align="end" className={cn(MENU_PANEL, "min-w-[200px] text-text-primary")}>
            <DropdownMenuItem
              className={cn(itemCls(false), MENU_ITEM_STATES)}
              disabled={!reviewing && (scopeState.loading || !scopeState.snapshot_id || scopeState.files.length === 0)}
              onSelect={() => (reviewing ? stopAgentReview() : void reviewWithAgent())}
            >
              <MenuRow
                glyph="stars"
                label={reviewing ? text("Stop review", "停止审阅") : text("Review with agent", "让 agent 审阅")}
              />
            </DropdownMenuItem>
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className={cn(itemCls(false), MENU_ITEM_STATES)}>
                <MenuRow glyph="tuning-2" label={text("File type", "文件类型")} trail={categoryLabels[category]} submenu />
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className={cn(MENU_PANEL, "text-text-primary")}>
                {(["All", "Code", "Tests", "Docs", "Large"] as const).map((value) => (
                  <DropdownMenuItem
                    key={value}
                    className={cn(itemCls(false), MENU_ITEM_STATES)}
                    onSelect={() => chooseCategory(value)}
                  >
                    <MenuRow glyph={CATEGORY_GLYPHS[value]} label={categoryLabels[value]} checked={category === value} />
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <div className={`${styles.body} grid grid-cols-[minmax(0,1fr)] flex-1 min-h-0`}>
        <aside className={styles.files} aria-label={text("Changed files", "修改的文件")}>
          {/* Files already read stay while they are read again; with none,
              the list is blank until they arrive. A failed read says nothing. */}
          {scopeState.files.length === 0 ? (
            noChanges ? <div className={styles.empty}>{text("No changes in this scope", "此范围没有修改")}</div> : null
          ) : scopeState.files.map((file) => (
            <button
              type="button"
              key={file.path}
              className={file.path === selectedPath ? styles.fileActive : styles.file}
              aria-current={file.path === selectedPath ? "true" : undefined}
              onClick={() => chooseFile(file.path)}
            >
              <FileTypeIcon name={file.path} />
              <span className="flex flex-col min-w-0">
                <span className={styles.fileName}>{fileName(file.rel)}</span>
                {fileFolder(file.rel) ? <span className={styles.fileFolder}>{fileFolder(file.rel)}</span> : null}
              </span>
              <span className={styles.counts}>
                <i>+{file.added ?? "—"}</i>
                <em>−{file.removed ?? "—"}</em>
              </span>
            </button>
          ))}
          {!scopeState.loading && filePages ? (
            <div className={styles.pagination}>
              <Button
                type="button"
                variant="elevated"
                size="sm"
                disabled={scopeState.prev_cursor == null}
                onClick={() => setFileCursor(scopeState.prev_cursor ?? null)}
              >
                {text("Previous", "上一页")}
              </Button>
              <span>{scopeState.page ?? 1}</span>
              <Button
                type="button"
                variant="elevated"
                size="sm"
                disabled={scopeState.next_cursor == null}
                onClick={() => setFileCursor(scopeState.next_cursor ?? null)}
              >
                {text("Next", "下一页")}
              </Button>
            </div>
          ) : null}
        </aside>

        <main
          className={styles.diff}
          data-mounted-diff-count={selectedPath ? "1" : "0"}
          data-mounted-diff-lines={diffState.line_count ?? 0}
        >
          {selected ? (
            <div className={styles.fileHeader}>
              <div className={`${styles.fileTitle} flex items-center gap-[8px] min-w-0`}>
                <FileTypeIcon name={selected.path} />
                <h2>{fileName(selected.rel)}</h2>
                {selected.op === "add" || selected.op === "delete" ? (
                  <span className={selected.op === "add" ? styles.badgeAdded : styles.badgeDeleted}>
                    {selected.op === "add" ? text("New file", "新文件") : text("Deleted", "已删除")}
                  </span>
                ) : null}
                <span className="ml-[auto] flex items-center gap-[4px] flex-none">
                  {/* Stage and Preview are icons that show while the header
                      is hovered or focused, as Codex's file header does. */}
                  <span className={styles.fileActions}>
                    {staging ? (
                      <HoverTip label={staging === "stage" ? text("Stage file", "暂存文件") : text("Unstage file", "取消暂存文件")}>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={staging === "stage" ? text("Stage file", "暂存文件") : text("Unstage file", "取消暂存文件")}
                          disabled={stagingFile}
                          onClick={() => {
                            setStagingFile(true);
                            void stageChange().finally(() => setStagingFile(false));
                          }}
                        >
                          {staging === "stage" ? <Plus aria-hidden="true" /> : <Minus aria-hidden="true" />}
                        </Button>
                      </HoverTip>
                    ) : null}
                    {previewable ? (
                      <HoverTip label={showPreview ? text("Show changes", "查看改动") : text("Preview", "预览")}>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={showPreview ? text("Show changes", "查看改动") : text("Preview", "预览")}
                          onClick={() => setView(showPreview ? "changes" : "preview")}
                        >
                          {showPreview ? <FileDiff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                        </Button>
                      </HoverTip>
                    ) : null}
                  </span>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        type="button"
                        variant="elevated"
                        size="sm"
                        className={styles.switcher}
                        aria-label={text("Choose file", "选择文件")}
                      >
                        {`${selectedIndex + 1} / ${scopeState.files.length}`}
                        <ChevronDown aria-hidden="true" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent className={MENU_PANEL}>
                      {scopeState.files.map((file) => (
                        <DropdownMenuItem
                          key={file.path}
                          className={`${itemCls(false)} ${MENU_ITEM_STATES}`}
                          onSelect={() => chooseFile(file.path)}
                        >
                          <FileTypeIcon name={file.path} />
                          <span className="min-w-0 flex-1 truncate">{file.rel}</span>
                          <span className={styles.counts}>
                            <i>+{file.added ?? "—"}</i>
                            <em>−{file.removed ?? "—"}</em>
                          </span>
                          {file.path === selectedPath
                            ? <MenuCheck />
                            : <span className={CHECK_SLOT_PAD} />}
                        </DropdownMenuItem>
                      ))}
                      {filePages ? (
                        <>
                          <DropdownMenuSeparator className={MENU_SEPARATOR} />
                          <DropdownMenuItem
                            className={`${itemCls(false)} ${MENU_ITEM_STATES}`}
                            disabled={scopeState.prev_cursor == null}
                            onSelect={() => setFileCursor(scopeState.prev_cursor ?? null)}
                          >
                            <MenuRow glyph="arrow-left" label={text("Previous page", "上一页")} />
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className={`${itemCls(false)} ${MENU_ITEM_STATES}`}
                            disabled={scopeState.next_cursor == null}
                            onSelect={() => setFileCursor(scopeState.next_cursor ?? null)}
                          >
                            <MenuRow glyph="arrow-right" label={text("Next page", "下一页")} />
                          </DropdownMenuItem>
                        </>
                      ) : null}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </span>
              </div>
              <div className={styles.fileMeta}>
                {[
                  fileFolder(selected.rel),
                  selected.actor_ids?.length || selected.actor_id
                    ? text(
                      `Actor ${(selected.actor_ids ?? [selected.actor_id]).filter(Boolean).join(", ")}`,
                      `执行者 ${(selected.actor_ids ?? [selected.actor_id]).filter(Boolean).join("、")}`,
                    )
                    : null,
                  selected.producer_turn_id
                    ? text(`Turn ${selected.producer_turn_id.slice(0, 8)}`, `来源轮次 ${selected.producer_turn_id.slice(0, 8)}`)
                    : null,
                  selected.turn_ids && selected.turn_ids.length > 1
                    ? text(`${selected.turn_ids.length} turns`, `${selected.turn_ids.length} 轮`)
                    : null,
                ].filter(Boolean).map((part) => <span key={part as string}>{part}</span>)}
                <span className={styles.counts}>
                  <i>+{selected.added ?? "—"}</i>
                  <em>−{selected.removed ?? "—"}</em>
                </span>
              </div>
            </div>
          ) : null}
          <div className={`${styles.diffBody} min-h-0 flex-1 overflow-auto flex flex-col`}>
            {!selected ? (
              // The file list is folded away in narrow panes, so its
              // loading, error and empty states show here too.
              <div className={styles.empty}>
                {scopeState.files.length
                  ? text("Select a file to inspect", "选择文件以查看差异")
                  : noChanges
                    ? text("No changes in this scope", "此范围没有修改")
                    : null}
              </div>
            ) : showPreview ? (
              <div className={styles.preview}><FileViewer projectId="" path={selected.path} abs sessionId={sessionId} /></div>
            ) : diffState.loading || diffState.error || scopeState.status !== "ready" ? (
              // No diff to draw: still on its way, refused, or the scope has
              // no snapshot to read diffs from. The file list says the rest.
              <div className={styles.empty} />
            ) : diffState.diff_state === "large" || diffState.diff_state === "large_line" ? (
              <div className={styles.empty}>{text("Diff exceeds the bounded preview size", "差异超过预览大小限制")}</div>
            ) : diffState.diff_state === "binary" ? (
              <div className={styles.empty}>{text("Binary file", "二进制文件")}</div>
            ) : diffState.diff ? (
              <ReviewDiff
                diff={diffState.diff}
                op={selected?.op}
                onRevertHunk={scope === "staged" ? undefined : revertHunk}
                staging={staging}
                onStageHunk={stageChange}
                onComment={commentOnLine}
                comments={fileComments}
                onEditComment={(id, body) => notes.editComment(sessionId, id, body)}
                onDeleteComment={(id) => notes.removeComment(sessionId, id)}
                findings={fileFindings}
                onDismissFinding={(id) => notes.removeFinding(sessionId, id)}
                onFixFinding={fixFinding}
              />
            ) : (
              <div className={styles.empty}>{text("No textual changes", "没有文本改动")}</div>
            )}
            {diffPages ? (
              <div className={styles.diffPagination}>
                <Button
                  type="button"
                  variant="elevated"
                  size="sm"
                  disabled={diffHistory.length === 0 || diffState.loading}
                  onClick={() => {
                    const prior = diffHistory[diffHistory.length - 1] ?? null;
                    setDiffHistory((history) => history.slice(0, -1));
                    setDiffCursor(prior);
                  }}
                >
                  {text("Previous", "上一页")}
                </Button>
                <Button
                  type="button"
                  variant="elevated"
                  size="sm"
                  disabled={diffState.next_cursor == null || diffState.loading}
                  onClick={() => {
                    if (diffCursor) {
                      setDiffHistory((history) => [...history, diffCursor]);
                    }
                    setDiffCursor(diffState.next_cursor ?? null);
                  }}
                >
                  {text("Next", "下一页")}
                </Button>
              </div>
            ) : null}
          </div>
          {comments.length ? (
            <div className={styles.commentBar} role="region" aria-label={text("Pending comments", "待发送的评论")}>
              <span className={styles.commentCount}>
                {text(`${comments.length} ${comments.length === 1 ? "comment" : "comments"}`, `${comments.length} 条评论`)}
              </span>
              <Button type="button" variant="ghost" size="sm" onClick={() => notes.clearComments(sessionId)}>
                {text("Discard", "丢弃")}
              </Button>
              <Button type="button" size="sm" onClick={sendComments}>
                {text("Send to agent", "发送给 agent")}
              </Button>
            </div>
          ) : null}
        </main>
      </div>
    </div>
  );
}

/** "No changes" is said only when the server listed the scope and it is
 *  empty: a listing that is still on its way, stale or refused says nothing.
 *  A snapshot the server could not keep ("unavailable") still lists files. */
export function reviewScopeIsEmpty(state: Pick<ScopeState, "loading" | "status" | "files">): boolean {
  return !state.loading && state.files.length === 0
    && (state.status === "ready" || state.status === "unavailable");
}

function fileName(rel: string): string {
  return rel.split("/").pop() || rel;
}

function fileFolder(rel: string): string {
  const index = rel.lastIndexOf("/");
  return index > 0 ? rel.slice(0, index + 1) : "";
}
