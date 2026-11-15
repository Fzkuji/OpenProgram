"use client";

/**
 * The data of Settings > System, read together so the page appears whole.
 * Settings opening fetches it ahead (`prefetchSystemPage`): the access check
 * asks macOS about each capability and takes most of a second.
 */
import { queryOptions, type QueryClient } from "@tanstack/react-query";

import { jsonFetch } from "@/lib/net/fetch-client";

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
  queryFn: () => jsonFetch<{ settings?: SettingRow[] }>("/api/settings").then((data) => data.settings ?? []),
  staleTime: 0,
});

export const systemAccessQuery = queryOptions({
  queryKey: ["system", "access"] as const,
  queryFn: () => jsonFetch<AccessReport>("/api/system/access"),
  staleTime: 0,
  refetchOnWindowFocus: true,
});

export function prefetchSystemPage(client: QueryClient) {
  void client.prefetchQuery(settingRowsQuery);
  void client.prefetchQuery(systemAccessQuery);
}
