/**
 * The History page's project rail, kept across visits. Its read is the
 * sidebar's own project list (one request serves both), started ahead of
 * the page on hover and at idle.
 */
import { prefetchKept } from "@/lib/abilities/kept-read";
import { listSessionProjects } from "./list-projects";

export const HISTORY_PROJECTS_KEY = "history:projects";

/** The home project leads; the rest keep the registry's order. */
export function systemFirst<T extends { is_default?: boolean }>(projects: T[]): T[] {
  return [...projects].sort((a, b) => Number(Boolean(b.is_default)) - Number(Boolean(a.is_default)));
}

/** The projects, home first. `projects: null` means the registry could not
 *  be read, not that there are none: that is a failure, tried again. */
export async function readHistoryProjects<T extends { is_default?: boolean }>(): Promise<T[]> {
  const data = await listSessionProjects<{ projects?: T[] | null; session_id?: string | null }>(null);
  if (!data || !Array.isArray(data.projects)) throw new Error("the project list could not be read");
  return systemFirst(data.projects);
}

export function prefetchHistory(): Promise<void> {
  return prefetchKept(HISTORY_PROJECTS_KEY, readHistoryProjects);
}
