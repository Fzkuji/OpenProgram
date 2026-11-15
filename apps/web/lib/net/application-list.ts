/**
 * application-list — the installed applications that every Launchpad and
 * the Applications page share, with the built-in applications the
 * Applications page switches on and off.
 *
 * A page opening shows the last list at once and refreshes it behind; the
 * list is kept in localStorage, so a cold start paints the Launchpad
 * without waiting for a busy server. Same localStorage +
 * ``useSyncExternalStore`` shape as ``lib/tabs/launchpad/launchpad-store.ts``.
 */

import { useSyncExternalStore } from "react";
import { applicationRequest, type ApplicationDefinition, type BuiltinApplication } from "./applications";

const STORAGE_KEY = "application_list";
const BUILTIN_STORAGE_KEY = "application_builtin_list";

export interface ApplicationList {
  applications: ApplicationDefinition[];
  builtin: BuiltinApplication[];
  /** The list came from the server, now or on an earlier visit. */
  loaded: boolean;
}

const EMPTY: ApplicationList = { applications: [], builtin: [], loaded: false };
const listeners = new Set<() => void>();
let current: ApplicationList | null = null;
let inflight: Promise<ApplicationDefinition[]> | null = null;

function readArray<T>(key: string): T[] | null {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(key) || "null");
    return Array.isArray(parsed) ? parsed as T[] : null;
  } catch {
    return null;
  }
}

function read(): ApplicationList {
  if (typeof window === "undefined") return EMPTY;
  const applications = readArray<ApplicationDefinition>(STORAGE_KEY);
  if (applications === null) return EMPTY;
  return { applications, builtin: readArray<BuiltinApplication>(BUILTIN_STORAGE_KEY) ?? [], loaded: true };
}

export function getApplicationList(): ApplicationList {
  if (current === null) current = read();
  return current;
}

function publish(applications: ApplicationDefinition[], builtin: BuiltinApplication[]): void {
  current = { applications, builtin, loaded: true };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(applications));
    window.localStorage.setItem(BUILTIN_STORAGE_KEY, JSON.stringify(builtin));
  } catch { /* holds for this page only */ }
  for (const listener of listeners) listener();
}

/** Ask the server for the list. Callers arriving while a request is out
 *  share it; `fresh` starts a new one, for reading back a change just made. */
export function refreshApplications({ fresh = false }: { fresh?: boolean } = {}): Promise<ApplicationDefinition[]> {
  if (inflight && !fresh) return inflight;
  const request = applicationRequest<{ applications: ApplicationDefinition[]; builtin?: BuiltinApplication[] }>("/api/applications")
    .then(value => { publish(value.applications, value.builtin ?? []); return value.applications; })
    .finally(() => { if (inflight === request) inflight = null; });
  inflight = request;
  return request;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useApplicationList(): ApplicationList {
  return useSyncExternalStore(subscribe, getApplicationList, () => EMPTY);
}
