export interface ProjectGroupSource {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  session_ids?: readonly string[];
}

export interface ProjectGroupItem {
  id: string;
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
    const projectId = owner.get(item.id) ?? defaultId;
    if (!projectId) continue;
    const groupItems = byProject.get(projectId);
    if (groupItems) groupItems.push(item);
    else byProject.set(projectId, [item]);
  }

  const rank = new Map(order.map((id, index) => [id, index]));
  return [...projects]
    .sort((a, b) => {
      const delta = (rank.get(a.id) ?? order.length) - (rank.get(b.id) ?? order.length);
      if (delta) return delta;
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
      return a.name.localeCompare(b.name);
    })
    .map((project) => ({
      key: project.id,
      name: project.name,
      path: project.path,
      items: byProject.get(project.id) ?? [],
    }))
    .filter((group) => group.items.length > 0);
}

/** Move one project without changing the relative order of any other project. */
export function moveProject(order: readonly string[], source: string, target: string, side: "before" | "after"): string[] {
  if (source === target || !order.includes(source) || !order.includes(target)) return [...order];
  const next = order.filter((id) => id !== source);
  next.splice(next.indexOf(target) + (side === "after" ? 1 : 0), 0, source);
  return next;
}
