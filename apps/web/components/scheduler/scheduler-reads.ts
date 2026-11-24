/** The Tasks page's Scheduled reads, kept across visits and started ahead of the page. */
import { prefetchKept } from "@/lib/abilities/kept-read";

export const TASKS_KEY = "/api/scheduler/tasks";
export const MEMORY_REFS_KEY = "/api/memory/refs?limit=200";

async function readArray(url: string): Promise<unknown[]> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data: unknown = await response.json();
  return Array.isArray(data) ? data : [];
}

export const readTasks = () => readArray(TASKS_KEY);
export const readMemoryRefs = () => readArray(MEMORY_REFS_KEY);

/** The tasks and the memory the form can attach, read side by side on hover or at idle. */
export async function prefetchScheduler(): Promise<void> {
  await Promise.all([prefetchKept(TASKS_KEY, readTasks), prefetchKept(MEMORY_REFS_KEY, readMemoryRefs)]);
}
