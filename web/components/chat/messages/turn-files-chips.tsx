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
 *   ↗ (row action)  → open the file as a center editor tab.
 *   Undo            → `revert_turn`, restoring every file this turn
 *                     touched. Turn-scoped, not per-file, so the row
 *                     hosting the button is just the affordance's spot.
 *
 * Both buttons stopPropagation so they never toggle the diff.
 * The filename itself is deliberately NOT the editor link any more:
 * with the whole row toggling the diff, a nested link inside it would
 * be two overlapping targets on one label. The ↗ icon separates them.
 */
import { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useCurrentProject } from "@/lib/state/files-shared";
import {
  ArrowUpRightIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  UndoIcon,
} from "@/components/animated-icons";
import { UnifiedDiff } from "./unified-diff";

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

export function TurnFilesChips({ assistantMsgId }: { assistantMsgId: string }) {
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

  if (!files || files.length === 0) return null;

  const totalAdded = files.reduce((n, f) => n + (f.added || 0), 0);
  const totalRemoved = files.reduce((n, f) => n + (f.removed || 0), 0);

  return (
    <div
      className="turn-files-card"
      data-reverted={reverted ? "1" : "0"}
      data-reverting={reverting ? "1" : "0"}
    >
      {/* Summary line only earns its row when there's more than one
          file — for a single edit it would just restate the row below. */}
      {files.length > 1 ? (
        <div className="turn-files-summary">
          <span className="turn-files-count">
            {text(
              `${files.length} files changed`,
              `${files.length} 个文件已修改`,
            )}
          </span>
          <span className="turn-files-stat is-add">+{totalAdded}</span>
          <span className="turn-files-stat is-del">-{totalRemoved}</span>
        </div>
      ) : null}

      {files.map((f) => {
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
                  <button
                    type="button"
                    className="turn-files-action"
                    onClick={(e) => {
                      e.stopPropagation();
                      openFileTab(project!.id, rel);
                    }}
                    title={text("Open in editor", "在编辑器打开")}
                  >
                    <span className="turn-files-action-icon">
                      <ArrowUpRightIcon />
                    </span>
                  </button>
                ) : null}
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
              </span>
            </div>

            {isOpen ? (
              <div className="turn-files-diff">
                {d?.approximate ? (
                  <div className="file-diff-note">
                    {text(
                      "Approximate — compared against the file's current contents, so later edits may appear.",
                      "近似差异——与文件当前内容比较，可能包含后续轮次的改动。",
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
    </div>
  );
}
