"use client";

/**
 * History, organised by project. Left rail: the projects, the System
 * project first (chats that belong to no project, and the personal memory
 * every project reads). Right: the selected project's Chats, Memory,
 * Settings (defaults + permission rules) and Info. Same-page switching,
 * no route change.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FolderSearch } from "lucide-react";

import shell from "./projects-shell.module.css";
import { SearchInput } from "@/components/ui/search-input";
import { useFolderPicker } from "@/components/ui/folder-picker";
import styles from "./projects-page.module.css";
import { ManageEmptyState, managePageStyles as shared, ManageLoadingState } from "@/components/ui/manage-page";
import { RailBadge, RailItem, RailSeparator, RailSplit, SecondaryRail } from "@/components/ui/secondary-rail";
import { useTranslation } from "@/lib/i18n";
import { SolarIcon } from "@/components/solar-icons";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { canConfirmInPlace, confirmProjectFolder, locationMessage, locationProblem, notifyProjectLocationChanged, relocateProject, type RelocateReply } from "@/lib/projects/project-location";
import { ProjectEditor, type EditableProject } from "@/components/sidebar/projects/project-editor";
import { Button } from "@/components/ui/button";
import { ChatsPage } from "@/components/chats/chats-page";
import { MemoryPage } from "@/components/memory";
import { useSessionStore, type ConvSummary } from "@/lib/session-store";
import { PermissionsSection } from "./permissions-section";
import { ComputerUseAppsSection } from "./computer-use-apps-section";
import { ProjectConfigSection } from "./project-config-section";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-ui";

interface Project extends EditableProject {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  /** Backend-computed: the folder no longer exists on disk. */
  path_missing?: boolean;
  path_replaced?: boolean;
  location_state?: string;
  location_revision?: number;
  migration_error?: string;
  session_count: number;
  unarchived_session_count?: number;
  session_ids?: string[];
  custom_name?: boolean;
  status: string;
}

/** The History page's project rail; its header shows the toggle. */
export const PROJECTS_RAIL = "projects";

export type ProjectTab = "chats" | "memory" | "settings" | "info";
const PROJECT_TABS: ProjectTab[] = ["chats", "memory", "settings", "info"];
/** Settings and Info scroll inside the panel, under fixed head and tabs, on
    the Settings page's blocks and the Chats tab's 24px edge. */
const TAB_SCROLL = "flex min-h-0 flex-1 flex-col gap-8 overflow-y-auto px-6 pt-5 pb-8 max-[760px]:px-4";

function tabParam(value: string | null | undefined): ProjectTab | null {
  return PROJECT_TABS.find((tab) => tab === value) ?? null;
}

