"use client";

/**
 * Per-turn file edit card — the list of files an assistant turn changed,
 * with per-file +N/-N counts, an inline diff preview and Undo.
 *
 * Fires `list_turn_files` over the shared WS once the bubble is no
 * longer streaming; renders nothing when the turn touched no files
 * (the common case for chat-only replies, so empty state stays quiet).
 *
 * Interaction:
 *   Click the ROW   → expand/collapse a full-width unified diff right
 *                     under it, fetched once via `turn_file_diff` and
 *                     cached in component state. Several rows may be
 *                     open at once; the diff reads in the chat column
 *                     instead of the narrow right rail.
 *   ↗ (row action)  → open the file as a center editor tab SPLIT beside
 *                     the chat, in this turn's diff mode, scrolled to
 *                     the first changed line.
 *   Folder (row)    → reveal the file in the right rail's project tree.
 *   Undo            → `revert_turn`, restoring every file this turn
 *                     touched. Turn-scoped, so it sits in the multi-file
 *                     header; a single file keeps it on its row.
 *
 * Both buttons stopPropagation so they never toggle the diff.
 * The filename itself is deliberately NOT the editor link any more:
 * with the whole row toggling the diff, a nested link inside it would
 * be two overlapping targets on one label. The ↗ icon separates them.
 */
import { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import type { AssistantBlock } from "@/lib/session-store/types";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { fileTabId, sessionTabId, useCenterTabs } from "@/lib/state/center-tabs-store";
import { findCenterTabGroup } from "@/lib/state/center-tab-groups";
import { useCurrentProject } from "@/lib/state/files-shared";
import {
  ArrowUpRightIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FolderOpenIcon,
  UndoIcon,
} from "@/components/animated-icons";
import { UnifiedDiff, parseUnifiedDiff } from "./unified-diff";

/** Rows past this collapse behind a "Show all N" toggle. */
const COLLAPSE_AFTER = 5;

/** One changed file, as returned by `list_turn_files`. */
interface TurnFile {
  path: string;
  rel: string;
  op: string;
  added: number;
  removed: number;
}

/** Cached `turn_file_diff` answer for one path. */
interface DiffState {
  loading: boolean;
  diff: string;
  approximate: boolean;
  error?: string | null;
}

function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(JSON.stringify(payload));
  return true;
}

function basename(p: string): string {
  if (!p) return p;
  const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return i >= 0 ? p.slice(i + 1) : p;
}

/** Tools whose whole job is writing to a file — the same three that call
 *  `checkpoint_before_edit` on the backend (openprogram/functions/tools). */
const FILE_WRITING_TOOLS = new Set(["write", "edit", "apply_patch"]);

/**
 * True when this turn tried to change files and every attempt errored.
 *
 * The signal is the tool calls themselves, not the reply text: a turn
 * that only CLAIMS an edit in prose gives us nothing reliable to check
 * (see the note in the component below), whereas "called edit, edit
 * returned an error, checkpoint list is empty" is unambiguous.
 */
function allFileWritesFailed(blocks?: AssistantBlock[]): boolean {
  if (!blocks) return false;
  const writes = blocks.filter(
    (b) => b.type === "tool" && FILE_WRITING_TOOLS.has((b.tool || "").toLowerCase()),
  );
  return writes.length > 0 && writes.every((b) => b.is_error === true);
}

