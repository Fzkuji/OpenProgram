"use client";

/** The display name, bundle id and icon of apps that computer_use rules and
 *  approvals name. The worker looks them up without launching anything; the
 *  answers are cached for the page's lifetime, since an app's icon and name
 *  do not change while it runs. */
import { useEffect, useState } from "react";

import { wsRequest } from "@/lib/net/ws/ws-request";

export interface ComputerUseApp {
  query: string;
  name: string;
  bundle_id: string;
  icon: string;
  installed: boolean;
  /** The worker's platform ("darwin", "linux"): what a step does to the user's own input differs. */
  platform?: string;
}

const cache = new Map<string, ComputerUseApp>();

function placeholder(query: string): ComputerUseApp {
  return { query, name: query, bundle_id: "", icon: "", installed: false };
}

async function lookUp(queries: string[]): Promise<void> {
  const missing = queries.filter((query) => !cache.has(query));
  if (!missing.length) return;
  const requestId = crypto.randomUUID();
  const reply = await wsRequest<{ request_id?: string; apps?: ComputerUseApp[] }>(
    "computer_use_apps", { apps: missing, request_id: requestId }, "computer_use_apps",
    (data) => data.request_id === requestId, 8000,
  );
  // Without an answer the next render asks again; the name stands in meanwhile.
  for (const app of reply?.apps ?? []) cache.set(app.query, app);
}

let running: { at: number; apps: ComputerUseApp[] } | null = null;
const RUNNING_FRESH_MS = 10_000;

/** The running apps, as the composer's @ menu offers them; kept for a few
 *  seconds so typing a name does not ask again on every key. */
export async function runningApps(): Promise<ComputerUseApp[]> {
  if (running && Date.now() - running.at < RUNNING_FRESH_MS) return running.apps;
  const requestId = crypto.randomUUID();
  const reply = await wsRequest<{ request_id?: string; apps?: ComputerUseApp[] }>(
    "computer_use_apps", { running: true, request_id: requestId }, "computer_use_apps",
    (data) => data.request_id === requestId, 8000,
  );
  if (!reply?.apps) return running?.apps ?? [];
  running = { at: Date.now(), apps: reply.apps };
  return reply.apps;
}

/** Apps keyed by the name they were asked for; unknown ones show that name. */
export function useComputerUseApps(queries: string[]): Record<string, ComputerUseApp> {
  const key = Array.from(new Set(queries.map((query) => query.trim()).filter(Boolean))).sort().join("\n");
  const [, setVersion] = useState(0);
  useEffect(() => {
    if (!key) return;
    let live = true;
    void lookUp(key.split("\n")).then(() => { if (live) setVersion((value) => value + 1); });
    return () => { live = false; };
  }, [key]);
  const out: Record<string, ComputerUseApp> = {};
  for (const query of key ? key.split("\n") : []) out[query] = cache.get(query) ?? placeholder(query);
  return out;
}
