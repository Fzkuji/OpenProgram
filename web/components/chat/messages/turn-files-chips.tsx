"use client";

/**
 * Per-turn file edit card — the list of files an assistant turn changed,
 * with per-file +N/-N counts and Undo / Review actions.
 *
 * Fires `list_turn_files` over the shared WS once the bubble is no
 * longer streaming; renders nothing when the turn touched no files
 * (the common case for chat-only replies, so empty state stays quiet).
 *
 * Actions:
 *   Undo   → `revert_turn`, restoring every file this turn touched to
 *            its pre-turn state. Turn-scoped, not per-file, so the row
 *            hosting the button is just the affordance's location.
 *   Review → `turn_file_diff`, rendered in the right rail's Details.
 *
 * Clicking the file NAME still opens it as a center file tab, which is
 * what the chips did before and what people already reach for.
 */
import { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useCurrentProject } from "@/lib/state/files-shared";
import { EyeIcon, UndoIcon } from "@/components/animated-icons";

/** One changed file, as returned by `list_turn_files`. */
interface TurnFile {
  path: string;
  rel: string;
  op: string;
  added: number;
  removed: number;
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
  const showFileDiff = useSessionStore((s) => s.showFileDiff);
  const [files, setFiles] = useState<TurnFile[] | null>(null);
  // "reverting" drives the optimistic grey-out; "reverted" is the
  // settled state. The backend also stamps metadata.reverted on the
  // node, which is what makes this survive a reload.
  const [reverting, setReverting] = useState(false);
  const [reverted, setReverted] = useState(false);

  // 点文件名 → 中间栏文件 tab（与右栏 FileTree 点文件同一通路）。
  // checkpoint manifest 记的是绝对路径，openFileTab 要项目相对路径，
  // 所以拿会话项目根剥前缀；落在项目外的文件保持纯展示。
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

  function review(f: TurnFile) {
    if (!sessionId) return;
    // Open the panel immediately in a loading state so the click lands
    // before the round-trip, then fill it in when the diff arrives.
    showFileDiff({
      path: f.path, rel: f.rel || basename(f.path),
      assistantMsgId, diff: "", approximate: false, loading: true,
    });
    const ok = wsSend({
      action: "turn_file_diff",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
      path: f.path,
    });
    const w = window as Window & { ws?: WebSocket };
    const ws = w.ws;
    if (!ok || !ws) {
      showFileDiff({
        path: f.path, rel: f.rel || basename(f.path),
        assistantMsgId, diff: "", approximate: false,
        error: text("Not connected", "连接已断开"),
      });
      return;
    }
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "turn_file_diff_result") return;
        const d = data.data ?? {};
        if (d.assistant_msg_id !== assistantMsgId || d.path !== f.path) return;
        ws.removeEventListener("message", onMsg);
        showFileDiff({
          path: f.path,
          rel: f.rel || basename(f.path),
          assistantMsgId,
          diff: d.diff ?? "",
          approximate: Boolean(d.approximate),
          error: d.error ?? null,
        });
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
        return (
          <div className="turn-files-row" key={f.path}>
            {rel !== null ? (
              <button
                type="button"
                className="turn-files-name is-clickable"
                title={f.path}
                onClick={() => openFileTab(project!.id, rel)}
              >
                {label}
              </button>
            ) : (
              <span className="turn-files-name" title={f.path}>{label}</span>
            )}
            <span className="turn-files-stat is-add">+{f.added ?? 0}</span>
            <span className="turn-files-stat is-del">-{f.removed ?? 0}</span>
            <span className="turn-files-actions">
              <button
                type="button"
                className="turn-files-action"
                onClick={() => review(f)}
                title={text("Review changes", "查看改动")}
              >
                <span className="turn-files-action-icon"><EyeIcon /></span>
                {text("Review", "查看")}
              </button>
              <button
                type="button"
                className="turn-files-action"
                onClick={undo}
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
        );
      })}
    </div>
  );
}
