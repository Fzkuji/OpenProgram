export interface ProjectGroupSource {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  hidden?: boolean;
  session_ids?: readonly string[];
}

export type ProjectSort = "recency" | "oldest" | "name" | "manual";

export interface ProjectGroupItem {
  archived?: boolean;
  id: string;
  updated_at?: number;
  created_at?: number;
}

export interface ProjectGroup<T extends ProjectGroupItem> {
  key: string;
  name: string;
  path: string;
  items: T[];
}

/** Join visible sessions to registry projects and omit every empty group. */
export function projectGroups<T extends ProjectGroupItem>(
  projects: readonly ProjectGroupSource[],
  items: readonly T[],
  order: readonly string[] = [],
  options: { sort?: ProjectSort; pinned?: readonly string[]; includeEmpty?: boolean; includeHidden?: boolean; activityItems?: readonly T[] } = {},
): ProjectGroup<T>[] {
  const owner = new Map<string, string>();
  for (const project of projects) {
    for (const sessionId of project.session_ids || []) {
      if (!owner.has(sessionId)) owner.set(sessionId, project.id);
    }
  }

  const defaultId = projects.find((project) => project.is_default)?.id ?? null;
  const byProject = new Map<string, T[]>();
  for (const item of items) {
    if (item.archived && !options.includeEmpty) continue;
    const projectId = owner.get(item.id) ?? defaultId;
    if (!projectId) continue;
    const groupItems = byProject.get(projectId);
    if (groupItems) groupItems.push(item);
    else byProject.set(projectId, [item]);
  }

  const activity = new Map<string, number>();
  for (const item of options.activityItems ?? items) {
    const projectId = owner.get(item.id) ?? defaultId;
    if (!projectId) continue;
    const timestamp = item.updated_at || item.created_at || 0;
    activity.set(projectId, Math.max(activity.get(projectId) ?? 0, Number.isFinite(timestamp) ? timestamp : 0));
  }
  const pinned = new Set(options.pinned);
  const rank = new Map(order.map((id, index) => [id, index]));
  return projects.filter(project => options.includeHidden || !project.hidden)
    .sort((a, b) => {
      const pinDelta = Number(pinned.has(b.id)) - Number(pinned.has(a.id));
      if (pinDelta) return pinDelta;
      if (options.sort === "recency" || options.sort === "oldest") {
        const delta = (activity.get(b.id) ?? 0) - (activity.get(a.id) ?? 0);
        if (delta) return options.sort === "oldest" ? -delta : delta;
      } else if (options.sort !== "name") {
        const delta = (rank.get(a.id) ?? order.length) - (rank.get(b.id) ?? order.length);
        if (delta) return delta;
      }
      if (options.sort === "name") return a.name.localeCompare(b.name);
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
      return a.name.localeCompare(b.name);
    })
    .map((project) => ({
      key: project.id,
      name: project.name,
      path: project.path,
      items: byProject.get(project.id) ?? [],
    }))
    .filter((group) => options.includeEmpty || group.items.length > 0);
}

/** Move one project without changing the relative order of any other project. */
export function moveProject(order: readonly string[], source: string, target: string, side: "before" | "after"): string[] {
  if (source === target || !order.includes(source) || !order.includes(target)) return [...order];
  const next = order.filter((id) => id !== source);
  next.splice(next.indexOf(target) + (side === "after" ? 1 : 0), 0, source);
  return next;
}

/** Registry IDs keep filtering stable after renames; accept old name preferences. */
export function filterProjectItems<T extends ProjectGroupItem & {project?: string}>(projects: readonly ProjectGroupSource[], items: readonly T[], selection: string): T[] {
  const project = projects.find(p=>p.id===selection) ?? projects.find(p=>p.name===selection);
  if (!project) return items.filter(item=>item.project===selection);
  const owned = new Set(project.session_ids);
  const claimed = new Set(projects.flatMap(p=>[...(p.session_ids ?? [])]));
  return items.filter(item=>owned.has(item.id) || (project.is_default && !claimed.has(item.id)));
}
