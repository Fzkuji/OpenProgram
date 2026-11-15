/**
 * Parent-folder prefixes that tell same-named projects apart.
 *
 * Two folders called "NeurIPS 2026" are two projects, but a list that
 * shows only names draws them identically. For each name shared by more
 * than one project this returns the shortest run of parent folders that
 * differs between them ("Conferences", "Dropbox"); a name used once gets
 * no prefix. Names compare case-insensitively.
 */
export type NamedProject = { id: string; name: string; path?: string | null };

const MAX_DEPTH = 3;

function parentSegments(path: string | null | undefined): string[] {
  const parts = (path ?? "").split(/[\\/]+/).filter(Boolean);
  return parts.slice(0, -1);
}

export function duplicateNamePrefixes(projects: NamedProject[]): Map<string, string> {
  const groups = new Map<string, NamedProject[]>();
  for (const project of projects) {
    const key = project.name.trim().toLocaleLowerCase();
    groups.set(key, [...(groups.get(key) ?? []), project]);
  }
  const prefixes = new Map<string, string>();
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const parents = group.map((project) => parentSegments(project.path));
    let depth = 1;
    for (; depth < MAX_DEPTH; depth++) {
      const labels = parents.map((segments) => segments.slice(-depth).join("/"));
      if (new Set(labels).size === labels.length) break;
    }
    group.forEach((project, index) => {
      const label = parents[index].slice(-depth).join("/");
      if (label) prefixes.set(project.id, label);
    });
  }
  return prefixes;
}
