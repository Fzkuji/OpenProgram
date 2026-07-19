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

  return [...projects]
    .sort((a, b) => {
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
