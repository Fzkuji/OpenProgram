import { listSessionProjects } from "@/lib/projects/list-projects";
import { useSessionStore } from "@/lib/session-store";
import type { Project } from "@/lib/files/files-shared";
/** Pure project-relative path helpers used by FileTree queries and rows. */
export function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}
export function parentOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}

export function baseOf(path: string): string {
  return path.split("/").pop() || path;
}

export const projectPathCache = new Map<string, string>();

export async function projectAbsPath(projectId: string): Promise<string | null> {
  const hit = projectPathCache.get(projectId);
  if (hit) return hit;
  const sessionId = useSessionStore.getState().currentSessionId ?? "";
  const data = await listSessionProjects<{ projects: Project[]; session_id?: string | null }>(sessionId);
  const p = data?.projects?.find((x) => x.id === projectId);
  if (!p?.path) return null;
  projectPathCache.set(projectId, p.path);
  return p.path;
}