function cls(...xs: (string | false | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

/** The home project leads; the rest keep the registry's order. */
function systemFirst(projects: Project[]): Project[] {
  return [...projects].sort((a, b) => Number(b.is_default) - Number(a.is_default));
}

export function ProjectsPage({
  embedded,
  query: queryProp,
  initialTab = "chats",
}: {
  embedded?: boolean;
  query?: string;
  initialTab?: ProjectTab;
} = {}) {
  const { text } = useTranslation();
  const params = useSearchParams();
  const requestedProject = params?.get("project");
  const requestedTab = tabParam(params?.get("tab"));
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<ProjectTab>(requestedTab ?? initialTab);
  const conversations = useSessionStore((state) => state.conversations);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [localQuery, setLocalQuery] = useState("");
  const { pickFolder, folderPickerDialog } = useFolderPicker();
  const query = queryProp !== undefined ? queryProp : localQuery;
  const setQuery = setLocalQuery;

  const refresh = useCallback(async () => {
    for (let i = 0; i < 10; i++) {
      const data = await wsRequest<{ projects: Project[] | null }>(
        "list_projects", {}, "projects_list",
      );
      // `projects: null` means the registry could not be read, not that
      // there are no projects; retry instead of showing the empty state.
      if (data && Array.isArray(data.projects)) {
        const list = systemFirst(data.projects);
        setProjects(list);
        setSelectedId((cur) => cur ?? (list[0]?.id ?? null));
        setLoaded(true);
        return;
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    setError(text("Could not load projects", "无法加载项目"));
  }, [text]);

  useEffect(() => {
    function onChanged() { refresh(); }
    window.addEventListener("project-changed", onChanged);
    refresh();
    return () => window.removeEventListener("project-changed", onChanged);
  }, [refresh]);

  useEffect(() => { if (requestedProject) setSelectedId(requestedProject); }, [requestedProject]);
  useEffect(() => { setTab(initialTab); }, [initialTab]);

  const selected = useMemo(
    () => projects.find((p) => p.id === selectedId) || null,
    [projects, selectedId],
  );
  const selectedProblem = locationProblem(selected);

  // A chat belongs to the first bound project that lists it; a chat no
  // project claims is the home project's, as on the server.
  const owner = useMemo(() => {
    const map = new Map<string, string>();
    for (const project of projects) {
      if (project.is_default) continue;
      for (const sessionId of project.session_ids || []) {
        if (!map.has(sessionId)) map.set(sessionId, project.id);
      }
    }
    return map;
  }, [projects]);
  const systemId = useMemo(() => projects.find((p) => p.is_default)?.id ?? null, [projects]);
  const projectOf = useCallback(
    (conv: ConvSummary) => owner.get(conv.id) ?? systemId, [owner, systemId],
  );
  const includeSelected = useCallback(
    (conv: ConvSummary) => projectOf(conv) === selectedId, [projectOf, selectedId],
  );
  const chatCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const conv of Object.values(conversations) as ConvSummary[]) {
      if (conv.archived) continue;
      const projectId = projectOf(conv);
      if (projectId) counts.set(projectId, (counts.get(projectId) ?? 0) + 1);
    }
    return counts;
  }, [conversations, projectOf]);

  const label = useCallback((project: Project) => project.name, []);

  // 定位缺失项目的新目录：改项目 path、保留 id（relocate_project）。
  const repairProject = useCallback(async (run: () => Promise<RelocateReply>) => {
    setError(null);
    try {
      const reply = await run();
      if (!reply.ok) setError(reply.error || text("Could not relocate the project.", "重新定位项目失败。"));
      else notifyProjectLocationChanged();
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }, [refresh, text]);
  const locateProject = useCallback(async (project: Project) => {
    try {
      const path = await pickFolder(project.path);
      if (path) await repairProject(() => relocateProject(project, path));
    } catch (e) {
      setError(String(e));
    }
  }, [pickFolder, repairProject]);

  const addProject = useCallback(async () => {
    setError(null);
    try {
      const path = await pickFolder();
      if (!path) return;
      const d = await wsRequest<{ ok: boolean; project?: Project; error?: string }>(
        "create_project", { path }, "project_created",
      );
      if (d?.error) setError(d.error);
      if (d?.project) setSelectedId(d.project.id);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }, [pickFolder, refresh]);

  // Arrow/Home/End keyboard model shared with ManageSubnav.
  function moveProjectTab(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % PROJECT_TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + PROJECT_TABS.length) % PROJECT_TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = PROJECT_TABS.length - 1;
    else return;
    event.preventDefault();
    setTab(PROJECT_TABS[next]);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button[role=tab]")[next]?.focus();
  }

  const view = (
      <div className="h-full flex flex-col overflow-visible" style={embedded ? { flex: 1, minHeight: 0, height: "auto" } : undefined}>
        {folderPickerDialog}
        {editing && selected && <ProjectEditor key={selected.id} project={selected} onClose={()=>setEditing(false)} onSaved={updated=>setProjects(items=>items.map(item=>item.id===updated.id?{...item,...updated}:item))}/>}
        {(!embedded || queryProp === undefined) && (
          <div className={shell.topbar}>
            {!embedded && <span className={shell.title}>{text("Projects", "项目")}</span>}
            {queryProp === undefined && (
              <div className="flex items-center gap-[10px] ml-[auto] pb-[2px]">
                <SearchInput
                  className="flex-1 max-w-[320px]"
                  placeholder={text("Search this project...", "搜索这个项目...")}
                  value={query}
                  onChange={setQuery}
                />
              </div>
            )}
          </div>
        )}

        {error && <div className={shared.errorBar} role="alert">{error}</div>}

        <RailSplit railKey={PROJECTS_RAIL}>
          <SecondaryRail railKey={PROJECTS_RAIL} id="projects-rail" label={text("Projects", "项目")}>
            {projects.map((p) => {
              const unavailable = locationProblem(p) !== null;
              return (
                <RailItem
                  key={p.id}
                  icon={p.icon || <ProjectGlyph size={16} open={p.id === selectedId} />}
                  label={label(p)}
                  badge={p.hidden ? <RailBadge>{text("Hidden", "已隐藏")}</RailBadge> : undefined}
                  trailing={unavailable ? (
                    <SolarIcon
                      name="danger-triangle"
                      size={14}
                      motionPreset="none"
                      className={styles.statusSlot}
                      role="img"
                      aria-label={text("Folder missing", "目录缺失")}
                    />
                  ) : undefined}
                  active={p.id === selectedId}
                  onClick={() => setSelectedId(p.id)}
                />
              );
            })}
            <RailSeparator />
            <RailItem
              quiet
              icon={<SolarIcon name="add-folder" size={16} motionPreset="none" aria-hidden="true" />}
              label={text("Add project folder…", "添加项目文件夹…")}
              onClick={addProject}
            />
          </SecondaryRail>

          {/* 右栏：选中项目的内容 */}
          <div className={cls(shell.content, styles.content, selected ? styles.contentFill : undefined)}>
            {!selected && !loaded ? (
              <ManageLoadingState>{text("Loading…", "加载中…")}</ManageLoadingState>
            ) : !selected ? (
              <ManageEmptyState
                compact
                icon={<ProjectGlyph size={20} />}
                title={projects.length ? text("Select a project", "选择一个项目") : text("No projects yet", "还没有项目")}
                description={text("A project is a folder on disk. Its chats, default settings and permission rules travel with it.", "项目对应磁盘上的一个文件夹，其会话、默认设置和权限规则随项目保存。")}
                action={projects.length ? undefined : <Button onClick={addProject}><SolarIcon name="add-folder" size={16} motionPreset="none" aria-hidden="true" />{text("Add project folder…", "添加项目文件夹…")}</Button>}
              />
            ) : (
              <>
                <div className="flex items-center gap-[12px] m-[2px_0_8px]">
                  <span className={styles.detailIcon} aria-hidden="true">{selected.icon || <ProjectGlyph size={20} />}</span>
                  <div className="flex-1 min-w-0">
                    <h2 className={styles.detailTitle}>{label(selected)}</h2>
                    {selected.is_default ? (
                      <span className={styles.detailNote}>
                        {text("Chats outside any project, and the personal memory every project reads.", "不属于任何项目的会话，以及每个项目都会读的个人记忆。")}
                      </span>
                    ) : (
                      <span className={styles.detailPath}>{selected.path}</span>
                    )}
                  </div>
                  {selected.hidden&&<Button variant="elevated" onClick={async()=>{try{const result=await wsRequest<{ok:boolean;error?:string}>("restore_project",{project_id:selected.id},"restore_project_result");if(!result?.ok)throw new Error(result?.error||text("Could not restore project", "无法恢复项目"));await refresh();window.dispatchEvent(new Event("project-changed"));}catch(err){setError(err instanceof Error?err.message:String(err));}}}>{text("Restore to sidebar", "恢复到侧边栏")}</Button>}
                  {!selected.is_default && <Button variant="elevated" onClick={()=>setEditing(true)}>{text("Edit project", "编辑项目")}</Button>}
                </div>
                {selectedProblem && (
                  <div role="alert" className="my-2 flex flex-wrap items-center gap-2 text-fs-xs text-accent-orange">
                    <SolarIcon name="danger-triangle" size={14} motionPreset="none" aria-hidden="true" />
                    <span>{locationMessage(selectedProblem, selected, text)}</span>
                    {canConfirmInPlace(selectedProblem) && (
                      <Button variant="elevated" onClick={() => repairProject(() => confirmProjectFolder(selected))}>
                        {text("Use the folder at this location", "就是这个位置的文件夹")}
                      </Button>
                    )}
                    <Button variant="elevated" onClick={() => locateProject(selected)}>
                      <FolderSearch aria-hidden="true" />
                      {text("Locate folder…", "定位文件夹…")}
                    </Button>
                  </div>
                )}

                <div className={styles.tabs} role="tablist" aria-label={text("Project sections", "项目分区")}>
                  {PROJECT_TABS.map((tk, index) => (
                    <button
                      key={tk}
                      type="button"
                      role="tab"
                      id={`project-tab-${tk}`}
                      aria-selected={tab === tk}
                      aria-controls="project-tabpanel"
                      tabIndex={tab === tk ? 0 : -1}
                      onClick={() => setTab(tk)}
                      onKeyDown={(event) => moveProjectTab(event, index)}
                      className={cls(styles.tab, tab === tk && styles.tabActive)}
                    >
                      {tk === "chats"
                        ? <>{text("Chats", "会话")} <span className={shared.tabCount}>{chatCounts.get(selected.id) ?? 0}</span></>
                        : tk === "memory"
                          ? text("Memory", "记忆")
                          : tk === "settings"
                            ? text("Settings", "设置")
                            : text("Info", "信息")}
                    </button>
                  ))}
                </div>

                <div
                  id="project-tabpanel"
                  role="tabpanel"
                  aria-labelledby={`project-tab-${tab}`}
                  className={styles.panelFill}
                >
                {tab === "chats" && (
                  <ChatsPage
                    key={selected.id}
                    embedded
                    rail={false}
                    query={query}
                    include={includeSelected}
                  />
                )}

                {tab === "memory" && (
                  <MemoryPage
                    key={selected.id}
                    embedded
                    query={query}
                    projectId={selected.is_default ? null : selected.id}
                  />
                )}

                {tab === "settings" && (
                  <div className={TAB_SCROLL}>
                    <ProjectConfigSection projectId={selected.id} />
                    <ComputerUseAppsSection projectId={selected.id} />
                    <PermissionsSection projectId={selected.id} />
                  </div>
                )}

                {tab === "info" && (
                  <div className={TAB_SCROLL}>
                    <SettingsSection>
                      <SettingsRow label={text("Type", "类型")}>
                        <span className="text-fs-base text-text-muted">
                          {selected.is_default ? text("System (global)", "系统（全局）") : text("Project folder", "项目文件夹")}
                        </span>
                      </SettingsRow>
                      <SettingsRow label={selected.is_default ? text("Working folder", "工作目录") : text("Path", "路径")}><InfoCode>{selected.path}</InfoCode></SettingsRow>
                      <SettingsRow label={text("Stored in", "存放位置")}><InfoCode>{`~/.openprogram/projects/${selected.id}/`}</InfoCode></SettingsRow>
                      <SettingsRow label={text("Status", "状态")}>
                        <span className="text-fs-base text-text-muted">
                          {({ active: text("Active", "进行中"), paused: text("Paused", "已暂停"), done: text("Done", "已完成") } as Record<string, string>)[selected.status] ?? selected.status}
                        </span>
                      </SettingsRow>
                      <SettingsRow label={text("Chats", "会话数")}><span className="text-fs-base text-text-muted">{chatCounts.get(selected.id) ?? 0}</span></SettingsRow>
                      <SettingsRow label="ID"><InfoCode>{selected.id}</InfoCode></SettingsRow>
                    </SettingsSection>
                  </div>
                )}
                </div>
              </>
            )}
          </div>
        </RailSplit>
      </div>
  );

  if (embedded) return view;
  return <div className="main">{view}</div>;
}

/** A project without its own icon shows the main sidebar's Solar folder,
    so the sidebar, this rail and the Memory tree draw one folder; the
    selected project's folder is open, like an expanded sidebar group. */
function ProjectGlyph({ size, open = false }: { size: number; open?: boolean }) {
  return <SolarIcon name={open ? "folder-open" : "folder"} size={size} motionPreset="none" className="text-[var(--folder-color)]" aria-hidden="true" />;
}

/** A read-only path or id on the Info tab. */
function InfoCode({ children }: { children: React.ReactNode }) {
  return <code className="min-w-0 truncate font-mono text-fs-xs text-text-muted">{children}</code>;
}
