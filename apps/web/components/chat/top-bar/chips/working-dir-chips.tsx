"use client";

/**
 * WorkingDirChips — claude.ai-style chips for the session's additional
 * working directories, rendered in the composer's envChips row right of
 * <ProjectBadge />. One folder pill per directory (folder icon + basename,
 * ✕ remove on hover, then the git segment when it is in a checkout) plus a trailing icon-only "add" chip that opens a picker menu:
 * recent projects (one click adds that project's path as a working dir)
 * plus a "Choose folder…" row that pops the OS-native directory chooser
 * via POST /api/pick-folder.
 *
 * Persistence follows the working-dir contract: optimistic store write
 * first (instant UI); real sessions then send `set_working_dirs` whose
 * `working_dirs` broadcast is authoritative. Drafts only write the
 * store — the first chat frame carries the list (send-chat-message.ts).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { SolarIcon } from "@/components/solar-icons";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useBoundChat } from "../bound-chat";
import { GROUP_LABEL, MENU_NOTE, MENU_NOTE_ERROR, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { activateOnKey, cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { GitChip } from "./git-chip";
import { FOLDER_PILL } from "./folder-pill";

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
  source_folders?: string[];
}

export function WorkingDirChips() {
  const { text } = useTranslation();
  const { sessionId, chatKey: activeChatKey } = useBoundChat();
  const pendingProjectId = useSessionStore((s) =>
    activeChatKey ? s.pendingProjectsByChat[activeChatKey] ?? null : null,
  );
  const workingDirsKey = sessionId ?? activeChatKey;
  const explicitWorkingDirs = useSessionStore((s) =>
    workingDirsKey
      ? s.additionalWorkingDirsBySession[workingDirsKey]
      : undefined,
  );
  const setAdditionalWorkingDirs = useSessionStore(
    (s) => s.setAdditionalWorkingDirs,
  );
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const effectiveProjectId = !sessionId ? pendingProjectId ?? currentProjectId : currentProjectId;
  const selectedProject = projects.find(project=>project.id===effectiveProjectId) ?? projects.find(project=>project.is_default);
  const workingDirs = explicitWorkingDirs ?? selectedProject?.source_folders ?? NO_WORKING_DIRS;
  const refreshGeneration = useRef(0);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const { pickFolder, folderPickerDialog } = useFolderPicker();

  // Whole-list replace: optimistic store write, then backend persist for
  // real (non-draft) sessions.
  function applyWorkingDirs(dirs: string[]) {
    if (!workingDirsKey) return;
    setAdditionalWorkingDirs(workingDirsKey, dirs);
    const isDraft = !sessionId;
    if (!isDraft) {
      void wsRequest(
        "set_working_dirs",
        { session_id: sessionId, dirs },
        "working_dirs",
        (d) => (d as { session_id?: string }).session_id === sessionId,
      );
    }
  }

  // The project chip's git menu adds a worktree here when the main
  // folder is frozen (see ProjectBadge).
  const applyRef = useRef(applyWorkingDirs);
  applyRef.current = applyWorkingDirs;
  const dirsRef = useRef(workingDirs);
  dirsRef.current = workingDirs;
  useEffect(() => {
    const onAdd = (event: Event) => {
      const path = (event as CustomEvent<{ path?: string }>).detail?.path;
      if (path && !dirsRef.current.includes(path)) applyRef.current([...dirsRef.current, path]);
    };
    window.addEventListener("op:workdir-add", onAdd);
    return () => window.removeEventListener("op:workdir-add", onAdd);
  }, []);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
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
    if (generation !== refreshGeneration.current) return true;
    if (data?.projects) {
      setProjects(data.projects);
      setCurrentProjectId(data.current_project_id ?? null);
      return true;
    }
    return false;
  }, [sessionId]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const refreshes = refreshGeneration;
    const attempt = async () => {
      if (!await refresh() && !cancelled && tries++ < 20) timer = setTimeout(attempt, 300);
    };
    const changed = () => { tries = 0; void attempt(); };
    void attempt();
    window.addEventListener("project-changed", changed);
    return () => { cancelled = true; ++refreshes.current; clearTimeout(timer); window.removeEventListener("project-changed", changed); };
  }, [refresh]);

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
      {workingDirs.map((dir, index) => (
        <span key={dir} className={`${FOLDER_PILL} runtime-badge workdir-badge`} data-path={dir}>
          <span className="folder-pill-seg">
            <HoverTip
              label={
                <TipBody
                  title={text(`Working folder: ${baseName(dir)}`, `工作目录：${baseName(dir)}`)}
                  detail={<>{dir}<br />{text("The agent can read and edit files here", "agent 可以读写这里的文件")}</>}
                />
              }
            >
              <span className="folder-pill-name">
                <SolarIcon name="folder-with-files" size={16} fit="fill" className="workdir-icon" />
                <span className="badge-short">{baseName(dir)}</span>
              </span>
            </HoverTip>
            {/* ✕ appears on hover / keyboard focus only; it takes no width otherwise. */}
            <HoverTip
              label={
                <TipBody
                  title={text("Remove from this conversation", "从本会话移除")}
                  detail={text("Files on disk stay untouched", "磁盘上的文件不受影响")}
                />
              }
            >
              <span
                role="button"
                tabIndex={0}
                aria-label={text("Remove folder", "移除文件夹")}
                className="workdir-remove"
                onClick={() => applyWorkingDirs(workingDirs.filter((d) => d !== dir))}
                onKeyDown={activateOnKey(() =>
                  applyWorkingDirs(workingDirs.filter((d) => d !== dir)),
                )}
              >
                <SolarIcon name="close-circle" size={14} />
              </span>
            </HoverTip>
          </span>
          <GitChip
            path={dir}
            order={index + 1}
            onUseFolder={(next) => applyWorkingDirs(workingDirs.map((d) => (d === dir ? next : d)))}
          />
        </span>
      ))}
      <Popover open={open} onOpenChange={onOpenChange}>
        <HoverTip
          label={
            <TipBody
              title={text("Add working folder", "添加工作目录")}
              detail={text("Give the agent another folder to work in", "让 agent 在另一个目录里工作")}
            />
          }
        >
          <PopoverTrigger asChild>
            <button
              type="button"
              className={`${cn(buttonVariants({ variant: "elevated", size: "icon-sm" }))} runtime-badge workdir-badge workdir-add-badge`}
              aria-label={text("Add working folder", "添加工作目录")}
            >
              <SolarIcon name="add-folder" size={16} fit="fill" className="workdir-icon" />
            </button>
          </PopoverTrigger>
        </HoverTip>
        <PopoverContent
          side="top"
          align="start"
          sideOffset={10}
          className="w-auto border-0 bg-transparent p-0 shadow-none"
        >
          <div className={MENU_PANEL}>
            {recent.length > 0 ? (
              <>
                <div className={GROUP_LABEL}>{text("Recent", "最近")}</div>
                {recent.map((p) => (
                  <div
                    key={p.id}
                    className={itemCls(false)}
                    data-tip={p.path}
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
              <SolarIcon name="folder-with-files" size={16} fit="fill" className="opacity-70" />
              <span className="flex-1">
                {text("Choose folder…", "选择文件夹…")}
              </span>
            </div>
            {pickerError ? (
              <div className={cn(MENU_NOTE, MENU_NOTE_ERROR, "pt-1")} role="alert">
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
