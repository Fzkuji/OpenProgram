"use client";

/**
 * The schema-driven setting rows (Model Services → Behavior, General → Web
 * server) and this computer's system access (Machines). Settings opening
 * fetches both ahead (`prefetchSystemPage`): the access check asks macOS
 * about each capability and takes most of a second.
 */
import { queryOptions, type QueryClient } from "@tanstack/react-query";

import { jsonFetch } from "@/lib/net/fetch-client";
import { QUIET_QUERY, warnOnFailure } from "./quiet-query";

export interface SettingRow {
  key: string;
  group: string;
  label: string;
  widget: "number" | "toggle" | "enum" | "status" | "text" | "json";
  apply: "live" | "next_start";
  help?: string;
  value?: unknown;
  choices?: string[];
  set?: boolean;
}

export interface Capability {
  id: string;
  label?: string;
  label_zh?: string;
  status: string;
  detail?: string;
  instruction?: string;
  can_request?: boolean;
  request_mode?: string;
  setup_group?: string;
  category?: string;
}

export interface AccessReport {
  application?: string;
  platform: string;
  host: string;
  executable: string;
  capabilities: Capability[];
}

export const settingRowsQuery = queryOptions({
  queryKey: ["settings", "rows"] as const,
  queryFn: warnOnFailure("/api/settings", () =>
    jsonFetch<{ settings?: SettingRow[] }>("/api/settings").then((data) => data.settings ?? [])),
  staleTime: 0,
  ...QUIET_QUERY,
});

export const systemAccessQuery = queryOptions({
  queryKey: ["system", "access"] as const,
  queryFn: warnOnFailure("/api/system/access", () => jsonFetch<AccessReport>("/api/system/access")),
  // Each read starts a native check (0.3 s). Settings reads it ahead as it
  // opens; the Machines page mounting a moment later uses that answer.
  staleTime: 2000,
  refetchOnWindowFocus: true,
  ...QUIET_QUERY,
});

export function prefetchSystemPage(client: QueryClient) {
  void client.prefetchQuery(settingRowsQuery);
  void client.prefetchQuery(systemAccessQuery);
}