export function TurnFilesChips({
  assistantMsgId,
  blocks,
}: {
  assistantMsgId: string;
  blocks?: AssistantBlock[];
}) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [files, setFiles] = useState<TurnFile[] | null>(null);
  // "reverting" drives the optimistic grey-out; "reverted" is the
  // settled state. The backend also stamps metadata.reverted on the
  // node, which is what makes this survive a reload.
  const [reverting, setReverting] = useState(false);
  const [reverted, setReverted] = useState(false);
  // Which paths are expanded, and the diff cached per path. Kept apart
  // so collapsing and re-expanding doesn't re-fetch.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [diffs, setDiffs] = useState<Record<string, DiffState>>({});

  // ↗ → 中间栏文件 tab（与右栏 FileTree 点文件同一通路）。
  // checkpoint manifest 记的是绝对路径，openFileTab 要项目相对路径，
  // 所以拿会话项目根剥前缀；落在项目外的文件没有这个入口。
  const openFileTab = useCenterTabs((s) => s.openFileTab);
  const project = useCurrentProject();
  const [showAll, setShowAll] = useState(false);
  const toRelative = useCallback(
    (p: string): string | null => {
      if (!project?.path) return null;
      const root = project.path.endsWith("/") ? project.path : project.path + "/";
      return p.startsWith(root) ? p.slice(root.length) : null;
    },
    [project?.path],
  );

  useEffect(() => {
    if (!sessionId || !assistantMsgId) return;
    const w = window as Window & { ws?: WebSocket };
    const ws = w.ws;
    if (!ws) return;
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "list_turn_files_result") return;
        if (data?.data?.assistant_msg_id !== assistantMsgId) return;
        ws.removeEventListener("message", onMsg);
        const rows: TurnFile[] = data?.data?.files ?? [];
        setFiles(rows);
        if (data?.data?.reverted) setReverted(true);
      } catch {
        /* ignore */
      }
    };
    ws.addEventListener("message", onMsg);
    const ok = wsSend({
      action: "list_turn_files",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
    });
    if (!ok) ws.removeEventListener("message", onMsg);
    return () => {
      ws.removeEventListener("message", onMsg);
    };
  }, [sessionId, assistantMsgId]);

  function undo() {
    if (!sessionId || reverting || reverted) return;
    // 0ms feedback: grey the card out now, settle when the WS answers.
    setReverting(true);
    const ok = wsSend({
      action: "revert_turn",
      session_id: sessionId,
      msg_id: assistantMsgId,
    });
    const w = window as Window & { ws?: WebSocket };
    const ws = w.ws;
    if (!ok || !ws) {
      setReverting(false);
      showToast(text("Undo failed: not connected", "撤回失败：连接已断开"));
      return;
    }
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "revert_turn_result") return;
        if (data?.data?.msg_id !== assistantMsgId) return;
        ws.removeEventListener("message", onMsg);
        const d = data.data ?? {};
        const errors: string[] = d.errors ?? [];
        setReverting(false);
        if (errors.length) {
          showToast(
            text(`Undo failed: ${errors.join("; ")}`,
                 `撤回失败：${errors.join("; ")}`),
          );
          return;
        }
        setReverted(true);
        const n = (d.reverted_paths ?? []).length;
        showToast(
          text(`Reverted ${n} file${n === 1 ? "" : "s"}`, `已撤回 ${n} 个文件`),
        );
      } catch {
        /* ignore */
      }
    };
    ws.addEventListener("message", onMsg);
  }

  /** Toggle the inline diff for one file, fetching it the first time. */
  function toggle(f: TurnFile) {
    const wasOpen = open[f.path];
    setOpen((s) => ({ ...s, [f.path]: !wasOpen }));
    if (wasOpen || diffs[f.path] || !sessionId) return;

    setDiffs((s) => ({
      ...s,
      [f.path]: { loading: true, diff: "", approximate: false },
    }));
    const ok = wsSend({
      action: "turn_file_diff",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
      path: f.path,
    });
    const w = window as Window & { ws?: WebSocket };
    const ws = w.ws;
    if (!ok || !ws) {
      setDiffs((s) => ({
        ...s,
        [f.path]: {
          loading: false, diff: "", approximate: false,
          error: text("Not connected", "连接已断开"),
        },
      }));
      return;
    }
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "turn_file_diff_result") return;
        const d = data.data ?? {};
        if (d.assistant_msg_id !== assistantMsgId || d.path !== f.path) return;
        ws.removeEventListener("message", onMsg);
        setDiffs((s) => ({
          ...s,
          [f.path]: {
            loading: false,
            diff: d.diff ?? "",
            approximate: Boolean(d.approximate),
            error: d.error ?? null,
          },
        }));
      } catch {
        /* ignore */
      }
    };
    ws.addEventListener("message", onMsg);
  }

  /** First changed line of a cached diff, so the opened tab lands on the
   *  change. Not cached yet → no hint; never blocks the click on a fetch. */
  function firstChangedLine(path: string): number | undefined {
    const diff = diffs[path]?.diff;
    if (!diff) return undefined;
    const rows = parseUnifiedDiff(diff);
    const add = rows.find((r) => r.kind === "add" && r.newNo);
    if (add) return Number(add.newNo);
    const hunk = rows.find((r) => r.kind === "hunk");
    const m = hunk && /\+(\d+)/.exec(hunk.text);
    return m ? Number(m[1]) : undefined;
  }

  /** ↗ — open the file beside the chat, landing on this turn's change.
   *  Already split? Just focus it; regrouping would shuffle the panes. */
  function openBesideChat(f: TurnFile, rel: string) {
    const line = firstChangedLine(f.path);
    openFileTab(project!.id, rel, {
      diffSessionId: sessionId ?? undefined,
      diffMsgId: assistantMsgId,
      scrollToLine: line,
      highlightLines: line ? [line, line] : undefined,
    });
    const id = fileTabId(project!.id, rel);
    const s = useCenterTabs.getState();
    const group = findCenterTabGroup(s.groups, id);
    if (group) {
      s.focusGroupMember(group.id, id);
      return;
    }
    // Group with the session tab this card lives in — that's the chat
    // the user is reading, so the file lands beside it as a second pane.
    const chatId = sessionId ? sessionTabId(sessionId) : null;
    if (chatId && s.tabs.some((t) => t.id === chatId) && s.groupTab(id, chatId, 1)) {
      const g = findCenterTabGroup(useCenterTabs.getState().groups, id);
      if (g) useCenterTabs.getState().focusGroupMember(g.id, id);
      return;
    }
    s.setActive(id);
  }

  function revealInTree(rel: string) {
    const w = window as Window & { rightDock?: { show?: (v: string) => void } };
    w.rightDock?.show?.("files");
    window.dispatchEvent(
      new CustomEvent("project-file-reveal-in-tree", {
        detail: { projectId: project!.id, path: rel },
      }),
    );
  }

  // `files` is the checkpoint list: empty means this turn changed nothing
  // on disk. Pair that with "every file-writing tool call errored" and the
  // reply's claim of an edit is demonstrably wrong, so say so under the
  // bubble.
  //
  // Deliberately NOT flagged: a turn that made ZERO tool calls and merely
  // narrates an edit it never attempted. Detecting that needs prose
  // matching ("I edited X", "已修改"), which misfires on the model quoting
  // the user, describing a plan, or explaining someone else's diff — a
  // false "this didn't happen" badge on a correct answer is worse than a
  // missed one. No tool call, no signal, no notice.
  if (!files || files.length === 0) {
    if (allFileWritesFailed(blocks)) {
      return (
        <div className="turn-files-failed-note">
          {text(
            "File changes in this turn did not go through.",
            "本轮文件操作未成功执行。",
          )}
        </div>
      );
    }
    return null;
  }

  const totalAdded = files.reduce((n, f) => n + (f.added || 0), 0);
  const totalRemoved = files.reduce((n, f) => n + (f.removed || 0), 0);
  const hasHeader = files.length > 1;
  const shown = showAll ? files : files.slice(0, COLLAPSE_AFTER);

  const undoButton = (
    <button
      type="button"
      className="turn-files-action"
      onClick={(e) => {
        e.stopPropagation();
        undo();
      }}
      disabled={reverting || reverted}
      title={text(
        "Undo every file this turn changed",
        "撤回本轮修改的所有文件",
      )}
    >
      <span className="turn-files-action-icon"><UndoIcon /></span>
      {reverting
        ? text("Reverting…", "撤回中…")
        : reverted
          ? text("Reverted", "已撤回")
          : text("Undo", "撤回")}
    </button>
  );

  return (
    <div
      className="turn-files-card"
      data-reverted={reverted ? "1" : "0"}
      data-reverting={reverting ? "1" : "0"}
    >
      {/* Summary line only earns its row when there's more than one
          file — for a single edit it would just restate the row below. */}
      {hasHeader ? (
        <div className="turn-files-summary">
          <span className="turn-files-count">
            {text(`Edited ${files.length} files`, `已编辑 ${files.length} 个文件`)}
          </span>
          <span className="turn-files-stat is-add">+{totalAdded}</span>
          <span className="turn-files-stat is-del">-{totalRemoved}</span>
          {/* Undo is turn-scoped, so the header is its home whenever
              there is one; a single file keeps it on its row. */}
          <span className="turn-files-summary-actions">{undoButton}</span>
        </div>
      ) : null}

      {shown.map((f) => {
        const rel = toRelative(f.path);
        const label = f.rel || basename(f.path);
        const isOpen = Boolean(open[f.path]);
        const d = diffs[f.path];
        return (
          <div className="turn-files-file" key={f.path}>
            <div
              className="turn-files-row is-toggle"
              role="button"
              tabIndex={0}
              aria-expanded={isOpen}
              title={f.path}
              onClick={() => toggle(f)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(f);
                }
              }}
            >
              <span className="turn-files-caret">
                {isOpen ? <ChevronDownIcon /> : <ChevronRightIcon />}
              </span>
              <span className="turn-files-name">{label}</span>
              <span className="turn-files-stat is-add">+{f.added ?? 0}</span>
              <span className="turn-files-stat is-del">-{f.removed ?? 0}</span>
              <span className="turn-files-actions">
                {rel !== null ? (
                  <>
                    <button
                      type="button"
                      className="turn-files-action"
                      onClick={(e) => {
                        e.stopPropagation();
                        revealInTree(rel);
                      }}
                      title={text("Reveal in file tree", "在文件树中定位")}
                    >
                      <span className="turn-files-action-icon">
                        <FolderOpenIcon />
                      </span>
                    </button>
                    <button
                      type="button"
                      className="turn-files-action"
                      onClick={(e) => {
                        e.stopPropagation();
                        openBesideChat(f, rel);
                      }}
                      title={text("Jump to the change", "跳转到本次改动")}
                    >
                      <span className="turn-files-action-icon">
                        <ArrowUpRightIcon />
                      </span>
                    </button>
                  </>
                ) : null}
                {hasHeader ? null : undoButton}
              </span>
            </div>

            {isOpen ? (
              <div className="turn-files-diff">
                {d?.approximate ? (
                  <div className="file-diff-note">
                    {text(
                      "Approximate diff — the file changed after this turn, so unrelated edits may show.",
                      "近似差异——该文件在本轮之后又被改动过，可能显示无关的编辑。",
                    )}
                  </div>
                ) : null}
                {d?.loading || !d ? (
                  <div className="file-diff-empty">
                    {text("Loading diff…", "正在加载差异…")}
                  </div>
                ) : d.error ? (
                  <div className="file-diff-empty is-error">{d.error}</div>
                ) : d.diff ? (
                  <UnifiedDiff diff={d.diff} />
                ) : (
                  <div className="file-diff-empty">
                    {text("No textual changes.", "没有文本改动。")}
                  </div>
                )}
              </div>
            ) : null}
          </div>
        );
      })}

      {files.length > COLLAPSE_AFTER ? (
        <button
          type="button"
          className="turn-files-more"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll
            ? text("Collapse", "收起")
            : text(`Show all ${files.length}`, `显示全部 ${files.length} 个`)}
        </button>
      ) : null}
    </div>
  );
}
