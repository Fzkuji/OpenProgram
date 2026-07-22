"use client";

/**
 * WorkingDirChips — claude.ai-style chips for the session's additional
 * working directories, rendered in the composer's envChips row right of
 * <ProjectBadge />. One chip per directory (folder icon + basename + ✕
 * remove) plus a trailing icon-only "add" chip that pops the OS-native
 * directory chooser via POST /api/pick-folder.
 *
 * Persistence follows the working-dir contract: optimistic store write
 * first (instant UI); real sessions then send `set_working_dirs` whose
 * `working_dirs` broadcast is authoritative. Drafts only write the
 * store — the first chat frame carries the list (legacy-send.ts).
 */
import { Folder, FolderPlus, X } from "lucide-react";

import { HoverTip } from "@/components/ui/tooltip";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";

/** Stable empty list so the zustand selector doesn't churn renders. */
const NO_WORKING_DIRS: string[] = [];

/** Last path segment for the chip label; full path lives in `title`. */
function baseName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

export function WorkingDirChips() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const activeChatKey = useSessionStore((s) => s.activeChatKey);
  const workingDirsKey = sessionId ?? activeChatKey;
  const workingDirs = useSessionStore((s) =>
    workingDirsKey
      ? s.additionalWorkingDirsBySession[workingDirsKey] ?? NO_WORKING_DIRS
      : NO_WORKING_DIRS,
  );
  const setAdditionalWorkingDirs = useSessionStore(
    (s) => s.setAdditionalWorkingDirs,
  );

  // Whole-list replace: optimistic store write, then backend persist for
  // real (non-draft) sessions.
  function applyWorkingDirs(dirs: string[]) {
    if (!workingDirsKey) return;
    setAdditionalWorkingDirs(workingDirsKey, dirs);
    const isDraft = !sessionId || sessionId.startsWith("local_");
    if (!isDraft) {
      void wsRequest(
        "set_working_dirs",
        { session_id: sessionId, dirs },
        "working_dirs",
        (d) => (d as { session_id?: string }).session_id === sessionId,
      );
    }
  }

  async function addWorkingDir() {
    try {
      const res = await fetch("/api/pick-folder", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      const data = res.ok
        ? ((await res.json()) as { path?: string | null; unsupported?: boolean })
        : null;
      if (data?.path) {
        if (!workingDirs.includes(data.path)) {
          applyWorkingDirs([...workingDirs, data.path]);
        }
      } else if (!data || data.unsupported) {
        // ponytail: chips row has no room for error copy — console only.
        console.warn("pick-folder unavailable — restart the worker");
      }
      // data.path == null (and supported) → user cancelled; no-op.
    } catch (e) {
      console.warn("pick-folder failed", e);
    }
  }

  return (
    <>
      {workingDirs.map((dir) => (
        <span key={dir} className="runtime-badge workdir-badge" title={dir}>
          <Folder size={14} strokeWidth={2} className="workdir-icon" />
          <span className="badge-short">{baseName(dir)}</span>
          <X
            size={13}
            role="button"
            aria-label={text("Remove folder", "移除文件夹")}
            className="workdir-remove"
            onClick={() => applyWorkingDirs(workingDirs.filter((d) => d !== dir))}
          />
        </span>
      ))}
      <HoverTip label={text("Add working folder", "添加工作目录")}>
        <button
          type="button"
          className="runtime-badge workdir-badge workdir-add-badge"
          aria-label={text("Add working folder", "添加工作目录")}
          onClick={() => void addWorkingDir()}
        >
          <FolderPlus size={14} strokeWidth={2} className="workdir-icon" />
        </button>
      </HoverTip>
    </>
  );
}
