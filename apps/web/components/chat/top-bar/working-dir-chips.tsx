"use client";

/**
 * WorkingDirChips — claude.ai-style chips for the session's additional
 * working directories, rendered in the composer's envChips row right of
 * <ProjectBadge />. One chip per directory (folder icon + basename + ✕
 * remove) plus a trailing icon-only "add" chip that opens a picker menu:
 * recent projects (one click adds that project's path as a working dir)
 * plus a "Choose folder…" row that pops the OS-native directory chooser
 * via POST /api/pick-folder.
 *
 * Persistence follows the working-dir contract: optimistic store write
 * first (instant UI); real sessions then send `set_working_dirs` whose
 * `working_dirs` broadcast is authoritative. Drafts only write the
 * store — the first chat frame carries the list (send-chat-message.ts).
 */
import { useCallback, useEffect, useState } from "react";
import { Folder, FolderPlus, X } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip } from "@/components/ui/tooltip";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useBoundChat } from "./bound-chat";
import { GROUP_LABEL, MENU_PANEL, MENU_SEPARATOR, itemCls } from "./menu-styles";
import { activateOnKey } from "@/lib/utils";

/** Stable empty list so the zustand selector doesn't churn renders. */
const NO_WORKING_DIRS: string[] = [];

/** Last path segment for the chip label; full path lives in `title`. */
function baseName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

interface Project {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  session_count: number;
}

export function WorkingDirChips() {
  const { text } = useTranslation();
  const { sessionId, chatKey: activeChatKey } = useBoundChat();
  const pendingProjectId = useSessionStore((s) =>
    activeChatKey ? s.pendingProjectsByChat[activeChatKey] ?? null : null,
  );
  const workingDirsKey = sessionId ?? activeChatKey;
  const workingDirs = useSessionStore((s) =>
    workingDirsKey
      ? s.additionalWorkingDirsBySession[workingDirsKey] ?? NO_WORKING_DIRS
      : NO_WORKING_DIRS,
  );
  const setAdditionalWorkingDirs = useSessionStore(
    (s) => s.setAdditionalWorkingDirs,
  );
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const { pickFolder, folderPickerDialog } = useFolderPicker();

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

  const refresh = useCallback(async () => {
    const data = await wsRequest<{
      projects: Project[];
      current_project_id: string | null;
      session_id: string | null;
    }>(
      "list_projects",
      { session_id: sessionId ?? "" },
      "projects_list",
      // 只认后端回显 session_id 匹配的回复——其他组件（侧栏 Projects
      // 分组等）并发发的 list_projects 不带会话，其 current_project_id
      // 恒为 null，误收会把选中态画到默认项目上。
      (d) => (d.session_id ?? null) === (sessionId || null),
    );
    if (data) {
      setProjects(data.projects || []);
      setCurrentProjectId(data.current_project_id ?? null);
    } else {
      setProjects([]);
    }
  }, [sessionId]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  // Mirror ProjectBadge: only one topbar dropdown open at a time.
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  async function chooseFolder() {
    setOpen(false);
    setPickerError(null);
    try {
      const path = await pickFolder();
      if (path) {
        if (!workingDirs.includes(path)) {
          applyWorkingDirs([...workingDirs, path]);
        }
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setPickerError(text(`Couldn't choose a folder: ${detail}`, `无法选择文件夹：${detail}`));
      setOpen(true);
    }
  }

  // 未绑定项目的会话 current_project_id 为 null，但它实际生效的是默认
  // 项目（chip 显示的就是它）——回落到 is_default，别让"当前项目"混进
  // 最近列表。
  const effectiveProjectId = !sessionId
    ? pendingProjectId ?? currentProjectId
    : currentProjectId;
  const currentProjectPath =
    projects.find((p) => p.id === effectiveProjectId)?.path ??
    projects.find((p) => p.is_default)?.path ??
    null;
  const recent = projects.filter(
    (p) =>
      p.path &&
      !workingDirs.includes(p.path) &&
      p.path !== currentProjectPath,
  );

  return (
    <>
      {workingDirs.map((dir) => (
        <span key={dir} className="runtime-badge workdir-badge" title={dir}>
          <Folder size={14} strokeWidth={2} className="workdir-icon" />
          <span className="badge-short">{baseName(dir)}</span>
          <X
            size={13}
            role="button"
            tabIndex={0}
            aria-label={text("Remove folder", "移除文件夹")}
            className="workdir-remove"
            onClick={() => applyWorkingDirs(workingDirs.filter((d) => d !== dir))}
            onKeyDown={activateOnKey(() =>
              applyWorkingDirs(workingDirs.filter((d) => d !== dir)),
            )}
          />
        </span>
      ))}
      <Popover open={open} onOpenChange={onOpenChange}>
        <HoverTip label={text("Add working folder", "添加工作目录")}>
          <PopoverTrigger asChild>
            <button
              type="button"
              className="runtime-badge workdir-badge workdir-add-badge"
              aria-label={text("Add working folder", "添加工作目录")}
            >
              <FolderPlus size={14} strokeWidth={2} className="workdir-icon" />
            </button>
          </PopoverTrigger>
        </HoverTip>
        <PopoverContent
          side="top"
          align="start"
          sideOffset={10}
          className="w-auto border-0 bg-transparent p-0 shadow-none"
        >
          <div className={`${MENU_PANEL} min-w-[230px] max-w-[340px]`}>
            {recent.length > 0 ? (
              <>
                <div className={GROUP_LABEL}>{text("Recent", "最近")}</div>
                {recent.map((p) => (
                  <div
                    key={p.id}
                    className={itemCls(false)}
                    title={p.path}
                    onClick={() => {
                      applyWorkingDirs([...workingDirs, p.path]);
                      setOpen(false);
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  </div>
                ))}
                <div className={MENU_SEPARATOR} />
              </>
            ) : null}
            <div className={itemCls(false)} onClick={() => void chooseFolder()}>
              <Folder size={14} strokeWidth={2} className="shrink-0 opacity-70" />
              <span className="flex-1">
                {text("Choose folder…", "选择文件夹…")}
              </span>
            </div>
            {pickerError ? (
              <div className="px-[8px] pb-[3px] pt-[4px] text-[11px] text-[var(--accent-orange)]" role="alert">
                {pickerError}
              </div>
            ) : null}
          </div>
        </PopoverContent>
      </Popover>
      {folderPickerDialog}
    </>
  );
}
