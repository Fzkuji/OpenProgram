/** The MCP page's reads, kept across visits and started ahead of the page. */
import { jsonFetch } from "@/lib/net/fetch-client";
import { prefetchKept } from "@/lib/abilities/kept-read";
import type { ServerDetail, ServerStatus } from "./mcp-detail-view";

export const MCP_SERVERS_KEY = "/api/mcp/servers";

export const mcpDetailKey = (name: string) => `/api/mcp/servers/${encodeURIComponent(name)}`;

export async function readMcpServers(): Promise<ServerStatus[]> {
  const data = await jsonFetch<{ servers?: ServerStatus[] }>(MCP_SERVERS_KEY);
  return Array.isArray(data.servers) ? data.servers : [];
}

export function readMcpDetail(name: string): Promise<ServerDetail> {
  return jsonFetch<ServerDetail>(mcpDetailKey(name));
}

/** The MCP page's first read, started on hover or at idle. */
export function prefetchMcp(): Promise<void> {
  return prefetchKept(MCP_SERVERS_KEY, readMcpServers);
}
