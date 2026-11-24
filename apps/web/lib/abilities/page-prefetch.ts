/**
 * The left-nav pages' first reads and code, started before a page opens:
 * on hover or focus of its nav row, and once at idle after the app starts.
 * A page then draws its list at once instead of waiting after the click.
 * Each page's reads are kept (lib/abilities/kept-read.ts or its own store),
 * and a page reads again behind what it shows, so a prefetch that fails
 * only warns.
 */
import { prefetchAgents } from "@/lib/agents/agents-query";
import { prefetchAgentModels } from "@/lib/agents/agent-models";
import { prefetchPlugins } from "@/lib/abilities/plugins-store";
import { prefetchSkills } from "@/lib/abilities/skills-store";
import { prefetchHistory } from "@/lib/projects/history-projects";
import { refreshApplications } from "@/lib/net/application-list";
import { prefetchMcp } from "@/components/mcp/mcp-reads";
import { prefetchPrograms } from "@/components/programs/programs-reads";
import { prefetchScheduler } from "@/components/scheduler/scheduler-reads";
import { prefetchMemory } from "@/components/memory/data/reads";

export type PrefetchPage =
  | "agents" | "programs" | "plugins" | "skills" | "mcp"
  | "applications" | "history" | "memory" | "tasks";

function warn(page: string) {
  return (cause: unknown) => { console.warn(`[prefetch] ${page}`, cause); };
}

// The page code the route loads lazily, fetched alongside its data.
const abilitiesCode = () => import("@/components/capabilities/capabilities-page");

const STARTS: Record<PrefetchPage, () => Promise<unknown>> = {
  agents: () => Promise.all([prefetchAgents(), prefetchAgentModels()]),
  programs: () => Promise.all([prefetchPrograms(), abilitiesCode()]),
  plugins: () => Promise.all([prefetchPlugins(), abilitiesCode()]),
  skills: () => Promise.all([prefetchSkills(), abilitiesCode()]),
  mcp: () => Promise.all([prefetchMcp(), abilitiesCode()]),
  applications: () => Promise.all([refreshApplications(), import("@/components/applications/applications-page")]),
  history: () => Promise.all([prefetchHistory(), import("@/components/history/history-page")]),
  memory: () => Promise.all([prefetchMemory(), import("@/components/history/history-page")]),
  tasks: () => Promise.all([prefetchScheduler(), import("@/components/tasks/tasks-page")]),
};

/** A page read ahead less than this long ago is not asked for again on hover. */
const QUIET_MS = 15_000;
const started = new Map<PrefetchPage, number>();

/** Start a page's first reads and code; a repeat within 15 s does nothing. */
export function prefetchPage(page: PrefetchPage): Promise<void> {
  const now = Date.now();
  const last = started.get(page);
  if (last !== undefined && now - last < QUIET_MS) return Promise.resolve();
  started.set(page, now);
  return STARTS[page]().then(() => undefined, warn(page));
}

let idleStarted = false;

/** Once per app start, when the browser is idle: every page, one after another. */
export function prefetchPagesWhenIdle(): () => void {
  if (idleStarted || typeof window === "undefined") return () => {};
  idleStarted = true;
  let stopped = false;
  let running = false;
  const run = async () => {
    running = true;
    for (const page of Object.keys(STARTS) as PrefetchPage[]) {
      if (stopped) return;
      await prefetchPage(page);
    }
  };
  // Stopped before it ran (a remount): the next mount schedules it again.
  const stop = (cancel: () => void) => () => { stopped = true; cancel(); if (!running) idleStarted = false; };
  if (typeof window.requestIdleCallback === "function") {
    const handle = window.requestIdleCallback(() => { void run(); }, { timeout: 3000 });
    return stop(() => window.cancelIdleCallback?.(handle));
  }
  const timer = setTimeout(() => { void run(); }, 1500);
  return stop(() => clearTimeout(timer));
}
